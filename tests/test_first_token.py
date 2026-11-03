import json
from types import SimpleNamespace as NS

from tests.portal_helpers import authenticated_client as TestClient

from app.api.main import create_app
from app.api.service import ChatService
from app.schemas.response import CustomerServiceResponse
from app.streaming.engine import StreamingRun
from app.streaming.results import business_result
from app.streaming import routes


class FakeStream:
    def __init__(self, chunks, before_last=None):
        self.chunks, self.before_last = chunks, before_last
    def __iter__(self):
        for index, chunk in enumerate(self.chunks):
            if index == len(self.chunks) - 1 and self.before_last:
                self.before_last()
            yield chunk
    def close(self): pass


def chunk(content=None, finish=None, tools=None):
    return NS(id='test-stream', usage=None, choices=[NS(delta=NS(content=content, tool_calls=tools), finish_reason=finish)])


def test_first_model_text_is_emitted_before_completion_and_metadata_does_not_rewrite_it():
    events, requests = [], []
    answer = '您的订单已发货。'
    def before_last():
        assert ''.join(event['text'] for event in events if event['type'] == 'response_delta') == answer
    streams = iter([
        FakeStream([chunk('您'), chunk('的订单'), chunk('已发货。'), chunk(finish='stop')], before_last),
        FakeStream([chunk(json.dumps({'intent': 'order_query', 'confidence': 0.9, 'requires_human': False, 'follow_up_question': None, 'reply': '不应该替换正文'})), chunk(finish='stop')]),
    ])
    def create(**kwargs):
        requests.append(kwargs)
        return next(streams)
    client = NS(chat=NS(completions=NS(create=create)))
    run = StreamingRun(NS(client=client, model='fake'), events.append)
    completion = run.model_completion(client, {'model': 'fake', 'messages': []})
    result = run.final_reply(completion.choices[0].message.content)
    assert result.reply == answer
    assert len(requests) == 2
    assert not any(event['type'] == 'delta' for event in events)
    assert not any('不应该替换正文' in event.get('text', '') for event in events)
    assert events[-1]['phase'] == 'metadata'


def test_model_text_before_a_tool_call_becomes_activity_not_the_final_answer():
    events = []
    tool = NS(index=0, id='call-1', function=NS(name='query_order', arguments='{"order_id":"ORD-1"}'))
    stream = FakeStream([chunk('我先查询订单。'), chunk(tools=[tool]), chunk(finish='tool_calls')])
    client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    run = StreamingRun(NS(client=client), events.append)
    run.model_completion(client, {'model': 'fake', 'messages': [], 'tools': []})
    assert next(event for event in events if event['type'] == 'response_end')['kind'] == 'activity'
    assert not run.answer_streamed


def test_order_labels_are_derived_from_real_status_and_mixed_orders_stay_generic():
    shipped = business_result('query_order', {'success': True, 'order': {'order_id': 'ORD-1', 'status': 'shipped'}})
    assert shipped['label'] == '查询已发货订单'
    assert shipped['block']['title'] == '查询已发货订单信息'
    pending = business_result('query_order', {'success': True, 'order': {'order_id': 'ORD-2', 'status': 'pending'}})
    assert pending['label'] == '查询待发货订单'
    mixed = business_result('list_user_orders', {'success': True, 'orders': [{'status': 'shipped'}, {'status': 'pending'}]})
    assert mixed['label'] == '查询订单' and mixed['block']['title'] == '订单信息'


def test_first_token_duration_excludes_queue_wait_and_does_not_use_full_duration(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(routes, 'perf_counter', lambda: clock[0])
    class Agent:
        def __init__(self, session_path): self.raw_messages = []
        def chat(self, text, on_trace=None, on_event=None):
            clock[0] = 10
            on_event({'type': 'model', 'state': 'running', 'phase': 'react'})
            clock[0] = 10.2
            on_event({'type': 'status', 'text': '正在处理'})
            clock[0] = 10.5
            on_event({'type': 'response_delta', 'id': 'm', 'text': ' '})
            clock[0] = 11.5
            on_event({'type': 'response_delta', 'id': 'm', 'text': '您'})
            clock[0] = 18
            on_event({'type': 'response_delta', 'id': 'm', 'text': '好'})
            clock[0] = 20
            return CustomerServiceResponse(intent='greeting', confidence=1, reply=' 您好', requires_human=False)
        def close(self): pass
    service = ChatService(root=tmp_path, agent_factory=Agent)
    with TestClient(create_app(service), base_url='http://127.0.0.1', client=('127.0.0.1', 1234)) as client:
        session = client.post('/api/sessions').json()['id']
        response = client.post(f'/api/sessions/{session}/chat/stream', json={'message': '你好'})
        events = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]
        request_id = events[0]['request_id']
        token = client.post('/api/debug/access', headers={'X-Debug-Request': '1'}).json()['token']
        trace = client.get('/api/debug/traces/' + request_id, headers={'X-Debug-Token': token}).json()
        assert trace['first_token_ms'] == 1500
        assert trace['elapsed_ms'] == 20000
        assert trace['model_started_at_ms'] == 10000
