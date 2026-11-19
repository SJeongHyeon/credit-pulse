import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createReviewsController } from "../backend/static/reviews.js";

const M = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  listFailed: "심사 건 목록을 불러오지 못했습니다.",
  askFailed: "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  askUnknown: "알 수 없는 응답입니다. 화면을 새로 불러온 뒤 다시 시도해 주세요.",
  badInput: "입력을 확인한 뒤 다시 시도해 주세요.",
  question: "질문을 1~500자로 입력하세요",
  empty: "아직 심사 건이 없습니다. 위에서 질문하면 심사 건이 만들어집니다.",
  logout: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
  502: "서버 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.",
  503: "서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  504: "서버 응답 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.",
};

const ME = { status: 200, json: async () => ({ display_name: "심사역" }) };
const json = (status, body) => ({ status, json: async () => body });

const review = (overrides = {}) => ({
  id: "r-1",
  corp_code: "00126380",
  corp_name: "삼성전자",
  fiscal_year: 2025,
  status: "open",
  query_count: 3,
  created_at: "2026-11-04T03:34:00Z",
  updated_at: "2026-11-04T03:34:00Z",
  ...overrides,
});


function createElement(tagName = "div", values = {}) {
  const attributes = {};
  return {
    tagName: tagName.toUpperCase(),
    children: [],
    hidden: false,
    disabled: false,
    open: false,
    textContent: "",
    value: "",
    focused: false,
    listeners: {},
    ...values,
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
    prepend(...nodes) {
      this.children.unshift(...nodes);
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
    focus() {
      this.focused = true;
    },
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      this.listeners.close?.();
    },
    // 서버 문자열을 HTML로 해석하는 코드가 있으면 테스트가 즉시 실패하게 한다.
    set innerHTML(_) {
      throw new Error("innerHTML must not be used");
    },
  };
}


function allText(element) {
  return [element.textContent, ...element.children.map(allText)].join(" ");
}


function descendants(element) {
  return element.children.flatMap((child) => [child, ...descendants(child)]);
}


function createPage() {
  const template = readFileSync(new URL("../backend/templates/reviews.html", import.meta.url), "utf8");
  const examples = Array.from(template.matchAll(/data-question-example="([^"]+)">([^<]+)</g), ([, question, label]) =>
    createElement("button", { dataset: { questionExample: question }, textContent: label }),
  );
  const sortOptions = ["recent", "oldest", "name"].map((sort) => {
    const option = createElement("button");
    option.setAttribute("data-sort", sort);
    return option;
  });
  const elements = {
    "reviews-loading": createElement("p", { hidden: false }),
    "current-user": createElement("span"),
    "reviews-error": createElement("p", { hidden: true }),
    "reload-button": createElement("button", { hidden: true }),
    "reviews-empty": createElement("p", { hidden: true }),
    "reviews-list": createElement("ul"),
    "reviews-sort": createElement("button"),
    "reviews-sort-label": createElement("span", { textContent: "최신순" }),
    "reviews-sort-menu": createElement("ul", { hidden: true, querySelectorAll: () => sortOptions }),
    "reviews-sort-status": createElement("p"),
    "review-delete-all": createElement("button", { disabled: true }),
    "review-delete-dialog": createElement("dialog"),
    "review-delete-question": createElement("p"),
    "review-delete-error": createElement("p", { hidden: true }),
    "review-delete-yes": createElement("button"),
    "review-delete-no": createElement("button"),
    "more-button": createElement("button", { hidden: true }),
    "ask-form": createElement("form", { querySelectorAll: () => examples }),
    "ask-question": createElement("textarea"),
    "ask-submit": createElement("button", { textContent: "질문하기" }),
    "ask-error": createElement("p", { hidden: true }),
    "ask-result": createElement("div", { hidden: true }),
    "ask-result-message": createElement("p"),
    "ask-result-news": createElement("ul", { hidden: true }),
    "logout-button": createElement("button"),
    "rules-error": createElement("p", { hidden: true }),
    "rules-status": createElement("p", { hidden: true }),
    "rules-list": createElement("ul"),
  };
  return {
    elements,
    examples,
    sortOptions,
    document: {
      getElementById: (id) => elements[id],
      createElement: (tag) => createElement(tag),
    },
  };
}


// handlers: { "GET /api/v1/reviews": (path, options) => response | Promise<response> | throws }
function createFetch(handlers) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    const method = options.method ?? "GET";
    calls.push({ path, method, options });
    const key = `${method} ${path.split("?")[0]}`;
    const handler = handlers[key];
    if (!handler) throw new Error(`unexpected request: ${key}`);
    return handler(path, options);
  };
  return { fetch, calls };
}


async function start(handlers, setup = () => {}) {
  const page = createPage();
  setup(page.elements);
  const navigations = [];
  const { fetch, calls } = createFetch({
    "GET /api/v1/auth/me": () => ME,
    "GET /api/v1/rules": () => json(200, { items: [] }),  // 규칙 섹션 기본값(rules-web-ui 시험은 덮어씀)
    ...handlers,
  });
  const controller = createReviewsController({
    document: page.document,
    fetch,
    navigate: (path) => navigations.push(path),
  });
  await controller.initialize();
  return { ...page, calls, navigations };
}


const submit = (elements) => elements["ask-form"].listeners.submit({ preventDefault() {} });
const listItems = (elements) => elements["reviews-list"].children;

test("question examples fill and focus the input without submitting", async () => {
  const { elements, examples, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [] }),
    "POST /api/v1/queries": () => json(200, { review: { id: "r-1" } }),
  });
  const question = elements["ask-question"];
  assert.deepEqual(examples.map((example) => example.textContent), ["단기차입금", "주요 소송", "지급보증"]);
  for (const example of examples) {
    example.listeners.click();
    assert.equal(question.value, example.dataset.questionExample);
    assert.equal(question.focused, true);
  }
  assert.equal(calls.filter((call) => call.method === "POST").length, 0);

  question.value = "한화투자증권의 2025년 지급보증을 확인해 주세요.";
  await submit(elements);
  const request = calls.find((call) => call.path === "/api/v1/queries");
  assert.deepEqual(JSON.parse(request.options.body), { question: question.value });
});

test("sort changes request the first page and keep the selected order when loading more", async () => {
  const newest = review({ corp_name: "최근 기업" });
  const oldest = review({ corp_name: "오래된 기업" });
  const alphabetical = review({ corp_name: "가나다 기업" });
  const { elements, sortOptions, calls } = await start({
    "GET /api/v1/reviews": (path) => {
      const query = new URL(path, "https://testserver").searchParams;
      if (query.get("sort") === "oldest") {
        if (query.get("cursor")) return json(200, { items: [newest], next_cursor: null });
        return json(200, { items: [oldest], next_cursor: "oldest|id|key" });
      }
      const item = query.get("sort") === "name" ? alphabetical : newest;
      return json(200, { items: [item], next_cursor: null });
    },
  });

  assert.equal(elements["reviews-sort-label"].textContent, "최신순");
  elements["reviews-sort"].listeners.click();
  await sortOptions[1].listeners.click();
  assert.equal(elements["reviews-sort-label"].textContent, "오래된순");
  assert.equal(elements["reviews-sort-menu"].hidden, true);
  assert.equal(sortOptions[1].getAttribute("aria-selected"), "true");
  assert.match(allText(listItems(elements)[0]), /오래된 기업/);

  await elements["more-button"].listeners.click();
  assert.equal(calls.at(-1).path, "/api/v1/reviews?page_size=20&sort=oldest&cursor=oldest%7Cid%7Ckey");
  assert.match(allText(listItems(elements)[1]), /최근 기업/);

  elements["reviews-sort"].listeners.click();
  await sortOptions[2].listeners.click();
  assert.equal(calls.at(-1).path, "/api/v1/reviews?page_size=20&sort=name");
  assert.equal(listItems(elements).length, 1);
  assert.match(allText(listItems(elements)[0]), /가나다 기업/);
});

test("review deletion asks first and No leaves the card without an API request", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
  });
  const button = descendants(listItems(elements)[0]).find((node) => node.className === "review-delete-button secondary");
  assert.match(button.getAttribute("aria-label"), /삼성전자 심사 건 삭제/);

  await button.listeners.click();
  assert.equal(elements["review-delete-dialog"].open, true);
  assert.equal(elements["review-delete-question"].textContent, "해당 심사건 내역을 삭제하겠습니까?");
  await elements["review-delete-no"].listeners.click();
  assert.equal(elements["review-delete-dialog"].open, false);
  assert.equal(listItems(elements).length, 1);
  assert.equal(calls.some((call) => call.method === "DELETE"), false);
});


test("confirmed review deletion removes the card and shows empty guidance", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
    "DELETE /api/v1/reviews/r-1": () => ({ status: 204 }),
  });
  const button = descendants(listItems(elements)[0]).find((node) => node.className === "review-delete-button secondary");
  await button.listeners.click();
  await elements["review-delete-yes"].listeners.click();

  assert.equal(calls.at(-1).method, "DELETE");
  assert.equal(calls.at(-1).path, "/api/v1/reviews/r-1");
  assert.equal(listItems(elements).length, 0);
  assert.equal(elements["reviews-empty"].hidden, false);
  assert.equal(elements["review-delete-dialog"].open, false);
});


test("failed review deletion keeps the card and shows a fixed message", async () => {
  const { elements } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
    "DELETE /api/v1/reviews/r-1": () => json(409, { error: { message: "private value" } }),
  });
  const button = descendants(listItems(elements)[0]).find((node) => node.className === "review-delete-button secondary");
  await button.listeners.click();
  await elements["review-delete-yes"].listeners.click();

  assert.equal(listItems(elements).length, 1);
  assert.equal(elements["review-delete-error"].hidden, false);
  assert.match(elements["review-delete-error"].textContent, /처리 중인 심사 건/);
  assert.doesNotMatch(elements["review-delete-error"].textContent, /private value/);
});


test("bulk deletion opens the correct dialog, focuses No and cancels without a request", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
  });

  const button = elements["review-delete-all"];
  assert.equal(button.disabled, false);
  button.listeners.click();
  assert.equal(elements["review-delete-dialog"].open, true);
  assert.equal(elements["review-delete-question"].textContent, "전체 심사건 내역을 삭제하시겠습니까?");
  assert.equal(elements["review-delete-no"].focused, true);
  elements["review-delete-no"].listeners.click();

  assert.equal(elements["review-delete-dialog"].open, false);
  assert.equal(listItems(elements).length, 1);
  assert.equal(calls.some((call) => call.method === "DELETE"), false);
});


test("confirmed bulk deletion sends one request and clears the list and pagination", async () => {
  let finishDelete;
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review(), review({ id: "r-2" })], next_cursor: "more" }),
    "DELETE /api/v1/reviews": () => new Promise((resolve) => { finishDelete = resolve; }),
  });

  elements["review-delete-all"].listeners.click();
  const confirm = elements["review-delete-yes"].listeners.click;
  const request = confirm();
  await confirm();
  assert.equal(elements["review-delete-yes"].disabled, true);
  assert.equal(calls.filter((call) => call.method === "DELETE").length, 1);
  assert.equal(calls.at(-1).path, "/api/v1/reviews");
  assert.equal(calls.at(-1).options.credentials, "same-origin");
  finishDelete({ status: 204 });
  await request;

  assert.equal(listItems(elements).length, 0);
  assert.equal(elements["reviews-empty"].hidden, false);
  assert.equal(elements["more-button"].hidden, true);
  assert.equal(elements["review-delete-dialog"].open, false);
});


test("blocked bulk deletion leaves the list and displays the conflict", async () => {
  const { elements } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review(), review({ id: "r-2" })], next_cursor: null }),
    "DELETE /api/v1/reviews": () => json(409, { error: { code: "CONFLICT_REVIEW_IN_PROGRESS" } }),
  });

  elements["review-delete-all"].listeners.click();
  await elements["review-delete-yes"].listeners.click();

  assert.equal(listItems(elements).length, 2);
  assert.equal(elements["review-delete-dialog"].open, true);
  assert.equal(elements["review-delete-error"].hidden, false);
  assert.match(elements["review-delete-error"].textContent, /처리 중인 심사 건/);
});


// AC-02
test("shows each review with name, year, status label, query count and KST creation time in server order", async () => {
  const items = [
    review({ id: "r-1", corp_name: "삼성전자", status: "awaiting_approval", query_count: 3 }),
    review({ id: "r-2", corp_name: "현대자동차", fiscal_year: 2024, status: "closed", query_count: 0, created_at: "2026-11-03T15:05:00Z" }),
  ];
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => json(200, { items, next_cursor: null }),
  });

  assert.equal(calls[1].path, "/api/v1/reviews?page_size=20");
  assert.equal(calls[1].options.credentials, "same-origin");
  assert.equal(elements["reviews-loading"].hidden, true);
  assert.equal(listItems(elements).length, 2);

  const first = allText(listItems(elements)[0]);
  assert.match(first, /삼성전자/);
  assert.match(first, /2025/);
  assert.match(first, /승인 대기/);
  assert.match(first, /3/);
  assert.match(first, /2026-11-04 12:34/);

  const second = allText(listItems(elements)[1]);
  assert.match(second, /현대자동차/);
  assert.match(second, /종료/);
  assert.match(second, /2026-11-04 00:05/);
});


// AC-03
test("an empty list shows the guidance and hides the more button", async () => {
  const { elements } = await start({
    "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }),
  });

  assert.equal(elements["reviews-empty"].hidden, false);
  assert.equal(elements["reviews-empty"].textContent, M.empty);
  assert.equal(elements["more-button"].hidden, true);
  assert.equal(listItems(elements).length, 0);
});


// AC-04
test("the more button appends the next page with the cursor and hides at the end", async () => {
  const pages = [
    json(200, { items: [review({ id: "r-1", corp_name: "삼성전자" })], next_cursor: "abc=/+" }),
    json(200, { items: [review({ id: "r-2", corp_name: "LG전자" })], next_cursor: null }),
  ];
  const { elements, calls } = await start({
    "GET /api/v1/reviews": () => pages.shift(),
  });

  assert.equal(elements["more-button"].hidden, false);
  await elements["more-button"].listeners.click();

  assert.equal(calls.at(-1).path, `/api/v1/reviews?page_size=20&cursor=${encodeURIComponent("abc=/+")}`);
  assert.equal(listItems(elements).length, 2);
  assert.match(allText(listItems(elements)[1]), /LG전자/);
  assert.equal(elements["more-button"].hidden, true);
  assert.equal(elements["more-button"].disabled, false);
});


// AC-09
test("a 401 from any request sends the user to the login page", async () => {
  const unauthorized = () => json(401, { error: { code: "AUTH_REQUIRED" } });

  const onMe = await start({ "GET /api/v1/auth/me": unauthorized, "GET /api/v1/reviews": () => assert.fail("no list request") });
  assert.deepEqual(onMe.navigations, ["/"]);

  const onList = await start({ "GET /api/v1/reviews": unauthorized });
  assert.deepEqual(onList.navigations, ["/"]);

  const onAsk = await start({
    "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }),
    "POST /api/v1/queries": unauthorized,
  }, (e) => {
    e["ask-question"].value = "오뚜기의 2025년 말 단기차입금은?";
  });
  await submit(onAsk.elements);
  assert.deepEqual(onAsk.navigations, ["/"]);

  const pages = [json(200, { items: [], next_cursor: "c1" }), unauthorized()];
  const onMore = await start({ "GET /api/v1/reviews": () => pages.shift() });
  await onMore.elements["more-button"].listeners.click();
  assert.deepEqual(onMore.navigations, ["/"]);
});


// AC-11
test("a failed list load shows an error with a reload button that retries", async () => {
  const cases = [
    { respond: async () => { throw new Error("network unavailable"); }, message: M.listFailed },
    { respond: async () => json(500, {}), message: M.listFailed },
    { respond: async () => json(404, {}), message: M.listFailed },
    { respond: async () => json(429, {}), message: M.tooMany },
    ...[502, 503, 504].map((status) => ({ respond: async () => json(status, {}), message: M[status] })),
  ];

  for (const { respond, message } of cases) {
    let attempts = 0;
    const { elements } = await start({
      "GET /api/v1/reviews": async () => {
        attempts += 1;
        return attempts === 1 ? respond() : json(200, { items: [review()], next_cursor: null });
      },
    });

    assert.equal(elements["reviews-error"].hidden, false);
    assert.equal(elements["reviews-error"].textContent, message);
    assert.equal(elements["reload-button"].hidden, false);
    assert.equal(elements["reviews-loading"].hidden, true);

    await elements["reload-button"].listeners.click();

    assert.equal(elements["reviews-error"].hidden, true);
    assert.equal(elements["reload-button"].hidden, true);
    assert.equal(listItems(elements).length, 1);
  }
});


test("a failed more request keeps the list and lets the user retry", async () => {
  const pages = [
    json(200, { items: [review({ corp_name: "삼성전자" })], next_cursor: "c1" }),
    json(500, {}),
    json(200, { items: [review({ id: "r-2", corp_name: "LG전자" })], next_cursor: null }),
  ];
  const { elements } = await start({ "GET /api/v1/reviews": () => pages.shift() });

  await elements["more-button"].listeners.click();
  assert.equal(elements["reviews-error"].textContent, M.listFailed);
  assert.equal(listItems(elements).length, 1);
  assert.equal(elements["more-button"].disabled, false);
  assert.equal(elements["more-button"].hidden, false);

  await elements["more-button"].listeners.click();
  assert.equal(listItems(elements).length, 2);
  assert.equal(elements["reviews-error"].hidden, true);
});


// AC-12
test("server strings are shown as text and never become elements", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const { elements } = await start({
    "GET /api/v1/reviews": () => json(200, {
      items: [review({ corp_name: evil, status: "<script>x</script>" })],
      next_cursor: null,
    }),
  });

  const item = listItems(elements)[0];
  assert.match(allText(item), /<img src=x onerror=alert\(1\)>/);
  assert.equal(descendants(item).some((node) => node.tagName === "IMG" || node.tagName === "SCRIPT"), false);
  assert.match(allText(item), /알 수 없음/);
});


// AC-13
test("logout goes to the login page, and a failed logout stays with a message", async () => {
  const ok = await start({
    "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }),
    "POST /api/v1/auth/logout": () => ({ status: 204 }),
  });
  await ok.elements["logout-button"].listeners.click();
  assert.equal(ok.calls.at(-1).path, "/api/v1/auth/logout");
  assert.deepEqual(ok.navigations, ["/"]);

  for (const respond of [async () => ({ status: 500 }), async () => { throw new Error("network unavailable"); }]) {
    const failed = await start({
      "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }),
      "POST /api/v1/auth/logout": respond,
    });
    await failed.elements["logout-button"].listeners.click();
    assert.deepEqual(failed.navigations, []);
    assert.equal(failed.elements["reviews-error"].hidden, false);
    assert.equal(failed.elements["reviews-error"].textContent, M.logout);
  }
});


test("the loading indicator disappears whatever the first lookup returns", async () => {
  const outcomes = [
    { "GET /api/v1/reviews": async () => json(200, { items: [], next_cursor: null }) },
    { "GET /api/v1/reviews": async () => json(500, {}) },
    { "GET /api/v1/auth/me": async () => json(500, {}) },
    { "GET /api/v1/auth/me": async () => { throw new Error("network unavailable"); } },
  ];

  for (const handlers of outcomes) {
    const { elements } = await start(handlers);
    assert.equal(elements["reviews-loading"].hidden, true);
  }
});

test("first session lookup gives a status-specific message and keeps the reload action", async () => {
  for (const status of [502, 503, 504]) {
    const { elements } = await start({ "GET /api/v1/auth/me": () => json(status, { details: "secret" }) });
    assert.equal(elements["reviews-error"].textContent, M[status]);
    assert.equal(elements["reload-button"].hidden, false);
    assert.equal(elements["reviews-loading"].hidden, true);
    assert.doesNotMatch(elements["reviews-error"].textContent, /secret/);
  }
});


// AC-14
test("the header shows the user name as text and tolerates a missing name", async () => {
  const shown = await start({ "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }) });
  assert.equal(shown.elements["current-user"].textContent, "심사역님");

  const cases = [
    { json: async () => ({}) },
    { json: async () => ({ display_name: 42 }) },
    { json: async () => ({ display_name: "" }) },
    { json: async () => { throw new Error("broken json"); } },
  ];
  for (const me of cases) {
    const { elements } = await start({
      "GET /api/v1/auth/me": () => ({ status: 200, ...me }),
      "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
    });
    assert.equal(elements["current-user"].textContent, "");
    assert.equal(listItems(elements).length, 1);
  }

  const evil = await start({
    "GET /api/v1/auth/me": () => json(200, { display_name: "<img src=x onerror=alert(1)>" }),
    "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }),
  });
  assert.equal(evil.elements["current-user"].textContent, "<img src=x onerror=alert(1)>님");
  assert.equal(evil.elements["current-user"].children.length, 0);
});



// review-detail-ui AC-02
test("links each review name to its detail page with an encoded id", async () => {
  const items = [review({ id: "r-1" }), review({ id: "x/<img src=x>?", corp_name: "현대자동차" })];
  const { elements } = await start({
    "GET /api/v1/reviews": () => json(200, { items, next_cursor: null }),
  });

  const links = listItems(elements).map((item) => descendants(item).find((node) => node.tagName === "A"));
  assert.equal(links[0].getAttribute("href"), "/reviews/r-1");
  assert.equal(links[0].textContent, "삼성전자");
  assert.equal(links[1].getAttribute("href"), `/reviews/${encodeURIComponent("x/<img src=x>?")}`);
  assert.equal(links[1].textContent, "현대자동차");
  assert.equal(descendants(listItems(elements)[1]).some((node) => node.tagName === "IMG"), false);
});


// ── rules-web-ui (내 판정 규칙) ─────────────────────────────
const R = {
  empty: "저장된 판정 규칙이 없습니다. 승인 때 판정·사유를 고치면 여기에 쌓입니다.",
  unavailable: "이 환경에서는 판정 규칙 기억을 쓰지 않습니다(데모 모드).",
  listFailed: "판정 규칙을 불러오지 못했습니다.",
  deleteFailed: "규칙을 삭제하지 못했습니다. 다시 시도해 주세요.",
  alreadyDeleted: "이미 삭제된 규칙입니다.",
};
const EMPTY_REVIEWS = () => json(200, { items: [], next_cursor: null });
const rule = (overrides = {}) => ({
  rule_id: "memo-1:litigation",
  item: "litigation",
  instruction: "소송은 연결 기준으로 본다",
  source_memo_id: "memo-1",
  ...overrides,
});
const ruleItems = (elements) => elements["rules-list"].children;
const buttonsOf = (item) => descendants(item).filter((node) => node.tagName === "BUTTON");
const deletes = (calls) => calls.filter((c) => c.method === "DELETE");


// rules-web-ui AC-02
test("rules are listed in server order with item label, instruction and source memo", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [
      rule(),
      rule({ rule_id: "memo-2:borrowing_structure", item: "borrowing_structure", instruction: "차입금은 별도 기준", source_memo_id: "memo-2" }),
    ] }),
  });

  assert.equal(calls.find((c) => c.path === "/api/v1/rules").options.credentials, "same-origin");
  assert.equal(ruleItems(elements).length, 2);
  const first = allText(ruleItems(elements)[0]);
  assert.match(first, /소송/);
  assert.match(first, /소송은 연결 기준으로 본다/);
  assert.match(first, /memo-1/);
  assert.match(allText(ruleItems(elements)[1]), /차입 구조/);
  assert.deepEqual(buttonsOf(ruleItems(elements)[0]).map((b) => [b.textContent, b.hidden]), [["삭제", false], ["취소", true]]);
  assert.equal(elements["rules-status"].hidden, true);
  assert.equal(elements["rules-error"].hidden, true);
});


// rules-web-ui AC-03
test("no rules and demo mode show the section guidance while reviews still load", async () => {
  const none = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
  });
  assert.equal(none.elements["rules-status"].hidden, false);
  assert.equal(none.elements["rules-status"].textContent, R.empty);
  assert.equal(listItems(none.elements).length, 1);

  const demo = await start({
    "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
    "GET /api/v1/rules": () => json(503, { error: { code: "SERVICE_UNAVAILABLE_RULES", request_id: "req-9" } }),
  });
  assert.equal(demo.elements["rules-status"].textContent, R.unavailable);
  assert.equal(demo.elements["rules-error"].hidden, true);
  assert.equal(demo.elements["reviews-error"].hidden, true);
  assert.equal(listItems(demo.elements).length, 1);
});


test("a failed rules load stays inside the rules section", async () => {
  const cases = [
    { respond: async () => { throw new Error("network unavailable"); }, message: R.listFailed },
    { respond: async () => json(500, {}), message: R.listFailed },
    { respond: async () => json(503, { error: { code: "SERVICE_UNAVAILABLE_DB" } }), message: M[503] },
    { respond: async () => json(200, { items: "nope" }), message: R.listFailed },
    { respond: async () => json(429, {}), message: M.tooMany },
  ];
  for (const { respond, message } of cases) {
    const { elements } = await start({
      "GET /api/v1/reviews": () => json(200, { items: [review()], next_cursor: null }),
      "GET /api/v1/rules": respond,
    });
    assert.equal(elements["rules-error"].hidden, false);
    assert.equal(elements["rules-error"].textContent, message);
    assert.equal(elements["reviews-error"].hidden, true);
    assert.equal(listItems(elements).length, 1);
  }
});


// rules-web-ui AC-04
test("deleting a rule takes two clicks, sends one encoded DELETE and removes it", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule({ rule_id: "memo-1:a/b?c" }), rule({ rule_id: "memo-2:litigation", source_memo_id: "memo-2" })] }),
    "DELETE /api/v1/rules/memo-1%3Aa%2Fb%3Fc": () => ({ status: 204 }),
  });
  const [deleteButton, cancelButton] = buttonsOf(ruleItems(elements)[0]);

  await deleteButton.listeners.click();
  assert.equal(deletes(calls).length, 0);
  assert.equal(deleteButton.textContent, "정말 삭제");
  assert.equal(cancelButton.hidden, false);

  await deleteButton.listeners.click();
  assert.equal(deletes(calls).length, 1);
  assert.equal(deletes(calls)[0].path, `/api/v1/rules/${encodeURIComponent("memo-1:a/b?c")}`);
  assert.equal(deletes(calls)[0].options.credentials, "same-origin");
  assert.equal(ruleItems(elements).length, 1);
  assert.match(allText(ruleItems(elements)[0]), /memo-2/);
});


test("cancel returns to the plain delete button without a request", async () => {
  const { elements, calls } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule()] }),
  });
  const [deleteButton, cancelButton] = buttonsOf(ruleItems(elements)[0]);

  await deleteButton.listeners.click();
  cancelButton.listeners.click();

  assert.equal(deleteButton.textContent, "삭제");
  assert.equal(cancelButton.hidden, true);
  assert.equal(deletes(calls).length, 0);
  await deleteButton.listeners.click();  // 다시 첫 단계부터
  assert.equal(deletes(calls).length, 0);
});


test("deleting the last rule shows the empty guidance", async () => {
  const { elements } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule()] }),
    "DELETE /api/v1/rules/memo-1%3Alitigation": () => ({ status: 204 }),
  });
  const [deleteButton] = buttonsOf(ruleItems(elements)[0]);
  await deleteButton.listeners.click();
  await deleteButton.listeners.click();

  assert.equal(ruleItems(elements).length, 0);
  assert.equal(elements["rules-status"].textContent, R.empty);
});


test("a pending delete sends only one request", async () => {
  const releases = [];
  const { elements, calls } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule()] }),
    "DELETE /api/v1/rules/memo-1%3Alitigation": () => new Promise((resolve) => { releases.push(() => resolve({ status: 204 })); }),
  });
  const [deleteButton] = buttonsOf(ruleItems(elements)[0]);
  await deleteButton.listeners.click();
  const pending = deleteButton.listeners.click();
  await Promise.resolve();

  assert.equal(deleteButton.disabled, true);
  await deleteButton.listeners.click();
  assert.equal(deletes(calls).length, 1);

  releases[0]();
  await pending;
  assert.equal(ruleItems(elements).length, 0);
});


// rules-web-ui AC-05
test("a 404 delete removes the rule and says it was already deleted", async () => {
  const { elements } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule()] }),
    "DELETE /api/v1/rules/memo-1%3Alitigation": () => json(404, { error: { code: "RESOURCE_NOT_FOUND", request_id: "req-4" } }),
  });
  const [deleteButton] = buttonsOf(ruleItems(elements)[0]);
  await deleteButton.listeners.click();
  await deleteButton.listeners.click();

  assert.equal(ruleItems(elements).length, 0);
  assert.equal(elements["rules-error"].textContent, R.alreadyDeleted);
  assert.doesNotMatch(elements["rules-error"].textContent, /req-4/);
});


// rules-web-ui AC-06
test("a failed delete keeps the rule, explains why and re-enables the button", async () => {
  const cases = [
    { respond: async () => { throw new Error("network unavailable"); }, message: M.network },
    { respond: async () => json(500, { error: { details: "stack trace" } }), message: M.server },
    { respond: async () => json(429, {}), message: M.tooMany },
    { respond: async () => json(400, {}), message: R.deleteFailed },
  ];
  for (const { respond, message } of cases) {
    const { elements } = await start({
      "GET /api/v1/reviews": EMPTY_REVIEWS,
      "GET /api/v1/rules": () => json(200, { items: [rule()] }),
      "DELETE /api/v1/rules/memo-1%3Alitigation": respond,
    });
    const [deleteButton, cancelButton] = buttonsOf(ruleItems(elements)[0]);
    await deleteButton.listeners.click();
    await deleteButton.listeners.click();

    assert.equal(ruleItems(elements).length, 1);
    assert.equal(elements["rules-error"].textContent, message);
    assert.doesNotMatch(elements["rules-error"].textContent, /stack trace/);
    assert.equal(deleteButton.disabled, false);
    assert.equal(deleteButton.textContent, "삭제");
    assert.equal(cancelButton.hidden, true);
  }
});


// rules-web-ui AC-07
test("a 401 on the rules list or a delete sends the user to the login page", async () => {
  const unauthorized = () => json(401, { error: { code: "AUTH_REQUIRED" } });

  const onList = await start({ "GET /api/v1/reviews": EMPTY_REVIEWS, "GET /api/v1/rules": unauthorized });
  assert.deepEqual(onList.navigations, ["/"]);

  const onDelete = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule()] }),
    "DELETE /api/v1/rules/memo-1%3Alitigation": unauthorized,
  });
  const [deleteButton] = buttonsOf(ruleItems(onDelete.elements)[0]);
  await deleteButton.listeners.click();
  await deleteButton.listeners.click();
  assert.deepEqual(onDelete.navigations, ["/"]);

  // 심사 건 목록이 401이면 규칙은 부르지 않는다.
  const onReviews = await start({ "GET /api/v1/reviews": unauthorized, "GET /api/v1/rules": () => assert.fail("no rules request") });
  assert.deepEqual(onReviews.navigations, ["/"]);
});


// rules-web-ui AC-08
test("rule strings are shown as text and never become elements", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const { elements } = await start({
    "GET /api/v1/reviews": EMPTY_REVIEWS,
    "GET /api/v1/rules": () => json(200, { items: [rule({ item: "constructor", instruction: evil, source_memo_id: evil })] }),
  });

  const item = ruleItems(elements)[0];
  assert.match(allText(item), /<img src=x onerror=alert\(1\)>/);
  assert.match(allText(item), /알 수 없음/);
  assert.equal(descendants(item).some((node) => node.tagName === "IMG" || node.tagName === "SCRIPT"), false);
});

// 바로 질문(SPEC §3.3): 회사를 고르지 않고 질문만 보낸다.
const EMPTY_LIST = { "GET /api/v1/reviews": () => json(200, { items: [], next_cursor: null }) };
const QUESTION = "오뚜기의 2025년 말 단기차입금은 얼마인가요?";
const typed = (value = QUESTION) => (e) => { e["ask-question"].value = value; };

test("a question that starts a review sends only the question and opens that review", async () => {
  const { elements, calls, navigations } = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => json(200, {
      outcome: "awaiting_approval", draft: [], applied_rules: [],
      review: { id: "r 1/새", corp_name: "오뚜기", fiscal_year: 2025 },
    }),
  }, typed(`  ${QUESTION}  `));

  await submit(elements);

  const post = calls.find((call) => call.method === "POST");
  assert.equal(post.path, "/api/v1/queries");
  assert.deepEqual(JSON.parse(post.options.body), { question: QUESTION });
  assert.deepEqual(navigations, [`/reviews/${encodeURIComponent("r 1/새")}`]);
  assert.equal(elements["ask-result"].hidden, true);
});

test("a refusal without a review stays on the page with the server guidance and news as text links", async () => {
  const { elements, navigations } = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => json(200, {
      outcome: "refused", refusal_reason: "CORP_NOT_IN_CORPUS", review: null, applied_rules: [],
      refusal_message: "공시 근거로는 답할 수 없어 뉴스를 참고로 붙였습니다.",
      news_items: [
        { title: "<b>기사</b>", originallink: "https://news.example/a" },
        { title: "위험한 링크", link: "javascript:alert(1)" },
      ],
    }),
  }, typed());

  await submit(elements);

  assert.deepEqual(navigations, []);
  assert.equal(elements["ask-result"].hidden, false);
  assert.equal(elements["ask-result-message"].textContent, "공시 근거로는 답할 수 없어 뉴스를 참고로 붙였습니다.");
  const [first, second] = elements["ask-result-news"].children;
  assert.equal(first.children[0].tagName, "A");
  assert.equal(first.children[0].textContent, "<b>기사</b>");
  assert.equal(first.children[0].href, "https://news.example/a");
  assert.notEqual(second.children[0].tagName, "A");
  assert.equal(elements["ask-result-news"].hidden, false);
});

test("a refusal without server guidance uses the fixed reason text, and asking back shows the server sentence", async () => {
  const refused = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => json(200, { outcome: "refused", refusal_reason: "PERIOD_NOT_IN_CORPUS", review: null }),
  }, typed());
  await submit(refused.elements);
  assert.equal(
    refused.elements["ask-result-message"].textContent,
    "답변할 수 없는 질문입니다. 요청한 사업연도의 공시가 수집되지 않았습니다.",
  );
  assert.equal(refused.elements["ask-result-news"].hidden, true);

  const askedBack = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => json(200, {
      outcome: "answered", target_required: true, review: null,
      refusal_message: "회사명과 사업연도를 포함해 다시 질문해 주세요.",
    }),
  }, typed("차입금이 얼마인가요?"));
  await submit(askedBack.elements);
  assert.equal(
    askedBack.elements["ask-result-message"].textContent,
    "회사명과 사업연도를 포함해 다시 질문해 주세요. 회사 이름과 사업연도만 적어 보내면 방금 질문에 이어서 답합니다.",
  );
});

test("the answer to a question that was asked back is sent with that question, once", async () => {
  // 162번: 되물음 → 회사 이름만 답함(clarifies와 함께) → 거절로 끝남 → 그다음 질문은 다시 혼자 간다
  const replies = [
    { outcome: "answered", target_required: true, review: null, refusal_message: "비교할 회사 이름과 사업연도를 모두 적어 다시 질문해 주세요." },
    { outcome: "refused", refusal_reason: "ITEM_NOT_DISCLOSED", review: null },
    { outcome: "refused", refusal_reason: "ITEM_NOT_DISCLOSED", review: null },
  ];
  const { elements, calls } = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => json(200, replies.shift()),
  }, typed("두 회사의 차입금을 비교해 주세요."));
  await submit(elements);
  elements["ask-question"].value = "오뚜기와 대상, 2025 사업연도";
  await submit(elements);
  elements["ask-question"].value = "오뚜기의 2025년 직원 수는?";
  await submit(elements);
  const bodies = calls.filter((call) => call.method === "POST").map((call) => JSON.parse(call.options.body));
  assert.deepEqual(bodies, [
    { question: "두 회사의 차입금을 비교해 주세요." },
    { question: "오뚜기와 대상, 2025 사업연도", clarifies: "두 회사의 차입금을 비교해 주세요." },
    { question: "오뚜기의 2025년 직원 수는?" },
  ]);
});

test("an empty or too long question never reaches the server", async () => {
  for (const value of ["", "   ", "가".repeat(501)]) {
    const { elements, calls } = await start(EMPTY_LIST, typed(value));
    await submit(elements);
    assert.equal(calls.filter((call) => call.method === "POST").length, 0);
    assert.equal(elements["ask-error"].textContent, M.question);
    assert.equal(elements["ask-error"].hidden, false);
    assert.equal(elements["ask-question"].focused, true);
  }
});

test("ask errors are told apart, keep the question and re-enable the button", async () => {
  const cases = [
    [400, M.badInput], [422, M.badInput], [429, M.tooMany], [503, M[503]], [500, M.server], [404, M.askFailed],
  ];
  for (const [status, message] of cases) {
    const { elements } = await start({
      ...EMPTY_LIST,
      "POST /api/v1/queries": () => json(status, { error: { code: "X", message: "<i>서버 문구</i>" } }),
    }, typed());
    await submit(elements);
    assert.equal(elements["ask-error"].textContent, message);
    assert.equal(elements["ask-question"].value, QUESTION);
    assert.equal(elements["ask-submit"].disabled, false);
    assert.equal(elements["ask-submit"].textContent, "질문하기");
  }

  const offline = await start({ ...EMPTY_LIST, "POST /api/v1/queries": () => { throw new Error("offline"); } }, typed());
  await submit(offline.elements);
  assert.equal(offline.elements["ask-error"].textContent, M.network);
});

test("the ask button is disabled while a question is pending and a second submit sends nothing", async () => {
  const releases = [];
  const { elements, calls } = await start({
    ...EMPTY_LIST,
    "POST /api/v1/queries": () => new Promise((resolve) => {
      releases.push(() => resolve(json(200, { outcome: "refused", refusal_reason: "OUT_OF_DOMAIN", review: null })));
    }),
  }, typed());

  const pending = submit(elements);
  await Promise.resolve();
  assert.equal(elements["ask-submit"].disabled, true);
  assert.equal(elements["ask-submit"].textContent, "분석 중…");
  await submit(elements);
  assert.equal(calls.filter((call) => call.method === "POST").length, 1);

  releases[0]();
  await pending;
  assert.equal(elements["ask-submit"].disabled, false);
  assert.equal(elements["ask-result"].hidden, false);
});
