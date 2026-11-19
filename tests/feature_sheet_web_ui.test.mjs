// landing-feature-pages AC-03·05·07·08·09: 기능 시트 셸
import assert from "node:assert/strict";
import test from "node:test";

import { createFeatureSheetController } from "../backend/static/feature-sheet.js";
import { allText, byClass, byTag, createElement, fire, json } from "./fake_dom.mjs";

const KEYS = ["search", "reviews", "reports", "approvals", "rules"];
const NAMES = ["공시 검색", "리스크 점검", "보고서", "승인 현황", "내 판정 규칙"];
const COMPANIES = [{ name: "LG화학", corp_code: "00356361", fiscal_year: 2025, filed_at: "2026-03-10" }];

function rect(left, width) {
  return { left, width, right: left + width };
}

function createPage({ auth = "authenticated", logo = rect(24, 160), logout = rect(1100, 90), viewport = 1280 } = {}) {
  const root = createElement("html");
  if (auth) root.setAttribute("data-auth", auth);
  root.clientWidth = viewport;
  const sheet = createElement("dialog", {
    open: false,
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      for (const listener of this.listeners.close ?? []) listener();
    },
  });
  const body = createElement("div");
  const title = createElement("h2");
  const closeButton = createElement("button");
  const tabs = KEYS.map((key, index) => {
    const tab = createElement("button", { textContent: NAMES[index] });
    tab.setAttribute("data-feature", key);
    tab.setAttribute("aria-selected", "false");
    return tab;
  });
  const tiles = KEYS.map((key) => {
    const tile = createElement("a");
    tile.setAttribute("data-feature", key);
    tile.setAttribute("href", `/?open=${key}`);
    return tile;
  });
  const stats = Object.fromEntries(KEYS.map((key) => {
    const stat = createElement("em");
    stat.setAttribute("data-stat", key);
    return [key, stat];
  }));
  const logoEl = createElement("img", { getBoundingClientRect: () => logo });
  const logoutEl = createElement("button", { getBoundingClientRect: () => logout });
  const elements = { "feature-sheet": sheet, "sheet-body": body, "sheet-title": title, "sheet-close": closeButton, "site-logout": logoutEl };
  const listeners = {};
  const document = {
    documentElement: root,
    getElementById: (id) => elements[id],
    querySelector: (selector) => (selector === ".site-logo" ? logoEl : null),
    querySelectorAll: (selector) =>
      selector === "#sheet-tablist [role=tab]" ? tabs : selector === "#features a[data-feature]" ? tiles : selector === "#sheet-tablist [data-stat]" ? Object.values(stats) : [],
    addEventListener: (name, listener) => {
      listeners[name] = listener;
    },
    createElement: (tag) => createElement(tag),
  };
  return { document, root, sheet, body, title, closeButton, tabs, tiles, listeners, stats };
}

// 요청 경로별로 응답을 정하고, 응답을 손으로 풀어 줄 수 있는 fetch
function createFetch(table = {}) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    calls.push(`${options.method ?? "GET"} ${path}`);
    const handler = table[path] ?? (() => json(200, { items: [], next_cursor: null }));
    return typeof handler === "function" ? handler() : handler;
  };
  return { fetch, calls };
}

function boot(pageOptions = {}, { fetchTable, search = "", urls = [] } = {}) {
  const page = createPage(pageOptions);
  const net = createFetch(fetchTable);
  const navigations = [];
  const controller = createFeatureSheetController({
    document: page.document,
    fetch: net.fetch,
    navigate: (path) => navigations.push(path),
    companies: COMPANIES,
    search,
    replaceUrl: (path) => urls.push(path),
  });
  controller.initialize();
  return { ...page, ...net, navigations, controller, urls };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const press = (element) => fire(element, "click");

// AC-03
test("Given signed in, When each of the 5 tiles is clicked, Then that feature's sheet opens without following the link", async () => {
  const { tiles, tabs, sheet, title, calls } = boot();
  for (let index = 0; index < KEYS.length; index += 1) {
    const event = await press(tiles[index]);
    await flush();
    assert.equal(event.prevented, true, KEYS[index]);
    assert.equal(sheet.open, true);
    assert.equal(title.textContent, NAMES[index]);
    assert.deepEqual(tabs.map((tab) => tab.getAttribute("aria-selected")), KEYS.map((key) => String(key === KEYS[index])));
    assert.deepEqual(tabs.map((tab) => tab.getAttribute("tabindex")), KEYS.map((key) => (key === KEYS[index] ? "0" : "-1")));
    sheet.close();
  }
  assert.ok(calls.some((call) => call.includes("/api/v1/rules")));
});

test("Given signed out, When a tile is clicked, Then the sheet stays closed and the click is left to the login handler", async () => {
  for (const auth of ["anonymous", null]) {
    const { tiles, sheet, calls } = boot({ auth });
    const event = await press(tiles[2]);
    assert.equal(event.prevented, false);
    assert.equal(sheet.open, false);
    assert.deepEqual(calls, []);
  }
});

// 탭 전환
test("tabs switch the body without closing, and arrow keys move between tabs", async () => {
  const { tiles, tabs, sheet, title, body } = boot({}, { fetchTable: { "/api/v1/rules": json(503, { error: { code: "SERVICE_UNAVAILABLE_RULES" } }) } });
  await press(tiles[0]);
  await flush();
  assert.match(allText(body), /보유 기업 1곳 중 1곳이 검색되었습니다/);

  await press(tabs[4]);
  await flush();
  assert.equal(sheet.open, true);
  assert.equal(title.textContent, "내 판정 규칙");
  assert.match(allText(body), /데모 모드/);

  await fire(tabs[4], "keydown", { key: "ArrowRight" }); // 마지막 → 처음
  await flush();
  assert.equal(title.textContent, "공시 검색");
  assert.ok(tabs[0].focused > 0);
  await fire(tabs[0], "keydown", { key: "ArrowLeft" });
  await flush();
  assert.equal(title.textContent, "내 판정 규칙");
});

// EC-13
test("a late response from a previous tab is discarded", async () => {
  let release;
  const slow = new Promise((resolve) => {
    release = resolve;
  });
  const { tiles, tabs, body } = boot({}, { fetchTable: { "/api/v1/rules": () => slow.then(() => json(200, { items: [{ rule_id: "r", item: "litigation", instruction: "늦게 온 규칙" }] })) } });
  await press(tiles[4]); // 규칙: 응답 대기
  await press(tabs[0]); // 곧바로 공시 검색으로
  await flush();
  release();
  await flush();
  await flush();
  assert.match(allText(body), /보유 기업/);
  assert.doesNotMatch(allText(body), /늦게 온 규칙/);
});

// AC-07
test("close button, backdrop click and Esc all close the sheet and return focus to the opener", async () => {
  const page = boot();
  for (const how of ["button", "backdrop", "escape"]) {
    const tile = page.tiles[1];
    const before = tile.focused;
    await press(tile);
    await flush();
    assert.equal(page.sheet.open, true);
    if (how === "button") await press(page.closeButton);
    else if (how === "backdrop") await fire(page.sheet, "click", { target: page.sheet });
    else page.sheet.close(); // 네이티브 Esc도 close 이벤트로 이어진다
    assert.equal(page.sheet.open, false, how);
    assert.equal(tile.focused, before + 1, how);
    assert.deepEqual(page.body.children, []);
  }
});

test("a click inside the sheet content does not close it", async () => {
  const { tiles, sheet, body } = boot();
  await press(tiles[0]);
  await flush();
  await fire(sheet, "click", { target: body });
  assert.equal(sheet.open, true);
});

// AC-08
test("the sheet spans from the logo centre to the logout button's left edge, and goes full width when narrow", async () => {
  const wide = boot({ logo: rect(24, 160), logout: rect(1100, 90), viewport: 1280 });
  await press(wide.tiles[0]);
  assert.equal(wide.sheet.getAttribute("style"), "left:104px;right:180px");

  const narrow = boot({ logo: rect(16, 120), logout: rect(260, 80), viewport: 360 });
  await press(narrow.tiles[0]);
  assert.equal(narrow.sheet.getAttribute("style"), "left:0px;right:0px");

  const hidden = boot({ logout: rect(0, 0) }); // 로그아웃 버튼이 보이지 않으면 전체 폭
  await press(hidden.tiles[0]);
  assert.equal(hidden.sheet.getAttribute("style"), "left:0px;right:0px");
});

// AC-09
test("401 from the sheet's API sends the user to the landing page", async () => {
  const { tiles, navigations } = boot({}, { fetchTable: { "/api/v1/rules": json(401, {}) } });
  await press(tiles[4]);
  await flush();
  assert.deepEqual(navigations, ["/"]);
});

test("failures show a fixed message and a retry button that reloads", async () => {
  let attempts = 0;
  const { tiles, tabs, body, calls } = boot({}, {
    fetchTable: {
      "/api/v1/reviews?page_size=50": () => {
        attempts += 1;
        return attempts === 1 ? json(500, {}) : json(200, { items: [], next_cursor: null });
      },
    },
  });
  await press(tiles[1]);
  await flush();
  assert.match(allText(body), /서버에 문제가 있어/);
  assert.equal(byClass(body, "sheet-alert")[0].getAttribute("role"), "alert");

  await press(byTag(body, "button")[0]); // 다시 불러오기
  await flush();
  assert.match(allText(body), /아직 심사 건이 없습니다\./);
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 2);
  void tabs;
});

test("a network exception becomes the network message", async () => {
  const { tiles, body } = boot({}, {
    fetchTable: {
      "/api/v1/rules": () => {
        throw new Error("offline");
      },
    },
  });
  await press(tiles[4]);
  await flush();
  assert.match(allText(body), /서버에 연결할 수 없습니다/);
});

test("switching between review-based tabs reuses one load; closing clears the cache", async () => {
  const { tiles, tabs, sheet, calls } = boot();
  await press(tiles[1]);
  await flush();
  await press(tabs[2]);
  await flush();
  await press(tabs[3]);
  await flush();
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 1);
  sheet.close();
  await press(tiles[2]);
  await flush();
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 2);
});

// AC-05
test("after login, /?open=<feature> opens that sheet once auth is known and the URL is cleaned", async () => {
  const urls = [];
  const { sheet, title, listeners, root } = boot({ auth: null }, { search: "?open=reports", urls });
  assert.deepEqual(urls, ["/"]);
  assert.equal(sheet.open, false); // 아직 로그인 여부를 모른다

  root.setAttribute("data-auth", "authenticated");
  listeners["auth-state"]({ detail: { state: "authenticated" } });
  await flush();
  assert.equal(sheet.open, true);
  assert.equal(title.textContent, "보고서");
});

test("an unknown ?open value or a signed-out state opens nothing but still cleans the URL", async () => {
  const urls = [];
  const bad = boot({ auth: null }, { search: "?open=evil", urls });
  bad.listeners["auth-state"]({ detail: { state: "authenticated" } });
  assert.equal(bad.sheet.open, false);
  assert.deepEqual(urls, ["/"]);

  const signedOut = boot({ auth: null }, { search: "?open=rules" });
  signedOut.listeners["auth-state"]({ detail: { state: "anonymous" } });
  signedOut.listeners["auth-state"]({ detail: { state: "authenticated" } });
  assert.equal(signedOut.sheet.open, false);
});

test("already authenticated at start with ?open opens immediately", async () => {
  const { sheet, title } = boot({ auth: "authenticated" }, { search: "?open=approvals" });
  await flush();
  assert.equal(sheet.open, true);
  assert.equal(title.textContent, "승인 현황");
});

// AC-04
test("logging out (anonymous state) closes an open sheet", async () => {
  const { tiles, sheet, listeners } = boot();
  await press(tiles[0]);
  assert.equal(sheet.open, true);
  listeners["auth-state"]({ detail: { state: "anonymous" } });
  assert.equal(sheet.open, false);
});

test("the controller does nothing when the sheet markup is missing", () => {
  const controller = createFeatureSheetController({
    document: { getElementById: () => null, querySelectorAll: () => [], documentElement: createElement("html") },
    fetch: async () => json(200, {}),
    navigate: () => {},
  });
  assert.equal(controller, null);
});

// AC-03: 목록은 ↑↓로도 옮긴다
test("ArrowDown and ArrowUp move between list items like the horizontal arrows", async () => {
  const { tiles, tabs, title } = boot();
  await press(tiles[0]);
  await flush();
  await fire(tabs[0], "keydown", { key: "ArrowDown" });
  await flush();
  assert.equal(title.textContent, "리스크 점검");
  await fire(tabs[1], "keydown", { key: "ArrowUp" });
  await flush();
  assert.equal(title.textContent, "공시 검색");
});

// AC-08b: 목록의 현재 수치
test("opening the sheet fills the list's current numbers from the shared data", async () => {
  const items = ["open", "running", "awaiting_approval", "awaiting_approval", "closed", "closed", "closed"].map((status, index) => ({
    id: String(index), status, corp_code: "00000001", corp_name: "회사", fiscal_year: 2025, query_count: 0, created_at: "2026-11-01T00:00:00Z",
  }));
  const { tiles, stats, calls } = boot({}, {
    fetchTable: {
      "/api/v1/reviews?page_size=50": json(200, { items, next_cursor: null }),
      "/api/v1/rules": json(200, { items: [{}, {}, {}] }),
    },
  });
  await press(tiles[2]);
  await flush();
  await flush();
  assert.equal(stats.search.textContent, "1곳");
  assert.equal(stats.reviews.textContent, "진행 4건");
  assert.equal(stats.reports.textContent, "저장 3건");
  assert.equal(stats.approvals.textContent, "대기 2건");
  assert.equal(stats.rules.textContent, "3개");
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 1); // 본문과 수치가 한 번 불러온 것을 공유
});

test("failed numbers stay blank without any error text; rules 503 leaves its number empty", async () => {
  const { tiles, stats, body } = boot({}, {
    fetchTable: {
      "/api/v1/reviews?page_size=50": json(500, {}),
      "/api/v1/rules": json(503, { error: { code: "SERVICE_UNAVAILABLE_RULES" } }),
    },
  });
  await press(tiles[0]);
  await flush();
  await flush();
  assert.equal(stats.search.textContent, "1곳");
  for (const key of ["reviews", "reports", "approvals", "rules"]) assert.equal(stats[key].textContent, "", key);
  assert.doesNotMatch(allText(body), /서버에 문제/); // 공시 검색 본문에는 수치 실패가 새지 않는다
});

test("deleting a rule lowers the rule count in the list", async () => {
  const { tiles, stats, body } = boot({}, {
    fetchTable: {
      "/api/v1/rules": json(200, { items: [{ rule_id: "a", item: "litigation", instruction: "x" }, { rule_id: "b", item: "litigation", instruction: "y" }] }),
      "/api/v1/rules/a": { status: 204 },
    },
  });
  await press(tiles[4]);
  await flush();
  await flush();
  assert.equal(stats.rules.textContent, "2개");
  const first = byClass(body, "sheet-rule")[0];
  const buttons = byTag(first, "button");
  await fire(buttons[0], "click");
  await fire(byTag(first, "button")[0], "click");
  await flush();
  assert.equal(stats.rules.textContent, "1개");
});
