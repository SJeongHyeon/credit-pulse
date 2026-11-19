// 랜딩의 기능 시트: 03 패널의 칸을 누르면(로그인 상태) 아래에서 올라온다.
// 위치·탭·닫기·포커스 복귀·오래된 응답 버리기를 맡고, 본문은 sheet-panels.js가 그린다.
import { COMPANIES } from "./landing-data.js";
import { el } from "./charts.js";
import { createFeatureData, sharedFeatureData } from "./feature-data.js";
import { MESSAGES, PANELS } from "./sheet-panels.js";

export const FEATURES = Object.freeze(["search", "reviews", "reports", "approvals", "rules"]);
const NAMES = Object.freeze({ search: "공시 검색", reviews: "리스크 점검", reports: "보고서", approvals: "승인 현황", rules: "내 판정 규칙" });
const NARROW_PX = 480; // 이보다 좁으면 전체 폭
const LOADING = "불러오는 중…";
const RETRY = "다시 불러오기";

export function createFeatureSheetController({ document, fetch, navigate, companies = COMPANIES, search = "", replaceUrl = () => {}, data }) {
  data ??= createFeatureData({ fetch, companies });
  const sheet = document.getElementById("feature-sheet");
  const body = document.getElementById("sheet-body");
  const title = document.getElementById("sheet-title");
  const closeButton = document.getElementById("sheet-close");
  const tabs = [...document.querySelectorAll("#sheet-tablist [role=tab]")];
  const tiles = [...document.querySelectorAll("#features a[data-feature]")];
  // 목록 항목 오른쪽의 현재 수치 칸(data-stat=<기능>)
  const statEls = {};
  for (const element of [...(document.querySelectorAll("#sheet-tablist [data-stat]") ?? [])]) {
    statEls[element.getAttribute("data-stat")] = element;
  }
  if (!sheet || !body || tabs.length === 0) return null;

  let current = null;
  let opener = null;
  let sequence = 0; // 늦게 도착한 이전 탭의 응답을 버리기 위한 번호
  let pendingKey = null;

  const isAuthenticated = () => document.documentElement?.getAttribute?.("data-auth") === "authenticated";
  const isOpen = () => Boolean(sheet.open);

  // 시안 B: 왼쪽 끝 = 로고의 가로 중앙, 오른쪽 끝 = 「로그아웃」 버튼의 왼쪽 끝. 좁으면 전체 폭.
  function place() {
    const logo = document.querySelector?.(".site-logo")?.getBoundingClientRect?.();
    const logout = document.getElementById("site-logout")?.getBoundingClientRect?.();
    const viewport = document.documentElement?.clientWidth ?? 0;
    let left = 0;
    let right = 0;
    if (logo && logout && logout.width > 0 && viewport > 0) {
      const candidateLeft = logo.left + logo.width / 2;
      const candidateRight = viewport - logout.left;
      if (viewport - candidateLeft - candidateRight >= NARROW_PX) {
        left = Math.round(candidateLeft);
        right = Math.round(candidateRight);
      }
    }
    sheet.setAttribute("style", `left:${left}px;right:${right}px`);
  }

  function setStat(key, text) {
    if (statEls[key]) statEls[key].textContent = text;
  }

  function showError(message, key) {
    const alert = el(document, "p", "sheet-alert", message);
    alert.setAttribute("role", "alert");
    const retry = el(document, "button", "sheet-btn", RETRY);
    retry.setAttribute("type", "button");
    retry.addEventListener("click", () => {
      data.reset();
      render(key);
    });
    body.replaceChildren(alert, retry);
  }

  async function render(key) {
    sequence += 1;
    const token = sequence;
    const isCurrent = () => token === sequence && isOpen();
    body.replaceChildren(el(document, "p", "sheet-note", LOADING));
    const ctx = {
      document,
      fetch,
      companies,
      reviews: data.reviews,
      setStat,
      replace: (nodes) => {
        if (isCurrent()) body.replaceChildren(...nodes);
      },
      onUnauthorized: () => navigate("/"),
    };
    let result;
    try {
      result = await PANELS[key](ctx);
    } catch {
      result = { error: MESSAGES.network };
    }
    if (!isCurrent()) return;
    if (result.unauthorized) {
      navigate("/");
      return;
    }
    if (result.error) {
      showError(result.error, key);
      return;
    }
    body.replaceChildren(...result.nodes);
  }

  function selectTab(key, { focus = false } = {}) {
    current = key;
    for (const tab of tabs) {
      const selected = tab.getAttribute("data-feature") === key;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.setAttribute("tabindex", selected ? "0" : "-1");
      if (selected && focus) tab.focus?.();
    }
    if (title) title.textContent = NAMES[key];
    return render(key);
  }

  function open(key, fromTile) {
    if (!FEATURES.includes(key)) return;
    if (fromTile) opener = fromTile;
    if (!isOpen()) {
      if (typeof sheet.showModal === "function") sheet.showModal();
      else sheet.setAttribute("open", "");
    }
    place();
    selectTab(key, { focus: true });
    data.loadStats(setStat); // 본문이 시작한 심사 건 불러오기를 그대로 공유한다
  }

  function close() {
    if (typeof sheet.close === "function") sheet.close();
    else sheet.removeAttribute("open");
    handleClosed();
  }

  // 「닫기」·Esc·배경 클릭 어느 쪽으로 닫혀도 같은 정리를 한다.
  function handleClosed() {
    sequence += 1; // 진행 중이던 응답은 버린다
    data.reset();
    body.replaceChildren();
    const back = opener;
    opener = null;
    back?.focus?.();
  }

  function moveFocus(index, step) {
    const next = tabs[(index + step + tabs.length) % tabs.length];
    selectTab(next.getAttribute("data-feature"), { focus: true });
  }

  function initialize() {
    for (const tile of tiles) {
      tile.addEventListener("click", (event) => {
        // 비로그인이면 feature-login.js가 로그인 창을 연다. 로그인 상태에서만 시트를 연다.
        if (!isAuthenticated()) return;
        event.preventDefault();
        open(tile.getAttribute("data-feature"), tile);
      });
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => selectTab(tab.getAttribute("data-feature")));
      tab.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowRight") moveFocus(index, 1);
        else if (event.key === "ArrowUp" || event.key === "ArrowLeft") moveFocus(index, -1);
        else return;
        event.preventDefault?.();
      });
    });
    closeButton?.addEventListener("click", close);
    sheet.addEventListener("click", (event) => {
      if (event.target === sheet) close(); // 배경(::backdrop) 클릭
    });
    sheet.addEventListener("close", handleClosed); // Esc
    if (typeof window !== "undefined") {
      window.addEventListener("resize", () => {
        if (isOpen()) place();
      });
    }

    // 로그인 직후 도착지: /?open=<기능>. 허용 목록에 있는 값만 따르고 주소에서는 지운다.
    const params = new URLSearchParams(search);
    if (params.has("open")) {
      const value = params.get("open");
      pendingKey = FEATURES.includes(value) ? value : null;
      replaceUrl("/");
    }
    document.addEventListener?.("auth-state", (event) => {
      const state = event?.detail?.state;
      if (state === "authenticated" && pendingKey) {
        const key = pendingKey;
        pendingKey = null;
        open(key, tiles.find((tile) => tile.getAttribute("data-feature") === key));
      } else if (state === "anonymous") {
        pendingKey = null;
        if (isOpen()) close(); // 로그아웃하면 시트를 닫는다
      }
    });
    if (pendingKey && isAuthenticated()) {
      const key = pendingKey;
      pendingKey = null;
      open(key);
    }
  }

  return { initialize, open, close };
}

if (typeof window !== "undefined" && window.document?.getElementById("feature-sheet")) {
  createFeatureSheetController({
    document: window.document,
    fetch: window.fetch.bind(window),
    navigate: (path) => window.location.assign(path),
    search: window.location.search,
    replaceUrl: (path) => window.history.replaceState(null, "", path),
    data: sharedFeatureData(),
  })?.initialize();
}
