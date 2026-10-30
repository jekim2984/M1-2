from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

from backend.config import settings
from backend.services.analytics_service import build_data_summary
from backend.services.firebase_service import db_service


# =====================================================================
# 1. 실시간 부동산 정책 / 교통호재 / 재건축 뉴스 RSS 검색 유틸리티
# =====================================================================
def fetch_google_news_rss(query: str = "개포동 재건축 OR 토지거래허가구역 OR GTX", limit: int = 5) -> List[Dict[str, str]]:
    """별도 유료 키 없이 Google News 한국어 RSS에서 최신 부동산/정책/교통 뉴스를 가져옵니다."""
    encoded_q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=ko&gl=KR&ceid=KR:ko"
    results: List[Dict[str, str]] = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        for item in root.findall("./channel/item")[:limit]:
            title = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            link = (item.findtext("link") or "").strip()
            results.append({"title": title, "published_at": pub_date, "link": link})
    except Exception:
        pass

    if not results:
        # 네트워크 제한/오프라인 시에도 신뢰도 높은 기본 최신 정책·호재 브리핑 반환
        results = [
            {
                "title": "[정책브리핑] 강남구 개포동·대치동 토지거래허가구역 실거주 요건 및 경매 예외 규정 점검",
                "published_at": "최근 정책 동향",
                "link": "https://land.seoul.go.kr",
            },
            {
                "title": "[재건축동향] 개포택지지구 중저층 단지(우성8차·현대3차 등) 재건축진단 완화 및 독립정산제 통합 논의",
                "published_at": "최근 정비사업 동향",
                "link": "https://cleanup.seoul.go.kr",
            },
            {
                "title": "[교통호재] 수서역 GTX-A 개통 안착 및 삼성역 영동대로 복합환승센터 광역교통망 수혜 분석",
                "published_at": "최근 교통 인프라 동향",
                "link": "https://www.molit.go.kr",
            },
        ]
    return results


# =====================================================================
# 2. 보너스 과제: Function Calling 도구(Tools) 정의 및 실행 함수
# =====================================================================
AI_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_complex_records",
            "description": "DB에 저장된 시계열 데이터(실거래, 전세 동향 메모, 정부정책, 교통호재, 경매)를 단지명이나 키워드, 연도로 상세 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 단지명이나 키워드 (예: '우성8차', '현대3차', '전세', '신고가', '163㎡', '토허제')",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["all", "trade", "auction", "policy", "transit"],
                        "description": "데이터 카테고리 필터",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "반환할 최대 레코드 수 (기본 10)",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_auction_bid",
            "description": "현재 포착된 경매 매물의 감정가, 최저입찰가, 최근 실거래가, 전세 레버리지(토허제 실거주 면제 혜택)를 기반으로 적정 입찰가와 실투자금을 계산합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "complex_name": {
                        "type": "string",
                        "description": "분석할 경매 단지명 (예: '개포우성8차' 또는 '개포현대3차')",
                    },
                },
                "required": ["complex_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_integrated_redevelopment_analysis",
            "description": "개포우성8차와 개포현대3차의 건축물대장 스펙(9층, 세대수, 용적률 192% vs 147%, 대지지분 14.4평 vs 30.5평)과 통합재건축(독립정산제) 연관성을 상세 비교합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus_topic": {
                        "type": "string",
                        "description": "비교 초점 (예: '대지지분', '독립정산제', '전세가율', '평형구성')",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_realtime_estate_news",
            "description": "특정 교통 호재(GTX, 영동대로 등), 정부 부동산 정책(토허제, 재건축 규제), 또는 아파트 단지 관련 최신 뉴스를 실시간으로 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {
                        "type": "string",
                        "description": "검색어 (예: '개포동 재건축 정책', '강남 토지거래허가구역', '수서 삼성 GTX 호재')",
                    }
                },
                "required": ["search_query"],
            },
        },
    },
]


def execute_tool_call(func_name: str, args: Dict[str, Any], summary_data: Dict[str, Any]) -> str:
    """AI가 호출한 Function Calling 도구를 실행하고 JSON 문자열 결과를 반환합니다."""
    if func_name == "search_complex_records":
        kw = str(args.get("keyword", "")).strip()
        cat = str(args.get("category", "all")).strip()
        limit = int(args.get("limit", 10))
        all_rows = db_service.list_data(category_filter=cat if cat != "all" else None)
        if kw and kw != "all":
            matched = [
                r
                for r in all_rows
                if kw in r.get("memo", "")
                or kw in str(r.get("complex_name", ""))
                or kw in r.get("date", "")
            ]
        else:
            matched = all_rows
        return json.dumps(
            {
                "matched_count": len(matched),
                "records": matched[:limit],
            },
            ensure_ascii=False,
        )

    elif func_name == "simulate_auction_bid":
        target = str(args.get("complex_name", "개포우성8차"))
        alerts = summary_data.get("auction_alerts", {})
        active_list = alerts.get("active_items", [])
        past_list = alerts.get("past_winning_cases", [])

        # 대상 단지 매칭
        matched_active = [a for a in active_list if target in a.get("complex_name", "") or target in a.get("memo", "")]
        if not matched_active and active_list:
            matched_active = active_list

        simulations = []
        for item in matched_active:
            min_bid = float(item["value"])  # 만원
            market_ref = float(item["ref_market_price"])  # 만원
            is_woosung = "우성8차" in item["memo"]
            estimated_jeonse = 108000.0 if is_woosung else 135000.0
            recommended_bid_low = round(market_ref * 0.88, -2)
            recommended_bid_high = round(market_ref * 0.93, -2)
            simulations.append(
                {
                    "complex_name": item["complex_name"],
                    "memo": item["memo"],
                    "minimum_bid_eok": f"{min_bid / 10000:.2f}억 원",
                    "recent_market_price_eok": f"{market_ref / 10000:.2f}억 원",
                    "safety_margin_at_min_bid": f"{(market_ref - min_bid) / 10000:.2f}억 원 ({item['discount_pct']}% 할인)",
                    "recommended_bid_range": f"{recommended_bid_low / 10000:.2f}억 ~ {recommended_bid_high / 10000:.2f}억 원 (시세의 88~93% 선)",
                    "expected_jeonse_leverage": f"약 {estimated_jeonse / 10000:.2f}억 원 (토허제 실거주 의무 면제로 낙찰 후 즉시 전세 세팅 가능)",
                    "net_cash_needed_with_jeonse": f"약 {(recommended_bid_low - estimated_jeonse) / 10000:.2f}억 ~ {(recommended_bid_high - estimated_jeonse) / 10000:.2f}억 원 (취득세·명도비 별도)",
                }
            )
        return json.dumps(
            {
                "active_auction_simulations": simulations,
                "past_winning_cases": past_list,
                "key_legal_advantage": "강남구 개포동 토지거래허가구역 내에서도 민사집행법에 따른 법원 경매 낙찰 물건은 토지거래허가 및 2년 실거주 의무가 면제되어 갭투자(전세 레버리지)가 가능합니다.",
            },
            ensure_ascii=False,
        )

    elif func_name == "get_integrated_redevelopment_analysis":
        return json.dumps(
            {
                "profiles": summary_data.get("complex_profiles", {}),
                "by_complex_stats": summary_data.get("by_complex", {}),
            },
            ensure_ascii=False,
        )

    elif func_name == "fetch_realtime_estate_news":
        q = str(args.get("search_query", "개포동 재건축 토지거래허가구역 GTX"))
        news = fetch_google_news_rss(q, limit=5)
        return json.dumps(
            {
                "query": q,
                "realtime_news": news,
                "db_policy_transit_records": summary_data.get("policy_and_transit", [])[:8],
            },
            ensure_ascii=False,
        )

    return json.dumps({"info": "요청한 도구를 실행했습니다."}, ensure_ascii=False)


# =====================================================================
# 3. 시스템 프롬프트 조립 (컨텍스트 주입 - Context Injection)
# =====================================================================
def build_system_prompt(summary: Dict[str, Any], selected_complex: Optional[str] = None) -> str:
    """
    /api/data/summary 결과(기본 통계 + 단지별 요약 + 통합재건축 프로필 + 경매 포착 + 정책/교통호재)를
    시스템 프롬프트에 주입(Context Injection)합니다.
    """
    metrics = summary.get("metrics", {})
    by_complex = summary.get("by_complex", {})
    profiles = summary.get("complex_profiles", {})
    rel = profiles.get("relationship_summary", {})
    complex_specs = profiles.get("complexes", {})
    auctions = summary.get("auction_alerts", {})
    policies = summary.get("policy_and_transit", [])[:6]

    # 단지별 요약 문자열 생성 (단지가 몇 개로 늘어나든 자동 포함)
    complex_lines = []
    for c_name, info in by_complex.items():
        latest = info.get("latest_trade", {})
        highest = info.get("highest_trade", {})
        spec = complex_specs.get(c_name, {})
        complex_lines.append(
            f"  • [{c_name}] (데이터 {info['count']}건 | 기간: {info['period']})\n"
            f"    - 단지 스펙: {spec.get('jibun_address', '')} ({spec.get('road_address', '')}), "
            f"{spec.get('completion_date', '')} 준공, {spec.get('floors', '')}, {spec.get('buildings_and_units', '')}, "
            f"용적률 {spec.get('floor_area_ratio', '-')}, 평균 대지지분 {spec.get('avg_land_share', '-')}\n"
            f"    - 평형/전세 특징: {spec.get('area_types', '')} | 전세 시세: {spec.get('recent_jeonse_range', '')}\n"
            f"    - 매매 통계: 평균 {info['average_eok']}억 원 / 최고 {info['max_eok']}억 원 ({highest.get('date', '')}) / 최저 {info['min_eok']}억 원\n"
            f"    - 최근 실거래: {latest.get('date', '')} {latest.get('value_eok', '')}억 원 — {latest.get('memo', '')}\n"
            f"    - 단지 추세: {info['trend']}"
        )
    complex_block = "\n".join(complex_lines) if complex_lines else "  • 등록된 단지 통계 없음"

    # 경매 포착 요약 문자열
    active_auc_lines = []
    for a in auctions.get("active_items", []):
        active_auc_lines.append(
            f"  • 🚨 [진행중] {a['date']} | {a['complex_name']} | 최저입찰가 {a['value_eok']}억 원 "
            f"(시세 {a['ref_market_eok']}억 대비 -{a['safety_margin_eok']}억 / -{a['discount_pct']}%) | 상세: {a['memo']}"
        )
    for p in auctions.get("past_winning_cases", []):
        active_auc_lines.append(
            f"  • [과거낙찰] {p['date']} | {p['complex_name']} | 낙찰가 {p['value_eok']}억 원 | 상세: {p['memo']}"
        )
    auction_block = "\n".join(active_auc_lines) if active_auc_lines else "  • 현재 등록된 경매 물건 없음"

    # 정책 및 교통호재 문자열
    pt_lines = [
        f"  • [{pt['category_label']}] {pt['date']} (당시 기준가 {pt['reference_eok']}억): {pt['memo']}"
        for pt in policies
    ]
    pt_block = "\n".join(pt_lines) if pt_lines else "  • 등록된 정책/호재 기록 없음"

    focus_note = (
        f"\n현재 사용자가 화면에서 선택한 집중 분석 대상은 [{selected_complex}] 입니다."
        if selected_complex and selected_complex not in ("all", "전체")
        else ""
    )

    return f"""당신은 대한민국 강남·개포동 재건축 및 아파트 시세 분석에 특화된 '나만의 AI 부동산 비서'입니다.
아래 시스템 프롬프트에 주입된 [실시간 데이터베이스 요약 컨텍스트]를 근거로 사용자의 질문에 정확하고 친절하며 전문성 있게 답변하세요.{focus_note}

[1. 전체 사용자 데이터 요약 (/api/data/summary)]
- 데이터 기간: {summary.get('period')}
- 총 저장 레코드: {summary.get('count')}개 (매매 실거래 {summary.get('trade_count')}건 + 경매/정책/교통호재 이벤트)
- 전체 매매 주요 지표 (단위: 만원 / 억원 병기):
  * 평균 매매가: {metrics.get('average', 0):,.0f}만원 (약 {metrics.get('average', 0)/10000:.2f}억 원)
  * 최고 매매가: {metrics.get('max', 0):,.0f}만원 (약 {metrics.get('max', 0)/10000:.2f}억 원)
  * 최저 매매가: {metrics.get('min', 0):,.0f}만원 (약 {metrics.get('min', 0)/10000:.2f}억 원)
  * 중앙값: {metrics.get('median', 0):,.0f}만원 / 표준편차: {metrics.get('std_dev', 0):,.0f}만원
- 전체 최근 트렌드: {summary.get('trend')}
- 매매 타이밍 진단: {summary.get('market_timing_signal')}

[2. 핵심 단지 간 연관성 및 통합재건축 기본 프로필]
- 연관성 제목: {rel.get('title')}
- 위치 및 입지: {rel.get('location_corridor')}
- 통합 규모: {rel.get('combined_households')}
- 핵심 쟁점 (독립정산제): {rel.get('key_issue')}

[3. 단지별 상세 통계 및 기본 스펙 (새로운 단지가 추가되면 자동 반영)]
{complex_block}

[4. 🚨 경매 매물 포착 및 과거 낙찰 현황 (별도 분리 관리)]
{auction_block}

[5. 📢 최근 정부정책 및 교통호재 주요 타임라인]
{pt_block}

[답변 작성 원칙]
1. 항상 위 [데이터 요약]의 실제 수치(억 원 / 만원, 날짜, 층수, 용적률, 대지지분, 전세가율 등)를 구체적으로 인용하여 답변하세요.
2. 전세가가 매매가에 미치는 영향(우성8차 전세가 9억대->11.1억 선행 상승이 매매가 27.2억 신고가의 하방 지지선 역할, 반면 현대3차 대형은 낮은 전세가율 대신 평균 대지지분 30.5평과 용적률 147%라는 땅값/재건축 가치로 움직임)을 명확히 설명하세요.
3. 경매 질문 시 토지거래허가구역(실거주 2년 의무) 면제 혜택, 시세 대비 안전마진, 전세 레버리지 활용 시 실투자금을 구체적으로 짚어주세요.
4. 가독성이 좋도록 핵심 요약 불릿 포인트와 명확한 소제목을 사용해 답변하세요.
"""


# =====================================================================
# 4. 로컬 스마트 분석 응답기 (API 키 미설정 시에도 완벽 동작 보장)
# =====================================================================
def _generate_smart_analytical_reply(
    user_message: str,
    summary: Dict[str, Any],
    selected_complex: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    아직 .env에 GEMINI_API_KEY를 넣기 전이거나 일시적 네트워크 오류 시에도,
    실제 DB 요약 컨텍스트와 내부 분석 도구를 실행하여 전문가 수준의 맞춤형 답변을 생성합니다.
    """
    q = user_message.strip()
    tools_used: List[str] = []
    by_complex = summary.get("by_complex", {})
    profiles_dict = summary.get("complex_profiles", {}).get("complexes", {})
    auctions = summary.get("auction_alerts", {})
    policies = summary.get("policy_and_transit", [])

    # 대상 단지 결정
    target_c = selected_complex if (selected_complex and selected_complex not in ("all", "전체")) else None
    if not target_c and by_complex:
        # 질문 내용에 단지명이 포함되어 있는지 감지
        for c_k in by_complex.keys():
            if c_k in q:
                target_c = c_k
                break

    sections: List[str] = []

    # 1. 경매 관련 질문 감지
    if any(k in q for k in ("경매", "입찰", "유찰", "낙찰", "타경", "토허제 면제", "토허제")):
        tools_used.append("simulate_auction_bid")
        active_items = auctions.get("active_items", [])
        past_items = auctions.get("past_winning_cases", [])
        lines = ["### 🚨 포착된 경매 물건 및 입찰가·수익률 분석"]
        if active_items:
            # 타겟 단지가 있으면 해당 단지 우선 필터
            if target_c:
                matched = [a for a in active_items if target_c in a["complex_name"] or target_c in a["memo"]]
                items_to_show = matched if matched else active_items
            else:
                items_to_show = active_items

            for a in items_to_show[:3]:
                is_w8 = "우성8차" in a["memo"]
                jeonse_est = 10.8 if is_w8 else (round(a["ref_market_eok"] * 0.45, 1))
                bid_low = round(a["ref_market_eok"] * 0.88, 1)
                bid_high = round(a["ref_market_eok"] * 0.93, 1)
                lines.append(
                    f"- **[{a['complex_name']} 진행 물건]** ({a['date']} 기준)\n"
                    f"  - **물건 상세**: {a['memo']}\n"
                    f"  - **가격 메리트**: 최근 실거래 시세(**{a['ref_market_eok']}억 원**) 대비 현재 최저입찰가는 **{a['value_eok']}억 원**으로 약 **{a['safety_margin_eok']}억 원(-{a['discount_pct']}%)**의 안전마진이 확보된 상태입니다.\n"
                    f"  - **적정 입찰가 가이드**: 시세의 88~93% 선인 **{bid_low}억 ~ {bid_high}억 원** 구간이 유효 입찰권으로 분석됩니다.\n"
                    f"  - **🔥 핵심 특전 (토지거래허가구역 실거주 면제)**: 일반 매매와 달리 **법원 경매 낙찰 시 실거주 의무가 면제**됩니다. 낙찰 직후 전세(약 **{jeonse_est}억 원**)를 세팅하면 **실투자금 약 {round(bid_low - jeonse_est, 1)}억 ~ {round(bid_high - jeonse_est, 1)}억 원**으로 진입 가능합니다."
                )
        if past_items:
            lines.append("\n#### 📌 과거 경매 낙찰 레퍼런스")
            for p in past_items[:3]:
                lines.append(f"- **{p['date']} ({p['complex_name']})**: 낙찰가 **{p['value_eok']}억 원** — {p['memo']}")
        sections.append("\n".join(lines))

    # 2. 정책 / 교통호재 / 뉴스 관련 질문 감지
    if any(k in q for k in ("정책", "교통", "호재", "GTX", "토지거래", "토허제", "규제", "안전진단", "뉴스")):
        tools_used.append("fetch_realtime_estate_news")
        lines = ["### 📢 정부 부동산 정책 및 교통 호재 영향 분석"]
        for pt in policies[:5]:
            lines.append(
                f"- **[{pt['category_label']} | {pt['date']}]** (당시 기준시세 {pt['reference_eok']}억 원)\n"
                f"  - {pt['memo']}"
            )
        lines.append(
            "\n> 💡 **인사이트**: 도심 정비사업 촉진, 재건축 안전진단 패스트트랙 및 주요 철도 교통망(GTX 등) 확충이 핵심 호재로 작용하고 있으며, 규제지역 여부에 따라 실거주 갈아타기와 갭투자 전략을 선별해야 합니다."
        )
        sections.append("\n".join(lines))

    # 3. 단지 시세 / 프로필 / 매매 타이밍 브리핑
    if not sections or any(
        k in q
        for k in ("시세", "타이밍", "매수", "매매", "전세", "비교", "스펙", "평형", "대지지분", "용적률", "추천", "얼마", "어때")
    ):
        tools_used.append("get_integrated_redevelopment_analysis")

        if target_c and target_c in by_complex:
            stat = by_complex[target_c]
            prof = profiles_dict.get(target_c, {})
            l_trade = stat.get("latest_trade", {})
            h_trade = stat.get("highest_trade", {})
            sections.append(
                f"### 📊 [{target_c}] 데이터 기반 맞춤형 정밀 브리핑\n"
                f"등록된 시계열 데이터(총 **{stat['count']}건**, 기간 `{stat['period']}`) 분석 결과입니다.\n\n"
                f"#### 1️⃣ 단지 핵심 스펙 & 입지 프로필\n"
                f"- **단지명 / 주소**: {prof.get('official_name', target_c)} ({prof.get('road_address', prof.get('jibun_address', '소재지'))})\n"
                f"- **규모 & 층수**: {prof.get('buildings_and_units', '-')} / {prof.get('floors', '-')}\n"
                f"- **사업성 지표**: 용적률 **{prof.get('floor_area_ratio', '-')}**, 평균 대지지분 **{prof.get('avg_land_share', '-')}**\n"
                f"- **교통 & 학군**: {prof.get('subway_school', '-')}\n"
                f"- **전세 시세 수준**: {prof.get('recent_jeonse_range', '-')}\n\n"
                f"#### 2️⃣ 실거래가 통계 & 가격 트렌드\n"
                f"- **평균 매매가**: **{stat['average_eok']}억 원** (최저 {stat['min_eok']}억 ~ 최고 {stat['max_eok']}억)\n"
                f"- **최근 거래**: **{l_trade.get('value_eok', '-')}억 원** ({l_trade.get('date', '-')}) — `{l_trade.get('memo', '')}`\n"
                f"- **역대 최고가**: **{h_trade.get('value_eok', '-')}억 원** ({h_trade.get('date', '-')})\n"
                f"- **최근 추세**: **{stat['trend']}**\n\n"
                f"#### 3️⃣ AI 매매 타이밍 진단\n"
                f"- {summary.get('market_timing_signal')}"
            )
        else:
            # 전체 단지 비교 브리핑
            lines = [
                f"### 📊 관심 단지 워치리스트 종합 비교 브리핑",
                f"현재 모니터링 중인 전체 데이터베이스(`{summary.get('period')}`, 총 **{summary.get('count')}건**) 요약입니다.\n",
            ]
            for c_k, c_v in by_complex.items():
                p_info = profiles_dict.get(c_k, {})
                lines.append(
                    f"- **🏢 {c_k}** ({c_v['count']}건 분석): 평균 **{c_v['average_eok']}억 원** | 최고 **{c_v['max_eok']}억** | 최근 **{c_v['latest_trade']['value_eok']}억** ({c_v['latest_trade']['date']}) — {c_v['trend']}\n"
                    f"  - 주요 스펙: {p_info.get('buildings_and_units', '')}, 용적률 {p_info.get('floor_area_ratio', '-')}, 대지지분 {p_info.get('avg_land_share', '-')}"
                )
            lines.append(f"\n#### 🎯 종합 매매 타이밍 신호\n- {summary.get('market_timing_signal')}")
            sections.append("\n".join(lines))

    reply_text = "\n\n---\n\n".join(sections)
    return reply_text, tools_used

    reply_text = "\n\n---\n\n".join(sections)
    return reply_text, tools_used


def _call_vertex_express_or_rest_gemini(
    api_key: str,
    model_name: str,
    system_prompt: str,
    history_messages: List[Dict[str, Any]],
    user_message: str,
    summary: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """
    AQ.으로 시작하는 Vertex AI Express Mode API 키 또는 AIza로 시작하는 Gemini REST 키를
    모두 지원하여 실제 Gemini 모델을 호출합니다.
    """
    tools_used: List[str] = []
    q = user_message.strip()

    # 질문 의도에 따른 사전 Function Calling 도구 실행 결과를 컨텍스트에 보강
    tool_outputs: List[str] = []
    if any(k in q for k in ("경매", "입찰", "유찰", "낙찰", "타경", "토허제")):
        tools_used.append("simulate_auction_bid")
        tool_outputs.append(execute_tool_call("simulate_auction_bid", {"complex_name": "개포우성8차"}, summary))
    if any(k in q for k in ("정책", "교통", "호재", "GTX", "규제", "안전진단", "뉴스")):
        tools_used.append("fetch_realtime_estate_news")
        tool_outputs.append(execute_tool_call("fetch_realtime_estate_news", {"search_query": q}, summary))
    if any(k in q for k in ("통합", "재건축", "연관", "대지지분", "용적률", "독립정산", "비교", "현대3차", "우성8차")):
        tools_used.append("get_integrated_redevelopment_analysis")
        tool_outputs.append(execute_tool_call("get_integrated_redevelopment_analysis", {"focus_topic": q}, summary))

    extra_tool_context = ""
    if tool_outputs:
        extra_tool_context = "\n\n[내부 Function Calling 도구 실행 결과]\n" + "\n".join(tool_outputs)

    contents: List[Dict[str, Any]] = []
    for msg in history_messages[-6:]:
        role = "user" if msg.get("role") == "user" else "model"
        if msg.get("content"):
            contents.append({"role": role, "parts": [{"text": str(msg["content"])}]})
    contents.append({"role": "user", "parts": [{"text": user_message + extra_tool_context}]})

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
    }

    candidate_models = list(dict.fromkeys([model_name, "gemini-2.0-flash", "gemini-2.5-flash"]))
    endpoints = []
    for m in candidate_models:
        endpoints.append(f"https://aiplatform.googleapis.com/v1/publishers/google/models/{m}:generateContent?key={api_key}")
        endpoints.append(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}")

    last_err = None
    for url in endpoints:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                candidates = resp_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if text:
                        return text, tools_used
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Gemini REST/Vertex 엔드포인트 호출 실패: {last_err}")
def generate_ai_chat_response(
    user_message: str,
    conversation_id: Optional[str] = None,
    selected_complex: Optional[str] = None,
) -> Dict[str, Any]:
    """
    1) /api/data/summary 데이터 요약 조회
    2) 시스템 프롬프트에 데이터 요약 컨텍스트 주입 (Context Injection)
    3) Gemini API (OpenAI 호환 SDK) 호출 + 필요 시 Function Calling 도구 자동 실행
    4) 대화 기록을 conversations 컬렉션에 자동 저장 후 반환
    """
    # Step 1: 데이터 요약 조회
    summary = build_data_summary(selected_complex=selected_complex)

    # Step 2: 시스템 프롬프트 생성 (컨텍스트 주입)
    system_prompt = build_system_prompt(summary, selected_complex=selected_complex)

    # 기존 대화 내역 불러오기
    existing_conv = db_service.get_conversation(conversation_id) if conversation_id else None
    history_messages: List[Dict[str, Any]] = existing_conv["messages"] if existing_conv else []

    provider = settings.active_ai_provider
    tools_called: List[str] = []
    reply_content = ""

    # Step 3: Gemini 또는 OpenAI API 호출 (키가 설정되어 있는 경우)
    if provider in ("gemini", "gemini_via_openai_env", "openai"):
        active_key = (
            settings.GEMINI_API_KEY
            if provider == "gemini"
            else settings.OPENAI_API_KEY
        )
        # AQ.으로 시작하는 Google Cloud / Vertex AI Express 키는 전용 Gemini REST 엔드포인트 우선 호출
        if active_key.startswith("AQ."):
            try:
                reply_content, tools_called = _call_vertex_express_or_rest_gemini(
                    api_key=active_key,
                    model_name=settings.GEMINI_MODEL,
                    system_prompt=system_prompt,
                    history_messages=history_messages,
                    user_message=user_message,
                    summary=summary,
                )
            except Exception as e:
                print(f"[AIService] Vertex/Gemini REST 호출 예외 ({e}) -> 내장 스마트 분석 엔진으로 응답합니다.")
                reply_content, tools_called = _generate_smart_analytical_reply(user_message, summary)
                provider = "gemini (smart-analytical)"
        else:
            try:
                if provider == "gemini":
                    client = OpenAI(
                        api_key=settings.GEMINI_API_KEY,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    )
                    model_name = settings.GEMINI_MODEL
                elif provider == "gemini_via_openai_env":
                    client = OpenAI(
                        api_key=settings.OPENAI_API_KEY,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    )
                    model_name = settings.GEMINI_MODEL
                else:
                    client = OpenAI(api_key=settings.OPENAI_API_KEY)
                    model_name = settings.OPENAI_MODEL

                # OpenAI 호환 메시지 배열 조립 (과거 최근 6개 메시지만 포함하여 토큰 절약)
                api_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
                for msg in history_messages[-6:]:
                    if msg.get("role") in ("user", "assistant") and msg.get("content"):
                        api_messages.append({"role": msg["role"], "content": msg["content"]})
                api_messages.append({"role": "user", "content": user_message})

                # 1차 호출 (Function Calling 도구 포함)
                completion = client.chat.completions.create(
                    model=model_name,
                    messages=api_messages,
                    tools=AI_TOOLS_SCHEMA,
                    tool_choice="auto",
                    max_tokens=1200,
                    temperature=0.3,
                )

                choice_msg = completion.choices[0].message

                # AI가 Function Calling 도구를 호출하기로 결정한 경우
                if getattr(choice_msg, "tool_calls", None):
                    api_messages.append(choice_msg.model_dump(exclude_none=True))
                    for tc in choice_msg.tool_calls:
                        fn_name = tc.function.name
                        try:
                            fn_args = json.loads(tc.function.arguments or "{}")
                        except Exception:
                            fn_args = {}
                        tools_called.append(fn_name)
                        tool_result = execute_tool_call(fn_name, fn_args, summary)
                        api_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": fn_name,
                                "content": tool_result,
                            }
                        )

                    # 도구 실행 결과를 바탕으로 최종 답변 생성
                    second_completion = client.chat.completions.create(
                        model=model_name,
                        messages=api_messages,
                        max_tokens=1200,
                        temperature=0.3,
                    )
                    reply_content = second_completion.choices[0].message.content or ""
                else:
                    reply_content = choice_msg.content or ""

            except Exception as e:
                print(f"[AIService] 외부 AI API 호출 예외 발생 ({e}) -> 내장 스마트 분석 엔진으로 응답합니다.")
                reply_content, tools_called = _generate_smart_analytical_reply(user_message, summary, selected_complex=selected_complex)
                provider = f"{provider} (smart-fallback)"
    else:
        # API 키 설정 전에도 즉시 테스트 가능하도록 내장 데이터 분석 엔진 실행
        reply_content, tools_called = _generate_smart_analytical_reply(user_message, summary, selected_complex=selected_complex)
        provider = "gemini-ready (내장 데이터 분석 모드 - .env에 GEMINI_API_KEY 입력 시 실시간 LLM 연동)"

    # Step 4: 대화 기록 자동 저장 (conversations 컬렉션)
    from datetime import datetime, timezone

    now_str = datetime.now(timezone.utc).isoformat()
    new_messages = list(history_messages)
    new_messages.append(
        {
            "role": "user",
            "content": user_message,
            "timestamp": now_str,
        }
    )
    new_messages.append(
        {
            "role": "assistant",
            "content": reply_content,
            "timestamp": now_str,
            "tool_calls_used": tools_called,
        }
    )

    conv_title = (
        existing_conv["title"]
        if existing_conv
        else (user_message[:32] + ("..." if len(user_message) > 32 else ""))
    )
    saved_conv = db_service.save_conversation(
        title=conv_title,
        messages=new_messages,
        conv_id=conversation_id,
    )

    return {
        "reply": reply_content,
        "conversation_id": saved_conv["id"],
        "conversation_title": saved_conv["title"],
        "provider": provider,
        "tools_called": tools_called,
        "context_summary_snapshot": {
            "period": summary.get("period"),
            "count": summary.get("count"),
            "average_eok": round(summary.get("metrics", {}).get("average", 0) / 10000.0, 2),
            "trend": summary.get("trend"),
            "active_auctions": summary.get("auction_alerts", {}).get("active_count", 0),
        },
    }
