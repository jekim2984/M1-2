/**
 * 나만의 AI 부동산 시세 & 매매 타이밍 비서 (바닐라 JS 프론트엔드)
 * - 대상 단지: 개포우성8차 & 개포현대3차 (통합재건축) + 사용자 자유 추가/확장 지원
 * - 주요 기능:
 *   1) 데이터 요약 조회 (/api/data/summary) 및 단지별 자동 탭 생성
 *   2) 3대 원클릭 알림 배지 모달 (🚨 경매 포착 / 🔗 통합재건축 프로필 / 📢 정책·교통호재 & 실시간 뉴스)
 *   3) 컨텍스트 주입 + Function Calling 기반 AI 채팅 (/api/chat) 및 대화 기록 불러오기 (/api/conversations)
 *   4) 시계열 데이터 CRUD (/api/data) + Chart.js 시각화 + CSV/JSON 내보내기 + 다크 모드
 */

// 백엔드 API 기본 주소 설정 (Vercel 환경변수 또는 로컬 자동 감지)
const API_BASE_URL = (() => {
  if (window.ENV_API_BASE_URL) return window.ENV_API_BASE_URL.replace(/\/$/, "");
  const saved = localStorage.getItem("API_BASE_URL");
  if (saved) return saved.replace(/\/$/, "");
  if (window.location.port === "8000") return window.location.origin;
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
    return "http://localhost:8000";
  }
  return window.location.origin;
})();

// 전역 상태 관리
const state = {
  summary: null,
  statistics: null,
  allData: [],
  conversations: [],
  currentConversationId: null,
  selectedComplex: "all",
  selectedCategory: "all",
  searchKeyword: "",
  chartInstance: null,
};

// DOM 헬퍼
const $ = (id) => document.getElementById(id);

function showToast(message) {
  const container = $("toastContainer");
  if (!container) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function formatEok(manwon) {
  const num = Number(manwon || 0);
  const eok = (num / 10000).toFixed(2);
  return `${eok}억 원 (${Math.round(num).toLocaleString()}만)`;
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdownLite(mdText) {
  let html = escapeHtml(mdText);
  html = html.replace(/^#### (.*$)/gim, "<h4>$1</h4>");
  html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\n---\n/g, "<hr style='margin:10px 0;border:none;border-top:1px solid var(--border-color);'/>");
  html = html.replace(/\n/g, "<br/>");
  return html;
}

// API 호출 공통 래퍼 (Render 무료 티어 콜드스타트 지연 안내 포함)
async function apiFetch(path, options = {}) {
  const coldBanner = $("coldStartBanner");
  const timer = setTimeout(() => {
    if (coldBanner) coldBanner.classList.remove("hidden");
  }, 2500);

  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `API 오류 (${res.status})`);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
    if (coldBanner) coldBanner.classList.add("hidden");
  }
}

// =====================================================================
// 1. 초기 로드 및 상태 동기화
// =====================================================================
async function initApp() {
  initTheme();
  initDefaultDate();
  bindEvents();

  const swaggerLink = $("swaggerLink");
  if (swaggerLink) swaggerLink.href = `${API_BASE_URL}/docs`;

  await Promise.all([
    loadHealthStatus(),
    loadSummaryAndModals(),
    loadDataList(),
    loadChartStatistics(),
    loadConversations(),
  ]);
}

async function loadHealthStatus() {
  try {
    const health = await apiFetch("/api/health");
    const dbBadge = $("dbStatusBadge");
    const aiBadge = $("aiStatusBadge");
    if (dbBadge) {
      dbBadge.textContent =
        health.database_mode === "firestore"
          ? "🔥 DB: Firestore 연동됨"
          : "💾 DB: 로컬 스토어 (119건 시딩됨)";
    }
    if (aiBadge) {
      aiBadge.textContent = health.ai_provider.includes("gemini")
        ? `✨ AI: Gemini (${health.ai_model})`
        : `🤖 AI: ${health.ai_provider}`;
    }
  } catch (e) {
    console.warn("Health check 실패:", e);
  }
}

// =====================================================================
// 2. 요약 데이터(/api/data/summary) 및 3대 알림 모달 렌더링
// =====================================================================
async function loadSummaryAndModals() {
  try {
    const query =
      state.selectedComplex && state.selectedComplex !== "all"
        ? `?complex_name=${encodeURIComponent(state.selectedComplex)}`
        : "";
    const summary = await apiFetch(`/api/data/summary${query}`);
    state.summary = summary;

    // 1) 상단 요약 카드 4개 업데이트
    $("sumTotalCount").textContent = `총 ${summary.count}건 (실거래 ${summary.trade_count}건)`;
    $("sumPeriod").textContent = `분석 기간: ${summary.period}`;
    const m = summary.metrics || {};
    $("sumOverallMetrics").textContent = `평균 ${(m.average / 10000).toFixed(2)}억 | 최고 ${(m.max / 10000).toFixed(2)}억 | 최저 ${(m.min / 10000).toFixed(2)}억`;

    const w8 = (summary.by_complex && summary.by_complex["개포우성8차"]) || null;
    if (w8) {
      $("sumW8Price").textContent = `평균 ${w8.average_eok}억 원`;
      $("sumW8Range").textContent = `최고 ${w8.max_eok}억 / 최저 ${w8.min_eok}억 (총 ${w8.count}건 · 전세가율 ~40%)`;
      $("sumW8Latest").textContent = `최근: ${w8.latest_trade.date} (${w8.latest_trade.value_eok}억)`;
    }

    const h3 = (summary.by_complex && summary.by_complex["개포현대3차"]) || null;
    if (h3) {
      $("sumH3Price").textContent = `평균 ${h3.average_eok}억 원`;
      $("sumH3Range").textContent = `최고 ${h3.max_eok}억 / 최저 ${h3.min_eok}억 (총 ${h3.count}건 · 용적률 147%)`;
      $("sumH3Latest").textContent = `최근: ${h3.latest_trade.date} (${h3.latest_trade.value_eok}억)`;
    }

    $("sumTrendShort").textContent = (summary.trend || "").split("(")[0].trim() || "상승 추세";
    $("sumTimingSignal").textContent = summary.trend || "";

    // 2) 동적 단지 선택 탭 렌더링 (사용자가 새 아파트를 추가하면 자동으로 버튼 생성!)
    renderDynamicComplexTabs(summary.by_complex || {});

    // 3) 상단 3대 알림 사인(배지) 및 모달 내부 데이터 업데이트
    renderAuctionBadgeAndModal(summary.auction_alerts || {});
    renderProfileModal(summary.complex_profiles || {}, summary.by_complex || {});
    renderPolicyBadgeAndModal(summary.policy_and_transit || []);
  } catch (err) {
    console.error("요약 로드 오류:", err);
  }
}

function renderDynamicComplexTabs(byComplex) {
  const container = $("complexSelectorTabs");
  if (!container) return;
  const complexNames = Object.keys(byComplex);

  let html = `<span class="filter-label">🏢 집중 분석 단지:</span>`;
  html += `<button class="complex-pill ${state.selectedComplex === "all" ? "active" : ""}" data-complex="all">전체 보기 (${complexNames.length}개 단지)</button>`;

  complexNames.forEach((name) => {
    const info = byComplex[name];
    const activeClass = state.selectedComplex === name ? "active" : "";
    html += `<button class="complex-pill ${activeClass}" data-complex="${escapeHtml(name)}">${escapeHtml(name)} (${info.count}건 · 평균 ${info.average_eok}억)</button>`;
  });

  container.innerHTML = html;
  container.querySelectorAll(".complex-pill").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.selectedComplex = btn.dataset.complex;
      await loadSummaryAndModals();
      renderDataTable();
    });
  });
}

function renderAuctionBadgeAndModal(auctionAlerts) {
  const activeCount = auctionAlerts.active_count || 0;
  const pastCount = auctionAlerts.past_count || 0;
  const badgeText = $("auctionBadgeText");
  const pulseDot = $("auctionPulseDot");

  if (badgeText) {
    badgeText.textContent =
      activeCount > 0
        ? `경매 포착! (진행중 ${activeCount}건 · 낙찰 ${pastCount}건)`
        : `경매 없음 (과거 낙찰 ${pastCount}건)`;
  }
  if (pulseDot) {
    pulseDot.style.display = activeCount > 0 ? "inline-block" : "none";
  }

  // 모달 내부: 진행중 경매 렌더링
  const activeContainer = $("activeAuctionContainer");
  if (activeContainer) {
    const actives = auctionAlerts.active_items || [];
    if (actives.length === 0) {
      activeContainer.innerHTML = `<div class="auction-card-item">현재 진행 중인 경매 물건이 없습니다.</div>`;
    } else {
      activeContainer.innerHTML = actives
        .map(
          (a) => `
          <div class="auction-card-item active-auc">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <strong style="color:var(--accent-red);font-size:0.95rem;">🚨 [${escapeHtml(a.complex_name)}] 최저입찰가 ${a.value_eok}억 원</strong>
              <span class="tag-badge tag-auc">시세(${a.ref_market_eok}억) 대비 -${a.safety_margin_eok}억 (-${a.discount_pct}%)</span>
            </div>
            <p style="margin-bottom:6px;">${escapeHtml(a.memo)}</p>
            <div style="font-size:0.76rem;color:var(--text-muted);">
              등록 기준일: ${escapeHtml(a.date)} | ✨ <strong>토지거래허가구역 실거주 2년 의무 면제 (낙찰 직후 전세 세팅 가능)</strong>
            </div>
          </div>
        `
        )
        .join("");
    }
  }

  // 모달 내부: 과거 낙찰 사례 렌더링
  const pastContainer = $("pastAuctionContainer");
  if (pastContainer) {
    const pasts = auctionAlerts.past_winning_cases || [];
    if (pasts.length === 0) {
      pastContainer.innerHTML = `<div class="auction-card-item">과거 낙찰 사례 기록이 없습니다.</div>`;
    } else {
      pastContainer.innerHTML = pasts
        .map(
          (p) => `
          <div class="auction-card-item">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
              <strong>📌 [${escapeHtml(p.complex_name)}] 낙찰가 ${p.value_eok}억 원</strong>
              <span style="font-size:0.76rem;color:var(--text-muted);">${escapeHtml(p.date)}</span>
            </div>
            <p>${escapeHtml(p.memo)}</p>
          </div>
        `
        )
        .join("");
    }
  }
}

function renderProfileModal(profiles, byComplex) {
  const rel = profiles.relationship_summary || {};
  const complexes = profiles.complexes || {};

  const relBox = $("profileRelationshipBox");
  if (relBox) {
    const watchList = (rel.buying_watch_points || [])
      .map((pt) => `<li>${escapeHtml(pt)}</li>`)
      .join("");
    relBox.innerHTML = `
      <h4 style="margin-bottom:6px;color:var(--primary);">🔗 ${escapeHtml(rel.title || "")}</h4>
      <p style="margin-bottom:4px;"><strong>📍 입지:</strong> ${escapeHtml(rel.location_corridor || "")}</p>
      <p style="margin-bottom:4px;"><strong>🏗️ 통합 규모:</strong> ${escapeHtml(rel.combined_households || "")}</p>
      <p style="margin-bottom:8px;"><strong>⚖️ 핵심 특이사항:</strong> ${escapeHtml(rel.key_issue || "")}</p>
      <ul style="padding-left:18px;font-size:0.8rem;">${watchList}</ul>
    `;
  }

  const grid = $("profileComplexGrid");
  if (grid) {
    grid.innerHTML = Object.keys(complexes)
      .map((name) => {
        const c = complexes[name];
        const stat = byComplex[name] || {};
        return `
          <div class="profile-card">
            <h4>🏢 ${escapeHtml(c.official_name || name)}</h4>
            <ul class="profile-spec-list">
              <li>• <strong>지번 / 도로명:</strong> ${escapeHtml(c.jibun_address)} (${escapeHtml(c.road_address)})</li>
              <li>• <strong>준공 / 층수:</strong> ${escapeHtml(c.completion_date)} / <strong>${escapeHtml(c.floors)}</strong></li>
              <li>• <strong>동수 / 세대수:</strong> ${escapeHtml(c.buildings_and_units)}</li>
              <li>• <strong>평형 구성:</strong> ${escapeHtml(c.area_types)}</li>
              <li>• <strong>용적률 / 대지지분:</strong> 용적률 <strong>${escapeHtml(c.floor_area_ratio)}</strong> / 평균 대지지분 <strong>${escapeHtml(c.avg_land_share)}</strong></li>
              <li>• <strong>전세 시세 수준:</strong> ${escapeHtml(c.recent_jeonse_range)}</li>
              <li>• <strong>DB 매매 통계:</strong> 평균 <strong>${stat.average_eok || "-"}억</strong> / 최고 <strong>${stat.max_eok || "-"}억</strong> (${stat.count || 0}건)</li>
              <li>• <strong>단지 특이사항:</strong> ${escapeHtml(c.characteristics)}</li>
            </ul>
          </div>
        `;
      })
      .join("");
  }
}

function renderPolicyBadgeAndModal(policyList) {
  const badge = $("policyBadgeText");
  if (badge) {
    badge.textContent = `정책·교통호재 (${policyList.length}건) & 뉴스`;
  }
  const container = $("policyTimelineContainer");
  if (!container) return;
  if (policyList.length === 0) {
    container.innerHTML = `<div class="auction-card-item">등록된 정책/교통호재가 없습니다.</div>`;
    return;
  }
  container.innerHTML = policyList
    .map(
      (pt) => `
      <div class="auction-card-item">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
          <span class="tag-badge tag-pol">${escapeHtml(pt.category_label)}</span>
          <span style="font-size:0.76rem;color:var(--text-muted);">${escapeHtml(pt.date)} (당시 기준가 ${pt.reference_eok}억)</span>
        </div>
        <p>${escapeHtml(pt.memo)}</p>
      </div>
    `
    )
    .join("");
}

// =====================================================================
// 3. 시계열 데이터 목록 조회 & CRUD 관리 & 내보내기
// =====================================================================
async function loadDataList() {
  try {
    const list = await apiFetch("/api/data");
    state.allData = list;
    $("cntAll").textContent = list.length;
    renderDataTable();
  } catch (err) {
    console.error("데이터 목록 조회 실패:", err);
  }
}

function getFilteredData() {
  return state.allData.filter((item) => {
    if (state.selectedComplex !== "all") {
      const matchComplex =
        item.complex_name === state.selectedComplex ||
        (item.memo && item.memo.includes(state.selectedComplex));
      const isMacro = item.category === "policy" || item.category === "transit";
      if (!matchComplex && !isMacro) return false;
    }
    if (state.selectedCategory !== "all" && item.category !== state.selectedCategory) {
      return false;
    }
    if (state.searchKeyword) {
      const kw = state.searchKeyword.toLowerCase();
      const target = `${item.date} ${item.complex_name || ""} ${item.memo} ${item.value}`.toLowerCase();
      if (!target.includes(kw)) return false;
    }
    return true;
  });
}

function renderDataTable() {
  const tbody = $("dataTableBody");
  if (!tbody) return;
  const rows = getFilteredData();

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">조건에 맞는 데이터가 없습니다.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows
    .map((item) => {
      let badgeClass = "tag-w8";
      let badgeLabel = item.complex_name || "실거래";
      if (item.category === "auction") {
        badgeClass = "tag-auc";
        badgeLabel = `🚨 경매 (${item.complex_name || "단지"})`;
      } else if (item.category === "policy") {
        badgeClass = "tag-pol";
        badgeLabel = "📢 정부정책";
      } else if (item.category === "transit") {
        badgeClass = "tag-pol";
        badgeLabel = "🚇 교통호재";
      } else if (item.complex_name === "개포현대3차") {
        badgeClass = "tag-h3";
      }

      const rowClass = item.category === "auction" ? "row-auction" : "";
      return `
        <tr class="${rowClass}">
          <td style="white-space:nowrap;font-weight:600;">${escapeHtml(item.date)}</td>
          <td style="white-space:nowrap;"><span class="tag-badge ${badgeClass}">${escapeHtml(badgeLabel)}</span></td>
          <td style="white-space:nowrap;font-weight:700;">${formatEok(item.value)}</td>
          <td>${escapeHtml(item.memo)}</td>
          <td style="white-space:nowrap;">
            <button class="action-btn" onclick="startEditData('${item.id}')">수정</button>
            <button class="action-btn action-btn-del" onclick="removeData('${item.id}')">삭제</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

window.startEditData = function (id) {
  const target = state.allData.find((x) => x.id === id);
  if (!target) return;
  $("editDataId").value = target.id;
  $("inputDate").value = target.date;
  $("inputValue").value = target.value;
  $("inputMemo").value = target.memo;
  $("btnSubmitData").textContent = "수정 완료";
  $("btnCancelEdit").classList.remove("hidden");
  $("inputMemo").focus();
};

window.removeData = async function (id) {
  if (!confirm("이 데이터를 삭제하시겠습니까?")) return;
  try {
    await apiFetch(`/api/data/${id}`, { method: "DELETE" });
    showToast("데이터가 삭제되었습니다.");
    await Promise.all([loadDataList(), loadSummaryAndModals(), loadChartStatistics()]);
  } catch (err) {
    alert(`삭제 오류: ${err.message}`);
  }
};

// =====================================================================
// 4. Chart.js 시계열 시각화 그래프 (보너스 과제)
// =====================================================================
async function loadChartStatistics() {
  try {
    const stats = await apiFetch("/api/data/statistics");
    state.statistics = stats;
    renderPriceChart();
  } catch (err) {
    console.error("차트 통계 로드 오류:", err);
  }
}

function renderPriceChart() {
  const canvas = $("priceTrendChart");
  if (!canvas || !window.Chart || !state.statistics) return;

  const filterMode = $("chartFilterSelect") ? $("chartFilterSelect").value : "all";
  const seriesByComplex = state.statistics.series_by_complex || {};
  const auctionPoints = state.statistics.auction_points || [];
  const policyPoints = state.statistics.policy_transit_points || [];

  // X축 날짜 라벨 통합
  const allDatesSet = new Set();
  state.allData.forEach((d) => allDatesSet.add(d.date));
  const sortedDates = Array.from(allDatesSet).sort();

  const datasets = [];
  const colorPalette = ["#2563eb", "#059669", "#8b5cf6", "#ec4899", "#0891b2"];

  // 1) 개포우성8차 (전용 79㎡)
  if (filterMode === "all" || filterMode === "compare_30" || filterMode === "개포우성8차") {
    const w8Items = seriesByComplex["개포우성8차"] || [];
    datasets.push({
      label: "개포우성8차 (전용79㎡ 매매)",
      data: w8Items.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
      borderColor: "#2563eb",
      backgroundColor: "rgba(37, 99, 235, 0.12)",
      borderWidth: 2.5,
      pointRadius: 3,
      tension: 0.25,
    });
  }

  // 2) 개포현대3차 (국민평형 84㎡ vs 대형 131·163㎡ 분리 표시로 가독성 극대화!)
  if (filterMode === "all" || filterMode === "compare_30" || filterMode === "개포현대3차") {
    const h3Items = seriesByComplex["개포현대3차"] || [];
    const h3Small = h3Items.filter((pt) => pt.value_eok < 30.0);
    const h3Large = h3Items.filter((pt) => pt.value_eok >= 30.0);

    if (h3Small.length > 0) {
      datasets.push({
        label: "개포현대3차 (전용84㎡/32평 매매)",
        data: h3Small.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
        borderColor: "#059669",
        backgroundColor: "rgba(5, 150, 105, 0.12)",
        borderWidth: 2.5,
        pointRadius: 3.5,
        tension: 0.25,
      });
    }
    if (h3Large.length > 0 && filterMode !== "compare_30") {
      datasets.push({
        label: "개포현대3차 (전용131·163㎡ 중대형 매매)",
        data: h3Large.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
        borderColor: "#0d9488",
        borderDash: [5, 4],
        backgroundColor: "rgba(13, 148, 136, 0.1)",
        borderWidth: 2,
        pointRadius: 3.5,
        tension: 0.25,
      });
    }
  }

  // 3) 사용자가 새로 추가한 다른 아파트 단지 자동 표시!
  Object.keys(seriesByComplex).forEach((cName, idx) => {
    if (cName === "개포우성8차" || cName === "개포현대3차") return;
    const cItems = seriesByComplex[cName] || [];
    const color = colorPalette[(idx + 2) % colorPalette.length];
    datasets.push({
      label: `${cName} (추가 단지)`,
      data: cItems.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2.5,
      pointRadius: 4,
      tension: 0.25,
    });
  });

  // 4) 🚨 경매 포착/낙찰 포인트 (눈에 확 띄는 빨간 별표/다이아몬드 마커!)
  if (auctionPoints.length > 0) {
    datasets.push({
      label: "🚨 경매 최저입찰가 / 낙찰가",
      data: auctionPoints.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
      borderColor: "#dc2626",
      backgroundColor: "#dc2626",
      pointStyle: "rectRot",
      pointRadius: 7,
      pointHoverRadius: 9,
      showLine: false,
    });
  }

  // 5) 📢 정부정책 & 교통호재 발표 시점 마커
  if (filterMode === "all" && policyPoints.length > 0) {
    datasets.push({
      label: "📢 정부정책 · 교통호재 발표",
      data: policyPoints.map((pt) => ({ x: pt.date, y: pt.value_eok, memo: pt.memo })),
      borderColor: "#d97706",
      backgroundColor: "#f59e0b",
      pointStyle: "triangle",
      pointRadius: 6,
      pointHoverRadius: 8,
      showLine: false,
    });
  }

  if (state.chartInstance) {
    state.chartInstance.destroy();
  }

  state.chartInstance = new Chart(canvas, {
    type: "line",
    data: {
      labels: sortedDates,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: {
          type: "category",
          ticks: { maxTicksLimit: 10, font: { size: 11 } },
        },
        y: {
          title: { display: true, text: "금액 (단위: 억 원)" },
          ticks: {
            callback: (val) => `${val}억`,
          },
        },
      },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 12, font: { size: 11 } },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const raw = ctx.raw || {};
              const memoShort = raw.memo ? ` | ${raw.memo.slice(0, 55)}...` : "";
              return `${ctx.dataset.label}: ${raw.y}억 원${memoShort}`;
            },
          },
        },
      },
    },
  });
}

// =====================================================================
// 5. AI 비서 채팅 (/api/chat) 및 대화 기록 저장/불러오기 (/api/conversations)
// =====================================================================
async function sendChatMessage(messageText) {
  const input = $("chatInput");
  const loading = $("chatLoading");
  const sendBtn = $("btnSendChat");

  const trimmed = (messageText || "").trim();
  if (!trimmed) return;

  if (input) input.value = "";
  appendChatMessage("user", trimmed, "나의 질문");

  if (loading) loading.classList.remove("hidden");
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await apiFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message: trimmed,
        conversation_id: state.currentConversationId,
        selected_complex: state.selectedComplex,
      }),
    });

    state.currentConversationId = res.conversation_id;
    const toolsInfo =
      res.tools_called && res.tools_called.length > 0
        ? res.tools_called.map((t) => `<span class="tool-call-badge">🛠️ ${escapeHtml(t)}</span>`).join("")
        : "";
    const metaLabel = `${ toolsInfo }AI 부동산 비서 (${escapeHtml(res.provider)})`;

    appendChatMessage("assistant", res.reply, metaLabel, true);
    await loadConversations();
  } catch (err) {
    appendChatMessage("assistant", `요청 처리 중 오류가 발생했습니다: ${err.message}`, "시스템 안내");
  } finally {
    if (loading) loading.classList.add("hidden");
    if (sendBtn) sendBtn.disabled = false;
  }
}

function appendChatMessage(role, content, metaText = "", isHtmlMeta = false) {
  const container = $("chatMessages");
  if (!container) return;

  const wrapper = document.createElement("div");
  wrapper.className = `message ${role === "user" ? "user-message" : "assistant-message"}`;

  const avatar = role === "user" ? "👤" : "🤖";
  const bodyHtml = role === "user" ? escapeHtml(content) : renderMarkdownLite(content);
  const metaHtml = isHtmlMeta ? metaText : escapeHtml(metaText);

  wrapper.innerHTML = `
    <div class="message-avatar">${avatar}</div>
    <div class="message-bubble">
      <div class="message-meta">${metaHtml}</div>
      <div class="message-content">${bodyHtml}</div>
    </div>
  `;
  container.appendChild(wrapper);
  container.scrollTop = container.scrollHeight;
}

async function loadConversations() {
  try {
    const convs = await apiFetch("/api/conversations");
    state.conversations = convs;
    const badge = $("convCountBadge");
    if (badge) badge.textContent = convs.length;

    const listEl = $("conversationList");
    if (!listEl) return;
    if (convs.length === 0) {
      listEl.innerHTML = `<li class="empty-item">저장된 대화가 없습니다.</li>`;
      return;
    }

    listEl.innerHTML = convs
      .map(
        (c) => `
        <li class="conv-item" onclick="selectConversation('${c.id}')">
          <span class="conv-item-title">💬 ${escapeHtml(c.title)}</span>
          <div class="conv-item-meta">
            <span>${c.message_count}개 메시지</span>
            <button class="action-btn action-btn-del" onclick="event.stopPropagation();removeConversation('${c.id}')">삭제</button>
          </div>
        </li>
      `
      )
      .join("");
  } catch (err) {
    console.error("대화 목록 로드 실패:", err);
  }
}

window.selectConversation = async function (convId) {
  try {
    const conv = await apiFetch(`/api/conversations/${convId}`);
    state.currentConversationId = conv.id;
    const container = $("chatMessages");
    if (container) container.innerHTML = "";

    (conv.messages || []).forEach((m) => {
      const toolsInfo =
        m.tool_calls_used && m.tool_calls_used.length > 0
          ? m.tool_calls_used.map((t) => `<span class="tool-call-badge">🛠️ ${escapeHtml(t)}</span>`).join("")
          : "";
      appendChatMessage(
        m.role,
        m.content,
        m.role === "user" ? "나의 질문 (불러온 대화)" : `${toolsInfo}AI 부동산 비서`,
        true
      );
    });

    $("conversationDrawer").classList.add("hidden");
    showToast(`'${conv.title}' 대화를 불러왔습니다.`);
  } catch (err) {
    alert(`대화 불러오기 실패: ${err.message}`);
  }
};

window.removeConversation = async function (convId) {
  if (!confirm("이 대화 기록을 삭제하시겠습니까?")) return;
  try {
    await apiFetch(`/api/conversations/${convId}`, { method: "DELETE" });
    if (state.currentConversationId === convId) {
      state.currentConversationId = null;
    }
    await loadConversations();
    showToast("대화 기록이 삭제되었습니다.");
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  }
};

// =====================================================================
// 6. 실시간 뉴스 검색 및 원클릭 DB 저장 기능
// =====================================================================
async function fetchRealtimeNews() {
  const qInput = $("newsQueryInput");
  const listEl = $("realtimeNewsList");
  if (!listEl) return;

  listEl.innerHTML = `<li class="empty-item">실시간 뉴스를 검색하는 중입니다...</li>`;
  try {
    const q = qInput ? qInput.value.trim() : "개포동 재건축";
    const res = await apiFetch(`/api/news?q=${encodeURIComponent(q)}`);
    const items = res.items || [];
    if (items.length === 0) {
      listEl.innerHTML = `<li class="empty-item">검색된 뉴스가 없습니다.</li>`;
      return;
    }
    listEl.innerHTML = items
      .map(
        (n, idx) => `
        <li class="news-item">
          <div style="max-width:76%;">
            <a href="${escapeHtml(n.link)}" target="_blank" style="font-weight:700;color:var(--text-main);text-decoration:none;">
              📰 ${escapeHtml(n.title)}
            </a>
            <div style="font-size:0.72rem;color:var(--text-muted);">${escapeHtml(n.published_at)}</div>
          </div>
          <button class="btn-outline btn-sm" onclick="saveNewsAsTopic(${idx})">+ DB 토픽으로 추가</button>
        </li>
      `
      )
      .join("");
    window._latestFetchedNews = items;
  } catch (err) {
    listEl.innerHTML = `<li class="empty-item">뉴스 조회 실패: ${escapeHtml(err.message)}</li>`;
  }
}

window.saveNewsAsTopic = async function (idx) {
  const list = window._latestFetchedNews || [];
  const item = list[idx];
  if (!item) return;
  const today = new Date().toISOString().slice(0, 10);
  const latestPrice =
    (state.summary && state.summary.metrics && state.summary.metrics.average) || 270000;
  try {
    await apiFetch("/api/data", {
      method: "POST",
      body: JSON.stringify({
        date: today,
        value: Math.round(latestPrice),
        memo: `[정부정책] ${item.title.slice(0, 140)}`,
      }),
    });
    showToast("뉴스 토픽이 시계열 DB에 추가되었습니다!");
    await Promise.all([loadDataList(), loadSummaryAndModals(), loadChartStatistics()]);
  } catch (err) {
    alert(`저장 실패: ${err.message}`);
  }
};

// =====================================================================
// 7. CSV / JSON 다운로드 (보너스 과제) 및 다크모드 / 이벤트 바인딩
// =====================================================================
function exportDataAsCSV() {
  const rows = getFilteredData();
  if (rows.length === 0) return alert("내보낼 데이터가 없습니다.");
  const header = ["id", "date", "complex_name", "category", "value_manwon", "memo"];
  const csvLines = [
    header.join(","),
    ...rows.map((r) =>
      [
        r.id,
        r.date,
        `"${(r.complex_name || "").replace(/"/g, '""')}"`,
        r.category || "",
        r.value,
        `"${(r.memo || "").replace(/"/g, '""')}"`,
      ].join(",")
    ),
  ];
  const blob = new Blob(["\uFEFF" + csvLines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `gaepo_ai_realestate_data_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("CSV 파일 다운로드가 완료되었습니다.");
}

function exportDataAsJSON() {
  const rows = getFilteredData();
  const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `gaepo_ai_realestate_data_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("JSON 파일 다운로드가 완료되었습니다.");
}

function initTheme() {
  const saved = localStorage.getItem("theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
  updateThemeBtnText(saved);
}

function updateThemeBtnText(theme) {
  const btn = $("btnThemeToggle");
  if (btn) btn.textContent = theme === "dark" ? "☀️ 라이트모드" : "🌙 다크모드";
}

function initDefaultDate() {
  const dateInput = $("inputDate");
  if (dateInput && !dateInput.value) {
    dateInput.value = new Date().toISOString().slice(0, 10);
  }
}

function bindEvents() {
  // 다크모드 토글
  $("btnThemeToggle")?.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    updateThemeBtnText(next);
  });

  // 상단 3대 알림 사인(배지) 클릭 시 모달 오픈
  $("btnOpenAuctionModal")?.addEventListener("click", () => {
    $("auctionModal")?.classList.remove("hidden");
  });
  $("btnOpenProfileModal")?.addEventListener("click", () => {
    $("profileModal")?.classList.remove("hidden");
  });
  $("btnOpenPolicyModal")?.addEventListener("click", () => {
    $("policyModal")?.classList.remove("hidden");
    fetchRealtimeNews();
  });

  // 모달 닫기 버튼 및 배경 클릭 닫기
  document.querySelectorAll(".modal-close").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.close;
      if (targetId) $(targetId)?.classList.add("hidden");
    });
  });
  document.querySelectorAll(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.add("hidden");
    });
  });

  // 모달 내부 -> AI 비서에게 바로 분석 맡기기 원클릭 연동
  $("btnAskAiAuction")?.addEventListener("click", () => {
    $("auctionModal")?.classList.add("hidden");
    sendChatMessage(
      "현재 포착된 경매 매물(우성8차 및 현대3차)의 감정가 대비 최저입찰가 안전마진, 토지거래허가구역 실거주 면제 혜택, 그리고 전세 레버리지를 활용한 적정 입찰가와 실투자금을 상세히 분석해 줘."
    );
  });
  $("btnAskAiProfile")?.addEventListener("click", () => {
    $("profileModal")?.classList.add("hidden");
    sendChatMessage(
      "개포우성8차와 개포현대3차의 층수, 세대수, 용적률, 대지지분 차이와 왜 독립정산제 방식으로 통합재건축을 추진하는지 연관성을 자세히 설명해 줘."
    );
  });
  $("btnAskAiPolicy")?.addEventListener("click", () => {
    $("policyModal")?.classList.add("hidden");
    sendChatMessage(
      "최근 정부 부동산 정책(재건축진단 완화, 토지거래허가구역)과 교통호재(GTX-A/C, 영동대로 복합개발)가 우성8차·현대3차 매매 시기에 미치는 영향을 분석해 줘."
    );
  });

  // 실시간 뉴스 검색 버튼
  $("btnFetchNews")?.addEventListener("click", fetchRealtimeNews);

  // 채팅 폼 전송 & 추천 질문 칩
  $("chatForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    sendChatMessage($("chatInput")?.value);
  });
  document.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      sendChatMessage(chip.dataset.prompt);
    });
  });

  // 대화 기록 서랍 열기/닫기 및 새 상담
  $("btnToggleHistory")?.addEventListener("click", () => {
    $("conversationDrawer")?.classList.toggle("hidden");
  });
  $("btnCloseDrawer")?.addEventListener("click", () => {
    $("conversationDrawer")?.classList.add("hidden");
  });
  $("btnNewChat")?.addEventListener("click", () => {
    state.currentConversationId = null;
    $("chatMessages").innerHTML = "";
    appendChatMessage(
      "assistant",
      "새로운 상담을 시작합니다! 개포우성8차·현대3차 시세, 전세가 동향, 경매 물건, 또는 새로 추가하신 아파트 단지에 대해 무엇이든 물어보세요.",
      "새 대화 시작됨"
    );
  });

  // 차트 필터 변경
  $("chartFilterSelect")?.addEventListener("change", renderPriceChart);

  // 데이터 CRUD 말머리 프리셋 버튼
  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const memoInput = $("inputMemo");
      if (memoInput) {
        memoInput.value = btn.dataset.prefix;
        memoInput.focus();
      }
    });
  });

  // 데이터 CRUD 추가/수정 폼 제출
  $("dataCrudForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const editId = $("editDataId").value;
    const payload = {
      date: $("inputDate").value,
      value: Number($("inputValue").value),
      memo: $("inputMemo").value.trim(),
    };

    try {
      if (editId) {
        await apiFetch(`/api/data/${editId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        showToast("데이터가 수정되었습니다!");
      } else {
        await apiFetch("/api/data", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("새 데이터가 추가되었습니다! 요약 및 차트가 즉시 갱신됩니다.");
      }

      $("editDataId").value = "";
      $("inputValue").value = "";
      $("inputMemo").value = "";
      $("btnSubmitData").textContent = "추가 저장";
      $("btnCancelEdit").classList.add("hidden");

      await Promise.all([loadDataList(), loadSummaryAndModals(), loadChartStatistics()]);
    } catch (err) {
      alert(`저장 오류: ${err.message}`);
    }
  });

  // 수정 취소 버튼
  $("btnCancelEdit")?.addEventListener("click", () => {
    $("editDataId").value = "";
    $("inputValue").value = "";
    $("inputMemo").value = "";
    $("btnSubmitData").textContent = "추가 저장";
    $("btnCancelEdit").classList.add("hidden");
  });

  // 카테고리 탭 및 검색어 필터
  document.querySelectorAll("#dataCategoryTabs .cat-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#dataCategoryTabs .cat-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.selectedCategory = tab.dataset.cat;
      renderDataTable();
    });
  });

  $("tableSearchInput")?.addEventListener("input", (e) => {
    state.searchKeyword = e.target.value.trim();
    renderDataTable();
  });

  // CSV / JSON 다운로드
  $("btnExportCSV")?.addEventListener("click", exportDataAsCSV);
  $("btnExportJSON")?.addEventListener("click", exportDataAsJSON);
}

document.addEventListener("DOMContentLoaded", initApp);

