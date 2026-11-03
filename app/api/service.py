"""Single-user local conversation service, wrapping the existing Agent.

The project registers Memory/Skill handlers as process-global references. Requests
must be serialized until the Agent's tool registry supports per-user contexts.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID, uuid4

from app.api.schemas import ChatResult, DisplayMessage, SessionDetail, SessionSummary
from app.config.settings import settings
from app.schemas.trace import AgentStep
from app.streaming.security import safe_steps, public_event

logger = logging.getLogger(__name__)


class SessionNotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


class ChatService:
    def __init__(self, root: Path | None = None, agent_factory: Callable | None = None):
        self.root = Path(root or settings.web_sessions_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self._agents: dict[str, object] = {}
        self._agent_factory = agent_factory or self._make_agent

    @staticmethod
    def _make_agent(path: str):
        if settings.multi_agent_enabled:
            from app.multi_agent.orchestrator import MultiAgentOrchestrator
            return MultiAgentOrchestrator(session_path=path)
        from app.agent.chat import EcomAgent
        return EcomAgent(session_path=path)

    def _dir(self, session_id: UUID) -> Path:
        return self.root / str(session_id)

    def _read(self, session_id: UUID) -> SessionDetail:
        path = self._dir(session_id) / "conversation.json"
        if not path.is_file():
            raise SessionNotFound(str(session_id))
        return SessionDetail.model_validate_json(path.read_text(encoding="utf-8"))

    def _write(self, detail: SessionDetail) -> None:
        _save_json(
            self._dir(UUID(detail.id)) / "conversation.json",
            detail.model_dump(mode="json"),
        )

    def list_sessions(self) -> list[SessionSummary]:
        result = []
        for path in self.root.glob("*/conversation.json"):
            try:
                session_id = UUID(path.parent.name)
                detail = self._read(session_id)
                summary = detail.model_dump()
                summary['preview'] = detail.messages[-1].content.replace('\n', ' ')[:100] if detail.messages else ''
                summary['message_count'] = len(detail.messages)
                result.append(SessionSummary.model_validate(summary))
            except (ValueError, OSError) as exc:
                logger.warning("Skipping invalid session file %s: %s", path, exc)
        result.sort(key=lambda s: s.updated_at, reverse=True)
        return result

    def create_session(self) -> SessionDetail:
        session_id = str(uuid4())
        now = _now()
        detail = SessionDetail(
            id=session_id, title="新会话", created_at=now, updated_at=now,
        )
        self._write(detail)
        return detail

    def get_session(self, session_id: UUID) -> SessionDetail:
        return self._read(session_id)

    @staticmethod
    def _bind_agent_tools(agent: object) -> None:
        # Restore the correct context when switching cached sessions.
        if hasattr(agent, "memory_manager"):
            from app.tools.memory_tool import set_memory_manager
            set_memory_manager(agent.memory_manager)
        if hasattr(agent, "skill_manager"):
            from app.tools.skill_tool import set_skill_manager
            set_skill_manager(agent.skill_manager)

    @staticmethod
    def _close_tools(agent: object) -> None:
        if hasattr(agent, "tool_manager"):
            agent.tool_manager.close()
        if hasattr(agent, "agents"):
            for sub_agent in agent.agents.values():
                sub_agent.tool_manager.close()

    def send_message(
        self,
        session_id: UUID,
        message: str,
        on_step: Optional[Callable[[AgentStep], None]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
        request_id: Optional[str] = None,
    ) -> ChatResult:
        # Validate session ID before calling the paid/external model.
        if guard := getattr(self, '_before_send', None):
            guard(session_id)
        detail = self._read(session_id)
        agent = self._agents.get(detail.id)
        if agent is None:
            agent = self._agent_factory(str(self._dir(session_id) / "agent.json"))
            if prepare := getattr(self, '_agent_preparer', None):
                prepare(detail.id, agent)
            self._agents[detail.id] = agent
        self._bind_agent_tools(agent)

        tools, blocks, progress = {}, [], []
        summary_before = getattr(agent, 'summary', None)

        def collect_event(event):
            if event['type'] == 'tool':
                tools[event['id']] = public_event(event)
            elif event['type'] == 'response_end' and event.get('kind') == 'activity':
                progress.append({'id': event['id'], 'text': event['text']})
            elif event['type'] == 'business':
                blocks.append(event['block'])
            if on_event:
                on_event(event)

        steps: list[AgentStep] = []

        def _step_collector(step: AgentStep):
            steps.append(step)
            if on_step:
                try:
                    on_step(step)
                except Exception:
                    pass

        try:
            import inspect
            sig = inspect.signature(agent.chat)
            if "on_event" in sig.parameters and on_event:
                reply = agent.chat(message, on_trace=_step_collector, on_event=collect_event)
            elif "on_trace" in sig.parameters:
                reply = agent.chat(message, on_trace=_step_collector)
            else:
                reply = agent.chat(message)
                if hasattr(agent, "last_trace") and agent.last_trace:
                    steps.extend(agent.last_trace)
        except Exception:
            # Agent may have appended a user message before the model failed.
            # Recreate it from its last persisted state on the next request.
            self._agents.pop(detail.id, None)
            try:
                self._close_tools(agent)
            except Exception:
                logger.exception("Failed to close Agent resources after request error")
            raise

        if not steps and hasattr(agent, "last_trace") and agent.last_trace:
            steps = list(agent.last_trace)

        if on_event:
            collect_event({'type': 'memory', 'history_count': len(getattr(agent, 'raw_messages', [])), 'summary_changed': getattr(agent, 'summary', None) != summary_before, 'summary_length': len(getattr(agent, 'summary', None) or '')})

        now = _now()
        user_message = DisplayMessage(role="user", content=message, created_at=now)
        assistant_message = DisplayMessage(
            role="assistant",
            content=reply.reply,
            created_at=_now(),
            metadata=reply,
            steps=[] if request_id else safe_steps(steps),
            presentation={'request_id': request_id, 'tools': list(tools.values()), 'blocks': blocks, 'progress': progress} if request_id else None,
        )
        detail.messages.extend([user_message, assistant_message])
        if detail.title == "新会话":
            detail.title = message.replace("\n", " ")[:26]
        detail.updated_at = _now()
        self._write(detail)
        return ChatResult(
            session_id=detail.id,
            reply=reply,
            user_message=user_message,
            assistant_message=assistant_message,
        )

    def reset_session(self, session_id: UUID) -> SessionDetail:
        detail = self._read(session_id)
        agent = self._agents.get(detail.id)
        if agent is not None:
            agent.reset()
        else:
            (self._dir(session_id) / "agent.json").unlink(missing_ok=True)
        detail.messages = []
        detail.title = "新会话"
        detail.updated_at = _now()
        self._write(detail)
        return detail

    def delete_session(self, session_id: UUID) -> None:
        detail = self._read(session_id)
        agent = self._agents.pop(detail.id, None)
        if agent is not None:
            self._close_tools(agent)
        shutil.rmtree(self._dir(session_id))

    def close_all(self) -> None:
        # Close network resources and perform the Agent's existing memory shutdown.
        for agent in self._agents.values():
            try:
                agent.close()
            except Exception:
                logger.exception("Agent cleanup failed")
        self._agents.clear()
