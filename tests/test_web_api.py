"""API integration tests with an injected fake Agent (no network/model calls)."""

from pathlib import Path
from uuid import uuid4

from tests.portal_helpers import authenticated_client as TestClient

from app.api.main import create_app
from app.api.service import ChatService
from app.schemas.response import CustomerServiceResponse, IntentType


class FakeAgent:
    def __init__(self, session_path: str):
        self.path = Path(session_path)
        self.history = []
        self.closed = False

    def chat(self, message: str) -> CustomerServiceResponse:
        self.history.append(message)
        return CustomerServiceResponse(
            intent=IntentType.ORDER_QUERY, confidence=0.97,
            reply=f"已收到：{message}", requires_human=False,
        )

    def reset(self) -> None:
        self.history.clear()

    def close(self) -> None:
        self.closed = True


def client_for(tmp_path):
    service = ChatService(root=tmp_path, agent_factory=FakeAgent)
    return TestClient(create_app(service)), service


def test_full_session_lifecycle(tmp_path):
    client, service = client_for(tmp_path)
    with client:
        assert client.get('/api/health').json()['status'] == 'ok'
        resp = client.post('/api/sessions')
        assert resp.status_code == 201
        session_id = resp.json()['id']
        assert client.get('/api/sessions').json()['sessions'][0]['id'] == session_id

        reply = client.post(f'/api/sessions/{session_id}/chat', json={'message': '  查一下订单  '})
        assert reply.status_code == 200
        assert reply.json()['reply']['reply'] == '已收到：查一下订单'
        assert reply.json()['assistant_message']['metadata']['confidence'] == 0.97
        detail = client.get(f'/api/sessions/{session_id}').json()
        assert len(detail['messages']) == 2
        assert detail['title'] == '查一下订单'
        assert set(detail['messages'][1]) == {'role', 'content', 'created_at', 'metadata', 'steps', 'presentation'}
        assert detail['messages'][1]['steps'] == []
        assert detail['messages'][1]['presentation'] is None

        assert client.post(f'/api/sessions/{session_id}/reset').status_code == 200
        assert client.get(f'/api/sessions/{session_id}').json()['messages'] == []
        assert client.delete(f'/api/sessions/{session_id}').status_code == 204
        assert client.get(f'/api/sessions/{session_id}').status_code == 404
        assert client.post(f'/api/sessions/{session_id}/chat', json={'message': 'test'}).status_code == 404
    assert not list(tmp_path.glob('*/conversation.json'))


def test_validation_and_isolated_conversation_records(tmp_path):
    client, _ = client_for(tmp_path)
    with client:
        a = client.post('/api/sessions').json()['id']
        b = client.post('/api/sessions').json()['id']
        for value in ['', '   ', 'x' * 4001]:
            assert client.post(f'/api/sessions/{a}/chat', json={'message': value}).status_code == 422
        assert client.get('/api/sessions/not-a-uuid').status_code == 422
        assert client.post(f'/api/sessions/{uuid4()}/chat', json={'message': 'hi'}).status_code == 404
        assert client.post(f'/api/sessions/{a}/chat', json={'message': 'A'}).status_code == 200
        assert client.post(f'/api/sessions/{b}/chat', json={'message': 'B'}).status_code == 200
        assert [m['content'] for m in client.get(f'/api/sessions/{a}').json()['messages']] == ['A', '已收到：A']
        assert [m['content'] for m in client.get(f'/api/sessions/{b}').json()['messages']] == ['B', '已收到：B']

    # Reading conversation records does not rely on live Agent objects.
    client2, _ = client_for(tmp_path)
    with client2:
        assert len(client2.get(f'/api/sessions/{a}').json()['messages']) == 2


def test_errors_do_not_reveal_exceptions(tmp_path):
    class BrokenAgent(FakeAgent):
        def chat(self, message):
            raise RuntimeError('DO_NOT_EXPOSE_SECRET')

    svc = ChatService(root=tmp_path, agent_factory=BrokenAgent)
    with TestClient(create_app(svc)) as client:
        sid = client.post('/api/sessions').json()['id']
        result = client.post(f'/api/sessions/{sid}/chat', json={'message': '查询'})
        assert result.status_code == 502
        assert 'DO_NOT_EXPOSE_SECRET' not in result.text
        assert client.get(f'/api/sessions/{sid}').json()['messages'] == []


def test_retry_recreates_agent_after_failure(tmp_path):
    attempts = []

    class FlakyAgent(FakeAgent):
        def chat(self, message):
            attempts.append(message)
            if len(attempts) == 1:
                self.history.append('partial-before-failure')
                raise RuntimeError('model timeout')
            assert not self.history  # no leftover partial message from first attempt
            return super().chat(message)

    svc = ChatService(root=tmp_path, agent_factory=FlakyAgent)
    with TestClient(create_app(svc)) as client:
        sid = client.post('/api/sessions').json()['id']
        assert client.post(f'/api/sessions/{sid}/chat', json={'message': 'first'}).status_code == 502
        assert client.post(f'/api/sessions/{sid}/chat', json={'message': 'retry'}).status_code == 200
        messages = client.get(f'/api/sessions/{sid}').json()['messages']
        assert [m['content'] for m in messages] == ['retry', '已收到：retry']
