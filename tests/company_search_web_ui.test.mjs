// landing-company-search AC-01~07: 02 기업 검색 패널
import assert from "node:assert/strict";
import test from "node:test";

import { COMPANIES } from "../backend/static/landing-data.js";
import { createCompanySearchController, matchCompanies, summarize } from "../backend/static/company-search.js";
import { allText, byClass, createElement, fire } from "./fake_dom.mjs";

const SMALL = [
  { name: "금양", corp_code: "00106119", fiscal_year: 2025, filed_at: "2026-03-23" },
  { name: "한국금융지주", corp_code: "00432102", fiscal_year: 2025, filed_at: "2026-03-19" },
  { name: "SKC", corp_code: "00139889", fiscal_year: 2025, filed_at: "2026-04-03" },
  { name: "극동유화", corp_code: "00105156", fiscal_year: 2025, filed_at: "2026-03-23" },
  { name: "LG화학", corp_code: "00356361", fiscal_year: 2025, filed_at: "2026-03-19" },
  { name: "대현", corp_code: "00113614", fiscal_year: 2025, filed_at: "2026-03-19" },
];

function createPage() {
  const input = createElement("input", { value: "" });
  const options = ["recent", "oldest", "name"].map((key, index) => {
    const option = createElement("button");
    option.setAttribute("data-sort", key);
    option.setAttribute("aria-selected", index === 0 ? "true" : "false");
    return option;
  });
  const menu = createElement("ul", { hidden: true, querySelectorAll: (selector) => (selector === "[role=option]" ? options : []) });
  const sortButton = createElement("button");
  sortButton.setAttribute("aria-expanded", "false");
  const elements = {
    "company-query": input,
    "cs-list": createElement("ul"),
    "cs-chips": createElement("div"),
    "cs-title": createElement("p"),
    "cs-empty": createElement("p", { hidden: true }),
    "cs-status": createElement("p"),
    "cs-sort": sortButton,
    "cs-sort-label": createElement("span", { textContent: "최신순" }),
    "cs-sort-menu": menu,
    "cs-count": createElement("strong"),
    "cs-count-label": createElement("small"),
    "cs-of": createElement("p"),
    "cs-hist": createElement("div"),
    "cs-latest": createElement("p"),
    "cs-peak": createElement("p"),
  };
  const docListeners = {};
  const document = {
    getElementById: (id) => elements[id],
    createElement: (tag) => createElement(tag),
    addEventListener: (name, listener) => {
      docListeners[name] = listener;
    },
  };
  return { document, elements, options, menu, docListeners };
}

function boot(companies = SMALL) {
  const page = createPage();
  const controller = createCompanySearchController({ document: page.document, companies });
  controller.initialize();
  return page;
}

const names = (list) => list.children.map((item) => item.children[0].textContent);
async function type(page, value) {
  page.elements["company-query"].value = value;
  await fire(page.elements["company-query"], "input");
}

// AC-01
test("Given the panel opens, Then every company is listed newest first with summary numbers", () => {
  const { elements } = boot();
  // 같은 날은 가나다순(한글이 영문보다 앞): 4/3 SKC, 3/23 극동유화·금양, 3/19 대현·한국금융지주·LG화학
  assert.deepEqual(names(elements["cs-list"]), ["SKC", "극동유화", "금양", "대현", "한국금융지주", "LG화학"]);
  assert.equal(elements["cs-title"].textContent, "최근 공시 · 전체 6곳");
  assert.equal(elements["cs-count"].textContent, "6");
  assert.equal(elements["cs-count-label"].textContent, "곳 보유");
  assert.equal(elements["cs-of"].textContent, "보유 기업 6곳 중");
  assert.equal(elements["cs-latest"].textContent, "2026-04-03");
  assert.equal(elements["cs-peak"].textContent, "2026-03-19 · 3곳");
  // 한 줄: 이름, corp_code, 공시일
  assert.deepEqual(elements["cs-list"].children[0].children.map((cell) => cell.textContent), ["SKC", "00139889", "2026-04-03"]);
  assert.equal(elements["cs-empty"].hidden, true);
  assert.equal(elements["cs-list"].hidden, false);
});

test("the 3 recent chips are the newest companies and the histogram has one bar per filing date", () => {
  const { elements } = boot();
  const chipNames = byClass(elements["cs-chips"], "cs-chip").map((chip) => chip.textContent);
  assert.deepEqual(chipNames, ["SKC", "극동유화", "금양"]);
  assert.equal(byClass(elements["cs-chips"], "cs-chip")[0].getAttribute("title"), "공시 2026-04-03");
  const bars = elements["cs-hist"].children;
  assert.equal(bars.length, 3); // 3/19, 3/23, 4/3
  assert.deepEqual(bars.map((bar) => bar.getAttribute("style")), ["height:100%", "height:67%", "height:33%"]); // 최다 3곳 기준
});

test("summarize picks the earliest date on a tie and handles an empty list", () => {
  const tie = summarize([
    { filed_at: "2026-03-02" }, { filed_at: "2026-03-01" }, { filed_at: "2026-03-01" }, { filed_at: "2026-03-02" },
  ]);
  assert.deepEqual(tie.peak, { date: "2026-03-01", count: 2 });
  const none = summarize([]);
  assert.equal(none.latest, "");
  assert.equal(none.peak, null);
});

test("the real 100 companies: first row is the newest filing, latest date and peak day match the data", () => {
  const { elements } = boot(COMPANIES);
  assert.equal(elements["cs-list"].children.length, COMPANIES.length);
  assert.equal(elements["cs-list"].children[0].children[2].textContent, "2026-04-03");
  assert.equal(elements["cs-latest"].textContent, "2026-04-03");
  assert.match(elements["cs-peak"].textContent, /^\d{4}-\d{2}-\d{2} · \d+곳$/);
  assert.equal(elements["cs-count"].textContent, String(COMPANIES.length));
});

// AC-02
test("Given a query, Then names and corp_codes are matched ignoring case and spaces, and the summary follows", async () => {
  const page = boot();
  await type(page, "  금 ");
  assert.deepEqual(names(page.elements["cs-list"]), ["금양", "한국금융지주"]);
  assert.equal(page.elements["cs-title"].textContent, "검색 결과 2곳");
  assert.equal(page.elements["cs-count"].textContent, "2");
  assert.equal(page.elements["cs-count-label"].textContent, "곳 검색됨");
  assert.equal(page.elements["cs-status"].textContent, "2곳이 검색되었습니다.");

  await type(page, "skc");
  assert.deepEqual(names(page.elements["cs-list"]), ["SKC"]);
  await type(page, "356361"); // corp_code
  assert.deepEqual(names(page.elements["cs-list"]), ["LG화학"]);
  await type(page, "   "); // EC-01: 공백뿐이면 전체
  assert.equal(page.elements["cs-list"].children.length, 6);
  assert.equal(page.elements["cs-title"].textContent, "최근 공시 · 전체 6곳");
});

test("the search event from the clear (x) button is handled like input", async () => {
  const page = boot();
  await type(page, "금양");
  page.elements["company-query"].value = "";
  await fire(page.elements["company-query"], "search");
  assert.equal(page.elements["cs-list"].children.length, 6);
});

test("matchCompanies uses the same rule as the slide filter", () => {
  assert.equal(matchCompanies(SMALL, "").length, 6);
  assert.equal(matchCompanies(SMALL, "LG").length, 1);
  assert.equal(matchCompanies(SMALL, "0010").length, 2); // corp_code 부분 일치
});

// 별칭 검색(PR #182)과 같은 규칙: aliases가 없는 지금 데이터에서도, 생기는 뒤에도 01 슬라이드와 결과가 같다.
test("aliases, full-width characters and inner spaces are matched like the slide filter, without changing displayed names", async () => {
  const aliased = [
    { name: "케이씨티시", corp_code: "00103042", fiscal_year: 2025, filed_at: "2026-03-19", aliases: ["KCTC", "케이씨티씨"] },
    { name: "NAVER", corp_code: "00266961", fiscal_year: 2025, filed_at: "2026-03-13", aliases: ["네이버"] },
    { name: "F&F 홀딩스", corp_code: "00126487", fiscal_year: 2025, filed_at: "2026-03-18", aliases: ["에프앤에프홀딩스"] },
    { name: "엔피씨", corp_code: "00108038", fiscal_year: 2025, filed_at: "2026-03-23", aliases: ["NPC"] },
  ];
  assert.deepEqual(matchCompanies(aliased, "kctc").map((c) => c.name), ["케이씨티시"]);
  assert.deepEqual(matchCompanies(aliased, "ＮＰＣ").map((c) => c.name), ["엔피씨"]); // 전각
  assert.deepEqual(matchCompanies(aliased, "네이버").map((c) => c.name), ["NAVER"]);
  assert.deepEqual(matchCompanies(aliased, "에프앤에프 홀딩스").map((c) => c.name), ["F&F 홀딩스"]); // 입력 속 공백 무시
  assert.deepEqual(matchCompanies(aliased, "F&F홀딩스").map((c) => c.name), ["F&F 홀딩스"]); // 이름 속 공백 무시
  assert.equal(matchCompanies(aliased, "존재하지않음").length, 0);

  const page = boot(aliased);
  await type(page, "kctc");
  assert.deepEqual(names(page.elements["cs-list"]), ["케이씨티시"]); // 별칭으로 찾아도 표시 이름은 그대로
  assert.doesNotMatch(allText(page.elements["cs-list"]), /KCTC/);
});

// AC-06
test("Given no match, Then a message replaces the list and the number is 0", async () => {
  const page = boot();
  await type(page, "없는회사");
  assert.equal(page.elements["cs-list"].hidden, true);
  assert.equal(page.elements["cs-empty"].hidden, false);
  assert.equal(page.elements["cs-count"].textContent, "0");
  assert.equal(page.elements["cs-title"].textContent, "검색 결과 0곳");
  assert.equal(page.elements["cs-status"].textContent, "검색 결과가 없습니다.");
  await type(page, "");
  assert.equal(page.elements["cs-empty"].hidden, true);
});

// AC-03
test("Given a chip, When clicked, Then the input is filled and the list is filtered", async () => {
  const page = boot();
  await fire(byClass(page.elements["cs-chips"], "cs-chip")[1], "click"); // 극동유화
  assert.equal(page.elements["company-query"].value, "극동유화");
  assert.deepEqual(names(page.elements["cs-list"]), ["극동유화"]);
  assert.ok(page.elements["company-query"].focused > 0);
});

test("a chip click dispatches a real input event so the slide filter follows", async () => {
  const page = createPage();
  const input = page.elements["company-query"];
  const dispatched = [];
  input.dispatchEvent = (event) => {
    dispatched.push(event.type);
    for (const listener of input.listeners[event.type] ?? []) listener(event);
  };
  createCompanySearchController({ document: page.document, companies: SMALL }).initialize();
  await fire(byClass(page.elements["cs-chips"], "cs-chip")[0], "click");
  assert.deepEqual(dispatched, ["input"]);
  assert.deepEqual(names(page.elements["cs-list"]), ["SKC"]); // 같은 이벤트로 목록도 갱신된다
});

// AC-04
test("Given the sort menu, When an option is chosen, Then order, label, aria and announcement change and focus returns", async () => {
  const page = boot();
  await fire(page.elements["cs-sort"], "click");
  assert.equal(page.menu.hidden, false);
  assert.equal(page.elements["cs-sort"].getAttribute("aria-expanded"), "true");

  await fire(page.options[1], "click"); // 오래된순
  assert.deepEqual(names(page.elements["cs-list"]), ["대현", "한국금융지주", "LG화학", "극동유화", "금양", "SKC"]);
  assert.equal(page.elements["cs-sort-label"].textContent, "오래된순");
  assert.equal(page.elements["cs-sort"].getAttribute("aria-label"), "정렬: 오래된순");
  assert.deepEqual(page.options.map((option) => option.getAttribute("aria-selected")), ["false", "true", "false"]);
  assert.equal(page.elements["cs-status"].textContent, "오래된순으로 정렬했습니다.");
  assert.equal(page.menu.hidden, true);
  assert.equal(page.elements["cs-sort"].getAttribute("aria-expanded"), "false");
  assert.ok(page.elements["cs-sort"].focused > 0);

  await fire(page.elements["cs-sort"], "click");
  await fire(page.options[2], "click"); // 가나다순
  assert.deepEqual(names(page.elements["cs-list"]), ["극동유화", "금양", "대현", "한국금융지주", "LG화학", "SKC"].sort((a, b) => a.localeCompare(b, "ko")));
});

test("the chosen sort applies to search results too", async () => {
  const page = boot();
  await fire(page.elements["cs-sort"], "click");
  await fire(page.options[1], "click"); // 오래된순
  await type(page, "금");
  assert.deepEqual(names(page.elements["cs-list"]), ["한국금융지주", "금양"]);
});

// AC-05
test("Esc, outside click, Tab and arrow keys behave like a listbox menu", async () => {
  const page = boot();
  const button = page.elements["cs-sort"];

  await fire(button, "keydown", { key: "ArrowDown" });
  assert.equal(page.menu.hidden, false);
  assert.ok(page.options[0].focused > 0); // 선택된 항목에 포커스

  await fire(page.options[0], "keydown", { key: "ArrowDown" });
  assert.ok(page.options[1].focused > 0);
  await fire(page.options[1], "keydown", { key: "End" });
  assert.ok(page.options[2].focused > 0);
  await fire(page.options[2], "keydown", { key: "ArrowUp" });
  assert.ok(page.options[1].focused > 1);
  await fire(page.options[1], "keydown", { key: "Home" });
  assert.ok(page.options[0].focused > 1);

  const before = button.focused;
  const escape = await fire(page.options[0], "keydown", { key: "Escape" });
  assert.equal(escape.prevented, true);
  assert.equal(page.menu.hidden, true);
  assert.equal(button.focused, before + 1);

  await fire(button, "click");
  page.docListeners.click({ target: createElement("div") }); // 바깥 클릭
  assert.equal(page.menu.hidden, true);
  assert.equal(button.getAttribute("aria-expanded"), "false");

  await fire(button, "click");
  page.docListeners.click({ target: button }); // 버튼 클릭은 바깥이 아니다(버튼 자신의 핸들러가 토글)
  assert.equal(page.menu.hidden, false);
  await fire(page.options[0], "keydown", { key: "Tab" });
  assert.equal(page.menu.hidden, true);

  await fire(button, "keydown", { key: "ArrowUp" }); // 마지막 항목으로 열기
  assert.ok(page.options[2].focused > 0);
});

// AC-07
test("company names are rendered as text only", async () => {
  const evil = [{ name: "<img src=x onerror=alert(1)>", corp_code: "00000001", fiscal_year: 2025, filed_at: "2026-01-01" }];
  const page = boot(evil);
  assert.match(allText(page.elements["cs-list"]), /<img src=x onerror=alert\(1\)>/);
  assert.equal(byClass(page.elements["cs-chips"], "cs-chip")[0].textContent, "<img src=x onerror=alert(1)>");
  // fake_dom은 innerHTML을 쓰면 던진다 — 여기까지 왔다면 쓰지 않은 것이다.
});

test("an empty company list renders without error and the controller is skipped without markup", () => {
  const page = boot([]);
  assert.equal(page.elements["cs-list"].hidden, true);
  assert.equal(page.elements["cs-empty"].hidden, false);
  assert.equal(page.elements["cs-latest"].textContent, "-");
  assert.equal(page.elements["cs-peak"].textContent, "-");
  assert.equal(page.elements["cs-count"].textContent, "0");

  const none = createCompanySearchController({ document: { getElementById: () => null, createElement: () => createElement() }, companies: SMALL });
  assert.equal(none, null);
});
