import assert from "node:assert/strict";
import test from "node:test";

import { createApprovalsController } from "../backend/static/approvals.js";

const ME = { status: 200, json: async () => ({ display_name: "심사역" }) };
const json = (status, body) => ({ status, json: async () => body });

const review = (overrides = {}) => ({
  id: "r-1",
  corp_code: "00126380",
  corp_name: "삼성전자",
  fiscal_year: 2025,
  status: "awaiting_approval",
  query_count: 3,
  created_at: "2026-11-04T03:34:00Z",
  ...overrides,
});

function createElement(tagName = "div", values = {}) {
  const attributes = {};
  return {
    tagName: tagName.toUpperCase(),
    children: [],
    hidden: false,
    disabled: false,
    textContent: "",
    listeners: {},
    ...values,
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      this.children = nodes;
    },
    setAttribute(name, value) {
      attributes[name] = value;
    },
    getAttribute(name) {
      return attributes[name];
    },
    set innerHTML(_) {
      throw new Error("innerHTML must not be used");
    },
  };
}

const allText = (element) => [element.textContent, ...element.children.map(allText)].join(" ");
const descendants = (element) => element.children.flatMap((child) => [child, ...descendants(child)]);

function createPage() {
  const elements = {
    "approvals-loading": createElement("p"),
    "current-user": createElement("span"),
    "approvals-error": createElement("p", { hidden: true }),
    "reload-button": createElement("button", { hidden: true }),
    "approvals-notice": createElement("p", { hidden: true }),
    "approvals-status": createElement("p", { hidden: true }),
    "approvals-empty": createElement("p", { hidden: true }),
    "approvals-list": createElement("ul"),
    "logout-button": createElement("button"),
    "filter-awaiting_approval": createElement("button"),
    "filter-open": createElement("button"),
    "filter-running": createElement("button"),
  };
  return {
    elements,
    document: {
      getElementById: (id) => elements[id],
      createElement: (tag) => createElement(tag),
    },
  };
}

function createFetch(handlers) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    const method = options.method ?? "GET";
    calls.push({ path, method, options });
    const handler = handlers[`${method} ${path.split("?")[0]}`];
    if (!handler) throw new Error(`unexpected request: ${method} ${path}`);
    return handler(path, options);
  };
  return { fetch, calls };
}

async function start(handlers) {
  const page = createPage();
  const navigations = [];
  const { fetch, calls } = createFetch({ "GET /api/v1/auth/me": () => ME, ...handlers });
  const controller = createApprovalsController({
    document: page.document,
    fetch,
    navigate: (path) => navigations.push(path),
  });
  await controller.initialize();
  return { ...page, calls, navigations };
}

const click = (elements, id) => elements[id].listeners.click();
const rows = (elements) => elements["approvals-list"].children;
const mixed = () => [
  review({ id: "a-1", corp_name: "삼성전자", status: "awaiting_approval" }),
  review({ id: "o-1", corp_name: "현대자동차", status: "open", query_count: 0 }),
  review({ id: "o-2", corp_name: "SK하이닉스", status: "open" }),
  review({ id: "a-2", corp_name: "LG화학", status: "awaiting_approval" }),
  review({ id: "o-3", corp_name: "네이버", status: "open" }),
  review({ id: "g-1", corp_name: "처리중회사", status: "running" }),
  review({ id: "s-1", corp_name: "저장중회사", status: "saving" }),
  review({ id: "c-1", corp_name: "종료회사", status: "closed" }),
  review({ id: "x-1", corp_name: "모름회사", status: "weird" }),
];
const ok = (items, next_cursor = null) => () => json(200, { items, next_cursor });

// AC-02
test("counts three statuses, selects awaiting approval first, ignores other statuses", async () => {
  const { elements, calls } = await start({ "GET /api/v1/reviews": ok(mixed()) });

  assert.equal(calls[1].path, "/api/v1/reviews?page_size=50");
  assert.equal(calls[1].options.credentials, "same-origin");
  assert.equal(elements["current-user"].textContent, "심사역님");
  assert.equal(elements["filter-awaiting_approval"].textContent, "승인 대기 2");
  assert.equal(elements["filter-open"].textContent, "진행 가능 3");
  assert.equal(elements["filter-running"].textContent, "처리 중 1");
  assert.equal(elements["filter-awaiting_approval"].getAttribute("aria-pressed"), "true");
  assert.equal(elements["filter-open"].getAttribute("aria-pressed"), "false");
  assert.deepEqual(rows(elements).map((row) => allText(row).match(/삼성전자|LG화학/)?.[0]), ["삼성전자", "LG화학"]);
  assert.equal(elements["approvals-empty"].hidden, true);
  assert.equal(elements["approvals-loading"].hidden, true);
});

// AC-03
test("switching status filters locally without another request", async () => {
  const { elements, calls } = await start({ "GET /api/v1/reviews": ok(mixed()) });
  const before = calls.length;

  click(elements, "filter-open");

  assert.equal(calls.length, before);
  assert.equal(rows(elements).length, 3);
  assert.equal(elements["filter-open"].getAttribute("aria-pressed"), "true");
  assert.equal(elements["filter-awaiting_approval"].getAttribute("aria-pressed"), "false");
  assert.equal(elements["approvals-status"].textContent, "진행 가능 3건");
});

// AC-04
test("each row shows name, year, status, query count, KST time and a link to the review", async () => {
  const { elements } = await start({ "GET /api/v1/reviews": ok(mixed()) });

  const first = rows(elements)[0];
  const text = allText(first);
  assert.match(text, /삼성전자/);
  assert.match(text, /2025 사업연도/);
  assert.match(text, /승인 대기/);
  assert.match(text, /질의 3건/);
  assert.match(text, /2026-11-04 12:34/);
  const link = descendants(first).find((node) => node.tagName === "A");
  assert.equal(link.getAttribute("href"), "/reviews/a-1");
  assert.equal(link.textContent, "승인하러 가기");

  click(elements, "filter-running");
  const other = descendants(rows(elements)[0]).find((node) => node.tagName === "A");
  assert.equal(other.textContent, "열기");
});

// AC-05
test("follows next_cursor up to 5 pages of 50", async () => {
  let page = 0;
  const { elements, calls } = await start({
    "GET /api/v1/reviews": (path) => {
      page += 1;
      return json(200, { items: [review({ id: `p-${page}` })], next_cursor: page < 3 ? `c${page}` : null });
    },
  });

  const paths = calls.slice(1).map((call) => call.path);
  assert.deepEqual(paths, [
    "/api/v1/reviews?page_size=50",
    "/api/v1/reviews?page_size=50&cursor=c1",
    "/api/v1/reviews?page_size=50&cursor=c2",
  ]);
  assert.equal(elements["filter-awaiting_approval"].textContent, "승인 대기 3");
  assert.equal(elements["approvals-notice"].hidden, true);
});

// AC-06
test("stops after 5 pages and says the count is a lower bound", async () => {
  let page = 0;
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => {
      page += 1;
      return json(200, { items: [review({ id: `p-${page}` })], next_cursor: `c${page}` });
    },
  });

  assert.equal(calls.length - 1, 5);
  assert.equal(elements["approvals-notice"].hidden, false);
  assert.equal(elements["filter-awaiting_approval"].textContent, "승인 대기 5 이상");
  assert.equal(elements["approvals-status"].textContent, "승인 대기 5건 이상");
});

// AC-07
test("empty states use status specific sentences", async () => {
  const { elements } = await start({ "GET /api/v1/reviews": ok([review({ status: "closed" })]) });

  assert.equal(elements["approvals-empty"].hidden, false);
  assert.equal(elements["approvals-empty"].textContent, "승인 대기 중인 심사 건이 없습니다.");
  assert.equal(rows(elements).length, 0);
  click(elements, "filter-open");
  assert.equal(elements["approvals-empty"].textContent, "진행 가능한 심사 건이 없습니다.");
  click(elements, "filter-running");
  assert.equal(elements["approvals-empty"].textContent, "처리 중인 심사 건이 없습니다.");
});

// AC-08
test("401 from me or reviews goes to the landing page and leaves nothing behind", async () => {
  const a = await start({ "GET /api/v1/auth/me": () => json(401, {}) });
  assert.deepEqual(a.navigations, ["/"]);
  assert.equal(a.elements["approvals-error"].hidden, true);

  const b = await start({ "GET /api/v1/reviews": () => json(401, {}) });
  assert.deepEqual(b.navigations, ["/"]);
  assert.equal(b.elements["approvals-error"].hidden, true);
  assert.equal(rows(b.elements).length, 0);
});

// AC-09
test("errors show fixed sentences with reload, never server text", async () => {
  const secret = { error: { code: "X", message: "내부 상세", details: "SECRET", request_id: "REQ-9" } };
  const cases = [
    [() => json(500, secret), "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."],
    [() => json(429, secret), "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."],
    [() => json(404, secret), "승인 현황을 불러오지 못했습니다."],
    [() => { throw new TypeError("offline"); }, "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요."],
  ];
  for (const [handler, message] of cases) {
    const { elements } = await start({ "GET /api/v1/reviews": handler });
    assert.equal(elements["approvals-error"].textContent, message);
    assert.equal(elements["approvals-error"].hidden, false);
    assert.equal(elements["reload-button"].hidden, false);
    assert.doesNotMatch(allText(elements["approvals-error"]), /SECRET|REQ-9|내부 상세/);
  }
});

// EC-08
test("a failure on a later page discards the pages already received", async () => {
  let page = 0;
  const { elements } = await start({
    "GET /api/v1/reviews": () => {
      page += 1;
      return page === 1 ? json(200, { items: [review()], next_cursor: "c1" }) : json(500, {});
    },
  });

  assert.equal(rows(elements).length, 0);
  assert.equal(elements["filter-awaiting_approval"].textContent, "승인 대기");
  assert.equal(elements["approvals-status"].hidden, true);
  assert.equal(elements["approvals-error"].hidden, false);
});

// EC-11
test("reload retries the whole load", async () => {
  let attempts = 0;
  const { elements } = await start({
    "GET /api/v1/reviews": () => (++attempts === 1 ? json(500, {}) : json(200, { items: [review()], next_cursor: null })),
  });
  assert.equal(elements["reload-button"].hidden, false);

  await elements["reload-button"].listeners.click();

  assert.equal(elements["approvals-error"].hidden, true);
  assert.equal(elements["reload-button"].hidden, true);
  assert.equal(rows(elements).length, 1);
});

// AC-10
test("server strings are rendered as text, never as HTML", async () => {
  const evil = "<img src=x onerror=1>&";
  const { elements } = await start({ "GET /api/v1/reviews": ok([review({ corp_name: evil, id: "a/b?c" })]) });

  assert.match(allText(rows(elements)[0]), /<img src=x onerror=1>&/);
  const link = descendants(rows(elements)[0]).find((node) => node.tagName === "A");
  assert.equal(link.getAttribute("href"), "/reviews/a%2Fb%3Fc");
});

// AC-13
test("logout posts then goes to the landing page; failure shows a sentence", async () => {
  const ok204 = await start({
    "GET /api/v1/reviews": ok([]),
    "POST /api/v1/auth/logout": () => ({ status: 204 }),
  });
  await ok204.elements["logout-button"].listeners.click();
  assert.deepEqual(ok204.navigations, ["/"]);

  const failed = await start({
    "GET /api/v1/reviews": ok([]),
    "POST /api/v1/auth/logout": () => json(500, {}),
  });
  await failed.elements["logout-button"].listeners.click();
  assert.deepEqual(failed.navigations, []);
  assert.equal(failed.elements["approvals-error"].textContent, "로그아웃하지 못했습니다. 다시 시도해 주세요.");
});
