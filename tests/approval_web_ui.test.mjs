import assert from "node:assert/strict";
import test from "node:test";

import { createApprovalPanel } from "../backend/static/approval-panel.js";

const M = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  badInput: "입력 내용을 확인해 주세요.",
  failed: "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  reason: "이유를 1~1000자로 입력하세요",
  conflict: "이미 처리된 승인입니다. 화면을 새로 불러옵니다",
  approved: "심사 메모로 저장했습니다.",
  approvedStale: "저장했습니다. 상태를 새로 불러오지 못했습니다.",
  cancelled: "초안을 취소했습니다. 저장된 메모는 없습니다.",
  cancelledStale: "취소했습니다. 상태를 새로 불러오지 못했습니다.",
  noDraft: "승인할 초안을 불러오지 못했습니다. 초안을 취소하고 다시 질문해 주세요.",
  unknown: "알 수 없음",
};

const json = (status, body) => ({ status, json: async () => body });

function createElement(tagName = "div", values = {}) {
  const attributes = {};
  return {
    tagName: tagName.toUpperCase(),
    children: [],
    hidden: false,
    disabled: false,
    checked: false,
    textContent: "",
    className: "",
    value: "",
    type: "",
    name: "",
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
    set innerHTML(_) {
      throw new Error("innerHTML must not be used");
    },
  };
}

const descendants = (element) => element.children.flatMap((child) => [child, ...descendants(child)]);
const allText = (element) => [element.textContent, ...element.children.map(allText)].join(" ");
const byTag = (element, tag) => descendants(element).filter((node) => node.tagName === tag.toUpperCase());
const byClass = (element, name) => descendants(element).filter((node) => node.className.split(" ").includes(name));

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

const twoDrafts = () => [
  draftItem(),
  draftItem({ item: "guarantee_collateral", verdict: "applicable", reason: "담보 제공 내역이 있습니다." }),
];

const APPROVALS = "POST /api/v1/reviews/r-1/approvals";
const CANCEL = "POST /api/v1/reviews/r-1/approvals:cancel";

function setup({ handlers = {}, confirmAnswer = true, settleState = "ok" } = {}) {
  const elements = {
    "approval-section": createElement("section", { hidden: true }),
    "approval-question": createElement("div", { hidden: true }),
    "approval-question-text": createElement("p"),
    "approval-summary": createElement("p"),
    "approval-list": createElement("ul"),
    "approval-empty": createElement("p", { hidden: true }),
    "approval-error": createElement("p", { hidden: true }),
    "approval-done": createElement("p", { hidden: true }),
    "approval-submit": createElement("button", { textContent: "승인" }),
    "approval-cancel": createElement("button", { textContent: "취소" }),
  };
  const document = {
    getElementById: (id) => elements[id],
    createElement: (tag) => createElement(tag),
  };
  const requests = [];
  const confirms = [];
  const settled = [];
  let missing = 0;
  const panel = createApprovalPanel({
    document,
    request: async (path, options = {}) => {
      const key = `${options.method ?? "GET"} ${path}`;
      requests.push({ key, options });
      const handler = handlers[key];
      if (!handler) throw new Error(`unexpected request: ${key}`);
      return handler(path, options);
    },
    confirm: (message) => {
      confirms.push(message);
      return confirmAnswer;
    },
    renderCitation: (item) => createElement("li", { textContent: String(item.quote ?? "") }),
    approvalsPath: "/api/v1/reviews/r-1/approvals",
    onSettled: async (kind) => {
      settled.push(kind);
      return settleState;
    },
    onMissing: () => {
      missing += 1;
    },
  });
  return { elements, panel, requests, confirms, settled, missingCount: () => missing };
}

const fields = (elements) => byTag(elements["approval-list"], "fieldset");
const radios = (fieldset) => byTag(fieldset, "input").filter((node) => node.type === "radio");
const reasonBox = (fieldset) => byTag(fieldset, "textarea")[0];
const approve = (elements) => elements["approval-submit"].listeners.click();
const cancel = (elements) => elements["approval-cancel"].listeners.click();
const choose = (fieldset, verdict) => {
  const target = radios(fieldset).find((node) => node.value === verdict);
  radios(fieldset).forEach((node) => {
    node.checked = node === target;
  });
  target.listeners.change();
};
const typeReason = (fieldset, text) => {
  const box = reasonBox(fieldset);
  box.value = text;
  box.listeners.input();
};

// AC-01
test("shows each draft item as a fieldset with verdict radios, editable reason and read-only citations", () => {
  const { elements, panel } = setup();
  panel.show(twoDrafts());

  assert.equal(elements["approval-section"].hidden, false);
  assert.equal(elements["approval-empty"].hidden, true);
  const sets = fields(elements);
  assert.equal(sets.length, 2);
  assert.match(allText(byTag(sets[0], "legend")[0]), /소송/);
  assert.match(allText(byTag(sets[1], "legend")[0]), /보증·담보/);
  assert.deepEqual(
    radios(sets[0]).map((node) => [node.value, node.checked]),
    [["applicable", false], ["not_applicable", true], ["needs_check", false]],
  );
  assert.deepEqual(new Set(radios(sets[0]).map((node) => node.name)).size, 1);
  assert.notEqual(radios(sets[0])[0].name, radios(sets[1])[0].name);
  assert.equal(reasonBox(sets[0]).value, "주석 27에 계류 중인 소송이 없다고 기재되어 있습니다.");
  assert.ok(reasonBox(sets[0]).className.split(" ").includes("review-question"));
  assert.match(allText(sets[0]), /계류 중인 소송이 없습니다/);
  assert.equal(elements["approval-submit"].hidden, false);
  assert.equal(elements["approval-cancel"].hidden, false);
});

test("hide closes the panel", () => {
  const { elements, panel } = setup();
  panel.show(twoDrafts(), "소송 현황을 알려주세요.");
  assert.equal(elements["approval-question"].hidden, false);
  panel.hide();

  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["approval-question"].hidden, true);
});

// AC-02
test("marks an item as edited when verdict or reason differs and unmarks when restored", () => {
  const { elements, panel } = setup();
  panel.show(twoDrafts());
  const sets = fields(elements);
  const badge = (set) => byClass(set, "edited-badge")[0];
  const comparison = (set) => byClass(set, "approval-comparison")[0];

  assert.equal(badge(sets[0]).hidden, true);
  assert.equal(comparison(sets[0]).hidden, true);
  assert.match(elements["approval-summary"].textContent, /수정 0개 \/ 전체 2개/);

  choose(sets[0], "needs_check");
  assert.equal(badge(sets[0]).hidden, false);
  assert.equal(comparison(sets[0]).hidden, false);
  assert.deepEqual(byClass(sets[0], "approval-comparison-verdict").map((node) => node.textContent), ["비해당", "확인필요"]);
  assert.match(badge(sets[0]).textContent, /수정됨/);
  assert.match(elements["approval-summary"].textContent, /수정 1개 \/ 전체 2개/);

  choose(sets[0], "not_applicable");
  assert.equal(badge(sets[0]).hidden, true);
  assert.equal(comparison(sets[0]).hidden, true);

  typeReason(sets[1], "담보 제공 내역을 확인했습니다.");
  assert.equal(badge(sets[1]).hidden, false);
  assert.deepEqual(byClass(sets[1], "approval-comparison-reason").map((node) => node.textContent), [
    "담보 제공 내역이 있습니다.",
    "담보 제공 내역을 확인했습니다.",
  ]);
  assert.match(elements["approval-summary"].textContent, /수정 1개 \/ 전체 2개/);

  typeReason(sets[1], "담보 제공 내역이 있습니다.");
  assert.equal(badge(sets[1]).hidden, true);
  assert.equal(comparison(sets[1]).hidden, true);
});

// AC-03
test("approve sends every item with item, verdict and reason only, never an approver", async () => {
  const { elements, panel, requests, confirms } = setup({ handlers: { [APPROVALS]: () => json(200, {}) } });
  panel.show(twoDrafts());
  const sets = fields(elements);
  choose(sets[0], "needs_check");
  typeReason(sets[1], "  담보를 확인했습니다.  ");

  await approve(elements);

  assert.equal(confirms.length, 1);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].key, APPROVALS);
  assert.equal(requests[0].options.headers["content-type"], "application/json");
  const body = JSON.parse(requests[0].options.body);
  assert.deepEqual(Object.keys(body), ["decisions"]);
  assert.deepEqual(body.decisions, [
    { item: "litigation", verdict: "needs_check", reason: "주석 27에 계류 중인 소송이 없다고 기재되어 있습니다." },
    { item: "guarantee_collateral", verdict: "applicable", reason: "담보를 확인했습니다." },
  ]);
  assert.doesNotMatch(requests[0].options.body, /approved_by|user_id|approver/);
});

test("disables both buttons and all inputs while saving, and a second click sends nothing", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const { elements, panel, requests, confirms } = setup({ handlers: { [APPROVALS]: async () => (await gate, json(200, {})) } });
  panel.show(twoDrafts());

  const first = approve(elements);
  assert.equal(elements["approval-submit"].disabled, true);
  assert.equal(elements["approval-cancel"].disabled, true);
  assert.equal(elements["approval-submit"].textContent, "저장 중…");
  assert.ok(byTag(elements["approval-list"], "input").every((node) => node.disabled));
  assert.ok(byTag(elements["approval-list"], "textarea").every((node) => node.disabled));
  await approve(elements);
  await cancel(elements);
  release();
  await first;

  assert.equal(requests.length, 1);
  assert.equal(confirms.length, 1);
});

// AC-04
test("after approval the panel closes, the review is reloaded and success is announced", async () => {
  const { elements, panel, settled } = setup({ handlers: { [APPROVALS]: () => json(200, {}) } });
  panel.show(twoDrafts());

  await approve(elements);

  assert.deepEqual(settled, ["approved"]);
  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["approval-done"].hidden, false);
  assert.equal(elements["approval-done"].textContent, M.approved);
  assert.equal(elements["approval-error"].hidden, true);
  assert.equal(elements["approval-submit"].textContent, "승인");
});

test("when the reload after approval fails it says so and never sends approval again", async () => {
  const { elements, panel, requests } = setup({
    handlers: { [APPROVALS]: () => json(200, {}) },
    settleState: "failed",
  });
  panel.show(twoDrafts());

  await approve(elements);

  assert.equal(elements["approval-done"].textContent, M.approvedStale);
  assert.equal(requests.length, 1);
  assert.equal(elements["approval-section"].hidden, true);
});

test("declining the confirmation sends nothing and keeps the edits", async () => {
  const { elements, panel, requests } = setup({ confirmAnswer: false });
  panel.show(twoDrafts());
  choose(fields(elements)[0], "needs_check");

  await approve(elements);

  assert.equal(requests.length, 0);
  assert.equal(elements["approval-submit"].disabled, false);
  assert.equal(radios(fields(elements)[0]).find((node) => node.checked).value, "needs_check");
});

// AC-05
test("cancel calls approvals:cancel once without a body and returns to the question state", async () => {
  const { elements, panel, requests, settled, confirms } = setup({ handlers: { [CANCEL]: () => json(204, null) } });
  panel.show(twoDrafts());

  await cancel(elements);

  assert.equal(confirms.length, 1);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].key, CANCEL);
  assert.equal(requests[0].options.body, undefined);
  assert.deepEqual(settled, ["cancelled"]);
  assert.equal(elements["approval-section"].hidden, true);
  assert.equal(elements["approval-done"].textContent, M.cancelled);
});

test("cancel accepts 200 as well as 204, and says so when the reload fails", async () => {
  const { elements, panel } = setup({ handlers: { [CANCEL]: () => json(200, {}) }, settleState: "failed" });
  panel.show(twoDrafts());

  await cancel(elements);

  assert.equal(elements["approval-done"].textContent, M.cancelledStale);
});

test("declining the cancel confirmation sends nothing", async () => {
  const { elements, panel, requests } = setup({ confirmAnswer: false });
  panel.show(twoDrafts());

  await cancel(elements);

  assert.equal(requests.length, 0);
  assert.equal(elements["approval-section"].hidden, false);
});

// AC-06
test("blocks approval with an empty or oversized reason and names the item", async () => {
  const { elements, panel, requests, confirms } = setup();
  panel.show(twoDrafts());
  const sets = fields(elements);
  const itemError = (set) => byClass(set, "item-error")[0];

  typeReason(sets[0], "   ");
  await approve(elements);
  assert.equal(requests.length, 0);
  assert.equal(confirms.length, 0);
  assert.equal(itemError(sets[0]).hidden, false);
  assert.equal(itemError(sets[0]).textContent, M.reason);
  assert.equal(itemError(sets[1]).hidden, true);
  assert.equal(reasonBox(sets[0]).focused, true);

  typeReason(sets[0], "가".repeat(1001));
  await approve(elements);
  assert.equal(requests.length, 0);
  assert.equal(itemError(sets[0]).hidden, false);

  typeReason(sets[0], "가".repeat(1000));
  assert.equal(itemError(sets[0]).hidden, true);
});

// AC-07
test("a 409 conflict explains, reloads the review and never resends", async () => {
  const { elements, panel, requests, settled } = setup({ handlers: { [APPROVALS]: () => json(409, { error: { code: "CONFLICT_NOT_AWAITING_APPROVAL" } }) } });
  panel.show(twoDrafts());

  await approve(elements);

  assert.equal(requests.length, 1);
  assert.deepEqual(settled, ["conflict"]);
  assert.equal(elements["approval-error"].textContent, M.conflict);
  assert.equal(elements["approval-error"].hidden, false);
  assert.equal(elements["approval-section"].hidden, true);
});

// AC-08
test("401 stops quietly because the caller already left for the login page", async () => {
  const { elements, panel, settled } = setup();
  const original = panel;
  void original;
  const unauthorized = createApprovalPanel({
    document: { getElementById: (id) => elements[id], createElement: (tag) => createElement(tag) },
    request: async () => null,
    confirm: () => true,
    renderCitation: () => createElement("li"),
    approvalsPath: "/api/v1/reviews/r-1/approvals",
    onSettled: async (kind) => settled.push(kind),
    onMissing() {},
  });
  unauthorized.show(twoDrafts());

  await approve(elements);

  assert.deepEqual(settled, []);
  assert.equal(elements["approval-error"].hidden, true);
  assert.equal(elements["approval-submit"].disabled, false);
});

test("404 hands over to the missing-review view for approve and cancel", async () => {
  const { elements, panel, missingCount } = setup({ handlers: { [APPROVALS]: () => json(404, {}), [CANCEL]: () => json(404, {}) } });
  panel.show(twoDrafts());

  await approve(elements);
  await cancel(elements);

  assert.equal(missingCount(), 2);
});

// AC-09
for (const [label, handler, message] of [
  ["network failure", () => Promise.reject(new Error("offline")), M.network],
  ["500", () => json(500, { error: { code: "INTERNAL_ERROR", details: { secret: "db" }, request_id: "req-9" } }), M.server],
  ["503", () => json(503, {}), M.server],
  ["429", () => json(429, {}), M.tooMany],
  ["400", () => json(400, { error: { code: "VALIDATION_ERROR", details: { field: "decisions" } } }), M.badInput],
  ["418", () => json(418, {}), M.failed],
]) {
  test(`approve failure (${label}) shows a status-based message, keeps edits and allows a retry`, async () => {
    const { elements, panel, settled } = setup({ handlers: { [APPROVALS]: handler } });
    panel.show(twoDrafts());
    choose(fields(elements)[0], "needs_check");
    typeReason(fields(elements)[1], "수정한 이유");

    await approve(elements);

    assert.equal(elements["approval-error"].hidden, false);
    assert.equal(elements["approval-error"].textContent, message);
    assert.doesNotMatch(allText(elements["approval-error"]), /secret|req-9|INTERNAL_ERROR|VALIDATION_ERROR|decisions/);
    assert.equal(elements["approval-submit"].disabled, false);
    assert.equal(elements["approval-cancel"].disabled, false);
    assert.equal(elements["approval-submit"].textContent, "승인");
    assert.equal(radios(fields(elements)[0]).find((node) => node.checked).value, "needs_check");
    assert.equal(reasonBox(fields(elements)[1]).value, "수정한 이유");
    assert.ok(byTag(elements["approval-list"], "input").every((node) => !node.disabled));
    assert.deepEqual(settled, []);
    assert.equal(elements["approval-section"].hidden, false);
  });
}

test("cancel failure shows a status-based message and keeps the panel open", async () => {
  const { elements, panel } = setup({ handlers: { [CANCEL]: () => json(503, {}) } });
  panel.show(twoDrafts());

  await cancel(elements);

  assert.equal(elements["approval-error"].textContent, M.server);
  assert.equal(elements["approval-section"].hidden, false);
  assert.equal(elements["approval-cancel"].disabled, false);
});

test("a 409 on cancel also reloads the review", async () => {
  const { elements, panel, settled } = setup({ handlers: { [CANCEL]: () => json(409, {}) } });
  panel.show(twoDrafts());

  await cancel(elements);

  assert.deepEqual(settled, ["conflict"]);
  assert.equal(elements["approval-error"].textContent, M.conflict);
});

// AC-10
test("without a draft only cancel is offered and approval is impossible", async () => {
  for (const missing of [null, undefined, [], "x", {}]) {
    const { elements, panel, requests } = setup();
    panel.show(missing);

    assert.equal(elements["approval-section"].hidden, false);
    assert.equal(elements["approval-empty"].hidden, false);
    assert.equal(elements["approval-empty"].textContent, M.noDraft);
    assert.equal(elements["approval-submit"].hidden, true);
    assert.equal(elements["approval-cancel"].hidden, false);
    assert.equal(fields(elements).length, 0);
    await approve(elements);
    assert.equal(requests.length, 0);
  }
});

test("a later draft restores the approve button after an empty one", () => {
  const { elements, panel } = setup();
  panel.show(null);
  panel.show(twoDrafts());

  assert.equal(elements["approval-submit"].hidden, false);
  assert.equal(elements["approval-empty"].hidden, true);
});

// AC-11
test("server strings render as text and unknown values stay unknown and are sent back unchanged", async () => {
  const hostile = "<img src=x onerror=alert(1)>";
  const { elements, panel, requests } = setup({ handlers: { [APPROVALS]: () => json(200, {}) } });
  panel.show([
    draftItem({ item: "constructor", verdict: "__proto__", reason: hostile }),
    draftItem({ item: "litigation", verdict: "maybe", reason: "정상" }),
  ]);
  const sets = fields(elements);

  assert.match(allText(byTag(sets[0], "legend")[0]), new RegExp(M.unknown));
  assert.equal(reasonBox(sets[0]).value, hostile);
  assert.ok(radios(sets[0]).every((node) => node.checked === false));
  assert.ok(descendants(elements["approval-list"]).every((node) => node.tagName !== "IMG"));

  await approve(elements);

  const body = JSON.parse(requests[0].options.body);
  assert.deepEqual(body.decisions[0], { item: "constructor", verdict: "__proto__", reason: hostile });
  assert.equal(body.decisions[1].verdict, "maybe");
});

test("skips non-object draft entries and warns when a citation failed verification", () => {
  const failing = draftItem({ citations: [citation({ verification: { passed: false, failed_checks: ["quote_not_in_chunk"] } })] });
  const { elements, panel } = setup();
  panel.show([null, 3, failing]);

  const sets = fields(elements);
  assert.equal(sets.length, 1);
  assert.match(allText(sets[0]), /인용 검증 실패 항목 있음/);
  assert.doesNotMatch(allText(sets[0]), /quote_not_in_chunk/);
  assert.match(elements["approval-summary"].textContent, /전체 1개/);
});

test("showing the same draft again keeps unsaved edits, a different draft replaces them", () => {
  const { elements, panel } = setup();
  panel.show(twoDrafts());
  choose(fields(elements)[0], "needs_check");

  panel.show(twoDrafts());
  assert.equal(radios(fields(elements)[0]).find((node) => node.checked).value, "needs_check");

  panel.show([draftItem({ reason: "다른 초안" })]);
  assert.equal(fields(elements).length, 1);
  assert.equal(reasonBox(fields(elements)[0]).value, "다른 초안");
});

test("a new draft clears the previous result and error messages", async () => {
  const { elements, panel } = setup({ handlers: { [APPROVALS]: () => json(500, {}) } });
  panel.show(twoDrafts());
  await approve(elements);
  assert.equal(elements["approval-error"].hidden, false);

  panel.show([draftItem({ reason: "다른 초안" })]);
  assert.equal(elements["approval-error"].hidden, true);
  panel.clearDone();
  assert.equal(elements["approval-done"].hidden, true);
});

// AC-13
test("the panel never calls rules, memos or chunks endpoints", async () => {
  const { elements, panel, requests } = setup({ handlers: { [APPROVALS]: () => json(200, {}), [CANCEL]: () => json(200, {}) } });
  panel.show(twoDrafts());
  await approve(elements);
  panel.show(twoDrafts());
  await cancel(elements);

  for (const request of requests) assert.doesNotMatch(request.key, /rules|memos|chunks/);
});

test("after hide the same draft is rendered fresh, without edits from the closed panel", () => {
  const { elements, panel } = setup();
  panel.show(twoDrafts());
  choose(fields(elements)[0], "needs_check");

  panel.hide();
  panel.show(twoDrafts());

  assert.equal(radios(fields(elements)[0]).find((node) => node.checked).value, "not_applicable");
});

test("shows a cited amount in won without changing the original approval reason", async () => {
  const original = "163,578,745";
  const { elements, panel, requests } = setup({ handlers: { [APPROVALS]: () => json(200, {}) } });
  panel.show([draftItem({ reason: original, citations: [citation({ quote: "(단위 : 천원) 유동 은행차입금 163,578,745" })] })]);
  const field = fields(elements)[0];
  assert.equal(byClass(field, "reason-display")[0].textContent, "163,578,745,000원");
  assert.equal(reasonBox(field).hidden, true);
  assert.match(allText(field), /이유 수정/);
  assert.equal(elements["approval-summary"].textContent, "수정 0개 / 전체 1개");

  await approve(elements);
  assert.equal(JSON.parse(requests[0].options.body).decisions[0].reason, original);
});

test("a company list wraps as separate pairs and remains editable", async () => {
  const original = "고려종합국제운송(주) 86,373,776 | (주)에이치엠티메가라인 26,292,472";
  const { elements, panel, requests } = setup({ handlers: { [APPROVALS]: () => json(200, {}) } });
  panel.show([draftItem({ reason: original, citations: [citation({ quote: `(단위 : 천원) ${original}` })] })]);
  const field = fields(elements)[0];
  assert.equal(byClass(field, "reason-pair").length, 2);
  assert.equal(reasonBox(field).hidden, true);
  const edit = byClass(field, "reason-edit")[0];
  edit.listeners.click();
  assert.equal(reasonBox(field).hidden, false);
  assert.equal(reasonBox(field).focused, true);
  typeReason(field, "직접 검토한 이유입니다.");

  await approve(elements);
  assert.equal(JSON.parse(requests[0].options.body).decisions[0].reason, "직접 검토한 이유입니다.");
});
