"""Stream behavior and access control; models/tools are controlled test doubles."""
import json
from types import SimpleNamespace as NS

import pytest
from tests.portal_helpers import authenticated_client as TestClient

from app.api.main import create_app
from app.api.service import ChatService
from app.schemas.response import CustomerServiceResponse
from app.streaming.engine import StreamingRun, streaming_session
from app.streaming.results import business_result
from app.streaming.security import redact, public_event, safe_steps


def chunk(text=None, tools=None, finish=None, reasoning=None):
    return NS(id='model-test', usage=None, choices=[NS(delta=NS(content=text, tool_calls=tools, reasoning_content=reasoning), finish_reason=finish)])


class FakeStream:
    def __init__(self, chunks, before_last=None):
        self.chunks, self.before_last, self.closed = chunks, before_last, False
    def __iter__(self):
        for i, value in enumerate(self.chunks):
            if i == len(self.chunks) - 1 and self.before_last:
                self.before_last()
            yield value
    def close(self):
        self.closed = True


def test_reply_is_emitted_before_generation_finishes_and_saved_text_matches():
    events = []
    result = {'reply': '您好，订单😀已发货。\n下一行含 "引号"。', 'intent': 'order_query', 'confidence': 0.9, 'requires_human': False, 'follow_up_question': None}
    raw = json.dumps(result, ensure_ascii=True)
    stream = FakeStream([chunk(raw[i:i + 3]) for i in range(0, len(raw), 3)] + [chunk(finish='stop')])
    def before_last():
        assert any(event['type'] == 'delta' for event in events)
    stream.before_last = before_last
    client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    run = StreamingRun(NS(client=client, model='fake'), events.append)
    response = run.final_reply('客户可见的回复')
    assert ''.join(event['text'] for event in events if event['type'] == 'delta') == response.reply == result['reply']
    assert stream.closed
    assert not any('reasoning_content' in str(event) for event in events)


def test_tools_accumulate_arguments_before_execution_and_restore_adapters():
    original_client = NS(chat=NS(completions=NS(create=lambda **kwargs: FakeStream([
        chunk(tools=[NS(index=0, id='call_1', function=NS(name='query_order', arguments='{"order_'))]),
        chunk(tools=[NS(index=0, id=None, function=NS(name=None, arguments='id":"ORD-1"}'))]),
        chunk(finish='tool_calls', reasoning='hidden reasoning must not be sent'),
    ]))))
    tools = NS(source_for=lambda name: 'local', execute_tool=lambda name, args: json.dumps({'success': True, 'order': {'order_id': args['order_id'], 'status': 'shipped', 'total': 12, 'phone': '13812345678'}}))
    agent = NS(client=original_client, tool_manager=tools)
    events = []
    with streaming_session(agent, events.append):
        reply = agent.client.chat.completions.create(model='fake', messages=[], tools=[])
        tool = reply.choices[0].message.tool_calls[0]
        assert json.loads(tool.function.arguments) == {'order_id': 'ORD-1'}
        agent.tool_manager.execute_tool(tool.function.name, json.loads(tool.function.arguments))
    states = [event for event in events if event['type'] == 'tool']
    assert [event['state'] for event in states] == ['waiting', 'running', 'succeeded']
    assert len({event['id'] for event in states}) == 1
    assert agent.client is original_client and agent.tool_manager is tools
    assert 'phone' not in next(event for event in events if event['type'] == 'business')['block']['data'][0]


def test_instrumentation_restores_after_failure_and_sync_path_unchanged():
    original = NS()
    agent = NS(client=original)
    with pytest.raises(RuntimeError):
        with streaming_session(agent, lambda event: None):
            raise RuntimeError('test')
    assert agent.client is original
    with streaming_session(agent) as run:
        assert run is None and agent.client is original


def test_redaction_and_failed_business_results():
    result = redact({'api_key': 'private', 'nested': {'phone': '13812345678'}, 'text': 'Bearer abcdefghijkl', 'payload': '{"password":"private"}'})
    assert 'private' not in str(result) and '13812345678' not in str(result)
    assert business_result('query_order', '{"success":false,"error":"订单不存在"}')['success'] is False
    assert business_result('unknown_mcp_tool', '{"success":true}')['block'] is None


def test_public_history_never_contains_private_tool_arguments_or_internals():
    event = public_event({'type': 'tool', 'id': 'x', 'name': 'search_knowledge', 'state': 'failed', 'error': 'index missing: /private/path', 'args': {'token': 'secret'}, 'result': 'private payload'})
    assert 'args' not in event and 'result' not in event
    assert '/private/path' not in event['error']
    steps = safe_steps([{'id': '1', 'type': 'thought', 'content': 'private reasoning'}, {'id': '2', 'type': 'observation', 'tool_name': 'load_skill', 'tool_args': {'token': 'secret'}, 'tool_result': '{"instructions":"private instructions"}', 'content': 'private instructions'}])
    assert len(steps) == 1 and steps[0]['tool_args'] is None and steps[0]['tool_result'] is None
    assert 'private' not in str(steps)


class StreamingAgent:
    def __init__(self, session_path):
        self.raw_messages = []
    def chat(self, message, on_trace=None, on_event=None):
        on_event({'type': 'model', 'id': 'm', 'state': 'running', 'phase': 'react', 'model': 'fake', 'messages': [{'role': 'system', 'content': 'token=SECRET_VALUE'}]})
        on_event({'type': 'delta', 'text': '查询'})
        on_event({'type': 'delta', 'text': '完成'})
        return CustomerServiceResponse(intent='order_query', confidence=0.9, reply='查询完成', requires_human=False)
    def close(self):
        pass


def test_sse_complete_persistence_and_protected_debug(tmp_path):
    service = ChatService(root=tmp_path, agent_factory=StreamingAgent)
    with TestClient(create_app(service), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        session_id = client.post('/api/sessions').json()['id']
        response = client.post(f'/api/sessions/{session_id}/chat/stream', json={'message': '查询'})
        events = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]
        assert [event['type'] for event in events] == ['start', 'delta', 'delta', 'done']
        assert 'SECRET_VALUE' not in response.text
        request_id = events[0]['request_id']
        assert client.get('/api/debug/traces/' + request_id).status_code == 403
        assert client.post('/api/debug/access', headers={'X-Debug-Request': '1', 'Origin': 'https://evil.example'}).status_code == 403
        token = client.post('/api/debug/access', headers={'X-Debug-Request': '1'}).json()['token']
        trace = client.get('/api/debug/traces/' + request_id, headers={'X-Debug-Token': token}).json()
        assert trace['status'] == 'succeeded' and 'SECRET_VALUE' not in json.dumps(trace)
        saved = client.get('/api/sessions/' + session_id).json()['messages'][-1]
        assert saved['content'] == '查询完成' and saved['presentation']['request_id'] == request_id


def test_partial_failure_emits_error_without_done_or_completed_message(tmp_path):
    class Broken(StreamingAgent):
        def chat(self, message, on_trace=None, on_event=None):
            on_event({'type': 'delta', 'text': '部分回复'})
            raise RuntimeError('SECRET_INTERNAL_EXCEPTION')
    service = ChatService(root=tmp_path, agent_factory=Broken)
    with TestClient(create_app(service)) as client:
        session_id = client.post('/api/sessions').json()['id']
        response = client.post(f'/api/sessions/{session_id}/chat/stream', json={'message': '查询'})
        assert '"type": "error"' in response.text and '"type": "done"' not in response.text
        assert 'SECRET_INTERNAL_EXCEPTION' not in response.text
        assert client.get('/api/sessions/' + session_id).json()['messages'] == []
