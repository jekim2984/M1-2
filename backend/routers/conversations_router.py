from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, status

from backend.models.schemas import ConversationCreate, ConversationResponse
from backend.services.firebase_service import db_service

router = APIRouter(prefix="/api/conversations", tags=["2. 대화 기록 관리 (Conversations History)"])


@router.get(
    "",
    response_model=List[ConversationResponse],
    summary="대화 목록 조회 (전체 messages 포함)",
)
def list_conversations() -> List[Dict[str, Any]]:
    """저장된 모든 AI 상담 대화 목록을 최신순으로 조회합니다. (응답에 messages 배열 포함)"""
    return db_service.list_conversations()


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="특정 대화 상세 불러오기 (전체 messages 조회)",
)
def get_conversation_detail(conversation_id: str) -> Dict[str, Any]:
    """특정 대화 ID의 전체 메시지 내역을 불러옵니다."""
    conv = db_service.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ID가 '{conversation_id}'인 대화 기록을 찾을 수 없습니다.",
        )
    return conv


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="대화 저장",
)
def create_conversation(payload: ConversationCreate) -> Dict[str, Any]:
    """대화 제목과 메시지 목록을 conversations 컬렉션에 저장합니다."""
    msgs = [m.model_dump() for m in payload.messages]
    return db_service.save_conversation(title=payload.title, messages=msgs)


@router.delete(
    "/{conversation_id}",
    summary="대화 삭제",
)
def delete_conversation(conversation_id: str) -> Dict[str, Any]:
    """특정 대화 기록을 삭제합니다."""
    deleted = db_service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ID가 '{conversation_id}'인 대화 기록을 찾을 수 없습니다.",
        )
    return {"status": "deleted", "id": conversation_id, "message": "대화 기록이 삭제되었습니다."}

