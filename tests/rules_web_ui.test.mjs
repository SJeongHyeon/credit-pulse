// landing-feature-pages AC-13: 내 판정 규칙 시트
import assert from "node:assert/strict";
import test from "node:test";

import { panelRules } from "../backend/static/sheet-panels.js";
import { allText, byClass, byTag, createDocument, fire, json, wrap } from "./fake_dom.mjs";

const document = createDocument();
const RULES = [
  { rule_id: "ru-1", item: "litigation", instruction: "소송가액 100억 미만은 비해당", source_memo_id: "m-1" },
  { rule_id: "ru-2", item: "litigation", instruction: "<b>굵게</b>", source_memo_id: "m-2" },
  { rule_id: "ru-3", item: "borrowing_structure", instruction: "차입 구조 규칙", source_memo_id: "m-3" },
];

function setup(handlers) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    calls.push({ path, method: options.method ?? "GET" });
    const handler = handlers[`${options.method ?? "GET"} ${path}`];
    if (!handler) throw new Error(`unexpected ${path}`);
    return typeof handler === "function" ? handler() : handler;
  };
  const state = { body: null, unauthorized: 0 };
  const ctx = {
    document,
    fetch,
    replace: (nodes) => {
      state.body = wrap(nodes);
    },
    onUnauthorized: () => {
      state.unauthorized += 1;
    },
  };
  return { ctx, calls, state };
}

const button = (root, label) => byTag(root, "button").find((node) => node.textContent === label);

test("Given rules, When the rules sheet opens, Then cards, item chart and rules are shown", async () => {
  const { ctx } = setup({ "GET /api/v1/rules": json(200, { items: RULES }) });
  const result = await panelRules(ctx);
  const root = wrap(result.nodes);

  assert.match(allText(root), /저장된 판정 규칙은 3개이고 항목은 2종류입니다\./);
  const cards = byClass(root, "sheet-cards")[0].children.map((card) => allText(card).trim().replace(/\s+/g, " "));
  assert.deepEqual(cards, ["3개 규칙", "2개 항목 종류"]);
  assert.match(byClass(root, "sheet-chart-figure")[0].getAttribute("aria-label"), /항목별 규칙 수: 소송 2개, 차입 구조 1개/);
  assert.match(allText(root), /소송가액 100억 미만은 비해당/);
  assert.match(allText(root), /<b>굵게<\/b>/); // HTML이 아니라 글자
});

test("Given no rules, Then the empty explanation shows and no chart", async () => {
  const { ctx } = setup({ "GET /api/v1/rules": json(200, { items: [] }) });
  const root = wrap((await panelRules(ctx)).nodes);
  assert.match(allText(root), /저장된 판정 규칙이 없습니다\. 승인 때 판정·사유를 고치면 여기에 쌓입니다\./);
  assert.equal(byClass(root, "sheet-chart").length, 0);
});

test("Given the demo-mode 503, Then the demo explanation shows; real 5xx shows an error", async () => {
  const demo = setup({ "GET /api/v1/rules": json(503, { error: { code: "SERVICE_UNAVAILABLE_RULES" } }) });
  assert.match(allText(wrap((await panelRules(demo.ctx)).nodes)), /이 환경에서는 판정 규칙 기억을 쓰지 않습니다\(데모 모드\)\./);

  const other = setup({ "GET /api/v1/rules": json(503, { error: { code: "SERVICE_UNAVAILABLE_DB" } }) });
  assert.match((await panelRules(other.ctx)).error, /서버에 문제가 있어/);
  assert.deepEqual(await panelRules(setup({ "GET /api/v1/rules": json(401, {}) }).ctx), { unauthorized: true });
  assert.match((await panelRules(setup({ "GET /api/v1/rules": json(429, {}) }).ctx)).error, /요청이 너무 많습니다/);
  assert.ok((await panelRules(setup({ "GET /api/v1/rules": json(200, { items: "x" }) }).ctx)).error);
});

test("delete needs two steps: 삭제 → 정말 삭제 calls DELETE once, then the list and numbers update", async () => {
  const { ctx, calls, state } = setup({
    "GET /api/v1/rules": json(200, { items: RULES }),
    "DELETE /api/v1/rules/ru-1": { status: 204 },
  });
  const root = wrap((await panelRules(ctx)).nodes);
  const first = byClass(root, "sheet-rule")[0];

  await fire(button(first, "삭제"), "click");
  assert.deepEqual(calls.map((call) => call.method), ["GET"]); // 한 번 눌러서는 삭제하지 않는다
  assert.equal(button(first, "정말 삭제") !== undefined, true);
  assert.equal(button(first, "취소").hidden, false);

  await fire(button(first, "정말 삭제"), "click");
  assert.deepEqual(calls.at(-1), { path: "/api/v1/rules/ru-1", method: "DELETE" });
  assert.match(allText(state.body), /저장된 판정 규칙은 2개이고 항목은 2종류입니다\./);
  assert.doesNotMatch(allText(state.body), /소송가액 100억 미만은 비해당/);
});

test("cancel resets the confirmation without calling the API", async () => {
  const { ctx, calls } = setup({ "GET /api/v1/rules": json(200, { items: RULES }) });
  const root = wrap((await panelRules(ctx)).nodes);
  const first = byClass(root, "sheet-rule")[0];
  await fire(button(first, "삭제"), "click");
  await fire(button(first, "취소"), "click");
  assert.equal(button(first, "삭제") !== undefined, true);
  assert.equal(button(first, "취소").hidden, true);
  assert.equal(calls.length, 1);
});

test("delete 404 removes the rule with a notice; 5xx keeps it with a fixed message; 401 signs out", async () => {
  const cases = [
    { status: 404, remains: 2, message: /이미 삭제된 규칙입니다\./ },
    { status: 500, remains: 3, message: /서버에 문제가 있어/ },
    { status: 429, remains: 3, message: /요청이 너무 많습니다/ },
    { status: 400, remains: 3, message: /규칙을 삭제하지 못했습니다/ },
  ];
  for (const { status, remains, message } of cases) {
    const { ctx, state } = setup({ "GET /api/v1/rules": json(200, { items: RULES }), "DELETE /api/v1/rules/ru-1": { status } });
    const root = wrap((await panelRules(ctx)).nodes);
    const first = byClass(root, "sheet-rule")[0];
    await fire(button(first, "삭제"), "click");
    await fire(button(first, "정말 삭제"), "click");
    assert.match(allText(state.body), message, String(status));
    assert.equal(byClass(state.body, "sheet-rule").length, remains, String(status));
    assert.equal(byClass(state.body, "sheet-alert")[0].getAttribute("role"), "alert");
  }

  const unauthorized = setup({ "GET /api/v1/rules": json(200, { items: RULES }), "DELETE /api/v1/rules/ru-1": { status: 401 } });
  const root = wrap((await panelRules(unauthorized.ctx)).nodes);
  await fire(button(byClass(root, "sheet-rule")[0], "삭제"), "click");
  await fire(button(byClass(root, "sheet-rule")[0], "정말 삭제"), "click");
  assert.equal(unauthorized.state.unauthorized, 1);
});

test("a network error on delete shows the network message and keeps the rule", async () => {
  const { ctx, state } = setup({
    "GET /api/v1/rules": json(200, { items: RULES }),
    "DELETE /api/v1/rules/ru-1": () => {
      throw new Error("offline");
    },
  });
  const root = wrap((await panelRules(ctx)).nodes);
  await fire(button(byClass(root, "sheet-rule")[0], "삭제"), "click");
  await fire(button(byClass(root, "sheet-rule")[0], "정말 삭제"), "click");
  assert.match(allText(state.body), /서버에 연결할 수 없습니다/);
  assert.equal(byClass(state.body, "sheet-rule").length, 3);
});

test("rule ids are URL-encoded in the DELETE path", async () => {
  const { ctx, calls } = setup({
    "GET /api/v1/rules": json(200, { items: [{ rule_id: "a/b", item: "litigation", instruction: "x" }] }),
    "DELETE /api/v1/rules/a%2Fb": { status: 204 },
  });
  const root = wrap((await panelRules({ ...ctx, replace: () => {} })).nodes);
  await fire(button(root, "삭제"), "click");
  await fire(button(root, "정말 삭제"), "click");
  assert.equal(calls.at(-1).path, "/api/v1/rules/a%2Fb");
});
