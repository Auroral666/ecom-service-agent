"""FastAPI entry point: uvicorn app.api.main:app --host 127.0.0.1 --port 8000"""

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import ChatRequest, ChatResult, SessionDetail, SessionList
from app.api.service import ChatService, SessionNotFound
from app.config.settings import settings

logger = logging.getLogger(__name__)


def create_app(service: ChatService | None = None) -> FastAPI:
    chat_service = service or ChatService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        async with chat_service.lock:
            await run_in_threadpool(chat_service.close_all)

    app = FastAPI(title="电商客服 Agent API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Portal-Request", "X-Debug-Request", "X-Debug-Token"],
    )

    from app.portal.routes import register_portal
    portal = register_portal(app, chat_service)

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "model_configured": bool(settings.openai_api_key),
            "agent_mode": "multi_agent" if settings.multi_agent_enabled else "react",
        }

    @app.get("/api/sessions", response_model=SessionList)
    async def list_sessions(request: Request):
        async with chat_service.lock:
            sessions = await run_in_threadpool(chat_service.list_sessions)
            user = request.state.user
            visible = [s for s in sessions if portal.owner(s.id) == user['id'] or (user['role'] == 'dev' and portal.owner(s.id) is None)]
            return SessionList(sessions=visible)

    @app.post("/api/sessions", response_model=SessionDetail, status_code=201)
    async def create_session(request: Request):
        async with chat_service.lock:
            detail = await run_in_threadpool(chat_service.create_session)
            await run_in_threadpool(portal.bind, detail.id, request.state.user['id'])
            return detail

    @app.get("/api/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(session_id: UUID):
        async with chat_service.lock:
            try:
                return await run_in_threadpool(chat_service.get_session, session_id)
            except SessionNotFound as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc

    @app.post("/api/sessions/{session_id}/chat", response_model=ChatResult)
    async def chat(session_id: UUID, payload: ChatRequest):
        async with chat_service.lock:
            try:
                await run_in_threadpool(chat_service.get_session, session_id)
                if not settings.openai_api_key and chat_service._agent_factory == chat_service._make_agent:
                    raise HTTPException(status_code=503, detail="请先在 .env 中配置 OPENAI_API_KEY")
                return await run_in_threadpool(chat_service.send_message, session_id, payload.message)
            except SessionNotFound as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Agent request failed for session %s", session_id)
                raise HTTPException(status_code=502, detail="Agent 处理失败，请查看后端日志和模型配置") from exc

    from app.streaming.routes import register_streaming
    register_streaming(app, chat_service)

    @app.post("/api/sessions/{session_id}/reset", response_model=SessionDetail)
    async def reset_session(session_id: UUID):
        async with chat_service.lock:
            try:
                portal.check_bot(session_id)
                detail = await run_in_threadpool(chat_service.get_session, session_id)
                if owner := portal.owner(session_id):
                    await run_in_threadpool(portal.archive, owner, detail.model_dump(mode='json'))
                return await run_in_threadpool(chat_service.reset_session, session_id)
            except SessionNotFound as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: UUID):
        async with chat_service.lock:
            try:
                portal.check_bot(session_id)
                detail = await run_in_threadpool(chat_service.get_session, session_id)
                if owner := portal.owner(session_id):
                    await run_in_threadpool(portal.archive, owner, detail.model_dump(mode='json'))
                await run_in_threadpool(chat_service.delete_session, session_id)
            except SessionNotFound as exc:
                raise HTTPException(status_code=404, detail="会话不存在") from exc
            return Response(status_code=204)

    return app


app = create_app()
