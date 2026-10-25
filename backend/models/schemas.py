from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# =====================================================================
# 1. 시계열 데이터(Data) 관련 Pydantic 스키마
#    - date (날짜: YYYY-MM-DD 또는 YYYY-MM 형식 검증)
#    - value (금액: 만원 단위 양수 검증, 예: 272000 = 27억 2,000만 원)
#    - memo (메모: 단지명/실거래/전세동향/정책/교통호재/경매 정보)
# =====================================================================

class DataBase(BaseModel):
    date: str = Field(
        ...,
        description="기록 날짜 (YYYY-MM-DD 형식 권장, 예: 2026-02-15)",
        examples=["2026-02-15"],
    )
    value: float = Field(
        ...,
        gt=0,
        description="매매 실거래가/기준가 또는 경매 최저입찰가 (단위: 만원, 예: 272000 = 27억 2,000만원)",
        examples=[272000],
    )
    memo: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description="메모 ([단지명] 평형·층수·전세동향 또는 [정부정책]/[교통호재]/[경매매물] 상세 내용)",
        examples=["[개포우성8차] 전용79㎡ 5층 매매 실거래 신고가 (전세가 10.8억 강세 동반)"],
    )

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{4}-\d{2}(-\d{2})?$", v):
            raise ValueError("날짜(date)는 'YYYY-MM-DD' 또는 'YYYY-MM' 형식이어야 합니다. (예: 2026-02-15)")
        return v

    @field_validator("memo")
    @classmethod
    def validate_memo_clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("메모(memo) 내용을 입력해 주세요.")
        return v


class DataCreate(DataBase):
    """POST /api/data 요청 바디 검증 모델"""
    pass


class DataUpdate(BaseModel):
    """PUT /api/data/{id} 요청 바디 검증 모델 (부분 수정 허용)"""
    date: Optional[str] = Field(None, description="수정할 날짜 (YYYY-MM-DD)")
    value: Optional[float] = Field(None, gt=0, description="수정할 금액 (단위: 만원)")
    memo: Optional[str] = Field(None, min_length=2, max_length=500, description="수정할 메모")

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not re.match(r"^\d{4}-\d{2}(-\d{2})?$", v):
            raise ValueError("날짜(date)는 'YYYY-MM-DD' 또는 'YYYY-MM' 형식이어야 합니다.")
        return v


class DataItem(DataBase):
    """DB에 저장된 데이터 응답 모델"""
    id: str = Field(..., description="데이터 고유 ID")
    category: Optional[str] = Field(None, description="자동 분류 카테고리 (trade | auction | policy | transit)")
    complex_name: Optional[str] = Field(None, description="자동 인식된 아파트 단지명 (예: 개포우성8차, 개포현대3차)")
    created_at: Optional[str] = Field(None, description="생성 시각 (ISO 8601)")


class MetricsSummary(BaseModel):
    total: float = Field(..., description="합계 (만원)")
    average: float = Field(..., description="평균 매매가 (만원)")
    max: float = Field(..., description="최고 매매가 (만원)")
    min: float = Field(..., description="최저 매매가 (만원)")
    median: Optional[float] = Field(None, description="중앙값 (만원)")
    std_dev: Optional[float] = Field(None, description="표준편차 (만원)")


class DataSummaryResponse(BaseModel):
    """GET /api/data/summary 응답 모델 (과제 필수 필드 + 부동산 특화 인사이트 확장)"""
    period: str = Field(..., description="전체 데이터 기간 (예: 2023-01-12 ~ 2026-10-18)")
    count: int = Field(..., description="전체 저장된 레코드 수 (100개 이상)")
    trade_count: int = Field(..., description="일반 매매 실거래/시세 레코드 수")
    metrics: MetricsSummary = Field(..., description="매매 실거래 주요 통계 지표 (단위: 만원)")
    trend: str = Field(..., description="최근 시세 트렌드 요약")
    market_timing_signal: str = Field(..., description="전세가 동향 및 실거래 추이 기반 매매 타이밍 진단")
    by_complex: Dict[str, Any] = Field(default_factory=dict, description="단지별(우성8차, 현대3차 등) 개별 통계 요약")
    complex_profiles: Dict[str, Any] = Field(default_factory=dict, description="단지 고정 기본 프로필 및 통합재건축 연관성")
    auction_alerts: Dict[str, Any] = Field(default_factory=dict, description="경매 매물 포착 현황 및 낙찰 사례 특별 요약")
    policy_and_transit: List[Dict[str, Any]] = Field(default_factory=list, description="정부정책 및 교통호재 주요 타임라인")


# =====================================================================
# 2. 대화 기록(Conversations) 및 AI 채팅(Chat) 관련 Pydantic 스키마
# =====================================================================

class MessageItem(BaseModel):
    role: str = Field(..., description="메시지 작성자 역할 (user | assistant | system)")
    content: str = Field(..., description="메시지 본문")
    timestamp: Optional[str] = Field(None, description="메시지 생성 시각")
    tool_calls_used: Optional[List[str]] = Field(None, description="호출된 AI 도구(Function Calling) 목록")


class ConversationCreate(BaseModel):
    """POST /api/conversations 요청 바디"""
    title: str = Field(..., min_length=1, max_length=200, description="대화 제목")
    messages: List[MessageItem] = Field(default_factory=list, description="대화 메시지 목록")


class ConversationResponse(BaseModel):
    """대화 기록 조회 응답 모델"""
    id: str = Field(..., description="대화 고유 ID")
    title: str = Field(..., description="대화 제목")
    created_at: str = Field(..., description="생성 일시")
    updated_at: str = Field(..., description="최종 수정 일시")
    message_count: int = Field(..., description="총 메시지 개수")
    messages: List[MessageItem] = Field(default_factory=list, description="메시지 배열")


class ChatRequest(BaseModel):
    """POST /api/chat 요청 바디"""
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="사용자의 자연어 질문",
        examples=["우성8차랑 현대3차 최근 시세 흐름이랑 통합재건축 연관성, 지금 매수 타이밍 어때?"],
    )
    conversation_id: Optional[str] = Field(
        None,
        description="이어서 대화할 기존 대화 ID (없으면 새 대화 자동 생성)",
    )
    selected_complex: Optional[str] = Field(
        None,
        description="특정 단지 포커스 필터 (전체 또는 단지명)",
    )


class ChatResponse(BaseModel):
    """POST /api/chat 응답 모델"""
    reply: str = Field(..., description="데이터 요약 컨텍스트가 반영된 AI 비서의 답변")
    conversation_id: str = Field(..., description="자동 저장된 대화 ID")
    conversation_title: str = Field(..., description="대화방 제목")
    provider: str = Field(..., description="사용된 AI 엔진 (gemini / openai / analytical-fallback)")
    tools_called: List[str] = Field(default_factory=list, description="Function Calling으로 실행된 내부 도구 목록")
    context_summary_snapshot: Dict[str, Any] = Field(
        default_factory=dict, description="시스템 프롬프트에 주입된 핵심 요약 정보 스냅샷"
    )

