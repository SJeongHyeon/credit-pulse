// 랜딩 02 「기업 검색」 패널: 최근 공시 칩, 정렬 가능한 결과 목록, 요약 수치.
// 데이터는 landing-data.js의 공개 보유 기업 목록이라 로그인 없이 동작한다. DOM은 createElement + textContent만 쓴다.
// 기존 폼(#company-search)과 landing.js의 슬라이드 필터, stage-tabs.js의 01 이동은 그대로 둔다.
import { COMPANIES } from "./landing-data.js";
import { bindSortMenu } from "./sort-menu.js";

const CHIP_COUNT = 3;
const MIN_BAR_PERCENT = 6;

const byName = (a, b) => String(a.name).localeCompare(String(b.name), "ko");
const sorters = {
  recent: (a, b) => String(b.filed_at).localeCompare(String(a.filed_at)) || byName(a, b),
  oldest: (a, b) => String(a.filed_at).localeCompare(String(b.filed_at)) || byName(a, b),
  name: byName,
};

// 검색어·값 정규화: 전각/반각 통일(NFKC), 소문자, 공백 전부 무시.
// landing.js의 슬라이드 필터(별칭 검색, PR #182)와 같은 규칙이라 01 슬라이드와 02 목록의 결과가 같다.
const normalize = (text) => String(text ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");

// 이름·corp_code·별칭(aliases, 있을 때만) 중 하나에 부분 일치
export function matchCompanies(companies, query) {
  const needle = normalize(query);
  if (!needle) return companies;
  return companies.filter((company) =>
    [company.name, company.corp_code, ...(company.aliases ?? [])].some((value) => normalize(value).includes(needle)),
  );
}

export function summarize(companies) {
  const days = new Map();
  for (const company of companies) days.set(company.filed_at, (days.get(company.filed_at) ?? 0) + 1);
  const dates = [...days.keys()].sort();
  const latest = dates.length ? dates[dates.length - 1] : "";
  // 동률이면 이른 날
  let peak = null;
  for (const date of dates) if (!peak || days.get(date) > peak.count) peak = { date, count: days.get(date) };
  return { dates, days, latest, peak };
}

export function createCompanySearchController({ document, companies = COMPANIES }) {
  const input = document.getElementById("company-query");
  const list = document.getElementById("cs-list");
  const chips = document.getElementById("cs-chips");
  const title = document.getElementById("cs-title");
  const empty = document.getElementById("cs-empty");
  const status = document.getElementById("cs-status");
  const count = document.getElementById("cs-count");
  const countLabel = document.getElementById("cs-count-label");
  const of = document.getElementById("cs-of");
  const hist = document.getElementById("cs-hist");
  const latestEl = document.getElementById("cs-latest");
  const peakEl = document.getElementById("cs-peak");
  if (!input || !list) return null;

  const summary = summarize(companies);
  let sort = "recent";
  let query = "";

  function textElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    return element;
  }

  function row(company) {
    const item = document.createElement("li");
    item.append(
      textElement("span", "cs-name", String(company.name)),
      textElement("small", "cs-code", String(company.corp_code)),
      textElement("small", "cs-date", String(company.filed_at)),
    );
    return item;
  }

  function announce(message) {
    if (status) status.textContent = message;
  }

  function drawStatic() {
    if (latestEl) latestEl.textContent = summary.latest || "-";
    if (peakEl) peakEl.textContent = summary.peak ? `${summary.peak.date} · ${summary.peak.count}곳` : "-";
    if (hist) {
      const max = summary.peak?.count ?? 1;
      hist.replaceChildren(
        ...summary.dates.map((date) => {
          const bar = document.createElement("i");
          bar.setAttribute("style", `height:${Math.max(MIN_BAR_PERCENT, Math.round((summary.days.get(date) / max) * 100))}%`);
          return bar;
        }),
      );
    }
    if (chips) {
      const recent = [...companies].sort(sorters.recent).slice(0, CHIP_COUNT);
      chips.replaceChildren(
        textElement("small", "", "최근 공시"),
        ...recent.map((company) => {
          const chip = textElement("button", "cs-chip", String(company.name));
          chip.setAttribute("type", "button");
          chip.setAttribute("title", `공시 ${company.filed_at}`);
          chip.addEventListener("click", () => setQuery(String(company.name)));
          return chip;
        }),
      );
    }
  }

  function render({ announceResult = false } = {}) {
    const matched = matchCompanies(companies, query);
    const ordered = [...matched].sort(sorters[sort]);
    const searching = normalize(query) !== "";
    if (title) title.textContent = searching ? `검색 결과 ${matched.length}곳` : `최근 공시 · 전체 ${matched.length}곳`;
    if (count) count.textContent = String(matched.length);
    if (countLabel) countLabel.textContent = searching ? "곳 검색됨" : "곳 보유";
    if (of) of.textContent = `보유 기업 ${companies.length}곳 중`;
    list.replaceChildren(...ordered.map(row));
    list.hidden = ordered.length === 0;
    if (empty) empty.hidden = ordered.length !== 0;
    if (announceResult) announce(ordered.length === 0 ? "검색 결과가 없습니다." : `${matched.length}곳이 검색되었습니다.`);
  }

  // 칩: 입력칸을 바꾸고 input 이벤트를 보내 landing.js의 슬라이드 필터도 같은 검색어를 따르게 한다.
  function setQuery(value) {
    input.value = value;
    if (typeof input.dispatchEvent === "function" && typeof Event === "function") {
      input.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      onInput();
    }
    input.focus?.();
  }

  function onInput() {
    query = input.value;
    render({ announceResult: true });
  }

  function initialize() {
    drawStatic();
    render();
    input.addEventListener("input", onInput);
    // 입력칸의 지우기(x) 버튼은 input 대신 search 이벤트를 보내는 브라우저가 있다.
    input.addEventListener("search", onInput);
    bindSortMenu(document, "cs-sort", (key, label) => {
      sort = key;
      render();
      announce(`${label}으로 정렬했습니다.`);
    });
  }

  return { initialize };
}

if (typeof window !== "undefined" && window.document?.getElementById("cs-list")) {
  createCompanySearchController({ document: window.document })?.initialize();
}
