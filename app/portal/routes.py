"""Small integration boundary for existing chat APIs and independent staff APIs."""
from collections import defaultdict, deque
import re
from threading import Lock
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from app.api.schemas import ChatRequest
from app.api.service import SessionNotFound
from app.portal.store import PortalStore, digest

COOKIE = 'ecom_portal'


class Credentials(BaseModel):
    username: str = Field(pattern=r'^[A-Za-z0-9_]{3,32}$')
    password: str = Field(min_length=8, max_length=128)
    role: Literal['user', 'support', 'dev'] = 'user'


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=30)
    invitation: str = Field(default='', max_length=128)

    @field_validator('name')
    @classmethod
    def clean_name(cls, name):
        if not name.strip():
            raise ValueError('名称不能为空')
        return name.strip()


class Handoff(BaseModel):
    session_id: UUID


class HumanMessage(Handoff, ChatRequest):
    pass


class StaffAction(BaseModel):
    revision: int = Field(ge=1)


class StaffReply(StaffAction, ChatRequest):
    pass


def register_portal(app, service):
    store = PortalStore(service.root / '.portal')
    app.state.portal = store
    router = APIRouter(prefix='/api')
    attempts = defaultdict(deque)
    attempts_lock = Lock()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # FastAPI's default includes submitted inputs, including invalid passwords.
        return JSONResponse({'detail': [{key: item[key] for key in ('type', 'loc', 'msg')}
                                        for item in error.errors()]}, status_code=422)

    def rate_limit(request, account):
        with attempts_lock:
            check_attempts(request, account)

    def check_attempts(request, account):
        ip = request.client.host if request.client else 'unknown'
        current = time.monotonic()
        # A bounded bucket table, shared by login/registration, never stores passwords.
        for key in (f'ip:{ip}', f'account:{account.lower()}'):
            bucket = attempts[key]
            while bucket and current - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= (25 if key.startswith('ip:') else 10):
                raise HTTPException(429, '尝试过于频繁，请一分钟后再试')
            bucket.append(current)
        if len(attempts) > 2048:
            for key in list(attempts):
                if not attempts[key] or current - attempts[key][-1] > 60:
                    del attempts[key]

    def require_role(request, role):
        user = request.state.user
        if user['role'] != role:
            raise HTTPException(403, '当前身份无权执行此操作')
        return user

    @app.middleware('http')
    async def account_guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith('/api/') or path == '/api/health' or request.method == 'OPTIONS':
            return await call_next(request)
        try:
            if request.method not in {'GET', 'HEAD'}:
                origin = request.headers.get('origin')
                allowed = {str(request.base_url).rstrip('/'), 'http://127.0.0.1:5173', 'http://localhost:5173'}
                if request.headers.get('x-portal-request') != '1' or (origin and origin not in allowed):
                    raise HTTPException(403, '请求来源校验失败，请从客服页面操作')
            if path not in {'/api/auth/login', '/api/auth/register'}:
                user = await run_in_threadpool(store.current_user, request.cookies.get(COOKIE))
                if not user:
                    raise HTTPException(401, '请先登录')
                request.state.user = user
                if path.startswith('/api/debug/') and user['role'] != 'dev':
                    raise HTTPException(403, '只有开发人员可以查看调试信息')
                if path.startswith('/api/sessions'):
                    if user['role'] == 'support':
                        raise HTTPException(403, '请使用人工客服工作台')
                    match = re.match(r'^/api/sessions/([0-9a-fA-F-]{36})(?:/|$)', path)
                    if match:
                        await run_in_threadpool(store.check_session, user, match[1])
                        if request.method != 'GET':
                            await run_in_threadpool(store.check_bot, match[1])
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        except HTTPException as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status_code)

    def set_login(response, request, user):
        token = store.new_login(user['id'])
        response.set_cookie(COOKIE, token, max_age=43200, httponly=True,
                            secure=request.url.scheme == 'https', samesite='strict', path='/api')
        return {'user': user}

    @router.post('/auth/register', status_code=201)
    def register(payload: Registration, request: Request, response: Response):
        rate_limit(request, payload.username)
        user = store.register(payload.username, payload.name, payload.role, payload.password, payload.invitation)
        return set_login(response, request, user)

    @router.post('/auth/login')
    def login(payload: Credentials, request: Request, response: Response):
        rate_limit(request, payload.username)
        user = store.login(payload.username, payload.password, payload.role)
        return set_login(response, request, user)

    @router.get('/auth/me')
    def me(request: Request):
        return {'user': request.state.user}

    @router.post('/auth/logout', status_code=204)
    def logout(request: Request, response: Response):
        store.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path='/api')

    @router.get('/human/channel')
    def channel(request: Request):
        user = request.state.user
        if user['role'] == 'support':
            raise HTTPException(403, '请使用客服工作台')
        return {**store.channel(user['id']), 'messages': store.messages(user['id']), 'services': store.services(user['id'])}

    @router.post('/human/request')
    async def request_human(payload: Handoff, request: Request):
        user = request.state.user
        store.check_session(user, payload.session_id)
        if store.owner(payload.session_id) != user['id']:
            raise HTTPException(409, '历史演示记录不属于当前账号，请新建会话后转人工')
        async with service.lock:
            await run_in_threadpool(service.get_session, payload.session_id)
            await run_in_threadpool(store.request_human, user, str(payload.session_id))
        return store.channel(user['id'])

    @router.post('/human/messages', status_code=201)
    async def human_message(payload: HumanMessage, request: Request):
        user = request.state.user
        store.check_session(user, payload.session_id)
        async with service.lock:
            await run_in_threadpool(service.get_session, payload.session_id)
            await run_in_threadpool(store.customer_message, user, str(payload.session_id), payload.message)
        return {**store.channel(user['id']), 'messages': store.messages(user['id']), 'services': store.services(user['id'])}

    @router.get('/support/customers')
    def customers(request: Request):
        staff = require_role(request, 'support')
        return {'customers': store.queue(staff['id'])}

    @router.get('/support/customers/{user_id}')
    async def customer_history(user_id: str, request: Request):
        staff = require_role(request, 'support')
        channel = store.check_staff_view(staff['id'], user_id)
        async with service.lock:
            details = await run_in_threadpool(store.archived, user_id)
            for sid in store.owned_ids(user_id):
                try:
                    detail = await run_in_threadpool(service.get_session, UUID(sid))
                    details.append(detail.model_dump(mode='json'))
                except SessionNotFound:
                    continue
        messages, seen = [], set()
        for detail in details:
            for msg in detail['messages']:
                key = digest(detail['id'] + msg['created_at'] + msg['role'] + msg['content'])
                if key in seen:
                    continue
                seen.add(key)
                messages.append({**msg, 'id': key, 'kind': 'user' if msg['role'] == 'user' else 'ai',
                                 'session_id': detail['id'], 'session_title': detail['title'],
                                 'author_name': '用户' if msg['role'] == 'user' else '自动客服'})
        messages.extend(store.messages(user_id))
        # Windows timestamps can tie; preserve each session's user-before-answer order.
        messages.sort(key=lambda msg: msg['created_at'])
        return {**channel, 'messages': messages, 'services': store.services(user_id), 'session_count': len(store.owned_ids(user_id))}

    @router.post('/support/customers/{user_id}/claim')
    def claim(user_id: str, payload: StaffAction, request: Request):
        staff = require_role(request, 'support')
        store.staff_action(staff, user_id, 'claim', payload.revision)
        return store.channel(user_id)

    @router.post('/support/customers/{user_id}/reply', status_code=201)
    def reply(user_id: str, payload: StaffReply, request: Request):
        staff = require_role(request, 'support')
        store.staff_action(staff, user_id, 'reply', payload.revision, payload.message)
        return store.channel(user_id)

    @router.post('/support/customers/{user_id}/close')
    def close(user_id: str, payload: StaffAction, request: Request):
        staff = require_role(request, 'support')
        store.staff_action(staff, user_id, 'close', payload.revision)
        return store.channel(user_id)

    def prepare_agent(session_id, agent):
        # Existing Agent constructors load default memory; replace it before the first turn.
        owner = store.owner(session_id)
        manager = getattr(agent, 'memory_manager', None)
        if owner and manager:
            from app.agent.memory.long_term import LongTermMemory
            old = manager.ltm
            manager.ltm = LongTermMemory('account_' + owner, str(old.memory_dir), old.max_facts)
            if manager.memory_enabled:
                manager.ltm.load()

    service._before_send = store.check_bot
    service._agent_preparer = prepare_agent
    app.include_router(router)
    return store
