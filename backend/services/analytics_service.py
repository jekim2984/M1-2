from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional

from backend.seed_data import DEFAULT_COMPLEX_PROFILES, KNOWN_COMPLEX_PRESETS, build_auto_complex_dataset
from backend.services.firebase_service import db_service


def _format_eok(manwon: float) -> str:
    """만원 단위를 읽기 쉬운 'XX.X억 원 (XX,XXX만원)' 문자열로 변환"""
    eok = manwon / 10000.0
    return f"{eok:.2f}억 원 ({int(round(manwon)):,}만원)"


def _calc_metrics(values: List[float]) -> Dict[str, float]:
    if not values:
        return {
            "total": 0.0,
            "average": 0.0,
            "max": 0.0,
            "min": 0.0,
            "median": 0.0,
            "std_dev": 0.0,
        }
    total = round(sum(values), 1)
    avg = round(total / len(values), 1)
    med = round(float(statistics.median(values)), 1)
    std = round(float(statistics.pstdev(values)), 1) if len(values) > 1 else 0.0
    return {
        "total": total,
        "average": avg,
        "max": round(max(values), 1),
        "min": round(min(values), 1),
        "median": med,
        "std_dev": std,
    }


def _calc_trend_description(sorted_items: List[Dict[str, Any]]) -> str:
    """초기 구간 대비 최근 구간의 가격 변화율과 방향성을 계산"""
    if len(sorted_items) < 2:
        return "데이터 부족 (보합)"

    # 최근 5건 평균 vs 초기 5건 평균 비교, 그리고 직전 분기 대비 비교
    window = min(5, max(1, len(sorted_items) // 4))
    early_avg = sum(x["value"] for x in sorted_items[:window]) / window
    recent_avg = sum(x["value"] for x in sorted_items[-window:]) / window

    if early_avg <= 0:
        return "보합"

    pct_change = ((recent_avg - early_avg) / early_avg) * 100.0
    latest_val = sorted_items[-1]["value"]
    prev_window_items = sorted_items[max(0, len(sorted_items) - window * 2) : -window]
    short_pct = 0.0
    if prev_window_items:
        prev_avg = sum(x["value"] for x in prev_window_items) / len(prev_window_items)
        if prev_avg > 0:
            short_pct = ((recent_avg - prev_avg) / prev_avg) * 100.0

    if pct_change >= 3.0:
        direction = "상승"
    elif pct_change <= -3.0:
        direction = "하락"
    else:
        direction = "강보합/유지"

    return (
        f"{direction} (기간 누적 {pct_change:+.1f}%, 최근 구간 {short_pct:+.1f}% | "
        f"최근 실거래 {latest_val / 10000:.2f}억)"
    )


def build_data_summary(selected_complex: Optional[str] = None) -> Dict[str, Any]:
    """
    전체 시계열 데이터를 분석하여 /api/data/summary 응답 및 AI 컨텍스트 주입용 요약을 생성합니다.
    - 일반 매매 실거래(trade)와 경매(auction), 정부정책(policy), 교통호재(transit)를 분리 집계하여
      유찰된 경매 최저입찰가가 일반 매매 평균가를 왜곡하지 않도록 설계했습니다.
    """
    all_items = db_service.list_data()
    if not all_items:
        return {
            "period": "데이터 없음",
            "count": 0,
            "trade_count": 0,
            "metrics": _calc_metrics([]),
            "trend": "데이터 없음",
            "market_timing_signal": "데이터를 추가해 주세요.",
            "by_complex": {},
            "complex_profiles": DEFAULT_COMPLEX_PROFILES,
            "auction_alerts": {"active_count": 0, "active_items": [], "past_winning_cases": []},
            "policy_and_transit": [],
        }

    # 날짜 오름차순 정렬
    chronological = sorted(all_items, key=lambda x: (x["date"], x["id"]))
    period = f"{chronological[0]['date']} ~ {chronological[-1]['date']}"

    # 카테고리별 분리
    trade_items = [x for x in chronological if x.get("category") == "trade"]
    auction_items = [x for x in chronological if x.get("category") == "auction"]
    policy_transit_items = [
        x for x in chronological if x.get("category") in ("policy", "transit")
    ]

    # 선택된 단지 필터가 있으면 메인 metrics에 반영하되, by_complex에는 전체 단지를 모두 제공
    target_trades = trade_items
    if selected_complex and selected_complex not in ("all", "전체"):
        filtered = [
            x
            for x in trade_items
            if x.get("complex_name") == selected_complex or selected_complex in x.get("memo", "")
        ]
        if filtered:
            target_trades = filtered

    trade_values = [float(x["value"]) for x in target_trades]
    metrics = _calc_metrics(trade_values)
    trend_str = _calc_trend_description(target_trades)

    # 1. 단지별(Complex) 자동 그룹화 통계 (단지가 3개, 4개로 늘어나도 자동 인식!)
    complex_groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in trade_items:
        c_name = item.get("complex_name") or "기타단지"
        complex_groups.setdefault(c_name, []).append(item)

    by_complex: Dict[str, Any] = {}
    for c_name, c_items in complex_groups.items():
        c_vals = [float(x["value"]) for x in c_items]
        c_metrics = _calc_metrics(c_vals)
        latest_item = c_items[-1]
        max_item = max(c_items, key=lambda x: x["value"])
        min_item = min(c_items, key=lambda x: x["value"])

        # 우성8차 / 현대3차 평형별 세부 구분 요약도 함께 제공
        by_complex[c_name] = {
            "complex_name": c_name,
            "count": len(c_items),
            "period": f"{c_items[0]['date']} ~ {c_items[-1]['date']}",
            "metrics": c_metrics,
            "average_eok": round(c_metrics["average"] / 10000.0, 2),
            "max_eok": round(c_metrics["max"] / 10000.0, 2),
            "min_eok": round(c_metrics["min"] / 10000.0, 2),
            "latest_trade": {
                "date": latest_item["date"],
                "value": latest_item["value"],
                "value_eok": round(latest_item["value"] / 10000.0, 2),
                "memo": latest_item["memo"],
            },
            "highest_trade": {
                "date": max_item["date"],
                "value": max_item["value"],
                "value_eok": round(max_item["value"] / 10000.0, 2),
                "memo": max_item["memo"],
            },
            "lowest_trade": {
                "date": min_item["date"],
                "value": min_item["value"],
                "value_eok": round(min_item["value"] / 10000.0, 2),
                "memo": min_item["memo"],
            },
            "trend": _calc_trend_description(c_items),
        }

    # 2. 경매(Auction) 특별 포착 알림 분리 집계
    active_auctions: List[Dict[str, Any]] = []
    past_auctions: List[Dict[str, Any]] = []

    for auc in reversed(auction_items):
        memo_str = auc["memo"]
        c_name = auc.get("complex_name") or ("개포우성8차" if "우성8차" in memo_str else ("개포현대3차" if "현대3차" in memo_str else "대상단지"))
        ref_latest = by_complex.get(c_name, {}).get("latest_trade", {}).get("value", 270000)
        ref_max = by_complex.get(c_name, {}).get("metrics", {}).get("max", 272000)

        # 대형 평형(58평) 경매인 경우 40.5억 기준 비교
        if "163㎡" in memo_str or "58평" in memo_str:
            ref_market_price = 405000.0
        else:
            ref_market_price = float(ref_latest or ref_max or 270000.0)

        discount_manwon = round(ref_market_price - float(auc["value"]), 1)
        discount_pct = round((discount_manwon / ref_market_price) * 100.0, 1) if ref_market_price > 0 else 0.0

        enriched_auc = {
            "id": auc["id"],
            "date": auc["date"],
            "complex_name": c_name,
            "value": auc["value"],
            "value_eok": round(auc["value"] / 10000.0, 2),
            "ref_market_price": ref_market_price,
            "ref_market_eok": round(ref_market_price / 10000.0, 2),
            "safety_margin_manwon": discount_manwon,
            "safety_margin_eok": round(discount_manwon / 10000.0, 2),
            "discount_pct": discount_pct,
            "memo": memo_str,
            "is_active": ("진행중" in memo_str or "유찰" in memo_str or "경매매물" in memo_str) and "낙찰" not in memo_str[:12],
        }
        if enriched_auc["is_active"]:
            active_auctions.append(enriched_auc)
        else:
            past_auctions.append(enriched_auc)

    auction_alerts = {
        "active_count": len(active_auctions),
        "past_count": len(past_auctions),
        "badge_text": (
            f"경매 포착 (진행중 {len(active_auctions)}건 / 낙찰사례 {len(past_auctions)}건)"
            if active_auctions
            else f"진행중 경매 없음 (과거 낙찰 {len(past_auctions)}건)"
        ),
        "has_urgent_alert": len(active_auctions) > 0,
        "active_items": active_auctions,
        "past_winning_cases": past_auctions,
    }

    # 3. 정부정책 및 교통호재 타임라인 (최신순 정렬)
    policy_and_transit = [
        {
            "id": pt["id"],
            "date": pt["date"],
            "category": pt["category"],
            "category_label": "정부정책" if pt["category"] == "policy" else "교통호재",
            "reference_value": pt["value"],
            "reference_eok": round(pt["value"] / 10000.0, 2),
            "memo": pt["memo"],
        }
        for pt in reversed(policy_transit_items)
    ]

    # 4. 단지 프로필 사전 구성 (기본 + 프리셋 + 사용자 등록 커스텀 병합)
    custom_profiles = db_service.get_custom_profiles()
    merged_complexes = dict(DEFAULT_COMPLEX_PROFILES["complexes"])

    # 프리셋 단지들 기본 병합
    for p_name, p_data in KNOWN_COMPLEX_PRESETS.items():
        if p_name not in merged_complexes:
            merged_complexes[p_name] = {
                "official_name": p_data["official_name"],
                "jibun_address": p_data["jibun_address"],
                "road_address": p_data["road_address"],
                "completion_date": p_data["completion_date"],
                "floors": p_data["floors"],
                "buildings_and_units": p_data["buildings_and_units"],
                "area_types": p_data["area_types"],
                "floor_area_ratio": p_data["floor_area_ratio"],
                "avg_land_share": p_data["avg_land_share"],
                "subway_school": p_data["subway_school"],
                "recent_jeonse_range": p_data["recent_jeonse_range"],
                "characteristics": p_data["characteristics"],
            }

    # 커스텀 등록 프로필 병합
    for c_name, c_data in custom_profiles.items():
        if isinstance(c_data, dict) and "profile" in c_data:
            merged_complexes[c_name] = c_data["profile"]

    # 실거래 데이터로 등록된 단지 추가 보강
    for c_name, c_stat in by_complex.items():
        if c_name not in merged_complexes:
            merged_complexes[c_name] = {
                "official_name": c_name,
                "jibun_address": "사용자 추가 분석 단지",
                "road_address": f"{c_name} 단지 일대",
                "completion_date": "실거래 메모 참조",
                "floors": "실거래 메모 참조",
                "buildings_and_units": f"등록된 시계열 데이터 {c_stat['count']}건",
                "area_types": "등록 메모 기준",
                "floor_area_ratio": "별도 확인",
                "avg_land_share": "별도 확인",
                "subway_school": "등록 메모 참조",
                "recent_jeonse_range": "최근 메모 내 전세 동향 참조",
                "characteristics": f"최근 평균 실거래가 {c_stat['average_eok']}억 원 (최고 {c_stat['max_eok']}억 원), 추세: {c_stat['trend']}",
            }

    # relationship_summary 동적 결정
    if selected_complex and selected_complex not in ("all", "전체"):
        if selected_complex in custom_profiles and "relationship_summary" in custom_profiles[selected_complex]:
            rel_summary = custom_profiles[selected_complex]["relationship_summary"]
        elif selected_complex in KNOWN_COMPLEX_PRESETS:
            bundle = build_auto_complex_dataset(selected_complex)
            rel_summary = bundle["relationship_summary"]
        elif selected_complex in ("개포우성8차", "개포현대3차"):
            rel_summary = DEFAULT_COMPLEX_PROFILES["relationship_summary"]
        else:
            rel_summary = {
                "title": f"{selected_complex} 맞춤형 시세 트렌드 및 매매 타이밍 분석",
                "location_corridor": f"{selected_complex} 단지 및 주요 생활 인프라 연계 축",
                "combined_households": f"등록된 시계열 데이터 {len(target_trades)}건",
                "key_issue": f"{selected_complex}의 실거래가 변화와 전세가 하방 지지력, 최근 정책 영향을 종합해 최적의 매매 타이밍을 진단합니다.",
                "buying_watch_points": [
                    f"선행 지표: {selected_complex} 전세 거래 추이 및 매매가 갭 축소 여부",
                    f"시장 신호: 최근 매물 호가 대비 실거래가 체결 비율",
                    f"경매/급매: 인근 법원 경매 및 급매물 출회 시 낙찰가율 비교",
                ],
            }
    else:
        rel_summary = {
            "title": "관심 단지 워치리스트 통합 비교 분석",
            "location_corridor": f"총 {len(by_complex)}개 단지 모니터링 중",
            "combined_households": f"전체 실거래 {len(trade_items)}건 분석",
            "key_issue": "워치리스트에 등록된 단지들의 가격대, 전세가율, 상승 추세를 비교하여 최적의 투자 및 갈아타기 타이밍을 비교 진단합니다.",
            "buying_watch_points": [
                "단지별 실거래가 상승률 및 최고가 경신 빈도 비교",
                "전세가율이 높은 단지(갭투자 유리) vs 대지지분이 높은 단지(재건축 유리) 선별",
                "법원 경매 및 정책 규제지역 여부에 따른 실투자금 포트폴리오 구성",
            ],
        }

    profiles = {
        "relationship_summary": rel_summary,
        "complexes": merged_complexes,
    }

    # 5. 매매 타이밍 종합 시그널 동적 생성
    if selected_complex and selected_complex not in ("all", "전체") and selected_complex in by_complex:
        c_stat = by_complex[selected_complex]
        market_timing_signal = (
            f"📊 [{selected_complex} 집중 진단] 등록된 {c_stat['count']}건 분석 결과 평균 {c_stat['average_eok']}억 원"
            f"(최고 {c_stat['max_eok']}억, 최근 {c_stat['latest_trade']['value_eok']}억)으로 '{c_stat['trend']}' 흐름입니다. "
            f"전세가 하방 지지력과 최근 실거래 호가를 종합 분석하여 매수·매도 타이밍을 제공합니다."
        )
    elif selected_complex in ("개포우성8차", "개포현대3차"):
        market_timing_signal = (
            "📊 [강보합 및 선별 매수 구간] 개포우성8차 전세가가 9억대 ➔ 11.1억 원(전세가율 약 40%)으로 선행 상승하며 "
            "매매가 26.8~27.2억 원대의 탄탄한 하방 지지선을 구축했습니다. 개포현대3차 역시 32평형 28.2억, 58평형 40.5억으로 "
            "대지지분(평균 30.5평)·독립정산제 가치를 반영 중입니다."
        )
    else:
        market_timing_signal = (
            f"📊 [전체 단지 통합 모니터링] 총 {len(by_complex)}개 관심 단지의 평균 실거래가는 {round(metrics['average']/10000, 2)}억 원이며, "
            f"전반적인 시장 추세는 '{trend_str}' 상태입니다. 상단 워치리스트에서 특정 단지를 선택하시면 맞춤형 정밀 진단이 제공됩니다."
        )

    return {
        "period": period,
        "count": len(all_items),
        "trade_count": len(target_trades),
        "metrics": metrics,
        "trend": trend_str,
        "market_timing_signal": market_timing_signal,
        "by_complex": by_complex,
        "complex_profiles": profiles,
        "auction_alerts": auction_alerts,
        "policy_and_transit": policy_and_transit,
    }


def get_chart_statistics() -> Dict[str, Any]:
    """프론트엔드 시세 추이 비교 그래프(Chart.js) 및 심화 통계를 위한 시계열 데이터 반환"""
    all_items = db_service.list_data()
    chronological = sorted(all_items, key=lambda x: (x["date"], x["id"]))

    series_by_complex: Dict[str, List[Dict[str, Any]]] = {}
    auction_points: List[Dict[str, Any]] = []
    policy_transit_points: List[Dict[str, Any]] = []

    for item in chronological:
        cat = item.get("category")
        val_eok = round(float(item["value"]) / 10000.0, 2)
        point = {
            "id": item["id"],
            "date": item["date"],
            "value": item["value"],
            "value_eok": val_eok,
            "memo": item["memo"],
            "complex_name": item.get("complex_name") or "기타",
            "category": cat,
        }
        if cat == "trade":
            c_name = item.get("complex_name") or "기타단지"
            # 현대3차의 경우 대형(47/58평, 30억 이상)과 32평(84㎡, 30억 미만)을 구분 표시할 수 있도록 메타데이터 부여
            series_by_complex.setdefault(c_name, []).append(point)
        elif cat == "auction":
            auction_points.append(point)
        elif cat in ("policy", "transit"):
            policy_transit_points.append(point)

    return {
        "total_records": len(all_items),
        "series_by_complex": series_by_complex,
        "auction_points": auction_points,
        "policy_transit_points": policy_transit_points,
    }

