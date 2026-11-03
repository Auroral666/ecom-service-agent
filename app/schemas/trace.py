"""Agent 执行轨迹与单步跟踪数据模型。

用于记录 ReAct 循环中的思考 (thought)、工具调用 (action)、工具观测 (observation) 以及路由 (route)。
"""

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentStep(BaseModel):
    """ReAct 过程中的单步跟踪数据模型"""

    id: str = Field(default_factory=lambda: uuid4().hex, description="步骤唯一标识")
    type: Literal["thought", "action", "observation", "route"] = Field(
        description="步骤类型：thought (思考), action (工具调用), observation (观测结果), route (意图路由)"
    )
    title: str = Field(default="", description="步骤展示标题")
    content: str = Field(default="", description="主要文本内容或思考说明")
    tool_name: Optional[str] = Field(default=None, description="调用的工具名称")
    tool_args: Optional[dict[str, Any]] = Field(default=None, description="工具入参字典")
    tool_result: Optional[str] = Field(default=None, description="工具返回的观测原始数据")
    agent_name: Optional[str] = Field(default=None, description="执行此步骤的 Agent 名称")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"),
        description="步骤生成时间",
    )
