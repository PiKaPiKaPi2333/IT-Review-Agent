"""长期记忆候选的确认接口：用户确认后才写入长期记忆。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas import MemoryDecisionRequest, MemoryDecisionResponse
from app.memory.proposals import (
    ProposalAlreadyDecided,
    ProposalNotFound,
    ProposalSaveError,
    decide_proposal,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/memory/proposals/{proposal_id}/decision",
    response_model=MemoryDecisionResponse,
    summary="确认或拒绝一条长期记忆候选",
)
async def decide(proposal_id: str, req: MemoryDecisionRequest) -> MemoryDecisionResponse:
    try:
        status = await decide_proposal(
            proposal_id=proposal_id,
            user_id=req.user_id,
            accept=req.accept,
        )
    except ProposalNotFound as e:
        raise HTTPException(status_code=404, detail="候选记忆不存在") from e
    except ProposalAlreadyDecided as e:
        raise HTTPException(status_code=409, detail=f"该候选已处理（当前状态：{e}）") from e
    except ProposalSaveError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return MemoryDecisionResponse(id=proposal_id, status=status)
