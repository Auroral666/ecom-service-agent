# 客服流式输出模块

`engine.py` 适配模型正文直出流、回复元数据与工具调用；`routes.py` 管理 SSE、心跳、背压和受控调试记录；`results.py` 从真实工具结果提取业务卡片；`security.py` 负责敏感字段处理。

## 接入方式

单 Agent 和多 Agent 的 `chat()` 增加可选 `on_event`，在原有 ReAct 调用周围使用 `streaming_session(self, on_event)`。没有回调时走原有普通路径，工具、记忆和保存逻辑不变。适配器只绑定本轮实例，退出时在 `finally` 中还原；不修改全局 SDK。此机制沿用现有 `ChatService.lock` 的串行约束，不能移除该锁后直接并发复用同一 Agent。

`app/api/main.py` 只注册路由；`app/api/service.py` 传递事件并保存展示数据。工具来源通过 `ToolManager.source_for()` 读取，不让界面访问内部路由表。

## 三类数据

- 客户回复：ReAct 模型的可见正文通过 `response_delta` 立即发送。`response_end` 将工具轮说明标记为 `activity`，无工具调用的最终轮标记为 `answer`。最终正文直接保存，之后仅提取元数据，不二次生成或改写正文。兼容路径仍支持 JSON 的 `reply` 增量 `delta`；校验和保存完成后发送 `done`。前端让首段先绘制，不以定时器模拟打字，不保证每个网络片段恰好一个汉字。
- 业务事件：`status`、`tool`、`business` 分别表达处理状态、等待/执行/成功/失败和真实业务结果。耗时来自计时器，缺失金额、审核状态、Token 不补造。
- 调试详情：模型消息、参数、工具原始返回、Memory 和统计不放进普通 SSE。调试接口要求本机访问、允许的 Host/Origin 和临时能力令牌；敏感字段会脱敏。服务重启后记录和令牌失效。

不采集或展示模型隐藏推理原文；处理过程展示的是执行状态、工具事件和简短说明。模型服务未返回的 usage 显示“服务未返回”。前面的模型决策和业务查询仍有真实等待时间。

## 事件协议

`POST /api/sessions/{id}/chat/stream` 返回 `text/event-stream`。每帧为 `data: {JSON}\n\n`，可能出现 `start`、`status`、`tool`、`business`、`response_delta`、`response_end`、`delta`、`done`、`error`；空闲时用 SSE 注释心跳保活。`done` 是唯一成功完成标志。

浏览器断开后不重放退款等业务操作，已启动的请求继续完成保存。客户端保留收到的部分文本并提示重新加载核对记录。此版本不提供断点续传或自动重新提交。

## 调试

本机页面用 `POST /api/debug/access`，附 `X-Debug-Request: 1` 获取本次进程的能力令牌；`GET /api/debug/traces/{request_id}` 需附 `X-Debug-Token`。令牌不写进 URL 或浏览器持久存储。此限制适用于当前单用户本机项目；公网部署需要复用正式登录与角色授权，不能把本机限制当作多用户认证。

调试面板的“总耗时”和运行对比中的“耗时”均读取 `elapsed_ms`，表示从请求开始到处理完成的完整耗时。后端仍保留 `first_token_ms`（首次模型调用到第一个非空可见文本增量）与 `model_started_at_ms` 用于排障，面板不再以首字耗时替代总耗时。

## 验证

隔离测试：`python -B -X utf8 -m pytest -q tests/test_first_token.py tests/test_streaming.py tests/test_web_api.py tests/test_embedding_configuration.py --basetemp=frontend/.ui-review/pytest-new-run`。请给临时目录使用新的名称，pytest 会管理其中内容。

前端：在 `frontend` 运行 `npm run build`。浏览器脚本与运行报告位于 `frontend/.ui-review/`。原 `test_react_agent.py`、`test_multi_agent.py` 是调用真实模型的演示脚本，不应混作无网络单元测试批量执行。
