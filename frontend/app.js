/**
 * 나만의 AI 부동산 시세 & 매매 타이밍 비서 (바닐라 JS 프론트엔드)
 * - 관심 단지 워치리스트(Watchlist) 동적 관리 (추가 / 삭제 / 추천 랜드마크 칩 / Local-First 영구 보존)
 * - 선택 단지 동적 대시보드 (요약 카드 4개 / Chart.js 시세 차트 / AI 컨텍스트 주입 / 추천 질문 / CRUD 프리셋)
 * - 3대 원클릭 알림 배지 모달 (🚨 경매 포착 / 🔗 단지 프로필 / 📢 정책·교통호재 & 실시간 뉴스)
 * - 대화 내역 저장 및 불러오기 (/api/conversations) + CSV/JSON 내보내기 + 다크 모드
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

// 전역 상태 관리 (Local-First 워치리스트 & 선택 단지 영구 보존)
const state = {
  summary: null,
  statistics: null,
  allData: [],
  conversations: [],
  currentConversationId: null,
  watchlist: (() => {
    try {
      const saved = localStorage.getItem("REAL_ESTATE_WATCHLIST");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed;
      }
    } catch (e) {}
    return ["개포우성8차", "개포현대3차"];
  })(),
  selectedComplex: localStorage.getItem("LAST_SELECTED_COMPLEX") || "all",
  selectedCategory: "all",
  searchKeyword: "",
  chartInstance: null,
  presets: [],
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
// 1. 초기 로드 및 워치리스트 동기화
// =====================================================================
async function initApp() {
  initTheme();
  initDefaultDate();
  bindEvents();

  const swaggerLink = $("swaggerLink");
  if (swaggerLink) swaggerLink.href = `${API_BASE_URL}/docs`;

  // 1) 추천 랜드마크 프리셋 목록 조회
  await loadPresets();

  // 2) 초기 워치리스트 탭 바 렌더링
  renderWatchlistTabs();

  // 3) 초기 데이터 및 상태 동기화
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
          : `💾 DB: 로컬 스토어 (${health.total_data_count || 119}건)`;
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

async function loadPresets() {
  try {
    const res = await apiFetch("/api/data/presets");
    state.presets = res.presets || [];
    renderRecommendedPresets();
  } catch (err) {
    console.warn("프리셋 로드 실패:", err);
  }
}

function renderRecommendedPresets() {
  const container = $("recommendedPresetsContainer");
  if (!container) return;
  const topPresets = state.presets.filter(
    (p) => !["개포우성8차", "개포현대3차"].includes(p.key)
  ).slice(0, 6);

  container.innerHTML = topPresets
    .map(
      (p) =>
        `<button class="preset-chip-btn" onclick="addWatchlistComplex('${escapeHtml(p.key)}')">+ ${escapeHtml(p.key)}</button>`
    )
    .join("");
}

// =====================================================================
// 2. 워치리스트(Watchlist) 관리 (추가 / 삭제 / 전환)
// =====================================================================
window.addWatchlistComplex = async function (rawName) {
  const name = String(rawName || "").trim().replace(/[\[\]]/g, "");
  if (!name) return alert("추가할 아파트 단지명을 입력해주세요.");

  if (!state.watchlist.includes(name)) {
    state.watchlist.push(name);
    localStorage.setItem("REAL_ESTATE_WATCHLIST", JSON.stringify(state.watchlist));
  }

  // 백엔드에 해당 단지 데이터셋 보강 요청
  try {
    await apiFetch(`/api/data/complex/${encodeURIComponent(name)}`, { method: "POST" });
  } catch (e) {
    console.warn("단지 데이터 자동 세팅 요청 경고:", e);
  }

  const inputEl = $("inputNewComplex");
  if (inputEl) inputEl.value = "";

  await switchTargetComplex(name);
  showToast(`'${name}' 단지가 내 관심 워치리스트에 추가되었습니다!`);
};

window.removeWatchlistComplex = async function (name, event) {
  if (event) event.stopPropagation();

  state.watchlist = state.watchlist.filter((x) => x !== name);
  localStorage.setItem("REAL_ESTATE_WATCHLIST", JSON.stringify(state.watchlist));

  if (state.selectedComplex === name) {
    state.selectedComplex = state.watchlist.length > 0 ? "all" : "all";
    localStorage.setItem("LAST_SELECTED_COMPLEX", state.selectedComplex);
  }

  renderWatchlistTabs();
  await Promise.all([loadSummaryAndModals(), loadDataList(), loadChartStatistics()]);
  showToast(`'${name}' 단지가 워치리스트에서 제외되었습니다.`);
};

window.switchTargetComplex = async function (name) {
  state.selectedComplex = name;
  localStorage.setItem("LAST_SELECTED_COMPLEX", name);

  const focusLabel = $("currentFocusComplexLabel");
  if (focusLabel) {
    focusLabel.textContent = name === "all" ? "전체 보기 (통합 비교)" : name;
  }

  renderWatchlistTabs();
  updateNewsModal(name);
  updateQuickPrompts(name);
  updateCrudPresets(name);

  await Promise.all([loadSummaryAndModals(), loadDataList(), loadChartStatistics()]);
};

function renderWatchlistTabs() {
  const container = $("complexSelectorTabs");
  if (!container) return;

  const current = state.selectedComplex;
  let html = `<button class="complex-pill ${current === "all" ? "active" : ""}" onclick="switchTargetComplex('all')">
    <span class="pill-title">전체 보기 (통합 비교)</span>
  </button>`;

  if (state.watchlist.length === 0) {
    html += `<span class="empty-watchlist-msg">등록된 관심 단지가 없습니다. 상단에서 관심 단지를 추가해보세요.</span>`;
  } else {
    state.watchlist.forEach((name) => {
      const active = current === name ? "active" : "";
      html += `
        <button class="complex-pill ${active}" onclick="switchTargetComplex('${escapeHtml(name)}')">
          <span class="pill-title">🏢 ${escapeHtml(name)}</span>
          <span class="pill-del" title="워치리스트에서 제외" onclick="removeWatchlistComplex('${escapeHtml(name)}', event)">✕</span>
        </button>
      `;
    });
  }

  container.innerHTML = html;
}

// =====================================================================
// 3. 요약 데이터(/api/data/summary) 및 대시보드 카드 4개 동적 렌더링
// =====================================================================
async function loadSummaryAndModals() {
  try {
    const query =
      state.selectedComplex && state.selectedComplex !== "all"
        ? `?complex_name=${encodeURIComponent(state.selectedComplex)}`
        : "";
    const summary = await apiFetch(`/api/data/summary${query}`);
    state.summary = summary;

    updateDashboardCards(summary);

    // 상단 3대 알림 사인(배지) 및 모달 내부 데이터 업데이트
    renderAuctionBadgeAndModal(summary.auction_alerts || {});
    renderProfileModal(summary.complex_profiles || {}, summary.by_complex || {});
    renderPolicyBadgeAndModal(summary.policy_and_transit || []);
  } catch (err) {
    console.error("요약 로드 오류:", err);
  }
}

function updateDashboardCards(summary) {
  const current = state.selectedComplex;
  const m = summary.metrics || {};
  const byComplex = summary.by_complex || {};
  const profiles = (summary.complex_profiles && summary.complex_profiles.complexes) || {};

  // 1번 카드: 기간 및 건수
  $("sumTotalCount").textContent = `총 ${summary.count}건 (실거래 ${summary.trade_count}건)`;
  $("sumPeriod").textContent = `분석 기간: ${summary.period}`;
  $("sumOverallMetrics").textContent = `평균 ${(m.average / 10000).toFixed(2)}억 | 최고 ${(m.max / 10000).toFixed(2)}억 | 최저 ${(m.min / 10000).toFixed(2)}억`;

  if (current === "all") {
    // [전체 보기 모드]
    $("card1Label").textContent = "📅 전체 워치리스트 데이터 규모";
    $("card1Tag").textContent = `총 ${Object.keys(byComplex).length}개 단지`;

    const keys = Object.keys(byComplex);
    const firstKey = keys[0];
    const secondKey = keys[1];

    if (firstKey && byComplex[firstKey]) {
      const c1 = byComplex[firstKey];
      $("sumCard2Title").textContent = `🏢 ${firstKey}`;
      $("sumCard2Tag").textContent = `${c1.count}건 분석`;
      $("sumCard2Price").textContent = `평균 ${c1.average_eok}억 원`;
      $("sumCard2Range").textContent = `최고 ${c1.max_eok}억 / 최저 ${c1.min_eok}억`;
      $("sumCard2Latest").textContent = `최근: ${c1.latest_trade.date} (${c1.latest_trade.value_eok}억)`;
    } else {
      $("sumCard2Title").textContent = "🏢 관심 단지 등록 대기";
      $("sumCard2Price").textContent = "-";
      $("sumCard2Range").textContent = "상단 검색창에서 단지를 추가해주세요";
      $("sumCard2Latest").textContent = "";
    }

    if (secondKey && byComplex[secondKey]) {
      const c2 = byComplex[secondKey];
      $("sumCard3Title").textContent = `🏢 ${secondKey}`;
      $("sumCard3Tag").textContent = `${c2.count}건 분석`;
      $("sumCard3Price").textContent = `평균 ${c2.average_eok}억 원`;
      $("sumCard3Range").textContent = `최고 ${c2.max_eok}억 / 최저 ${c2.min_eok}억`;
      $("sumCard3Latest").textContent = `최근: ${c2.latest_trade.date} (${c2.latest_trade.value_eok}억)`;
    } else {
      $("sumCard3Title").textContent = "🏛️ 비교 단지 등록 대기";
      $("sumCard3Price").textContent = "-";
      $("sumCard3Range").textContent = "여러 단지를 추가하면 1:1 비교가 가능합니다";
      $("sumCard3Latest").textContent = "";
    }

    $("sumTrendShort").textContent = (summary.trend || "").split("(")[0].trim() || "시장 보합";
    $("sumTimingSignal").textContent = summary.market_timing_signal || "워치리스트 단지들의 가격대와 전세가율을 비교 분석 중입니다.";
  } else {
    // [특정 단지 집중 모드]
    const stat = byComplex[current] || {};
    const prof = profiles[current] || {};

    $("card1Label").textContent = `📅 [${current}] 분석 규모`;
    $("card1Tag").textContent = `${stat.count || 0}건 실거래`;

    // 2번 카드: 매매 실거래가
    $("sumCard2Title").textContent = `🏢 [${current}] 실거래가 통계`;
    $("sumCard2Tag").textContent = stat.trend || "시세 추적";
    $("sumCard2Price").textContent = stat.average_eok ? `평균 ${stat.average_eok}억 원` : "-";
    $("sumCard2Range").textContent = `최고 ${stat.max_eok || "-"}억 / 최저 ${stat.min_eok || "-"}억 (총 ${stat.count || 0}건)`;
    $("sumCard2Latest").textContent = stat.latest_trade
      ? `최근: ${stat.latest_trade.date} (${stat.latest_trade.value_eok}억)`
      : "최근 실거래 기록 없음";

    // 3번 카드: 스펙 & 전세/경매
    $("sumCard3Title").textContent = `🏛️ [${current}] 스펙 & 입지`;
    $("sumCard3Tag").textContent = prof.completion_date ? prof.completion_date.slice(0, 4) + "년 준공" : "스펙";
    $("sumCard3Price").textContent = prof.floor_area_ratio
      ? `용적률 ${prof.floor_area_ratio} / 대지 ${prof.avg_land_share || "-"}`
      : "스펙 확인 중";
    $("sumCard3Range").textContent = prof.buildings_and_units || "규모 정보";
    $("sumCard3Latest").textContent = prof.recent_jeonse_range ? `전세: ${prof.recent_jeonse_range}` : "전세가율 추적 중";

    // 4번 카드: 트렌드 & 타이밍
    $("sumTrendShort").textContent = stat.trend || summary.trend || "시세 진단 중";
    $("sumTimingSignal").textContent = summary.market_timing_signal || "매매 타이밍 및 갭 진단 중";
  }
}

function updateQuickPrompts(complexName) {
  const container = $("quickPromptsContainer");
  if (!container) return;

  if (complexName === "all") {
    container.innerHTML = `
      <button class="prompt-chip" data-prompt="현재 내 워치리스트에 등록된 아파트 단지들의 평균 시세와 전세가율, 상승 추세를 비교해 줘.">
        📊 워치리스트 단지 종합 비교
      </button>
      <button class="prompt-chip" data-prompt="지금 시장 상황에서 내 관심 단지 중 갭투자나 실거주 갈아타기로 가장 유리한 아파트는 어디일까?">
        🎯 추천 매수 타이밍 분석
      </button>
      <button class="prompt-chip" data-prompt="포착된 경매 물건들의 안전마진과 토지거래허가구역 실거주 면제 혜택을 분석해 줘.">
        🚨 경매 물건 입찰가 분석
      </button>
      <button class="prompt-chip" data-prompt="최근 정부 부동산 정책과 주요 철도/GTX 교통 호재가 전체 단지에 미치는 영향을 정리해 줘.">
        📢 정책 & 교통호재 브리핑
      </button>
    `;
  } else {
    container.innerHTML = `
      <button class="prompt-chip" data-prompt="${complexName}의 세대수, 용적률, 대지지분 등 핵심 스펙과 재건축/입지 가치를 자세히 분석해 줘.">
        🔗 ${complexName} 스펙 & 사업성
      </button>
      <button class="prompt-chip" data-prompt="${complexName}의 최근 실거래가와 전세가 흐름으로 볼 때 지금이 매수 적기인지 진단해 줘.">
        📊 ${complexName} 매수 타이밍 진단
      </button>
      <button class="prompt-chip" data-prompt="${complexName} 관련 경매 매물이나 급매물 출회 가능성, 토허제 면제 혜택과 적정 입찰가를 알려줘.">
        🚨 ${complexName} 경매·급매 입찰가
      </button>
      <button class="prompt-chip" data-prompt="최근 부동산 규제 완화와 인근 교통 호재가 ${complexName} 시세에 미치는 영향은 어때?">
        📢 ${complexName} 정책·교통호재
      </button>
    `;
  }

  container.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      sendChatMessage(chip.dataset.prompt);
    });
  });
}

function updateCrudPresets(complexName) {
  const container = $("crudPresetBar");
  if (!container) return;

  const target = complexName === "all" ? (state.watchlist[0] || "관심단지") : complexName;
  container.innerHTML = `
    <span class="preset-label">말머리 빠른 입력:</span>
    <button type="button" class="preset-btn" data-prefix="[${escapeHtml(target)}] 전용84㎡ 층 매매 실거래 (전세가 억 동반)">+ ${escapeHtml(target)} 실거래</button>
    <button type="button" class="preset-btn" data-prefix="[경매매물] [진행중/1회유찰] [${escapeHtml(target)}] 층 | 감정가 억 ➔ 최저입찰가 억 | 토허제 실거주 면제">+ 🚨 ${escapeHtml(target)} 경매</button>
    <button type="button" class="preset-btn" data-prefix="[정부정책] [${escapeHtml(target)}] ">+ 📢 정부정책</button>
    <button type="button" class="preset-btn" data-prefix="[교통호재] [${escapeHtml(target)}] ">+ 🚇 교통호재</button>
  `;

  container.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const memoInput = $("inputMemo");
      if (memoInput) {
        memoInput.value = btn.dataset.prefix;
        memoInput.focus();
      }
    });
  });
}

function updateNewsModal(complexName) {
  const queryInput = $("newsQueryInput");
  if (!queryInput) return;
  if (complexName === "all") {
    queryInput.value = "부동산 정책 OR 토지거래허가구역 OR 재건축 OR GTX";
  } else {
    queryInput.value = `${complexName} 재건축 OR 실거래 OR 토지거래허가구역`;
  }
}

// =====================================================================
// 4. 모달 렌더링 (경매, 프로필, 정책/뉴스)
// =====================================================================
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

  // 과거 낙찰 사례
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
      <h4 style="margin-bottom:6px;color:var(--primary);">🔗 ${escapeHtml(rel.title || "단지 종합 분석")}</h4>
      <p style="margin-bottom:4px;"><strong>📍 주요 입지:</strong> ${escapeHtml(rel.location_corridor || "")}</p>
      <p style="margin-bottom:4px;"><strong>🏗️ 규모 현황:</strong> ${escapeHtml(rel.combined_households || "")}</p>
      <p style="margin-bottom:8px;"><strong>⚖️ 핵심 분석 포인트:</strong> ${escapeHtml(rel.key_issue || "")}</p>
      <ul style="padding-left:18px;font-size:0.8rem;">${watchList}</ul>
    `;
  }

  const grid = $("profileComplexGrid");
  if (grid) {
    const current = state.selectedComplex;
    const namesToShow =
      current !== "all" && complexes[current]
        ? [current]
        : Object.keys(complexes);

    grid.innerHTML = namesToShow
      .map((name) => {
        const c = complexes[name] || {};
        const stat = byComplex[name] || {};
        return `
          <div class="profile-card">
            <h4>🏢 ${escapeHtml(c.official_name || name)}</h4>
            <ul class="profile-spec-list">
              <li>• <strong>지번 / 도로명:</strong> ${escapeHtml(c.jibun_address || "-")} (${escapeHtml(c.road_address || "-")})</li>
              <li>• <strong>준공 / 층수:</strong> ${escapeHtml(c.completion_date || "-")} / <strong>${escapeHtml(c.floors || "-")}</strong></li>
              <li>• <strong>세대수:</strong> ${escapeHtml(c.buildings_and_units || "-")}</li>
              <li>• <strong>평형 구성:</strong> ${escapeHtml(c.area_types || "-")}</li>
              <li>• <strong>용적률 / 대지지분:</strong> 용적률 <strong>${escapeHtml(c.floor_area_ratio || "-")}</strong> / 평균 대지지분 <strong>${escapeHtml(c.avg_land_share || "-")}</strong></li>
              <li>• <strong>전세 시세 수준:</strong> ${escapeHtml(c.recent_jeonse_range || "-")}</li>
              <li>• <strong>DB 매매 통계:</strong> 평균 <strong>${stat.average_eok || "-"}억</strong> / 최고 <strong>${stat.max_eok || "-"}억</strong> (${stat.count || 0}건)</li>
              <li>• <strong>단지 특이사항:</strong> ${escapeHtml(c.characteristics || "-")}</li>
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
// 5. 시계열 데이터 목록 조회 & CRUD 관리 & 내보내기
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
        badgeClass = "tag-tra";
        badgeLabel = "🚇 교통호재";
      }

      return `
        <tr>
          <td>${escapeHtml(item.date)}</td>
          <td><span class="tag-badge ${badgeClass}">${escapeHtml(badgeLabel)}</span></td>
          <td class="price-cell">${formatEok(item.value)}</td>
          <td>${escapeHtml(item.memo)}</td>
          <td class="action-cell">
            <button class="action-btn action-btn-edit" onclick="startEditData('${item.id}')">수정</button>
            <button class="action-btn action-btn-del" onclick="deleteDataItem('${item.id}')">삭제</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

window.startEditData = function (id) {
  const item = state.allData.find((x) => x.id === id);
  if (!item) return;
  $("editDataId").value = item.id;
  $("inputDate").value = item.date;
  $("inputValue").value = item.value;
  $("inputMemo").value = item.memo;
  $("btnSubmitData").textContent = "수정 완료";
  $("btnCancelEdit").classList.remove("hidden");
  $("inputMemo").focus();
};

window.deleteDataItem = async function (id) {
  if (!confirm("이 시계열 데이터를 삭제하시겠습니까?")) return;
  try {
    await apiFetch(`/api/data/${id}`, { method: "DELETE" });
    showToast("데이터가 삭제되었습니다.");
    await Promise.all([loadDataList(), loadSummaryAndModals(), loadChartStatistics()]);
  } catch (err) {
    alert(`삭제 실패: ${err.message}`);
  }
};

// =====================================================================
// 6. 시세 추이 비교 그래프 (Chart.js)
// =====================================================================
async function loadChartStatistics() {
  try {
    const stats = await apiFetch("/api/data/statistics");
    state.statistics = stats;
    updateChartFilterSelect();
    renderPriceChart();
  } catch (err) {
    console.error("차트 통계 로드 실패:", err);
  }
}

function updateChartFilterSelect() {
  const select = $("chartFilterSelect");
  if (!select) return;

  const currentVal = select.value || "all";
  let html = `<option value="all">전체 단지 + 경매/정책 표시</option>`;

  state.watchlist.forEach((cName) => {
    html += `<option value="${escapeHtml(cName)}">${escapeHtml(cName)}만 보기</option>`;
  });

  select.innerHTML = html;
  if (state.selectedComplex !== "all" && state.watchlist.includes(state.selectedComplex)) {
    select.value = state.selectedComplex;
  } else {
    select.value = currentVal;
  }
}

function renderPriceChart() {
  const canvas = $("priceTrendChart");
  if (!canvas || !window.Chart || !state.statistics) return;

  const stats = state.statistics;
  const filter = $("chartFilterSelect") ? $("chartFilterSelect").value : "all";

  const colorPalette = [
    { border: "#2563eb", bg: "rgba(37,99,235,0.15)" },
    { border: "#059669", bg: "rgba(5,150,105,0.15)" },
    { border: "#7c3aed", bg: "rgba(124,58,237,0.15)" },
    { border: "#d97706", bg: "rgba(217,119,6,0.15)" },
    { border: "#0284c7", bg: "rgba(2,132,199,0.15)" },
    { border: "#db2777", bg: "rgba(219,39,119,0.15)" },
  ];

  const seriesByComplex = stats.series_by_complex || {};
  const complexKeys = Object.keys(seriesByComplex);

  const datasets = [];
  let colorIdx = 0;

  complexKeys.forEach((cName) => {
    if (filter !== "all" && filter !== cName) return;

    const points = seriesByComplex[cName] || [];
    const color = colorPalette[colorIdx % colorPalette.length];
    colorIdx++;

    datasets.push({
      label: `${cName} 실거래가`,
      data: points.map((p) => ({ x: p.date, y: p.value_eok, memo: p.memo })),
      borderColor: color.border,
      backgroundColor: color.bg,
      borderWidth: 2.5,
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0.25,
      fill: false,
    });
  });

  // 경매 포인트
  const auctionPts = (stats.auction_points || []).filter(
    (p) => filter === "all" || p.complex_name === filter || p.memo.includes(filter)
  );
  if (auctionPts.length > 0) {
    datasets.push({
      label: "🚨 경매 최저입찰가",
      data: auctionPts.map((p) => ({ x: p.date, y: p.value_eok, memo: p.memo })),
      borderColor: "#dc2626",
      backgroundColor: "#dc2626",
      pointStyle: "star",
      pointRadius: 8,
      pointHoverRadius: 10,
      showLine: false,
    });
  }

  // 정책 포인트
  const policyPts = (stats.policy_transit_points || []).filter(
    (p) => filter === "all" || p.memo.includes(filter)
  );
  if (policyPts.length > 0) {
    datasets.push({
      label: "📢 정책 / 교통호재",
      data: policyPts.map((p) => ({ x: p.date, y: p.value_eok, memo: p.memo })),
      borderColor: "#d97706",
      backgroundColor: "#d97706",
      pointStyle: "triangle",
      pointRadius: 7,
      pointHoverRadius: 9,
      showLine: false,
    });
  }

  if (state.chartInstance) {
    state.chartInstance.destroy();
  }

  const ctx = canvas.getContext("2d");
  state.chartInstance = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { position: "top", labels: { boxWidth: 12, font: { size: 11, weight: "bold" } } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const raw = ctx.raw || {};
              return `${ctx.dataset.label}: ${raw.y}억 원`;
            },
            afterLabel: (ctx) => {
              const raw = ctx.raw || {};
              return raw.memo ? `메모: ${raw.memo}` : "";
            },
          },
        },
      },
      scales: {
        x: {
          type: "category",
          title: { display: true, text: "거래 및 이벤트 발생일" },
          grid: { display: false },
        },
        y: {
          title: { display: true, text: "금액 (억 원)" },
          ticks: { callback: (val) => `${val}억` },
        },
      },
    },
  });
}

// =====================================================================
// 7. AI 채팅 & 대화 기록 불러오기 (/api/chat, /api/conversations)
// =====================================================================
async function sendChatMessage(messageText) {
  const msg = (messageText || "").trim();
  if (!msg) return;

  const chatInput = $("chatInput");
  if (chatInput) chatInput.value = "";

  appendChatMessage("user", msg);

  const loading = $("chatLoading");
  const sendBtn = $("btnSendChat");
  if (loading) loading.classList.remove("hidden");
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await apiFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message: msg,
        conversation_id: state.currentConversationId,
        selected_complex: state.selectedComplex,
      }),
    });

    state.currentConversationId = res.conversation_id;

    const toolsUsed = res.tools_called || [];
    const toolsBadges =
      toolsUsed.length > 0
        ? toolsUsed.map((t) => `<span class="tool-call-badge">🛠️ ${escapeHtml(t)}</span>`).join("")
        : "";

    appendChatMessage("assistant", res.reply, `${toolsBadges} AI 부동산 비서`, true);
    await loadConversations();
  } catch (err) {
    appendChatMessage("assistant", `⚠️ 오류 발생: ${err.message}`, "오류");
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
        m.role === "user" ? "나의 질문 (불러온 대화)" : `${toolsInfo} AI 부동산 비서`,
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
// 8. 실시간 뉴스 검색 및 토픽 저장
// =====================================================================
async function fetchRealtimeNews() {
  const qInput = $("newsQueryInput");
  const listEl = $("realtimeNewsList");
  if (!listEl) return;

  listEl.innerHTML = `<li class="empty-item">실시간 뉴스를 검색하는 중입니다...</li>`;
  try {
    const q = qInput ? qInput.value.trim() : "부동산 정책 OR 재건축";
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
// 9. CSV / JSON 내보내기 및 유틸리티
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
  a.download = `my_ai_realestate_data_${new Date().toISOString().slice(0, 10)}.csv`;
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
  a.download = `my_ai_realestate_data_${new Date().toISOString().slice(0, 10)}.json`;
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

  // 상단 관심 단지 추가 버튼 및 엔터키
  $("btnAddComplex")?.addEventListener("click", () => {
    addWatchlistComplex($("inputNewComplex")?.value);
  });
  $("inputNewComplex")?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addWatchlistComplex($("inputNewComplex")?.value);
    }
  });

  // 3대 알림 사인 모달 오픈
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

  // 모달 닫기
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

  // 모달 내부 -> AI 비서 연동
  $("btnAskAiAuction")?.addEventListener("click", () => {
    $("auctionModal")?.classList.add("hidden");
    const target = state.selectedComplex === "all" ? "워치리스트 단지" : state.selectedComplex;
    sendChatMessage(
      `현재 포착된 [${target}] 경매 물건의 감정가 대비 최저입찰가 안전마진, 토지거래허가구역 실거주 면제 혜택, 그리고 전세 레버리지를 활용한 적정 입찰가와 실투자금을 상세히 분석해 줘.`
    );
  });
  $("btnAskAiProfile")?.addEventListener("click", () => {
    $("profileModal")?.classList.add("hidden");
    const target = state.selectedComplex === "all" ? "워치리스트 단지들" : state.selectedComplex;
    sendChatMessage(
      `[${target}]의 층수, 세대수, 용적률, 대지지분 등 기본 스펙과 핵심 입지 및 향후 정비사업/가치 평가를 자세히 설명해 줘.`
    );
  });
  $("btnAskAiPolicy")?.addEventListener("click", () => {
    $("policyModal")?.classList.add("hidden");
    const target = state.selectedComplex === "all" ? "전체 아파트" : state.selectedComplex;
    sendChatMessage(
      `최근 정부 부동산 정책(재건축진단 완화, 토지거래허가구역 등)과 교통 호재가 [${target}] 매매 타이밍에 미치는 영향을 분석해 줘.`
    );
  });

  // 실시간 뉴스 검색
  $("btnFetchNews")?.addEventListener("click", fetchRealtimeNews);

  // 채팅 전송
  $("chatForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    sendChatMessage($("chatInput")?.value);
  });

  // 대화 기록 서랍
  $("btnToggleHistory")?.addEventListener("click", () => {
    $("conversationDrawer")?.classList.toggle("hidden");
  });
  $("btnCloseDrawer")?.addEventListener("click", () => {
    $("conversationDrawer")?.classList.add("hidden");
  });
  $("btnNewChat")?.addEventListener("click", () => {
    state.currentConversationId = null;
    $("chatMessages").innerHTML = "";
    const target = state.selectedComplex === "all" ? "내 관심 워치리스트" : state.selectedComplex;
    appendChatMessage(
      "assistant",
      `새로운 상담을 시작합니다! <strong>[${target}]</strong> 시세 흐름, 전세 동향, 경매 물건, 투자 타이밍 등 무엇이든 물어보세요.`,
      "새 대화 시작됨"
    );
  });

  // 차트 필터
  $("chartFilterSelect")?.addEventListener("change", renderPriceChart);

  // 데이터 CRUD 추가/수정 폼
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

  // 수정 취소
  $("btnCancelEdit")?.addEventListener("click", () => {
    $("editDataId").value = "";
    $("inputValue").value = "";
    $("inputMemo").value = "";
    $("btnSubmitData").textContent = "추가 저장";
    $("btnCancelEdit").classList.add("hidden");
  });

  // 카테고리 탭
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
