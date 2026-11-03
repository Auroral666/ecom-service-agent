param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimeRoot = Join-Path $projectRoot '.runtime'
$logRoot = Join-Path $runtimeRoot 'logs'
$statePath = Join-Path $runtimeRoot 'services.json'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$records = @{}
$created = New-Object System.Collections.Generic.List[string]
$servicePorts = @{ frontend = 5173; backend = 18080; embedding = 18181 }
$serviceFiles = @{
    frontend = Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js'
    backend = Join-Path $PSScriptRoot 'run-web-backend.py'
    embedding = Join-Path $projectRoot 'app\rag\local_service.py'
}

function Save-State {
    $data = @{ projectRoot = $projectRoot; services = $records }
    $temporaryPath = $statePath + '.tmp'
    $data | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $statePath -Force
}

function Remember-Process($name, $process) {
    $records[$name] = @{
        processId = [int]$process.ProcessId
        createdAt = $process.CreationDate.ToUniversalTime().ToString('o')
        commandLine = $process.CommandLine
    }
    Save-State
}

function Get-TrackedProcess($name) {
    if (-not $records.ContainsKey($name)) { return $null }
    $saved = $records[$name]
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.processId)"
    if ($process -and $process.CreationDate.ToUniversalTime().ToString('o') -eq $saved.createdAt -and
        $process.CommandLine -ceq $saved.commandLine) { return $process }
    # A recycled PID must never cause an unrelated process to be stopped.
    $records.Remove($name)
    Save-State
    return $null
}

function Get-ServiceProcess($name) {
    $process = Get-TrackedProcess $name
    if ($process) { return $process }
    $expected = $serviceFiles[$name]
    $matches = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.Name -in @('python.exe', 'pythonw.exe', 'node.exe') -and
        $_.CommandLine.Replace('/', '\').IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($matches.Count -gt 1) { throw "$name 有多个本项目进程，请检查后重试。" }
    if ($matches.Count -eq 1) {
        Remember-Process $name $matches[0]
        return $matches[0]
    }
    return $null
}

function Get-ListenerIds($port) {
    @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-ProcessTreeMember($processId, $rootProcess) {
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    $visited = New-Object 'System.Collections.Generic.HashSet[int]'
    while ($candidate -and $visited.Add([int]$candidate.ProcessId)) {
        if ($candidate.CreationDate -lt $rootProcess.CreationDate) { return $false }
        if ($candidate.ProcessId -eq $rootProcess.ProcessId) {
            return $candidate.CreationDate -eq $rootProcess.CreationDate -and
                $candidate.CommandLine -ceq $rootProcess.CommandLine
        }
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($candidate.ParentProcessId)"
        if ($parent -and $parent.CreationDate -gt $candidate.CreationDate) { return $false }
        $candidate = $parent
    }
    return $false
}

function Test-Service($name) {
    try {
        if ($name -eq 'frontend') {
            $page = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173' -TimeoutSec 2
            return $page.StatusCode -eq 200 -and $page.Content.Contains('/@vite/client')
        }
        $url = if ($name -eq 'backend') { 'http://127.0.0.1:18080/api/health' } else { 'http://127.0.0.1:18181/health' }
        $response = Invoke-RestMethod -Uri $url -TimeoutSec 2
        if ($name -eq 'embedding') { return $response.status -eq 'ok' -and $response.model -eq 'jina-embeddings-v3' }
        return $response.status -eq 'ok' -and $null -ne $response.model_configured
    } catch { return $false }
}

function Stop-ServiceProcess($name) {
    $process = Get-ServiceProcess $name
    if (-not $process) {
        Write-Host "  $name：未运行。"
        if (@(Get-ListenerIds $servicePorts[$name]).Count) {
            Write-Host "  端口 $($servicePorts[$name]) 有未登记服务，未结束该进程。" -ForegroundColor Yellow
        }
        return
    }
    Write-Host "  正在停止 $name（PID $($process.ProcessId)）及其子进程……"
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Null
    # Process teardown may finish shortly after taskkill returns.
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $remaining = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)"
        if (-not $remaining -or $remaining.CreationDate -ne $process.CreationDate) { break }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    if ($remaining -and $remaining.CreationDate -eq $process.CreationDate) { throw "$name 停止失败。" }
    $records.Remove($name)
    Save-State
    Write-Host "  $name 已停止。" -ForegroundColor Green
}

function Ensure-Service($name, $executable, $arguments, $workingDirectory, $timeoutSeconds) {
    $process = Get-ServiceProcess $name
    $listenerIds = @(Get-ListenerIds $servicePorts[$name])
    # Windows venv Python may delegate to a child that owns the listening socket.
    if ($listenerIds.Count -and (-not $process -or @($listenerIds | Where-Object { -not (Test-ProcessTreeMember $_ $process) }).Count)) {
        throw "端口 $($servicePorts[$name]) 被其他进程占用（PID $($listenerIds -join ',')）。为避免影响其他程序，未结束这些进程。"
    }
    if (-not $process) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $outputPath = Join-Path $logRoot "$name-$stamp.out.log"
        $errorPath = Join-Path $logRoot "$name-$stamp.err.log"
        $launched = Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $workingDirectory -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath -PassThru
        $created.Add($name)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($launched.Id)"
        if (-not $process) { throw "$name 启动后立即退出，请查看 $errorPath" }
        Remember-Process $name $process
        Write-Host "  已创建 $name（PID $($process.ProcessId)），等待就绪。"
    } else { Write-Host "  $name 已在运行，复用 PID $($process.ProcessId)。" }
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $nextUpdate = Get-Date
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-TrackedProcess $name)) { throw "$name 已退出，请查看日志：$logRoot" }
        if (Test-Service $name) {
            Write-Host "  $name 就绪，端口 $($servicePorts[$name])。" -ForegroundColor Green
            return
        }
        if ((Get-Date) -ge $nextUpdate) {
            Write-Host "  $name 正在初始化，请稍候……"
            $nextUpdate = (Get-Date).AddSeconds(5)
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$name 在 $timeoutSeconds 秒内未就绪。日志：$logRoot"
}

function Find-Runtime($override, $candidates, $command) {
    if ($override) {
        if (-not (Test-Path -LiteralPath $override -PathType Leaf)) { throw "指定的运行环境不存在：$override" }
        return $override
    }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate } }
    $found = Get-Command $command -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    throw "找不到 $command 运行环境，请安装或设置对应的 ECOM 环境变量。"
}

$lockHandle = $null
try {
    try { $lockHandle = [IO.File]::Open((Join-Path $runtimeRoot 'control.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw '另一个启动或停止脚本正在执行，请等待其完成。' }
    if (Test-Path -LiteralPath $statePath) {
        $savedState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($savedState.projectRoot -ne $projectRoot) { throw '运行记录来自其他目录，未操作其中的进程。' }
        foreach ($property in $savedState.services.PSObject.Properties) { $records[$property.Name] = $property.Value }
    }
    Write-Host "项目目录：$projectRoot"
    if ($Action -eq 'Stop') {
        $step = 0
        foreach ($name in @('frontend', 'backend', 'embedding')) {
            $step++
            Write-Host "`n[$step/3] 停止 $name"
            Stop-ServiceProcess $name
        }
        Write-Host "`n停止流程完成；项目文件、账号和聊天记录已保留。" -ForegroundColor Green
    } elseif ($Action -eq 'Status') {
        foreach ($name in @('embedding', 'backend', 'frontend')) {
            $process = Get-ServiceProcess $name
            Write-Host "$name | PID=$($process.ProcessId) | port=$($servicePorts[$name]) | healthy=$(Test-Service $name)"
        }
    } else {
        Write-Host "`n[1/5] 检查 Python、Node.js、项目配置和前端依赖"
        Set-Location -LiteralPath $projectRoot
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        $python = Find-Runtime $env:ECOM_PYTHON @((Join-Path $projectRoot '.venv\Scripts\python.exe')) 'python.exe'
        $node = Find-Runtime $env:ECOM_NODE @('F:\NodeJS\nodejs\node.exe') 'node.exe'
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) { throw '缺少 .env，请先完成模型和项目配置。' }
        if (-not (Test-Path -LiteralPath $serviceFiles.frontend)) { throw '缺少前端依赖，请先在 frontend 目录执行 npm install。' }
        $localEmbeddingResult = & $python -B -X utf8 -c "import uvicorn, fastapi; from app.config.settings import settings; print('1' if settings.embedding_base_url.rstrip('/') == 'http://127.0.0.1:18181/v1' else '0')" 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Python 依赖或配置检查失败：$localEmbeddingResult" }
        $useLocalEmbedding = [string]($localEmbeddingResult | Select-Object -Last 1) -eq '1'
        Write-Host "  Python：$python（UTF-8）；Node.js：$node"
        Write-Host "`n[2/5] 准备本地向量服务"
        if ($useLocalEmbedding) {
            $embeddingPython = Find-Runtime $env:ECOM_EMBEDDING_PYTHON @('F:\anaconda\anaconda3\envs\rag-env\python.exe') 'python.exe'
            Ensure-Service 'embedding' $embeddingPython @('-B', '-X', 'utf8', ('"' + $serviceFiles.embedding + '"'), '--port', '18181') $projectRoot 120
        } else { Write-Host '  当前配置不使用本机 18181 向量服务，跳过。' }
        Write-Host "`n[3/5] 启动后端（UTF-8 编码）"
        Ensure-Service 'backend' $python @('-B', '-X', 'utf8', ('"' + $serviceFiles.backend + '"')) $projectRoot 60
        Write-Host "`n[4/5] 启动前端"
        Ensure-Service 'frontend' $node @(('"' + $serviceFiles.frontend + '"'), '--host', '127.0.0.1', '--port', '5173', '--strictPort') (Join-Path $projectRoot 'frontend') 30
        Write-Host "`n[5/5] 检查前端到后端的接口连接"
        $proxy = Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/health' -TimeoutSec 10
        if ($proxy.status -ne 'ok') { throw '前端到后端的接口检查未通过。' }
        Write-Host "`n启动完成！" -ForegroundColor Green
        Write-Host '  前端入口：http://127.0.0.1:5173'
        Write-Host '  客户页面：http://127.0.0.1:5173/chat'
        Write-Host '  客服页面：http://127.0.0.1:5173/support'
        Write-Host '  后端地址：http://127.0.0.1:18080'
        Write-Host '  接口文档：http://127.0.0.1:18080/docs'
        Write-Host '  健康检查：http://127.0.0.1:18080/api/health'
        if ($useLocalEmbedding) { Write-Host '  向量服务：http://127.0.0.1:18181/v1' }
        Write-Host '  访问链路：浏览器 -> 前端 5173 -> /api 代理 -> 后端 18080'
        if (-not $proxy.model_configured) { Write-Host '  提醒：模型密钥尚未配置，AI 回复暂不可用。' -ForegroundColor Yellow }
        Write-Host "  日志目录：$logRoot"
        Write-Host '  服务已在后台运行；关闭本窗口不会停止服务。请双击“停止项目.cmd”结束。'
    }
} catch {
    Write-Host "`n操作失败：$($_.Exception.Message)" -ForegroundColor Red
    if ($Action -eq 'Start' -and $created.Count) {
        Write-Host '正在清理本次启动创建的服务……'
        for ($index = $created.Count - 1; $index -ge 0; $index--) {
            try { Stop-ServiceProcess $created[$index] } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
        }
    }
    Write-Host "日志目录：$logRoot"
    exit 1
} finally { if ($lockHandle) { $lockHandle.Dispose() } }
