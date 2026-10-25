from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from backend.models.schemas import (
    DataCreate,
    DataItem,
    DataSummaryResponse,
    DataUpdate,
)
from backend.services.analytics_service import build_data_summary, get_chart_statistics
from backend.services.firebase_service import db_service

router = APIRouter(prefix="/api/data", tags=["1. 시계열 데이터 관리 & 요약 (Data CRUD & Summary)"])


@router.get(
    "/summary",
    response_model=DataSummaryResponse,
    summary="데이터 요약 조회 (프롬프트 주입용 + 단지별/경매/정책 분리 집계)",
)
def get_data_summary(
    complex_name: Optional[str] = Query(None, description="특정 단지 필터 (예: 개포우성8차, 개포현대3차)")
) -> Dict[str, Any]:
    """
    저장된 전체 시계열 데이터를 분석하여 요약 정보를 반환합니다.
    - **period**: 데이터 기간
    - **count**: 전체 레코드 수 (100개 이상)
    - **metrics**: 일반 매매 실거래 기준 합계/평균/최대/최소/중앙값/표준편차
    - **trend**: 최근 시세 추세
    - **by_complex**: 단지별(우성8차, 현대3차, 또는 추가된 단지) 개별 통계
    - **complex_profiles**: 단지 고정 스펙 및 통합재건축(독립정산제) 연관성
    - **auction_alerts**: 진행 중 경매 포착 알림 및 과거 낙찰 사례 (평균가 왜곡 방지를 위해 별도 분리)
    - **policy_and_transit**: 정부정책 및 교통호재 주요 타임라인
    """
    return build_data_summary(selected_complex=complex_name)


@router.get(
    "/statistics",
    summary="[보너스] 시세 비교 차트 및 심화 통계 데이터 조회",
)
def get_data_statistics() -> Dict[str, Any]:
    """프론트엔드 시세 추이 그래프(Chart.js) 렌더링을 위한 단지별 시계열 및 경매/정책 이벤트 포인트를 반환합니다."""
    return get_chart_statistics()


@router.get(
    "",
    response_model=List[DataItem],
    summary="데이터 목록 조회 (단지별 / 카테고리별 필터 지원)",
)
def list_all_data(
    complex_name: Optional[str] = Query(None, description="단지명 필터 (예: 개포우성8차, 개포현대3차)"),
    category: Optional[str] = Query(None, description="카테고리 필터 (trade | auction | policy | transit)"),
) -> List[Dict[str, Any]]:
    """저장된 시계열 데이터 목록을 최신순으로 조회합니다."""
    return db_service.list_data(complex_filter=complex_name, category_filter=category)


@router.post(
    "",
    response_model=DataItem,
    status_code=status.HTTP_201_CREATED,
    summary="새 데이터 추가 (실거래 / 정책 / 교통호재 / 경매 / 신규 단지)",
)
def create_data_item(payload: DataCreate) -> Dict[str, Any]:
    """
    `(date, value, memo)` 형태의 새 데이터를 추가합니다.
    - `memo`에 `[단지명]`을 적으면 자동으로 해당 단지로 분류되며, 새로운 아파트를 적어도 즉시 새 단지로 확장됩니다.
    - `memo`에 `[경매매물]`, `[정부정책]`, `[교통호재]`를 적으면 자동으로 특별 카테고리로 분류됩니다.
    """
    return db_service.create_data(
        date=payload.date,
        value=payload.value,
        memo=payload.memo,
    )


@router.put(
    "/{item_id}",
    response_model=DataItem,
    summary="데이터 수정",
)
def update_data_item(item_id: str, payload: DataUpdate) -> Dict[str, Any]:
    """특정 ID의 시계열 데이터(`date`, `value`, `memo`)를 수정합니다."""
    updated = db_service.update_data(
        doc_id=item_id,
        date=payload.date,
        value=payload.value,
        memo=payload.memo,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ID가 '{item_id}'인 데이터를 찾을 수 없습니다.",
        )
    return updated


@router.delete(
    "/{item_id}",
    summary="데이터 삭제",
)
def delete_data_item(item_id: str) -> Dict[str, Any]:
    """특정 ID의 시계열 데이터를 삭제합니다."""
    deleted = db_service.delete_data(doc_id=item_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ID가 '{item_id}'인 데이터를 찾을 수 없습니다.",
        )
    return {"status": "deleted", "id": item_id, "message": "데이터가 삭제되었습니다."}


@router.post(
    "/seed",
    summary="초기 데이터(개포우성8차 & 현대3차 119건) 복원/재시딩",
)
def reseed_default_data(reset: bool = Query(True, description="기존 데이터를 초기화하고 기본 119건으로 복원할지 여부")) -> Dict[str, Any]:
    """개포우성8차 & 개포현대3차 기본 시계열 데이터(119건)를 DB에 일괄 세팅합니다."""
    count = db_service.seed_default_data(reset=reset)
    return {
        "status": "seeded",
        "count": count,
        "message": f"개포우성8차 & 개포현대3차 시계열 데이터 {count}건이 세팅되었습니다.",
    }

