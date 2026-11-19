import assert from "node:assert/strict";
import test from "node:test";

import { createReviewDetailController } from "../backend/static/review-detail.js";

const M = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "질의가 너무 잦거나 오늘 질의 한도를 모두 썼습니다. 잠시 후 다시 시도해 주세요.",
  badInput: "입력을 확인한 뒤 다시 시도해 주세요.",
  loadFailed: "심사 건을 불러오지 못했습니다.",
  queryFailed: "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  question: "질문을 1~500자로 입력하세요",
  conflict: "지금은 질문할 수 없습니다. 처리 중이거나 이 심사 건의 오늘 질의 한도를 넘었습니다. 화면을 새로 불러옵니다",
  unknown: "알 수 없음",
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

const citation = (overrides = {}) => ({
  chunk_id: "c-1",
  rcept_no: "20260310000123",
  chapter: "NOTES",
  note_no: "27",
  quote: "당사는 계류 중인 소송이 없습니다.",
  verification: { passed: true, failed_checks: [] },
  ...overrides,
});

const draftItem = (overrides = {}) => ({
  item: "litigation",
  verdict: "not_applicable",
  reason: "주석 27에 계류 중인 소송이 없다고 기재되어 있습니다.",
  citations: [citation()],
  ...overrides,
});

const answered = (overrides = {}) => ({
  request_id: "req-1",
  review_id: "r-1",
  outcome: "answered",
  refusal_reason: null,
  answer: "소송 항목은 비해당으로 보입니다.",
  draft: [draftItem()],
  applied_rules: [],
  trace: {},
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
    className: "",
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
  const conversation = createElement("div", { hidden: true });
  const turnTemplate = {
    content: {
      cloneNode() {
        const parts = {
          question: createElement("p"),
          notice: createElement("p", { hidden: true }),
          answer: createElement("p", { hidden: true }),
          drafts: createElement("ul"),
          rules: createElement("div", { hidden: true }),
          "rules-list": createElement("ul"),
          news: createElement("div", { hidden: true }),
          "news-list": createElement("ul"),
        };
        return { parts, querySelector: (selector) => parts[selector.match(/"([^"]+)"/)[1]] };
      },
    },
  };
  const elements = {
    "detail-loading": createElement("p", { hidden: false }),
    "current-user": createElement("span"),
    "logout-button": createElement("button"),
    "detail-error": createElement("p", { hidden: true }),
    "detail-reload": createElement("button", { hidden: true }),
    "detail-missing": createElement("div", { hidden: true }),
    "detail-body": createElement("section", { hidden: true }),
    "review-name": createElement("span"),
    "review-code": createElement("span"),
    "review-year": createElement("span"),
    "review-status": createElement("span"),
    "review-count": createElement("span"),
    "review-created": createElement("span"),
    "query-section": createElement("section"),
    "query-form": createElement("form"),
    question: createElement("textarea"),
    "query-submit": createElement("button", { textContent: "추가 질문 보내기" }),
    "query-error": createElement("p", { hidden: true }),
    "query-hint": createElement("p", { hidden: true }),
    "query-scope": createElement("p"),
    "review-conversation": conversation,
    "review-turn-template": turnTemplate,
    "approval-section": createElement("section", { hidden: true }),
    "approval-question": createElement("div", { hidden: true }),
    "approval-question-text": createElement("p"),
    "approval-rules": createElement("section", { hidden: true }),
    "approval-rules-list": createElement("ul"),
    "approval-summary": createElement("p"),
    "approval-list": createElement("div"),
    "approval-empty": createElement("p", { hidden: true }),
    "approval-error": createElement("p", { hidden: true }),
    "approval-done": createElement("p", { hidden: true }),
    "approval-submit": createElement("button", { textContent: "승인" }),
    "approval-cancel": createElement("button", { textContent: "취소" }),
  };
  // 기존 검증은 최근 응답을 읽고, 대화 검증은 전체 순서를 확인한다.
  for (const name of ["notice", "answer", "drafts", "rules", "rules-list", "news", "news-list"]) {
    Object.defineProperty(elements, `result-${name}`, { get: () => conversation.children.at(-1)?.parts[name] });
  }
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
    const key = `${method} ${path.split("?")[0]}`;
    const handler = handlers[key];
    if (!handler) throw new Error(`unexpected request: ${key}`);
    return handler(path, options);
  };
  return { fetch, calls };
}

const REVIEW_GET = "GET /api/v1/reviews/r-1";
const QUERY_POST = "POST /api/v1/reviews/r-1/queries";


async function start(handlers, { reviewId = "r-1", setup = () => {}, confirmAnswer = true } = {}) {
  const page = createPage();
  setup(page.elements);
  const navigations = [];
  const { fetch, calls } = createFetch({ "GET /api/v1/auth/me": () => ME, ...handlers });
  const controller = createReviewDetailController({
    document: page.document,
    fetch,
    navigate: (path) => navigations.push(path),
    reviewId,
    confirm: () => confirmAnswer,
  });
  await controller.initialize();
  return { ...page, calls, navigations };
}


const ask = async (elements, text) => {
  elements.question.value = text;
  await elements["query-form"].listeners.submit({ preventDefault() {} });
};
const draftNodes = (elements) => elements["result-drafts"].children;
const reviewCalls = (calls) => calls.filter((call) => call.path === "/api/v1/reviews/r-1");
const queryCalls = (calls) => calls.filter((call) => call.method === "POST");


// AC-03
test("shows the review summary with status label, query count and KST creation time", async () => {
  const { elements, calls } = await start({ [REVIEW_GET]: () => json(200, review({ status: "open", query_count: 3 })) });

  assert.equal(calls[1].path, "/api/v1/reviews/r-1");
  assert.equal(calls[1].options.credentials, "same-origin");
  assert.equal(elements["detail-loading"].hidden, true);
  assert.equal(elements["detail-body"].hidden, false);
  assert.equal(elements["current-user"].textContent, "심사역님");
  assert.equal(elements["review-name"].textContent, "삼성전자");
  assert.match(elements["review-code"].textContent, /00126380/);
  assert.match(elements["review-year"].textContent, /2025/);
  assert.equal(elements["review-status"].textContent, "진행 가능");
  assert.equal(elements["review-count"].textContent, "질의 3회");
  assert.match(elements["review-created"].textContent, /2026-11-04 12:34/);
  assert.equal(elements["query-hint"].hidden, true);
  assert.equal(elements.question.disabled, false);
  assert.equal(elements["query-submit"].disabled, false);
});


// AC-04
test("sends the normalized question and shows the answer and verdicts in server order, then refreshes the review", async () => {
  const drafts = [
    draftItem({ item: "litigation", verdict: "not_applicable", reason: "소송 없음" }),
    draftItem({ item: "guarantee_collateral", verdict: "applicable", reason: "담보 제공 있음" }),
    draftItem({ item: "fx_raw_material", verdict: "needs_check", reason: "근거 부족" }),
  ];
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, review({ query_count: reviewReads++ === 0 ? 3 : 4 })),
    [QUERY_POST]: () => json(200, answered({ draft: drafts })),
  });

  await ask(elements, "  소송이   있나요?  ");

  const post = queryCalls(calls)[0];
  assert.equal(post.path, "/api/v1/reviews/r-1/queries");
  assert.equal(post.options.credentials, "same-origin");
  assert.deepEqual(JSON.parse(post.options.body), { question: "소송이 있나요?" });
  assert.equal(elements["review-conversation"].hidden, false);
  assert.equal(elements["result-answer"].hidden, false);
  assert.equal(elements["result-answer"].textContent, "소송 항목은 비해당으로 보입니다.");
  assert.equal(draftNodes(elements).length, 3);
  const texts = draftNodes(elements).map(allText);
  assert.match(texts[0], /소송/);
  assert.match(texts[0], /비해당/);
  assert.match(texts[0], /소송 없음/);
  assert.match(texts[1], /보증·담보/);
  assert.match(texts[1], /해당/);
  assert.match(texts[2], /환율·원자재/);
  assert.match(texts[2], /확인 필요/);
  assert.equal(reviewCalls(calls).length, 2);
  assert.equal(elements["review-count"].textContent, "질의 4회");
  assert.equal(elements["query-submit"].disabled, false);
  assert.equal(elements["query-submit"].textContent, "추가 질문 보내기");
  assert.equal(elements["query-error"].hidden, true);
});

test("shows the cited amount in won in the analysis result", async () => {
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({
      reason: "163,578,745",
      citations: [citation({ quote: "(단위 : 천원) 유동 은행차입금 | 163,578,745" })],
    })] })),
  });

  await ask(elements, "단기차입금은?");
  assert.match(allText(draftNodes(elements)[0]), /163,578,745,000원/);
});


// AC-05
test("shows chapter, note number, receipt number and verification label for each citation, with the quote as text", async () => {
  const citations = [
    citation({ chapter: "NOTES", note_no: "27", quote: "인용문 하나", verification: { passed: true, failed_checks: [] } }),
    citation({ chunk_id: "c-2", chapter: "II", note_no: null, quote: "인용문 둘", verification: { passed: false, failed_checks: ["quote_not_in_chunk", "number_not_found"] } }),
  ];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({ citations })] })),
  });

  await ask(elements, "소송?");

  const text = allText(draftNodes(elements)[0]);
  assert.match(text, /NOTES/);
  assert.match(text, /27/);
  assert.match(text, /20260310000123/);
  assert.match(text, /인용문 하나/);
  assert.match(text, /인용문 둘/);
  assert.match(text, /인용 검증 통과/);
  assert.match(text, /인용 검증 실패 \(2건\)/);
  assert.doesNotMatch(text, /quote_not_in_chunk|number_not_found/);
  const details = descendants(draftNodes(elements)[0]).filter((node) => node.tagName === "DETAILS");
  assert.equal(details.length, 2);
});


// 거절 뉴스 참고(docs/specs/refusal-news-search.md): 서버가 준 안내 문구와 뉴스 최대 5건을 근거와 다른 영역에 보인다
test("shows the server refusal message and up to five reference news items as text, linking only http(s) urls", async () => {
  const item = (n, extra = {}) => ({
    title: `뉴스 ${n} <b>굵게</b>`, originallink: `https://press${n}.example/a`, link: `https://n.news.naver.com/${n}`,
    description: "본문", pub_date: "Mon, 10 Nov 2026 09:00:00 +0900", source_domain: `press${n}.example`, ...extra,
  });
  const news = [item(0), item(1, { originallink: "javascript:alert(1)", link: "ftp://x/y" }), item(2), item(3), item(4), item(5)];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({
      outcome: "refused", refusal_reason: "CORP_NOT_IN_CORPUS", answer: "", draft: [],
      refusal_kind: "work_related", news_search: "ok", news_items: news,
      refusal_message: "참고용으로 관련 최신 뉴스를 함께 안내합니다. 뉴스는 공시 근거가 아니며 심사 판정과 메모 저장에는 사용되지 않습니다.",
    })),
  });

  await ask(elements, "삼성전자 채무보증 내역은?");

  assert.match(elements["result-notice"].textContent, /뉴스는 공시 근거가 아니며/);
  assert.equal(elements["result-news"].hidden, false);
  const rows = elements["result-news-list"].children;
  assert.equal(rows.length, 5);  // 6건이 와도 5건만
  assert.equal(rows[0].children[0].tagName, "A");
  assert.equal(rows[0].children[0].href, "https://press0.example/a");
  assert.equal(rows[0].children[0].getAttribute("rel"), "noopener noreferrer");
  assert.equal(rows[0].children[0].textContent, "뉴스 0 <b>굵게</b>");  // HTML로 해석하지 않고 글자 그대로
  assert.match(rows[0].children[1].textContent, /press0\.example · 2026-11-10$/);  // 영문 날짜는 YYYY-MM-DD로(D1)
  assert.equal(rows[1].children[0].tagName, "SPAN");  // http(s)가 아닌 주소는 링크로 만들지 않는다
  assert.equal(rows[1].children[0].href, undefined);
  assert.equal(draftNodes(elements).length, 0);  // 뉴스는 초안·인용 영역에 들어가지 않는다
});


// 날짜 형식(docs/specs/refusal-news-display.md D1·D2)
test("shows the news date as YYYY-MM-DD in Korean time and keeps an unreadable date as it is", async () => {
  const item = (n, pub_date) => ({ title: `뉴스 ${n}`, link: `https://n.example/${n}`, source_domain: "press.example", pub_date });
  const news = [
    item(0, "Wed, 23 Sep 2026 07:02:00 +0900"),
    item(1, "Tue, 10 Nov 2026 16:30:00 +0000"),  // UTC 16:30은 한국 시간으로 다음 날 01:30
    item(2, "날짜 아님"),
  ];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({
      outcome: "refused", refusal_reason: "CORP_NOT_IN_CORPUS", answer: "", draft: [],
      refusal_kind: "work_related", news_search: "ok", news_items: news, refusal_message: "안내",
    })),
  });

  await ask(elements, "삼성전자 채무보증 내역은?");

  const rows = elements["result-news-list"].children;
  assert.equal(rows[0].children[1].textContent, "press.example · 2026-09-23");
  assert.equal(rows[1].children[1].textContent, "press.example · 2026-11-11");
  assert.equal(rows[2].children[1].textContent, "press.example · 날짜 아님");
});


// 기사 요약(docs/specs/refusal-news-display.md D3·D4)
test("shows the news summary as plain text under the meta line, cut at 120 characters, and skips it when missing", async () => {
  const long = "가".repeat(130);
  const item = (n, description) => ({ title: `뉴스 ${n}`, link: `https://n.example/${n}`, source_domain: "press.example",
    pub_date: "Mon, 10 Nov 2026 09:00:00 +0900", description });
  const news = [item(0, "짧은 <b>요약</b>"), item(1, long), item(2, undefined), item(3, ""), item(4, 123)];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({
      outcome: "refused", refusal_reason: "CORP_NOT_IN_CORPUS", answer: "", draft: [],
      refusal_kind: "work_related", news_search: "ok", news_items: news, refusal_message: "안내",
    })),
  });

  await ask(elements, "삼성전자 채무보증 내역은?");

  const rows = elements["result-news-list"].children;
  assert.equal(rows[0].children.length, 3);
  assert.equal(rows[0].children[2].textContent, "짧은 <b>요약</b>");  // HTML로 해석하지 않고 글자 그대로
  assert.equal(rows[0].children[2].className, "news-reference-summary");
  assert.equal(rows[1].children[2].textContent, "가".repeat(120) + "…");  // 120자에서 자르고 …
  assert.equal(rows[2].children.length, 2);  // 요약이 없으면 요약 줄을 만들지 않는다
  assert.equal(rows[3].children.length, 2);
  assert.equal(rows[4].children.length, 2);  // 문자열이 아닌 값도 만들지 않는다
});


test("hides the news area when the refusal has no news, and clears it on the next non-refused result", async () => {
  const responses = [
    answered({ outcome: "refused", refusal_reason: "CORP_NOT_IN_CORPUS", answer: "", draft: [], refusal_kind: "work_related",
               news_search: "ok", news_items: [{ title: "뉴스", link: "https://n.example/1" }], refusal_message: "안내" }),
    answered({ outcome: "refused", refusal_reason: "OUT_OF_DOMAIN", answer: "", draft: [], refusal_kind: "work_related",
               news_search: "error", news_items: [], refusal_message: "뉴스 검색도 지금은 사용할 수 없습니다." }),
    answered({ outcome: "refused", refusal_reason: "OUT_OF_DOMAIN", answer: "", draft: [], news_items: "깨진 값" }),
    answered(),
  ];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, responses.shift()),
  });

  await ask(elements, "질문 1");
  assert.equal(elements["result-news"].hidden, false);
  await ask(elements, "질문 2");
  assert.equal(elements["result-news"].hidden, true);
  assert.match(elements["result-notice"].textContent, /뉴스 검색도 지금은 사용할 수 없습니다/);
  await ask(elements, "질문 3");
  assert.equal(elements["result-news"].hidden, true);
  assert.match(elements["result-notice"].textContent, /답변할 수 없는 질문입니다/);  // 안내 문구가 없으면 기존 문구
  await ask(elements, "질문 4");
  assert.equal(elements["result-news"].hidden, true);
  assert.equal(elements["result-news-list"].children.length, 0);
});


// AC-06
test("shows a refusal notice for each refusal_reason as a normal result, not an error", async () => {
  const reasons = {
    OUT_OF_DOMAIN: /관련 없는 질문/,
    CORP_NOT_IN_CORPUS: /회사의 공시가 아직 수집되지 않았습니다/,
    ITEM_NOT_DISCLOSED: /공시 근거를 찾지 못했습니다/,
    PERIOD_NOT_IN_CORPUS: /사업연도의 공시가 수집되지 않았습니다/,
    PRIOR_YEAR_NOT_IN_CORPUS: /전년도 공시가 수집되지 않았습니다/,
    SOMETHING_NEW: /답변할 수 없는 질문입니다/,
  };
  for (const [reason, pattern] of Object.entries(reasons)) {
    const { elements } = await start({
      [REVIEW_GET]: () => json(200, review()),
      [QUERY_POST]: () => json(200, answered({ outcome: "refused", refusal_reason: reason, answer: "", draft: [] })),
    });

    await ask(elements, "오늘 날씨는?");

    assert.equal(elements["review-conversation"].hidden, false, reason);
    assert.equal(elements["result-notice"].hidden, false, reason);
    assert.match(elements["result-notice"].textContent, /답변할 수 없는 질문입니다/, reason);
    assert.match(elements["result-notice"].textContent, pattern, reason);
    assert.equal(elements["result-answer"].hidden, true, reason);
    assert.equal(draftNodes(elements).length, 0, reason);
    assert.equal(elements["query-error"].hidden, true, reason);
    assert.equal(elements["detail-error"].hidden, true, reason);
  }
});


// AC-17
test("lists the rules the draft consulted, in server order, as text with a link to manage them", async () => {
  const appliedRules = [
    { rule_id: "m-1:borrowing_structure", item: "borrowing_structure", instruction: "단기차입금 비중 50% 초과면 해당", source_memo_id: "m-1" },
    { rule_id: "m-2:x", item: "constructor", instruction: "<img src=x onerror=alert(1)>", source_memo_id: "m-2" },
  ];
  for (const outcome of ["answered", "awaiting_approval"]) {
    const pending = outcome === "awaiting_approval";
    let reviewReads = 0;
    const { elements } = await start({
      [REVIEW_GET]: () => json(200, pending && reviewReads++ > 0
        ? awaitingReview({ pending_applied_rules: appliedRules })
        : review()),
      [QUERY_POST]: () => json(200, answered({ outcome, applied_rules: appliedRules })),
    });

    await ask(elements, "차입 구조는?");

    const section = pending ? "approval-rules" : "result-rules";
    const ruleList = elements[section + "-list"];
    assert.equal(elements[section].hidden, false, outcome);
    assert.equal(elements[pending ? "result-rules" : "approval-rules"].hidden, true, outcome);
    const rows = ruleList.children;
    assert.equal(rows.length, 2, outcome);
    assert.match(allText(rows[0]), /차입 구조/);
    assert.match(allText(rows[0]), /단기차입금 비중 50% 초과면 해당/);
    assert.match(allText(rows[1]), /알 수 없음/);
    assert.match(allText(rows[1]), /<img src=x onerror=alert\(1\)>/);
    assert.equal(descendants(ruleList).some((node) => node.tagName === "IMG"), false);
  }
});


// AC-18
test("hides the consulted-rules area when there are none, the field is malformed, or the question was refused", async () => {
  const responses = [
    answered({ applied_rules: [] }),
    answered({ applied_rules: undefined }),
    answered({ applied_rules: "nope" }),
    answered({ outcome: "refused", refusal_reason: "OUT_OF_DOMAIN", answer: "", draft: [], applied_rules: [{ item: "litigation", instruction: "x" }] }),
  ];
  for (const body of responses) {
    const { elements } = await start({
      [REVIEW_GET]: () => json(200, review()),
      [QUERY_POST]: () => json(200, body),
    });

    await ask(elements, "질문");

    assert.equal(elements["result-rules"].hidden, true, JSON.stringify(body.applied_rules));
    assert.equal(elements["result-rules-list"].children.length, 0);
  }

  // 규칙이 보인 뒤 다음 질의가 거절되면 이전 규칙이 남지 않는다.
  const bodies = [
    answered({ applied_rules: [{ item: "litigation", instruction: "소송 기준" }] }),
    answered({ outcome: "refused", refusal_reason: "OUT_OF_DOMAIN", answer: "", draft: [] }),
  ];
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, bodies.shift()),
  });
  await ask(elements, "첫 질문");
  assert.equal(elements["result-rules"].hidden, false);
  await ask(elements, "두 번째 질문");
  assert.equal(elements["result-rules"].hidden, true);
  assert.equal(elements["result-rules-list"].children.length, 0);
  const turns = elements["review-conversation"].children;
  assert.deepEqual(turns.map(({ parts }) => parts.question.textContent), ["첫 질문", "두 번째 질문"]);
  assert.equal(turns[0].parts["rules-list"].children.length, 1);
  assert.match(turns[1].parts.notice.textContent, /답변할 수 없는 질문입니다/);
  assert.equal(elements.question.value, "");
});


// AC-07
test("shows the draft with an approval-pending notice and leaves the follow-up composer enabled", async () => {
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, review(reviewReads++ === 0 ? {} : { status: "awaiting_approval", query_count: 4 })),
    [QUERY_POST]: () => json(200, answered({ outcome: "awaiting_approval" })),
  });

  await ask(elements, "소송?");

  assert.equal(elements["result-answer"].textContent, "소송 항목은 비해당으로 보입니다.");
  assert.equal(draftNodes(elements).length, 1);
  assert.match(elements["result-notice"].textContent, /검토할 초안/);
  assert.equal(elements["query-form"].hidden, false);
  assert.equal(elements.question.disabled, false);
  assert.equal(elements["query-submit"].disabled, false);
  assert.equal(calls.some((call) => call.path.includes("approvals")), false);
});


test("uses the follow-up endpoint after an approval-pending result even if refreshing the review fails", async () => {
  let reviewReads = 0;
  const followUp = "추가 근거는?";
  const { elements, calls } = await start({
    [REVIEW_GET]: () => (reviewReads++ === 0 ? json(200, review()) : json(500, {})),
    [QUERY_POST]: () => json(200, answered({ outcome: "awaiting_approval" })),
    [QUERY_POST + ":follow-up"]: () => json(200, answered({
      answer: "추가 답변",
      draft: [draftItem({ item: followUp, verdict: "needs_check", reason: "당기·전기 확인 필요" })],
    })),
  });

  await ask(elements, "소송?");

  assert.equal(elements.question.disabled, false);
  assert.equal(elements["query-submit"].disabled, false);
  assert.equal(draftNodes(elements).length, 1);
  await ask(elements, followUp);
  assert.equal(queryCalls(calls).at(-1).path, "/api/v1/reviews/r-1/queries:follow-up");
  assert.equal(elements["result-answer"].textContent, "추가 답변");
  assert.match(allText(draftNodes(elements).at(-1)), /추가 질문 답변.*확인 필요/);
  assert.equal(elements["approval-question-text"].textContent, "소송?");
});


// AC-08
test("blocks sending for running, saving, closed and unknown reviews", async () => {
  const cases = [
    [{ status: "running" }, /처리 중인 질의/],
    [{ status: "saving" }, /저장 중입니다/],
    [{ status: "closed" }, /승인이 끝나 종료된 심사 건입니다.*새 심사 건/],
    [{ status: "mystery" }, /질문할 수 없는 상태/],
  ];
  for (const [overrides, pattern] of cases) {
    const { elements, calls } = await start({ [REVIEW_GET]: () => json(200, review(overrides)) });

    assert.equal(elements.question.disabled, true, JSON.stringify(overrides));
    assert.equal(elements["query-submit"].disabled, true, JSON.stringify(overrides));
    assert.equal(elements["query-hint"].hidden, false, JSON.stringify(overrides));
    assert.match(elements["query-hint"].textContent, pattern, JSON.stringify(overrides));

    await ask(elements, "소송?");
    assert.equal(queryCalls(calls).length, 0, JSON.stringify(overrides));
  }
});


// AC-09
test("does not send blank or over-long questions and shows the validation message", async () => {
  const { elements, calls } = await start({ [REVIEW_GET]: () => json(200, review()) });

  for (const text of ["", "   \n  ", "가".repeat(501)]) {
    await ask(elements, text);
    assert.equal(elements["query-error"].hidden, false);
    assert.equal(elements["query-error"].textContent, M.question);
  }
  assert.equal(queryCalls(calls).length, 0);
  assert.equal(elements.question.focused, true);
});


test("accepts a question of exactly 500 characters", async () => {
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered()),
  });

  await ask(elements, "가".repeat(500));

  assert.equal(queryCalls(calls).length, 1);
  assert.equal(elements["query-error"].hidden, true);
});


// AC-10
test("redirects to the login page on 401 from me, the review or the query", async () => {
  const meExpired = await start({ "GET /api/v1/auth/me": () => json(401, {}) });
  assert.deepEqual(meExpired.navigations, ["/"]);

  const reviewExpired = await start({ [REVIEW_GET]: () => json(401, {}) });
  assert.deepEqual(reviewExpired.navigations, ["/"]);

  const queryExpired = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(401, {}),
  });
  await ask(queryExpired.elements, "소송?");
  assert.deepEqual(queryExpired.navigations, ["/"]);
});


test("shows a not-found notice with the list link and hides the question area on 404 or an invalid id", async () => {
  const missing = await start({ [REVIEW_GET]: () => json(404, { error: { code: "NOT_FOUND", details: { secret: "x" } } }) });
  assert.equal(missing.elements["detail-missing"].hidden, false);
  assert.equal(missing.elements["detail-body"].hidden, true);
  assert.equal(missing.elements["query-section"].hidden, true);
  assert.equal(missing.elements["detail-error"].hidden, true);
  assert.equal(missing.elements["detail-loading"].hidden, true);

  const invalid = await start({}, { reviewId: null });
  assert.equal(invalid.elements["detail-missing"].hidden, false);
  assert.equal(invalid.elements["query-section"].hidden, true);
  assert.equal(reviewCalls(invalid.calls).length, 0);
});


// AC-11
test("shows the matching message, keeps the question and re-enables the button on query failures", async () => {
  const cases = [
    [() => { throw new Error("offline"); }, M.network],
    [() => json(500, { error: { message: "internal", request_id: "req-9" } }), M.server],
    ...[502, 503, 504].map((status) => [() => json(status, {}), M[status]]),
    [() => json(429, {}), M.tooMany],
    [() => json(400, {}), M.badInput],
    [() => json(418, {}), M.queryFailed],
    [() => json(200, "not-an-object"), M.queryFailed],
  ];
  for (const [handler, message] of cases) {
    const { elements } = await start({
      [REVIEW_GET]: () => json(200, review()),
      [QUERY_POST]: handler,
    });

    await ask(elements, "소송이 있나요?");

    assert.equal(elements["query-error"].hidden, false, message);
    assert.equal(elements["query-error"].textContent, message);
    assert.equal(elements.question.value, "소송이 있나요?");
    assert.equal(elements["query-submit"].disabled, false);
  assert.equal(elements["query-submit"].textContent, "추가 질문 보내기");
    assert.doesNotMatch(allText(elements["query-error"]), /internal|req-9/);
  }
});


test("reloads the review when the query hits a state conflict (409)", async () => {
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, review(reviewReads++ === 0 ? {} : { status: "running" })),
    [QUERY_POST]: () => json(409, { error: { code: "CONFLICT_NOT_AWAITING_APPROVAL", details: { x: 1 } } }),
  });

  await ask(elements, "소송?");

  assert.equal(elements["query-error"].textContent, M.conflict);
  assert.equal(reviewCalls(calls).length, 2);
  assert.equal(elements["review-status"].textContent, "처리 중");
  assert.equal(elements.question.disabled, true);
});


// AC-12
test("shows a load error with a reload button and retries on click", async () => {
  const outcomes = [
    () => { throw new Error("offline"); },
    () => json(500, {}),
    () => json(200, "bad"),
  ];
  for (const first of outcomes) {
    let reads = 0;
    const { elements, calls } = await start({
      [REVIEW_GET]: () => (reads++ === 0 ? first() : json(200, review())),
    });

    assert.equal(elements["detail-error"].hidden, false);
    assert.equal(elements["detail-error"].textContent, M.loadFailed);
    assert.equal(elements["detail-reload"].hidden, false);
    assert.equal(elements["detail-loading"].hidden, true);

    await elements["detail-reload"].listeners.click();

    assert.equal(elements["detail-error"].hidden, true);
    assert.equal(elements["detail-reload"].hidden, true);
    assert.equal(elements["detail-body"].hidden, false);
    assert.equal(reviewCalls(calls).length, 2);
  }
});

test("detail page distinguishes first-load 502, 503 and 504 responses", async () => {
  for (const status of [502, 503, 504]) {
    const { elements } = await start({ [REVIEW_GET]: () => json(status, { details: "secret" }) });
    assert.equal(elements["detail-error"].textContent, M[status]);
    assert.equal(elements["detail-reload"].hidden, false);
    assert.equal(elements["detail-loading"].hidden, true);
    assert.doesNotMatch(elements["detail-error"].textContent, /secret/);
  }
});


// AC-13
test("renders question-answer text, reasons, quotes and company names as plain text", async () => {
  const payload = "<img src=x onerror=alert(1)>";
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review({ corp_name: payload })),
    [QUERY_POST]: () =>
      json(200, answered({
        answer: payload,
        draft: [draftItem({ reason: payload, citations: [citation({ quote: payload, chapter: payload, rcept_no: payload })] })],
      })),
  });

  await ask(elements, payload);

  assert.equal(elements["review-name"].textContent, payload);
  assert.equal(elements["result-answer"].textContent, payload);
  assert.match(allText(draftNodes(elements)[0]), /<img src=x onerror=alert\(1\)>/);
  const tags = descendants(elements["result-drafts"]).map((node) => node.tagName);
  assert.equal(tags.includes("IMG"), false);
});


// AC-14
test("shows an unknown label for unknown status, outcome, item and verdict without breaking", async () => {
  const weird = await start({
    [REVIEW_GET]: () => json(200, review({ status: "mystery" })),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({ item: "new_item", verdict: "maybe" })] })),
  });
  assert.equal(weird.elements["review-status"].textContent, M.unknown);

  const open = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({ item: "new_item", verdict: "maybe" })] })),
  });
  await ask(open.elements, "소송?");
  const text = allText(draftNodes(open.elements)[0]);
  assert.equal((text.match(/알 수 없음/g) ?? []).length >= 2, true);
  const verdict = descendants(draftNodes(open.elements)[0]).find((node) => node.className.includes("draft-verdict"));
  assert.match(verdict.className, /verdict-unknown/);

  const outcome = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ outcome: "teleported" })),
  });
  await ask(outcome.elements, "소송?");
  assert.equal(outcome.elements["result-notice"].hidden, false);
  assert.match(outcome.elements["result-notice"].textContent, /알 수 없는 응답/);
  assert.equal(draftNodes(outcome.elements).length, 0);
});


// EC-13
test("ignores a second submit while a query is in flight", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: async () => {
      await gate;
      return json(200, answered());
    },
  });

  const first = ask(elements, "소송?");
  await Promise.resolve();
  assert.equal(elements["query-submit"].disabled, true);
  assert.equal(elements["query-submit"].textContent, "분석 중…");
  const second = ask(elements, "소송?");
  await Promise.resolve();
  release();
  await Promise.all([first, second]);

  assert.equal(queryCalls(calls).length, 1);
});


test("logs out and returns to the login page, or shows an error on failure", async () => {
  const ok = await start({
    [REVIEW_GET]: () => json(200, review()),
    "POST /api/v1/auth/logout": () => ({ status: 204 }),
  });
  await ok.elements["logout-button"].listeners.click();
  assert.deepEqual(ok.navigations, ["/"]);

  const failed = await start({
    [REVIEW_GET]: () => json(200, review()),
    "POST /api/v1/auth/logout": () => json(500, {}),
  });
  await failed.elements["logout-button"].listeners.click();
  assert.deepEqual(failed.navigations, []);
  assert.equal(failed.elements["detail-error"].hidden, false);
  assert.match(failed.elements["detail-error"].textContent, /로그아웃하지 못했습니다/);
});


test("encodes the review id in every request path", async () => {
  const handlers = {
    "GET /api/v1/reviews/a%2Fb%3F": () => json(200, review({ id: "a/b?" })),
    "POST /api/v1/reviews/a%2Fb%3F/queries": () => json(200, answered()),
  };
  const { calls, elements } = await start(handlers, { reviewId: "a/b?" });
  await ask(elements, "소송?");

  assert.equal(calls.some((call) => call.path === "/api/v1/reviews/a%2Fb%3F/queries"), true);
  assert.equal(calls.some((call) => call.path.includes("a/b?")), false);
});


test("treats object prototype names as unknown values, not as labels", async () => {
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review({ status: "constructor" })),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({ item: "toString", verdict: "__proto__" })] })),
  });
  assert.equal(elements["review-status"].textContent, M.unknown);
  assert.equal(elements.question.disabled, true);

  const open = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ outcome: "refused", refusal_reason: "constructor", draft: [] })),
  });
  await ask(open.elements, "소송?");
  assert.doesNotMatch(open.elements["result-notice"].textContent, /function|Object/);
  assert.match(open.elements["result-notice"].textContent, /답변할 수 없는 질문입니다/);

  const drafts = await start({
    [REVIEW_GET]: () => json(200, review()),
    [QUERY_POST]: () => json(200, answered({ draft: [draftItem({ item: "toString", verdict: "__proto__" })] })),
  });
  await ask(drafts.elements, "소송?");
  const text = allText(draftNodes(drafts.elements)[0]);
  assert.equal((text.match(/알 수 없음/g) ?? []).length >= 2, true);
  assert.doesNotMatch(text, /function|Object/);
});


const APPROVALS_POST = "POST /api/v1/reviews/r-1/approvals";
const CANCEL_POST = "POST /api/v1/reviews/r-1/approvals:cancel";
const approvalFieldsets = (elements) => descendants(elements["approval-list"]).filter((node) => node.tagName === "FIELDSET");
const approvalCalls = (calls) => calls.filter((call) => call.path.includes("/approvals"));
const awaitingReview = (extra = {}) => review({ status: "awaiting_approval", pending_draft: [draftItem()], ...extra });


// approval-ui AC-01
test("opens the approval panel with the draft from the query answer and drops the old 'coming soon' notice", async () => {
  let reviewReads = 0;
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, review(reviewReads++ === 0 ? {} : { status: "awaiting_approval", query_count: 4 })),
    [QUERY_POST]: () => json(200, answered({ outcome: "awaiting_approval", draft: [draftItem(), draftItem({ item: "guarantee_collateral" })] })),
  });

  assert.equal(elements["approval-section"].hidden, true);
  await ask(elements, "소송?");

  assert.equal(elements["approval-section"].hidden, false);
  assert.equal(elements["approval-question-text"].textContent, "소송?");
  assert.equal(approvalFieldsets(elements).length, 2);
  assert.doesNotMatch(elements["result-notice"].textContent, /준비 중/);
  assert.doesNotMatch(elements["query-hint"].textContent, /준비 중/);
  assert.equal(elements.question.disabled, false);
});


// approval-ui AC-10 (contract assumption 1: pending_draft on the review)
test("reopening an awaiting review uses pending_draft from the review, and shows only cancel without it", async () => {
  const originalQuestion = "오뚜기의 계류 중인 소송은 무엇인가요?";
  const withDraft = await start({ [REVIEW_GET]: () => json(200, awaitingReview({ pending_question: originalQuestion })) });
  assert.equal(withDraft.elements["approval-section"].hidden, false);
  assert.equal(approvalFieldsets(withDraft.elements).length, 1);
  assert.equal(withDraft.elements["approval-submit"].hidden, false);
  assert.equal(withDraft.elements["approval-question"].hidden, false);
  assert.equal(withDraft.elements["approval-question-text"].textContent, originalQuestion);
  assert.equal(withDraft.elements["review-conversation"].children[0].parts.question.textContent, originalQuestion);

  const withoutDraft = await start({ [REVIEW_GET]: () => json(200, review({ status: "awaiting_approval" })) });
  assert.equal(withoutDraft.elements["approval-section"].hidden, false);
  assert.equal(withoutDraft.elements["approval-submit"].hidden, true);
  assert.equal(withoutDraft.elements["approval-empty"].hidden, false);
  assert.equal(withoutDraft.elements["approval-question"].hidden, true);
  assert.equal(withoutDraft.elements["approval-rules"].hidden, true);
});


test("restores the first draft rules separately from the rules used by a follow-up", async () => {
  const firstRule = { item: "borrowing_structure", instruction: "당기 연결 차입금 기준" };
  const followUpRule = { item: "litigation", instruction: "중요 소송 주석 기준" };
  const pending = awaitingReview({
    pending_question: "단기차입금은 얼마인가요?",
    pending_applied_rules: [firstRule],
  });
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, pending),
    [QUERY_POST + ":follow-up"]: () => json(200, answered({ applied_rules: [followUpRule] })),
  });

  assert.equal(elements["approval-rules"].hidden, false);
  assert.match(allText(elements["approval-rules-list"]), /당기 연결 차입금 기준/);
  assert.equal(elements["result-rules"].hidden, true);

  await ask(elements, "주요 소송은 무엇인가요?");

  assert.equal(elements["approval-rules-list"].children.length, 1);
  assert.match(allText(elements["approval-rules-list"]), /당기 연결 차입금 기준/);
  assert.equal(elements["result-rules"].hidden, false);
  assert.match(allText(elements["result-rules-list"]), /중요 소송 주석 기준/);
  assert.doesNotMatch(allText(elements["result-rules-list"]), /당기 연결 차입금 기준/);
});


test("the panel stays closed for every status other than awaiting_approval", async () => {
  for (const status of ["open", "running", "saving", "closed", "mystery"]) {
    const { elements } = await start({ [REVIEW_GET]: () => json(200, review({ status, pending_draft: [draftItem()] })) });
    assert.equal(elements["approval-section"].hidden, true, status);
  }
});


// approval-ui AC-04
test("approving reloads the review, which is now closed and takes no more questions", async () => {
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, reviewReads++ === 0 ? awaitingReview() : review({ query_count: 4, status: "closed" })),
    [APPROVALS_POST]: () => json(200, { review_id: "r-1", approval_seq: 1, memo_id: "m-1" }),
  });

  await elements["approval-submit"].listeners.click();

  assert.equal(approvalCalls(calls).length, 1);
  assert.equal(reviewCalls(calls).length, 2);
  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["approval-done"].textContent, "심사 메모로 저장했습니다.");
  assert.equal(elements["review-status"].textContent, "종료");  // 승인이 끝난 건은 닫힌다(SPEC §4)
  assert.equal(elements.question.disabled, true);
  assert.match(elements["query-hint"].textContent, /새 심사 건/);
});


test("a review with a large lifetime query count is still askable, since the daily limit is counted by the server", async () => {
  const { elements } = await start({ [REVIEW_GET]: () => json(200, review({ query_count: 137 })) });

  assert.equal(elements.question.disabled, false);
  assert.equal(elements["query-hint"].hidden, true);
  assert.equal(elements["review-count"].textContent, "질의 137회");
});


test("a failed reload after approving shows the reload button and does not approve twice", async () => {
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => (reviewReads++ === 0 ? json(200, awaitingReview()) : json(500, {})),
    [APPROVALS_POST]: () => json(200, {}),
  });

  await elements["approval-submit"].listeners.click();

  assert.equal(approvalCalls(calls).length, 1);
  assert.equal(elements["approval-done"].textContent, "저장했습니다. 상태를 새로 불러오지 못했습니다.");
  assert.equal(elements["detail-error"].textContent, M.loadFailed);
  assert.equal(elements["detail-reload"].hidden, false);
});


// approval-ui AC-05
test("cancelling returns the review to open without a stored memo", async () => {
  let reviewReads = 0;
  const initialQuestion = "진행 중인 소송은 무엇인가요?";
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, reviewReads++ === 0 ? awaitingReview({ pending_question: initialQuestion }) : review()),
    [CANCEL_POST]: () => json(204, null),
    [QUERY_POST]: () => json(200, answered()),
  });

  const followUp = "담보 내역도 알려주세요.";
  elements.question.value = followUp;
  await elements["approval-cancel"].listeners.click();

  assert.equal(approvalCalls(calls).length, 1);
  assert.equal(calls.find((call) => call.path.includes(":cancel")).options.body, undefined);
  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["approval-done"].textContent, "초안을 취소했습니다. 저장된 메모는 없습니다.");
  assert.equal(elements.question.disabled, false);
  assert.equal(elements["query-form"].hidden, false);
  assert.equal(elements["query-submit"].disabled, false);
  assert.equal(elements.question.value, followUp);
  await elements["query-form"].listeners.submit({ preventDefault() {} });
  assert.deepEqual(elements["review-conversation"].children.map(({ parts }) => parts.question.textContent), [
    initialQuestion,
    followUp,
  ]);
});


test("declined confirmations send nothing", async () => {
  const { elements, calls } = await start({ [REVIEW_GET]: () => json(200, awaitingReview()) }, { confirmAnswer: false });

  await elements["approval-submit"].listeners.click();
  await elements["approval-cancel"].listeners.click();

  assert.equal(approvalCalls(calls).length, 0);
});


// approval-ui AC-07
test("a 409 on approve reloads the review and does not resend", async () => {
  let reviewReads = 0;
  const { elements, calls } = await start({
    [REVIEW_GET]: () => json(200, reviewReads++ === 0 ? awaitingReview() : review()),
    [APPROVALS_POST]: () => json(409, { error: { code: "CONFLICT_NOT_AWAITING_APPROVAL" } }),
  });

  await elements["approval-submit"].listeners.click();

  assert.equal(approvalCalls(calls).length, 1);
  assert.equal(reviewCalls(calls).length, 2);
  assert.equal(elements["approval-error"].textContent, "이미 처리된 승인입니다. 화면을 새로 불러옵니다");
  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["review-status"].textContent, "진행 가능");
});


// approval-ui AC-08
test("401 during approve goes to the login page and 404 shows the missing view", async () => {
  const unauthorized = await start({
    [REVIEW_GET]: () => json(200, awaitingReview()),
    [APPROVALS_POST]: () => json(401, {}),
  });
  await unauthorized.elements["approval-submit"].listeners.click();
  assert.deepEqual(unauthorized.navigations, ["/"]);

  const missingCase = await start({
    [REVIEW_GET]: () => json(200, awaitingReview()),
    [APPROVALS_POST]: () => json(404, {}),
  });
  await missingCase.elements["approval-submit"].listeners.click();
  assert.equal(missingCase.elements["detail-missing"].hidden, false);
  assert.equal(missingCase.elements["detail-body"].hidden, true);
});


test("asking a new question clears the previous approval message", async () => {
  let reviewReads = 0;
  const { elements } = await start({
    [REVIEW_GET]: () => json(200, reviewReads++ === 0 ? awaitingReview() : review()),
    [CANCEL_POST]: () => json(204, null),
    [QUERY_POST]: () => json(200, answered()),
  });

  await elements["approval-cancel"].listeners.click();
  assert.equal(elements["approval-done"].hidden, false);
  await ask(elements, "소송?");

  assert.equal(elements["approval-done"].hidden, true);
});
