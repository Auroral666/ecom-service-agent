"""SSE transport and capability-protected, bounded local debug records."""
import asyncio
from collections import OrderedDict
import hmac
import json
import logging
import secrets
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.config.settings import settings
from app.streaming.security import redact, public_event

logger = logging.getLogger(__name__)
def register_streaming(app, service):
    traces = OrderedDict()
    debug_token = secrets.token_urlsafe(32)

    def local_access(request):
        host = request.url.hostname
        origin = request.headers.get('origin')
        return (request.client is not None and request.client.host in {'127.0.0.1', '::1', 'localhost'}
                and host in {'127.0.0.1', 'localhost', '::1'}
                and origin in {None, 'http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:18080', 'http://localhost:18080'})

    @app.post('/api/debug/access')
    async def debug_access(request: Request):
        if not local_access(request) or request.headers.get('x-debug-request') != '1':
            raise HTTPException(403, '调试功能仅允许本机页面访问')
        return {'token': debug_token}

    @app.get('/api/debug/traces/{request_id}')
    async def debug_trace(request_id: str, request: Request):
        token = request.headers.get('x-debug-token', '')
        if not local_access(request) or not hmac.compare_digest(token, debug_token):
            raise HTTPException(403, '无权访问调试信息')
        if request_id not in traces:
            raise HTTPException(404, '该记录不在当前进程中，可能已过期或服务已重启')
        return traces[request_id]

    @app.post('/api/sessions/{session_id}/chat/stream')
    async def chat_stream(session_id: UUID, payload: ChatRequest):
        # Validation occurs before opening an SSE response.
        async with service.lock:
            try:
                await run_in_threadpool(service.get_session, session_id)
            except FileNotFoundError as exc:
                raise HTTPException(404, '会话不存在') from exc
            except Exception as exc:
                from app.api.service import SessionNotFound
                if isinstance(exc, SessionNotFound):
                    raise HTTPException(404, '会话不存在') from exc
                raise
        if not settings.openai_api_key and service._agent_factory == service._make_agent:
            raise HTTPException(503, '客服暂不可用')

        request_id = uuid4().hex
        started = perf_counter()
        record = {'request_id': request_id, 'session_id': str(session_id), 'status': 'queued', 'user_message': redact(payload.message), 'events': [], 'output': None, 'elapsed_ms': None, 'model_started_at_ms': None, 'first_token_ms': None, 'error': None}
        traces[request_id] = record
        # Never evict a running trace; finish/oldest records are pruned first.
        for key in list(traces):
            if len(traces) <= 100:
                break
            if traces[key]['status'] not in {'running', 'queued'}:
                del traces[key]
        if len(traces) > 120:
            del traces[request_id]
            raise HTTPException(429, '当前请求较多，请稍后再试')
        queue = asyncio.Queue(maxsize=512)
        loop = asyncio.get_running_loop()
        attached = True

        async def deliver(event):
            if attached:
                await queue.put(event)

        def on_event(event):
            clean = dict(event) if event['type'] in {'delta', 'response_delta', 'response_end'} else redact(event)
            clean['at_ms'] = round((perf_counter() - started) * 1000)
            if event['type'] == 'model' and event.get('state') == 'running' and record['model_started_at_ms'] is None:
                record['model_started_at_ms'] = clean['at_ms']
            if event['type'] in {'delta', 'response_delta'} and str(event.get('text', '')).strip() and record['first_token_ms'] is None and record['model_started_at_ms'] is not None:
                record['first_token_ms'] = max(0, clean['at_ms'] - record['model_started_at_ms'])
            if event['type'] not in {'delta', 'response_delta'} and len(record['events']) < 1000:
                record['events'].append(clean)
            public = public_event(clean)
            if public is not None:
                # Backpressure bounds memory while the model runs in a worker thread.
                future = asyncio.run_coroutine_threadsafe(deliver(public), loop)
                future.result(timeout=30)

        async def worker():
            async with service.lock:
                record['status'] = 'running'
                try:
                    result = await run_in_threadpool(service.send_message, session_id, payload.message, None, on_event, request_id)
                    record['output'] = result.model_dump(mode='json')
                    record['status'] = 'succeeded'
                    await deliver({'type': 'done', 'result': result.model_dump(mode='json'), 'request_id': request_id})
                except Exception as exc:
                    logger.exception('Streaming request failed request_id=%s', request_id)
                    record['status'] = 'failed'
                    record['error'] = {'code': 'AGENT_EXECUTION_FAILED', 'message': '本次请求执行失败，请在本机日志按请求 ID 查看原因。', 'exception_type': type(exc).__name__, 'detail': redact(str(exc))}
                    await deliver({'type': 'error', 'request_id': request_id, 'code': record['error']['code'], 'message': record['error']['message']})
                finally:
                    record['elapsed_ms'] = round((perf_counter() - started) * 1000)

        task = asyncio.create_task(worker())
        # Retain a strong reference until the worker has persisted/cleaned up.
        if not hasattr(app.state, 'stream_workers'):
            app.state.stream_workers = set()
        app.state.stream_workers.add(task)
        task.add_done_callback(app.state.stream_workers.discard)

        async def generate():
            nonlocal attached
            try:
                yield 'data: ' + json.dumps({'type': 'start', 'request_id': request_id}, ensure_ascii=False) + '\n\n'
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=12)
                    except asyncio.TimeoutError:
                        yield ': heartbeat\n\n'
                        continue
                    yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    if event['type'] in {'done', 'error'}:
                        break
            finally:
                attached = False
                # Closing a browser stream never rolls back or replays a business action.
                while not queue.empty():
                    queue.get_nowait()

        return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no', 'X-Request-ID': request_id})
