"""ChatDock 端点(ADR-0031 P0):SSE 单轮对话 + 历史查询。

POST /chat 以 SSE 下发 ReAct 事件(step/tool_call/tool_result/reply/error);
GET /chat/history 供前端刷新后还原会话。鉴权与一键生成同口径
(require_story,owner/member)。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import engine
from app.application.chat_service import ChatService
from app.auth import AuthUser, get_current_user, require_story
from app.core.config import get_settings
from app.infrastructure.chat_store import ChatStore

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str


@router.post("/stories/{story_id}/chat")
def chat(story_id: str, req: ChatRequest,
         user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    # LLM 经引擎注入(deps.llm):测试回放与模型路由共用同一入口
    service = ChatService(deps, deps.llm, get_settings().log_dir / "chat")

    def gen():
        for evt in service.run_turn(story_id, user.id, req.message):
            yield (f"event: {evt['kind']}\n"
                   f"data: {json.dumps(evt['data'], ensure_ascii=False)}\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/stories/{story_id}/chat/history")
def chat_history(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return {"messages": ChatStore(deps.conn).history(story_id)}
