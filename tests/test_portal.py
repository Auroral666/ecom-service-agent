"""Real auth, authorization, persistence and handoff; no external model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from fastapi.testclient import TestClient
from app.api.main import create_app
from app.api.service import ChatService
from app.portal.store import PortalStore
from app.schemas.response import CustomerServiceResponse


class FakeAgent:
    calls = []
    def __init__(self, path):
        self.path = path
    def chat(self, message, on_trace=None, on_event=None):
        self.calls.append(message)
        answer = '自动客服已收到：' + message
        if on_event:
            on_event({'type': 'response_delta', 'id': 'fake', 'text': answer})
            on_event({'type': 'response_end', 'id': 'fake', 'kind': 'answer', 'text': answer})
        return CustomerServiceResponse(intent='other', confidence=0.9, reply=answer, requires_human=False)
    def close(self):
        pass
    def reset(self):
        pass


def make_app(tmp_path):
    return create_app(ChatService(root=tmp_path, agent_factory=FakeAgent))


def account(app, username, role='user', invitation=None):
    client = TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 12345))
    client.headers['X-Portal-Request'] = '1'
    if invitation is None and role != 'user':
        invitation = json.loads((app.state.portal.root / 'invitations.json').read_text())[role]
    result = client.post('/api/auth/register', json=dict(username=username, name=username, role=role,
                                                     password='Only-for-test-2026', invitation=invitation or ''))
    assert result.status_code == 201, result.text
    return client, result.json()['user']


def session(client):
    result = client.post('/api/sessions')
    assert result.status_code == 201, result.text
    return result.json()['id']


def test_login_registration_cookie_and_role_escalation(tmp_path):
    app = make_app(tmp_path)
    anonymous = TestClient(app)
    assert anonymous.get('/api/sessions').status_code == 401
    anonymous.headers['X-Portal-Request'] = '1'
    assert anonymous.post('/api/auth/register', json=dict(username='evil', name='e', role='dev', password='long-password')).status_code == 403
    client, user = account(app, 'alice')
    assert user['role'] == 'user' and set(user) == {'id', 'username', 'name', 'role'}
    assert client.get('/api/auth/me').json()['user']['id'] == user['id']
    assert client.post('/api/auth/logout').status_code == 204
    assert client.get('/api/auth/me').status_code == 401
    login = dict(username='alice', password='Only-for-test-2026', role='dev')
    assert client.post('/api/auth/login', json=login).status_code == 401
    login['role'] = 'user'
    response = client.post('/api/auth/login', json=login)
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert client.post('/api/auth/register', json=dict(username='alice', name='a', password='other-password')).status_code == 409
    invalid = client.post('/api/auth/login', json=dict(username='alice', password='secret', role='user'))
    assert invalid.status_code == 422 and 'secret' not in invalid.text
    assert client.post('/api/sessions', headers={'Origin': 'https://foreign.example'}).status_code == 403
    client.headers.pop('X-Portal-Request')
    assert client.post('/api/sessions').status_code == 403
    with app.state.portal.db() as db:
        assert 'Only-for-test' not in db.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()['password']


def test_account_isolation_and_dev_only_debug(tmp_path):
    app = make_app(tmp_path)
    alice, _ = account(app, 'alice')
    bob, _ = account(app, 'bobby')
    staff, _ = account(app, 'staff', 'support')
    dev, _ = account(app, 'developer', 'dev')
    sid = session(alice)
    assert len(alice.get('/api/sessions').json()['sessions']) == 1
    assert bob.get('/api/sessions').json()['sessions'] == []
    assert bob.get('/api/sessions/' + sid).status_code == 404
    assert bob.post('/api/sessions/' + sid + '/chat', json={'message': 'steal'}).status_code == 404
    assert dev.get('/api/sessions/' + sid).status_code == 404
    assert staff.post('/api/sessions').status_code == 403
    for client in (alice, bob, staff):
        assert client.post('/api/debug/access', headers={'X-Debug-Request': '1'}).status_code == 403
        assert client.get('/api/debug/traces/anything').status_code == 403
    assert dev.post('/api/debug/access', headers={'X-Debug-Request': '1'}).status_code == 200
    from uuid import uuid4
    # No claiming another customer's existing session via a user-provided ID.
    assert alice.post('/api/human/request', json={'session_id': str(uuid4())}).status_code == 404
    assert alice.get('/api/support/customers').status_code == 403


def test_multiple_sessions_single_customer_thread_and_end_service(tmp_path):
    FakeAgent.calls = []
    app = make_app(tmp_path)
    customer, user = account(app, 'alice')
    staff, staff_user = account(app, 'staff', 'support')
    other, _ = account(app, 'other_staff', 'support')
    a, b = session(customer), session(customer)
    for sid, message in [(a, '历史咨询甲'), (b, '历史咨询乙')]:
        assert customer.post(f'/api/sessions/{sid}/chat', json={'message': message}).status_code == 200
    assert customer.post('/api/human/request', json={'session_id': a}).status_code == 200
    assert customer.post('/api/human/request', json={'session_id': b}).status_code == 200
    rows = staff.get('/api/support/customers').json()['customers']
    assert len(rows) == 1 and rows[0]['user_id'] == user['id']
    revision = rows[0]['revision']
    prefix = '/api/support/customers/' + user['id']
    history = staff.get(prefix).json()
    assert history['session_count'] == 2
    assert {'历史咨询甲', '历史咨询乙'} <= {m['content'] for m in history['messages']}
    for sid in (a, b):
        assert customer.post(f'/api/sessions/{sid}/chat', json={'message': '不应调用模型'}).status_code == 409
        assert customer.post(f'/api/sessions/{sid}/chat/stream', json={'message': '不应调用模型'}).status_code == 409
    c = session(customer)
    assert customer.post('/api/human/messages', json={'session_id': c, 'message': '新会话也由人工处理'}).status_code == 201
    assert len(FakeAgent.calls) == 2
    assert staff.post(prefix + '/reply', json={'revision': revision, 'message': '尚未接入'}).status_code == 403
    assert staff.post(prefix + '/claim', json={'revision': revision}).status_code == 200
    assert other.post(prefix + '/claim', json={'revision': revision}).status_code == 409
    assert other.get(prefix).status_code == 404
    assert other.post(prefix + '/reply', json={'revision': revision, 'message': '越权'}).status_code == 403
    assert staff.post(prefix + '/reply', json={'revision': revision, 'message': '人工回复成功'}).status_code == 201
    received = customer.get('/api/human/channel').json()
    assert received['staff_name'] == staff_user['name']
    reply = next(m for m in received['messages'] if m['kind'] == 'staff')
    assert reply['content'] == '人工回复成功' and reply['session_id'] == c
    assert staff.post(prefix + '/close', json={'revision': revision}).status_code == 200
    assert customer.get('/api/human/channel').json()['state'] == 'bot'
    assert staff.post(prefix + '/reply', json={'revision': revision, 'message': '迟到回复'}).status_code == 403
    assert customer.post('/api/human/messages', json={'session_id': c, 'message': '已结束'}).status_code == 409
    assert customer.post(f'/api/sessions/{c}/chat', json={'message': '恢复自动'}).status_code == 200
    assert customer.post('/api/human/request', json={'session_id': c}).status_code == 200
    assert len(staff.get('/api/support/customers').json()['customers']) == 1
    assert staff.post(prefix + '/claim', json={'revision': revision}).status_code == 409


def test_persistence_and_deleted_history_retained_for_staff(tmp_path):
    app = make_app(tmp_path)
    customer, user = account(app, 'alice')
    staff, _ = account(app, 'staff', 'support')
    sid = session(customer)
    customer.post(f'/api/sessions/{sid}/chat', json={'message': '保留的历史'})
    assert customer.delete('/api/sessions/' + sid).status_code == 204
    new = session(customer)
    customer.post('/api/human/request', json={'session_id': new})
    app2 = make_app(tmp_path)
    client2 = TestClient(app2, base_url='http://127.0.0.1', headers={'X-Portal-Request': '1'})
    client2.cookies.update(customer.cookies)
    assert client2.get('/api/auth/me').json()['user']['id'] == user['id']
    assert client2.get('/api/human/channel').json()['state'] == 'waiting'
    history = staff.get('/api/support/customers/' + user['id']).json()['messages']
    assert '保留的历史' in [m['content'] for m in history]


def test_atomic_claim_allows_only_one_staff(tmp_path):
    from fastapi import HTTPException
    store = PortalStore(tmp_path)
    invite = json.loads((store.root / 'invitations.json').read_text())
    customer = store.register('alice', 'a', 'user', 'test-password', '')
    a = store.register('staff_a', 'a', 'support', 'test-password', invite['support'])
    b = store.register('staff_b', 'b', 'support', 'test-password', invite['support'])
    store.request_human(customer, 'example')
    def claim(staff):
        try: store.staff_action(staff, customer['id'], 'claim', 1); return 200
        except HTTPException as error: return error.status_code
    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(claim, [a, b])) == [200, 409]


def test_tied_message_dates_preserve_user_before_answer(tmp_path):
    app = make_app(tmp_path)
    customer, user = account(app, 'alice')
    staff, _ = account(app, 'staff', 'support')
    sid = session(customer)
    assert customer.post(f'/api/sessions/{sid}/chat', json={'message': '先问后答'}).status_code == 200
    service_detail = json.loads((tmp_path / sid / 'conversation.json').read_text(encoding='utf-8'))
    service_detail['messages'][1]['created_at'] = service_detail['messages'][0]['created_at']
    (tmp_path / sid / 'conversation.json').write_text(json.dumps(service_detail), encoding='utf-8')
    customer.post('/api/human/request', json={'session_id': sid})
    messages = staff.get('/api/support/customers/' + user['id']).json()['messages']
    assert [message['kind'] for message in messages[:2]] == ['user', 'ai']


def test_account_memory_context_does_not_load_default_identity(tmp_path):
    from types import SimpleNamespace as NS
    from app.agent.memory.long_term import LongTermMemory
    service = ChatService(root=tmp_path, agent_factory=FakeAgent)
    app = create_app(service)
    a, user_a = account(app, 'alice')
    b, user_b = account(app, 'bobby')
    for client, user in [(a, user_a), (b, user_b)]:
        sid = session(client)
        manager = NS(memory_enabled=True, ltm=LongTermMemory('default', str(tmp_path / 'memory')))
        manager.ltm.facts.append('must-not-carry-default-memory')
        agent = NS(memory_manager=manager)
        service._agent_preparer(sid, agent)
        assert manager.ltm.user_id == 'account_' + user['id']
        assert manager.ltm.facts == []


def test_service_rounds_staff_first_and_customer_first(tmp_path):
    import re
    app = make_app(tmp_path)
    customer, user = account(app, 'alice')
    staff, _ = account(app, 'staff', 'support')
    old, current = session(customer), session(customer)
    prefix = '/api/support/customers/' + user['id']
    first_id = None
    for index, sid in enumerate((old, current)):
        assert customer.post('/api/human/request', json={'session_id': sid}).status_code == 200
        channel = customer.get('/api/human/channel').json()
        revision = channel['revision']
        assert re.fullmatch(user['id'] + '-alice-' + r'\d{8}T\d{9}', channel['service_title'])
        assert staff.post(prefix + '/claim', json={'revision': revision}).status_code == 200
        if index == 0:
            first_id = channel['service_id']
            assert customer.post('/api/human/messages', json={'session_id': sid, 'message': '先问'}).status_code == 201
        assert staff.post(prefix + '/reply', json={'revision': revision, 'message': '主动问候'}).status_code == 201
        received = customer.get('/api/human/channel').json()
        reply = [m for m in received['messages'] if m['kind'] == 'staff'][-1]
        assert reply['session_id'] == sid
        assert reply['service_id'] == channel['service_id']
        assert reply['service_session_id'] == sid
        assert reply['service_title'] == channel['service_title']
        if index == 1:
            assert channel['service_id'] != first_id
            assert len(received['services']) == 2
            # Switching consultation windows still keeps both sides in this service round.
            another = session(customer)
            assert customer.post('/api/human/messages', json={'session_id': another, 'message': '换窗口追问'}).status_code == 201
            assert staff.post(prefix + '/reply', json={'revision': revision, 'message': '继续回复'}).status_code == 201
            history = staff.get(prefix).json()
            last = history['messages'][-1]
            assert last['service_id'] == channel['service_id'] and last['service_session_id'] == sid
        assert staff.post(prefix + '/close', json={'revision': revision}).status_code == 200
    persisted = PortalStore(tmp_path / '.portal')
    assert len(persisted.services(user['id'])) == 2
    assert all(item['ended_at'] for item in persisted.services(user['id']))
    assert len([m for m in persisted.messages(user['id']) if m['kind'] == 'staff']) == 3


def test_legacy_service_migration_keeps_messages_and_groups_wrong_session_reply(tmp_path):
    import sqlite3
    path = tmp_path / 'portal.sqlite3'
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY,username TEXT,name TEXT,role TEXT,password TEXT);
            INSERT INTO users VALUES ('customer','alice','小明','user','unused');
            CREATE TABLE channels (user_id TEXT PRIMARY KEY,state TEXT,staff_id TEXT,session_id TEXT,revision INTEGER,updated_at TEXT);
            INSERT INTO channels VALUES ('customer','active','staff','new-session',2,'2026-08-01T03:13:00+00:00');
            CREATE TABLE human_messages (id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,kind TEXT,author_id TEXT,author_name TEXT,content TEXT,created_at TEXT);
            INSERT INTO human_messages VALUES ('1','customer','old-session','system','customer','小明','已申请人工客服，接下来的消息将由人工客服处理。','2026-07-30T03:00:00+00:00');
            INSERT INTO human_messages VALUES ('2','customer','old-session','system','staff','客服','本次人工服务已结束，您可以继续向自动客服提问。','2026-07-30T04:00:00+00:00');
            INSERT INTO human_messages VALUES ('3','customer','new-session','system','customer','小明','已申请人工客服，接下来的消息将由人工客服处理。','2026-08-01T03:13:00+00:00');
            INSERT INTO human_messages VALUES ('4','customer','old-session','staff','staff','客服','历史错位回复','2026-08-01T03:14:00+00:00');
        """)
    store = PortalStore(tmp_path)
    messages = store.messages('customer')
    assert len(messages) == 4
    assert messages[-1]['service_id'] == messages[-2]['service_id']
    assert messages[-1]['service_session_id'] == 'new-session'
    assert store.channel('customer')['service_id'] == messages[-1]['service_id']
    assert store.channel('customer')['service_title'] == 'customer-小明-20260801T111300000'
    assert len(PortalStore(tmp_path).services('customer')) == 2
