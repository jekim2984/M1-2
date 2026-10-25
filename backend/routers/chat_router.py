from __future__ import annotations

from typing import Any, Dict, Optional
from fastapi import APIRouter, Query

from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.ai_service import (
    AI_TOOLS_SCHEMA,
    fetch_google_news_rss,
    generate_ai_chat_response,
)

router = APIRouter(prefix="/api", tags=["3. AI 부동산 비서 채팅 & 실시간 뉴스 (Context Injection & Function Calling)"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="AI 부동산 비서 대화 API (데이터 요약 컨텍스트 주입 + Function Calling + 자동 저장)",
)
def chat_with_ai(payload: ChatRequest) -> Dict[str, Any]:
    """
    1. `/api/data/summary` 요약 정보(단지별 실거래가, 통합재건축 기본 프로필, 전세 동향, 경매 포착, 정책/교통호재) 조회
    2. 시스템 프롬프트에 요약 컨텍스트 자동 주입 (Context Injection)
    3. Gemini API (`openai` 호환 SDK) 호출 및 필요 시 내부 도구(Function Calling) 실행
    4. 대화 내역을 `conversations` 컬렉션에 자동 저장 후 반환
    """
    return generate_ai_chat_response(
        user_message=payload.message,
        conversation_id=payload.conversation_id,
        selected_complex=payload.selected_complex,
    )


@router.get(
    "/news",
    summary="실시간 부동산 정책 · 교통호재 · 개포 재건축 뉴스 조회",
)
def get_realtime_policy_transit_news(
    q: Optional[str] = Query(
        "개포동 재건축 OR 강남 토지거래허가구역 OR GTX 수서 삼성",
        description="뉴스 검색 키워드",
    ),
) -> Dict[str, Any]:
    """Google News RSS를 통해 최신 정부 부동산 정책, 교통호재(GTX 등), 재건축 뉴스를 실시간으로 가져옵니다."""
    query_str = q or "개포동 재건축 OR 강남 토지거래허가구역 OR GTX"
    items = fetch_google_news_rss(query=query_str, limit=6)
    return {
        "query": query_str,
        "count": len(items),
        "items": items,
    }


@router.get(
    "/tools",
    summary="[보너스] AI 도구 호출(Function Calling) 및 GPT Actions / MCP 연동 스키마 조회",
)
def get_registered_ai_tools() -> Dict[str, Any]:
    """AI 비서에 등록된 내부 도구(Function Calling) 스키마 목록을 반환합니다."""
    return {
        "tool_count": len(AI_TOOLS_SCHEMA),
        "tools": AI_TOOLS_SCHEMA,
    }

