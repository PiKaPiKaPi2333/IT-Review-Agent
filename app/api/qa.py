"""问答路由。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app.api.schemas import (
    AskRequest,
    AskResponse,
    FeedbackItem,
    FeedbackRequest,
    FeedbackReviewRequest,
)
from app.graph.runner import run_question
from app.infra.db import session_scope
from app.infra.models import AnswerFeedback, Conversation, Message

logger = logging.getLogger(__name__)
router = APIRouter()


class ConversationSummary(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class StoredMessage(BaseModel):
    id: str
    role: str
    content: str | None
    citations: list | None = None
    answer: dict | None = None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    messages: list[StoredMessage]


@router.post("/ask", response_model=AskResponse, summary="审计问答")
async def ask(req: AskRequest) -> AskResponse:
    try:
        final = await run_question(
            question=req.question,
            user_id=req.user_id,
            thread_id=req.thread_id,
            conversation_id=req.conversation_id,
            audit_ctx_id=req.audit_ctx_id,
            audit_context_text=req.audit_context_text,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("问答执行失败")
        raise HTTPException(status_code=500, detail=f"问答执行失败：{e}") from e

    return AskResponse(**final.model_dump())


@router.post("/feedback", summary="答案反馈（👍/👎），同一用户对同一回答重复提交会覆盖")
async def feedback(req: FeedbackRequest) -> dict:
    async with session_scope() as db:
        msg = await _find_answer_message(db, req.answer_id)
        if msg is None:
            raise HTTPException(status_code=404, detail="回答不存在")
        conv = await db.get(Conversation, msg.conversation_id)
        # 只能对自己的会话中的回答反馈，不暴露他人会话是否存在
        if conv is None or conv.user_id != req.user_id:
            raise HTTPException(status_code=404, detail="回答不存在")

        question = (
            await db.execute(
                select(Message.content)
                .where(
                    Message.conversation_id == msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        fb = (
            await db.execute(
                select(AnswerFeedback).where(
                    AnswerFeedback.answer_id == req.answer_id,
                    AnswerFeedback.user_id == req.user_id,
                )
            )
        ).scalars().first()
        if fb is None:
            fb = AnswerFeedback(answer_id=req.answer_id, user_id=req.user_id, rating=req.rating)
            db.add(fb)

        fb.rating = req.rating
        # 原因只对 👎 有意义；改为 👍 时清空
        fb.reason = req.reason if req.rating == -1 else None
        fb.comment = req.comment if req.rating == -1 else None
        fb.conversation_id = msg.conversation_id
        fb.question = question
        await db.flush()
        return {"ok": True, "id": str(fb.id)}


@router.get("/feedback", response_model=list[FeedbackItem], summary="反馈列表（含问题与回答，供复核）")
async def list_feedback(
    rating: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[FeedbackItem]:
    stmt = select(AnswerFeedback).order_by(AnswerFeedback.created_at.desc())
    if rating is not None:
        stmt = stmt.where(AnswerFeedback.rating == rating)
    if status is not None:
        stmt = stmt.where(AnswerFeedback.status == status)
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))

    async with session_scope() as db:
        rows = (await db.execute(stmt)).scalars().all()
        answers = await _answers_by_id(db, [r.answer_id for r in rows])
    return [_to_item(r, answers.get(r.answer_id)) for r in rows]


@router.patch("/feedback/{feedback_id}", response_model=FeedbackItem, summary="复核反馈（确认坏例需填写标准答案）")
async def review_feedback(feedback_id: str, req: FeedbackReviewRequest) -> FeedbackItem:
    fid = _parse_uuid(feedback_id, detail="反馈不存在")
    if req.status == "confirmed":
        if not (req.ground_truth or "").strip():
            raise HTTPException(status_code=422, detail="确认为坏例时必须填写标准答案（ground_truth）")

    async with session_scope() as db:
        fb = await db.get(AnswerFeedback, fid)
        if fb is None:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if req.status == "confirmed" and fb.rating != -1:
            raise HTTPException(status_code=422, detail="只有 👎 反馈可以确认为坏例")

        fb.status = req.status
        fb.ground_truth = (req.ground_truth or "").strip() or None
        fb.review_note = req.review_note
        fb.reviewed_by = req.reviewed_by
        fb.reviewed_at = datetime.now(timezone.utc)
        await db.flush()
        answers = await _answers_by_id(db, [fb.answer_id])
    return _to_item(fb, answers.get(fb.answer_id))


# ── 反馈辅助函数 ─────────────────────────────────────────────

async def _find_answer_message(db, answer_id: str) -> Message | None:
    """通过助手消息中保存的 answer_id 定位回答。"""
    return (
        await db.execute(
            select(Message).where(
                Message.role == "assistant",
                Message.answer["answer_id"].as_string() == answer_id,
            )
        )
    ).scalars().first()


async def _answers_by_id(db, answer_ids: list[str]) -> dict[str, Message]:
    if not answer_ids:
        return {}
    msgs = (
        await db.execute(
            select(Message).where(
                Message.role == "assistant",
                Message.answer["answer_id"].as_string().in_(answer_ids),
            )
        )
    ).scalars().all()
    return {m.answer["answer_id"]: m for m in msgs if m.answer}


def _to_item(fb: AnswerFeedback, msg: Message | None) -> FeedbackItem:
    return FeedbackItem(
        id=str(fb.id),
        answer_id=fb.answer_id,
        user_id=fb.user_id,
        rating=fb.rating,
        reason=fb.reason,
        comment=fb.comment,
        status=fb.status or "pending",
        question=fb.question,
        answer_markdown=(msg.answer or {}).get("answer_markdown") if msg else None,
        citations=(msg.answer or {}).get("citations") if msg else None,
        conversation_id=str(fb.conversation_id) if fb.conversation_id else None,
        ground_truth=fb.ground_truth,
        review_note=fb.review_note,
        reviewed_by=fb.reviewed_by,
        reviewed_at=fb.reviewed_at,
        created_at=fb.created_at,
    )


def _parse_uuid(value: str, *, detail: str = "会话不存在") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=detail) from e


# ── 会话历史 ─────────────────────────────────────────────────

@router.get("/conversations", response_model=list[ConversationSummary], summary="会话列表（按最近活动倒序）")
async def list_conversations(user_id: str = "anonymous", limit: int = 50) -> list[ConversationSummary]:
    last_at = (
        select(func.max(Message.created_at))
        .where(Message.conversation_id == Conversation.id)
        .scalar_subquery()
    )
    updated = func.coalesce(last_at, Conversation.created_at)
    stmt = (
        select(Conversation, updated.label("updated_at"))
        .where(Conversation.user_id == user_id)
        .order_by(updated.desc())
        .limit(max(1, min(limit, 200)))
    )
    async with session_scope() as db:
        rows = (await db.execute(stmt)).all()
    return [
        ConversationSummary(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at,
            updated_at=updated_at,
        )
        for conv, updated_at in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail, summary="会话详情（含全部消息）")
async def get_conversation(conversation_id: str, user_id: str = "anonymous") -> ConversationDetail:
    cid = _parse_uuid(conversation_id)
    async with session_scope() as db:
        conv = await db.get(Conversation, cid)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")
        msgs = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == cid)
                .order_by(Message.created_at, Message.id)
            )
        ).scalars().all()
    return ConversationDetail(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at,
        messages=[
            StoredMessage(
                id=str(m.id),
                role=m.role,
                content=m.content,
                citations=m.citations,
                answer=m.answer,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.delete("/conversations/{conversation_id}", summary="删除会话（含消息）")
async def delete_conversation(conversation_id: str, user_id: str = "anonymous") -> dict:
    cid = _parse_uuid(conversation_id)
    async with session_scope() as db:
        conv = await db.get(Conversation, cid)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")
        await db.execute(delete(Message).where(Message.conversation_id == cid))
        await db.delete(conv)
    return {"ok": True}
