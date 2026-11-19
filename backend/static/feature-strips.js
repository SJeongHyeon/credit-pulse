// 03 기능 패널의 펼쳐지는 띠(시안 D). 한 번에 하나가 펼쳐지고, 로그인 상태에서만 수치·미리보기를 채운다.
// 미리보기는 시트와 같은 본문 함수(sheet-panels.js)를 쓰고, 목록·입력 등은 CSS로 숨긴다. 「열기」는 시트를 연다.
import { el } from "./charts.js";
import { createFeatureData, sharedFeatureData } from "./feature-data.js";
import { COMPANIES } from "./landing-data.js";
import { MESSAGES, PANELS } from "./sheet-panels.js";

export const FEATURES = Object.freeze(["search", "reviews", "reports", "approvals", "rules"]);
const DEFAULT_OPEN = "reviews";
const LOADING = "불러오는 중…";
const RETRY = "다시 불러오기";

export function createFeatureStripsController({ document, fetch, navigate, companies = COMPANIES, data }) {
  data ??= createFeatureData({ fetch, companies });
  const root = document.getElementById("feature-strips");
  const byKey = Object.fromEntries(
    FEATURES.map((key) => [
      key,
      {
        strip: document.getElementById(`strip-${key}`),
        rail: document.getElementById(`strip-rail-${key}`),
        full: document.getElementById(`strip-full-${key}`),
        preview: document.getElementById(`strip-preview-${key}`),
        stat: document.getElementById(`strip-stat-${key}`),
      },
    ]),
  );
  if (!root || FEATURES.some((key) => !byKey[key].strip || !byKey[key].rail || !byKey[key].full || !byKey[key].preview)) return null;

  let active = DEFAULT_OPEN;
  let authenticated = document.documentElement?.getAttribute?.("data-auth") === "authenticated";
  const loaded = new Set();
  const tokens = {};

  function setStat(key, text, short) {
    const stat = byKey[key]?.stat;
    if (!stat) return;
    stat.textContent = short ?? text.match(/\d+\+?/)?.[0] ?? "";
    stat.setAttribute("title", text); // 닫힌 띠의 짧은 숫자가 무엇인지 알린다
  }

  function clearStats() {
    for (const key of FEATURES) {
      if (!byKey[key].stat) continue;
      byKey[key].stat.textContent = "";
      byKey[key].stat.removeAttribute("title");
    }
  }

  function showError(key, message) {
    const alert = el(document, "p", "sheet-alert", message);
    alert.setAttribute("role", "alert");
    const retry = el(document, "button", "sheet-btn", RETRY);
    retry.setAttribute("type", "button");
    retry.addEventListener("click", () => {
      data.reset();
      loaded.delete(key);
      loadPreview(key);
    });
    byKey[key].preview.replaceChildren(alert, retry);
  }

  async function loadPreview(key) {
    if (!authenticated || loaded.has(key)) return;
    tokens[key] = (tokens[key] ?? 0) + 1;
    const token = tokens[key];
    const current = () => authenticated && tokens[key] === token;
    const { preview } = byKey[key];
    preview.replaceChildren(el(document, "p", "sheet-note", LOADING));
    const ctx = {
      document,
      fetch,
      companies,
      inputId: "strip-company-query",
      reviews: data.reviews,
      setStat,
      replace: (nodes) => {
        if (current()) preview.replaceChildren(...nodes);
      },
      onUnauthorized: () => navigate("/"),
    };
    let result;
    try {
      result = await PANELS[key](ctx);
    } catch {
      result = { error: MESSAGES.network };
    }
    if (!current()) return;
    if (result.unauthorized) {
      navigate("/");
      return;
    }
    if (result.error) {
      showError(key, result.error);
      return;
    }
    preview.replaceChildren(...result.nodes);
    loaded.add(key);
  }

  // 한 번에 하나만 펼친다. 닫힌 면은 조작할 수 없게(inert) 막는다.
  function select(key, { focus = false } = {}) {
    active = key;
    for (const name of FEATURES) {
      const open = name === key;
      const { strip, rail, full } = byKey[name];
      strip.setAttribute("data-open", open ? "true" : "false");
      rail.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) full.removeAttribute("inert");
      else full.setAttribute("inert", "");
    }
    if (focus) byKey[key].rail.focus?.();
    loadPreview(key);
  }

  function moveFocus(index, step) {
    select(FEATURES[(index + step + FEATURES.length) % FEATURES.length], { focus: true });
  }

  function onAuth(state) {
    if (state === "authenticated") {
      authenticated = true;
      data.loadStats(setStat);
      loadPreview(active);
    } else if (state === "anonymous") {
      authenticated = false;
      loaded.clear();
      for (const key of FEATURES) tokens[key] = (tokens[key] ?? 0) + 1; // 진행 중이던 응답은 버린다
      clearStats();
      for (const key of FEATURES) byKey[key].preview.replaceChildren();
      data.reset();
    }
  }

  function initialize() {
    FEATURES.forEach((key, index) => {
      const { rail } = byKey[key];
      rail.addEventListener("click", () => select(key));
      rail.addEventListener("keydown", (event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") moveFocus(index, 1);
        else if (event.key === "ArrowLeft" || event.key === "ArrowUp") moveFocus(index, -1);
        else return;
        event.preventDefault?.();
      });
    });
    document.addEventListener?.("auth-state", (event) => onAuth(event?.detail?.state));
    select(active);
    if (authenticated) data.loadStats(setStat);
  }

  return { initialize, select };
}

if (typeof window !== "undefined" && window.document?.getElementById("feature-strips")) {
  createFeatureStripsController({
    document: window.document,
    fetch: window.fetch.bind(window),
    navigate: (path) => window.location.assign(path),
    data: sharedFeatureData(),
  })?.initialize();
}
