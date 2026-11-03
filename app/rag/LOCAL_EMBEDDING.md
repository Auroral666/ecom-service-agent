# 独立嵌入服务与本地 Jina

聊天模型和嵌入模型分别配置。默认 `.env.example` 使用远程嵌入；本地 Jina 适配是可选路径，不会替代聊天模型。

## 配置方式

```dotenv
EMBEDDING_BASE_URL=http://127.0.0.1:18181/v1
EMBEDDING_MODEL=jina-embeddings-v3
EMBEDDING_API_KEY=local
EMBEDDING_TASK_AWARE=true
```

`local` 是本机兼容接口的占位符，不是外部服务密钥。不同的远程嵌入服务地址必须提供独立 `EMBEDDING_API_KEY`，代码不会默认向它发送聊天密钥。普通远程 API 通常保持 `EMBEDDING_TASK_AWARE=false`。

## 本地模型准备

需要单独的模型运行环境，具备 PyTorch、Transformers 以及 Jina 模型实现依赖，并准备完整的 `jina-embeddings-v3` 权重、配置和匹配的模型代码缓存。模型权重及使用场景须遵守模型自身条款；本仓库不分发权重。

`app/rag/local_service.py` 使用离线加载，不能仅凭一个模型名称自动下载模型。当前脚本包含原机器的缓存默认路径；其他机器必须传入实际 `--model-path`：

```powershell
& '你的模型环境\python.exe' -B -X utf8 app/rag/local_service.py --model-path '你的完整本地模型快照目录' --port 18181 --threads 2
```

确认 `GET http://127.0.0.1:18181/health` 返回 `status: ok` 后，设置上面的 `.env` 配置，再构建知识库索引。

## 索引和检索

```powershell
# 在项目根目录、使用应用的 Python 环境执行：
python -B -X utf8 -m app.scripts.build_kb_index
```

链路：`app/knowledge/*.md → 按标题分块 → retrieval.passage → 本地索引`；问题通过 `retrieval.query` 编码，再进行余弦检索。模型返回 1024 维归一化向量。

索引保存在 `app/sessions/`，不上传 GitHub。切换模型、改动知识文档后需要重建，并重启后端以清除旧 Retriever 缓存。

## 一键启动与停止

当配置使用本机 18181 服务时，根目录 `启动项目.cmd` 会检查已有嵌入服务；若尚未运行，使用 `ECOM_EMBEDDING_PYTHON` 指定的模型环境启动。需要自定义模型路径时，先按上面的显式命令启动，再使用一键入口复用。

```powershell
$env:ECOM_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:ECOM_EMBEDDING_PYTHON = '你的模型环境\python.exe'
.\启动项目.cmd
```

日志和进程记录在 `.runtime/`。`停止项目.cmd` 停止经核实属于本项目的前端、后端与嵌入服务；不会删除账号、会话或索引。完整操作见 [启动停止说明](../../启动停止说明.md)。
