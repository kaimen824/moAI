"""小说工作流端点:建书/详情/生成/恢复/停止/章节/Codex/指令通道。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.deps import _active, engine, locked
from app.api.sse import resume_command, sse_run
from app.application.run_service import BudgetExceeded, check_daily_budget
from app.auth import AuthUser, get_current_user, require_story
from app.infrastructure.queries import StoryQueries

router = APIRouter()


class CreateStory(BaseModel):
    title: str
    premise: str = ""


class GenerateRequest(BaseModel):
    # 上限 50(评审 6.10):一次生成不计成本地铺章是公网成本事故的直通车
    target_chapters: int = Field(default=1, ge=1, le=50)
    initial_input: str = ""
    tags: list[str] = []              # 题材标签(Dify 式 token):独立单元,可多选
    branch_id: str = ""
    auto_confirm: bool = False        # 自动模式(ADR-0016):细纲/章节闸自动确认


class ResumeRequest(BaseModel):
    action: str                      # confirm | revise
    feedback: str = ""
    threads: list[dict] = []         # 人工确认的伏笔变更


class DirectiveRequest(BaseModel):
    text: str


def _budget_http_error(exc: BudgetExceeded) -> HTTPException:
    """application 层预算异常 -> 429(错误契约:X-Error-Code + 文案)。"""
    env = "STORY" if exc.scope == "story" else "GLOBAL"
    return HTTPException(
        429, f"{exc.message}(已用 {exc.spent}/{exc.limit};明日重置,"
        f"或由管理员调高 NOVEL_{env}_DAILY_TOKEN_BUDGET)",
        headers={"X-Error-Code": "budget_exceeded"})


@router.get("/stories/{story_id}/run-state")
@locked
def run_state(story_id: str, user: AuthUser = Depends(get_current_user)):
    """会话恢复:前端挂载时查询 —— 运行中 / 等待中断(含中断卡数据)/ 空闲 + 事件历史。

    状态以 story_run_state(ADR-0027)为准,_active 仅作在跑标记:
    - DB waiting + 在跑 → running(用户已 resume,worker 尚未覆写完成)
    - DB running 但不在跑 → idle(崩溃残留读时兜底)
    中断卡数据优先取 DB payload(跨重启存活),事件快照兜底。
    """
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    snap = deps.snapshot(story_id)
    events = [{"kind": k, "data": d} for k, d in snap]
    snapshot_interrupt = next(
        (d for k, d in reversed(snap) if k == "interrupt"), None)
    row = deps.get_run_state(story_id)
    active = story_id in _active
    status = row["status"] if row else (
        "running" if active else ("waiting" if snapshot_interrupt else "idle"))
    if row and row["status"] == "waiting" and active:
        status = "running"
    elif row and row["status"] == "running" and not active:
        status = "idle"
    interrupt = snapshot_interrupt
    if status == "waiting" and row and row.get("interrupt_payload"):
        try:
            interrupt = json.loads(row["interrupt_payload"])
        except (ValueError, TypeError):
            pass                # payload 损坏:退回事件快照兜底
    return {"status": status, "interrupt": interrupt, "events": events,
            "run_id": row.get("run_id") if row else None}


@router.post("/stories/{story_id}/attach")
def attach(story_id: str, user: AuthUser = Depends(get_current_user)):
    """断线/刷新后重新订阅事件流(只收不发,不驱动图)。"""
    from app.api.sse import sse_response
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return sse_response(deps, deps.subscribe(story_id))


@router.post("/stories")
@locked
def create_story(req: CreateStory, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    story_id, branch_id = deps.repo.create_story(req.title, req.premise, owner_id=user.id)
    return {"story_id": story_id, "branch_id": branch_id}


@router.get("/stories")
@locked
def list_stories(user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    return StoryQueries(deps.conn).list_for_user(user.id)


@router.get("/stories/{story_id}")
@locked
def story_detail(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    try:
        return StoryQueries(deps.conn).detail(story_id)
    except LookupError:
        raise HTTPException(404, "story not found")


@router.post("/stories/{story_id}/generate")
@locked
def generate(story_id: str, req: GenerateRequest,
             user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    try:
        check_daily_budget(deps, story_id)
    except BudgetExceeded as exc:
        raise _budget_http_error(exc)
    story = StoryQueries(deps.conn).story_row(story_id)
    if not story:
        raise HTTPException(404, "story not found")
    branch = req.branch_id or story["main_branch_id"]
    # 共创输入组装:书名+一句话简介(建书时填的,审计#17:此前断在中途)
    # + 题材标签 + 本次补充构想
    premise_part = ""
    if story["premise"]:
        premise_part = f"书名:{story['title']}\n一句话简介:{story['premise']}\n"
    tags_part = ("题材标签:" + " ".join("#" + t for t in req.tags) + "\n") if req.tags else ""
    return sse_run(
        {"story_id": story_id, "branch_id": branch,
         "target_chapters": req.target_chapters,
         "auto_mode": req.auto_confirm,
         "initial_input": premise_part + tags_part + req.initial_input},
        thread_id=story_id, user_id=user.id,
    )


@router.post("/stories/{story_id}/resume")
@locked
def resume(story_id: str, req: ResumeRequest,
           user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return sse_run(
        resume_command(req),
        thread_id=story_id, user_id=user.id,
    )


@router.post("/stories/{story_id}/stop")
@locked
def stop(story_id: str, user: AuthUser = Depends(get_current_user)):
    """协作式中断:置位停止请求,图在下一个节点边界安全退出(断点可续跑)。"""
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    deps.request_stop(story_id)
    return {"ok": True, "active": story_id in _active}


@router.get("/stories/{story_id}/chapters/{chapter_no}")
@locked
def get_chapter(story_id: str, chapter_no: int,
                user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    row = StoryQueries(deps.conn).active_chapter(story_id, chapter_no)
    if not row:
        raise HTTPException(404, "chapter not found")
    return dict(row)


@router.get("/stories/{story_id}/codex")
@locked
def codex(story_id: str, user: AuthUser = Depends(get_current_user)):
    """设定集(Codex):角色卡 + 伏笔台账 + 当前有效世界记忆(排除被推翻/拒绝)。

    facts 推翻链与 world 回放同口径:有后续版本指向即失效,任一时点只呈现有效记忆。
    """
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    q = StoryQueries(deps.conn)
    if q.story_row(story_id) is None:
        raise HTTPException(404, "story not found")
    return q.codex(story_id, q.main_branch(story_id))


@router.post("/stories/{story_id}/directive")
@locked
def post_directive(story_id: str, req: DirectiveRequest,
                   user: AuthUser = Depends(get_current_user)):
    """用户指令通道:任意时刻提交,下一次章节生成的上下文中被主控消费。"""
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    if req.text.strip():
        deps.record_directive(story_id, req.text.strip())
    return {"ok": True,
            "pending": StoryQueries(deps.conn).pending_directive_count(story_id)}
