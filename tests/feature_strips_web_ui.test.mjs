// landing-feature-pages AC-15~18: 03 기능 패널의 펼쳐지는 띠
import assert from "node:assert/strict";
import test from "node:test";

import { createFeatureStripsController } from "../backend/static/feature-strips.js";
import { allText, byClass, byTag, createElement, fire, json } from "./fake_dom.mjs";

const KEYS = ["search", "reviews", "reports", "approvals", "rules"];
const COMPANIES = [{ name: "LG화학", corp_code: "00356361", fiscal_year: 2025, filed_at: "2026-03-10" }];
const REVIEWS = ["open", "running", "awaiting_approval", "awaiting_approval", "closed", "closed", "closed"].map((status, index) => ({
  id: String(index), status, corp_code: `0000000${index % 2}`, corp_name: "회사", fiscal_year: 2025, query_count: 0, created_at: "2026-11-01T00:00:00Z",
}));

function createPage({ auth = null } = {}) {
  const root = createElement("html");
  if (auth) root.setAttribute("data-auth", auth);
  const elements = { "feature-strips": createElement("div") };
  for (const key of KEYS) {
    const strip = createElement("div");
    strip.setAttribute("data-open", key === "reviews" ? "true" : "false");
    const rail = createElement("button");
    rail.setAttribute("aria-expanded", key === "reviews" ? "true" : "false");
    const full = createElement("div");
    if (key !== "reviews") full.setAttribute("inert", "");
    Object.assign(elements, {
      [`strip-${key}`]: strip,
      [`strip-rail-${key}`]: rail,
      [`strip-full-${key}`]: full,
      [`strip-preview-${key}`]: createElement("div"),
      [`strip-stat-${key}`]: createElement("em"),
    });
  }
  const listeners = {};
  const document = {
    documentElement: root,
    getElementById: (id) => elements[id],
    createElement: (tag) => createElement(tag),
    addEventListener: (name, listener) => {
      listeners[name] = listener;
    },
  };
  return { document, root, elements, listeners };
}

function createFetch(table = {}) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    calls.push(`${options.method ?? "GET"} ${path}`);
    const handler = table[path] ?? (() => json(200, { items: [], next_cursor: null }));
    return typeof handler === "function" ? handler() : handler;
  };
  return { fetch, calls };
}

const FULL_TABLE = {
  "/api/v1/reviews?page_size=50": json(200, { items: REVIEWS, next_cursor: null }),
  "/api/v1/rules": json(200, { items: [{ rule_id: "a", item: "litigation", instruction: "x" }, { rule_id: "b", item: "litigation", instruction: "y" }] }),
};

function boot(pageOptions, table) {
  const page = createPage(pageOptions);
  const net = createFetch(table);
  const navigations = [];
  const controller = createFeatureStripsController({
    document: page.document,
    fetch: net.fetch,
    navigate: (path) => navigations.push(path),
    companies: COMPANIES,
  });
  controller.initialize();
  return { ...page, ...net, navigations, controller };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const state = (elements) => ({
  open: KEYS.filter((key) => elements[`strip-${key}`].getAttribute("data-open") === "true"),
  expanded: KEYS.map((key) => elements[`strip-rail-${key}`].getAttribute("aria-expanded")),
  inert: KEYS.filter((key) => elements[`strip-full-${key}`].getAttribute("inert") !== null),
});

// AC-15
test("Given the landing, When the 03 panel starts, Then only the risk strip is open and the others are inert", () => {
  const { elements, calls } = boot({});
  assert.deepEqual(state(elements), { open: ["reviews"], expanded: ["false", "true", "false", "false", "false"], inert: ["search", "reports", "approvals", "rules"] });
  assert.deepEqual(calls, []); // 비로그인이면 API를 부르지 않는다
});

// AC-16
test("Given signed in, When another strip is clicked, Then only that strip opens, the previous closes and its preview loads once", async () => {
  const { elements, calls } = boot({ auth: "authenticated" }, FULL_TABLE);
  await flush();

  await fire(elements["strip-rail-approvals"], "click");
  await flush();
  assert.deepEqual(state(elements), { open: ["approvals"], expanded: ["false", "false", "false", "true", "false"], inert: ["search", "reviews", "reports", "rules"] });
  assert.match(allText(elements["strip-preview-approvals"]), /승인 대기 2건, 진행 가능 1건, 처리 중 1건, 승인 완료 3건입니다\./);

  await fire(elements["strip-rail-reviews"], "click");
  await fire(elements["strip-rail-approvals"], "click");
  await flush();
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 1); // 집계는 한 번만 부르고 모두 공유한다
});

test("Enter-less keyboard use: arrow keys move to the next strip and focus its rail", async () => {
  const { elements } = boot({});
  await fire(elements["strip-rail-reviews"], "keydown", { key: "ArrowRight" });
  assert.deepEqual(state(elements).open, ["reports"]);
  assert.ok(elements["strip-rail-reports"].focused > 0);
  await fire(elements["strip-rail-reports"], "keydown", { key: "ArrowLeft" });
  assert.deepEqual(state(elements).open, ["reviews"]);
  await fire(elements["strip-rail-search"], "keydown", { key: "ArrowUp" }); // 처음 → 마지막
  assert.deepEqual(state(elements).open, ["rules"]);
  const event = await fire(elements["strip-rail-rules"], "keydown", { key: "Tab" });
  assert.equal(event.prevented, false); // 다른 키는 건드리지 않는다
});

// AC-17
test("Given signed in, Then closed strips show short numbers and the open strip shows summary, cards and chart", async () => {
  const { elements, calls } = boot({ auth: "authenticated" }, FULL_TABLE);
  await flush();
  await flush();

  assert.deepEqual(KEYS.map((key) => elements[`strip-stat-${key}`].textContent), ["1", "4", "3", "2", "2"]);
  assert.equal(elements["strip-stat-reviews"].getAttribute("title"), "진행 4건");
  const preview = elements["strip-preview-reviews"];
  assert.match(allText(preview), /진행 중인 심사 건은 4건이고, 승인을 기다리는 건이 2건 있습니다\./);
  assert.equal(byClass(preview, "sheet-chart-figure")[0].getAttribute("role"), "img");
  assert.equal(calls.filter((call) => call.includes("/api/v1/reviews")).length, 1);
});

test("Given signed out, Then no API is called when strips are opened and previews stay empty", async () => {
  const { elements, calls } = boot({ auth: "anonymous" });
  await fire(elements["strip-rail-reports"], "click");
  await flush();
  assert.deepEqual(calls, []);
  assert.deepEqual(elements["strip-preview-reports"].children, []);
  assert.deepEqual(KEYS.map((key) => elements[`strip-stat-${key}`].textContent), ["", "", "", "", ""]);
});

test("auth-state: authenticated fills the numbers and the open preview; anonymous clears them", async () => {
  const { elements, listeners, calls } = boot({}, FULL_TABLE);
  assert.deepEqual(calls, []);

  listeners["auth-state"]({ detail: { state: "authenticated" } });
  await flush();
  await flush();
  assert.equal(elements["strip-stat-reports"].textContent, "3");
  assert.ok(elements["strip-preview-reviews"].children.length > 0);

  // AC-18
  listeners["auth-state"]({ detail: { state: "anonymous" } });
  assert.deepEqual(KEYS.map((key) => elements[`strip-stat-${key}`].textContent), ["", "", "", "", ""]);
  assert.ok(KEYS.every((key) => elements[`strip-preview-${key}`].children.length === 0));
  assert.equal(elements["strip-stat-reports"].getAttribute("title"), null);
});

test("logging out while a preview is loading discards the late response", async () => {
  let release;
  const slow = new Promise((resolve) => {
    release = resolve;
  });
  const { elements, listeners } = boot({ auth: "authenticated" }, {
    ...FULL_TABLE,
    "/api/v1/reviews?page_size=50": () => slow.then(() => json(200, { items: REVIEWS, next_cursor: null })),
  });
  await flush();
  listeners["auth-state"]({ detail: { state: "anonymous" } });
  release();
  await flush();
  await flush();
  assert.deepEqual(elements["strip-preview-reviews"].children, []);
});

test("a 401 sends the user to the landing page; failures show a fixed message with a retry that reloads", async () => {
  const unauthorized = boot({ auth: "authenticated" }, { "/api/v1/reviews?page_size=50": json(401, {}) });
  await flush();
  assert.deepEqual(unauthorized.navigations, ["/"]);

  let attempts = 0;
  const { elements } = boot({ auth: "authenticated" }, {
    ...FULL_TABLE,
    "/api/v1/reviews?page_size=50": () => {
      attempts += 1;
      return attempts === 1 ? json(500, {}) : json(200, { items: REVIEWS, next_cursor: null });
    },
  });
  await flush();
  await flush();
  const preview = elements["strip-preview-reviews"];
  assert.match(allText(preview), /서버에 문제가 있어/);
  assert.equal(byClass(preview, "sheet-alert")[0].getAttribute("role"), "alert");

  await fire(byTag(preview, "button")[0], "click"); // 다시 불러오기
  await flush();
  await flush();
  assert.match(allText(elements["strip-preview-reviews"]), /진행 중인 심사 건은 4건/);
});

test("a network exception becomes the network message", async () => {
  const { elements } = boot({ auth: "authenticated" }, {
    ...FULL_TABLE,
    "/api/v1/reviews?page_size=50": () => {
      throw new Error("offline");
    },
  });
  await flush();
  await flush();
  assert.match(allText(elements["strip-preview-reviews"]), /서버에 연결할 수 없습니다/);
});

test("the search strip preview uses its own input id and rules 503 leaves the number empty", async () => {
  const { elements } = boot({ auth: "authenticated" }, { ...FULL_TABLE, "/api/v1/rules": json(503, { error: { code: "SERVICE_UNAVAILABLE_RULES" } }) });
  await flush();
  await fire(elements["strip-rail-search"], "click");
  await flush();
  assert.equal(byTag(elements["strip-preview-search"], "input")[0].getAttribute("id"), "strip-company-query"); // 시트의 입력 id와 겹치지 않는다
  assert.equal(elements["strip-stat-rules"].textContent, "");
});

test("the controller does nothing when the strip markup is missing", () => {
  const controller = createFeatureStripsController({
    document: { getElementById: () => null, documentElement: createElement("html") },
    fetch: async () => json(200, {}),
    navigate: () => {},
  });
  assert.equal(controller, null);
});
