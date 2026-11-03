"""Public API contract. Do not expose internal ReAct/tool messages."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, field_serializer

from app.schemas.response import CustomerServiceResponse
from app.schemas.trace import AgentStep


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("消息不能为空")
        return cleaned


class DisplayMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    metadata: CustomerServiceResponse | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    presentation: dict | None = None

    @field_serializer('steps')
    def serialize_steps(self, value):
        from app.streaming.security import safe_steps
        return safe_steps(value)


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    preview: str = ''
    message_count: int = 0


class SessionDetail(SessionSummary):
    messages: list[DisplayMessage] = Field(default_factory=list)


class SessionList(BaseModel):
    sessions: list[SessionSummary]


class ChatResult(BaseModel):
    session_id: str
    reply: CustomerServiceResponse
    user_message: DisplayMessage
    assistant_message: DisplayMessage
