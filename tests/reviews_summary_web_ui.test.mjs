// landing-feature-pages AC-09·10·11·12: 리스크 점검·승인 현황 시트와 심사 건 집계
import assert from "node:assert/strict";
import test from "node:test";

import { MAX_PAGES, countByStatus, latestFirst, loadAllReviews } from "../backend/static/review-data.js";
import { panelApprovals, panelReviews } from "../backend/static/sheet-panels.js";
import {
  allText, byClass, createDocument, failReviews, hrefs, json, okReviews, review, wrap,
} from "./fake_dom.mjs";

const document = createDocument();
const ctx = (reviews) => ({ document, reviews });

const SAMPLE = [
  review({ id: "a", status: "open", created_at: "2026-11-01T00:00:00Z" }),
  review({ id: "b", status: "running", created_at: "2026-11-02T00:00:00Z" }),
  review({ id: "c", status: "awaiting_approval", corp_name: "LG화학", created_at: "2026-11-03T00:00:00Z" }),
  review({ id: "d", status: "awaiting_approval", corp_name: "현대차", created_at: "2026-11-04T00:00:00Z" }),
  review({ id: "e", status: "closed", created_at: "2026-11-05T00:00:00Z" }),
];

// ---- 집계 ----
test("countByStatus counts each status and latestFirst sorts by created_at without mutating", () => {
  assert.deepEqual(countByStatus(SAMPLE), { open: 1, running: 1, awaiting_approval: 2, closed: 1 });
  const sorted = latestFirst(SAMPLE);
  assert.deepEqual(sorted.map((item) => item.id), ["e", "d", "c", "b", "a"]);
  assert.equal(SAMPLE[0].id, "a");
  assert.equal(latestFirst([review({ id: "x", created_at: "bad" }), review({ id: "y" })])[0].id, "y");
});

test("loadAllReviews follows next_cursor, stops at 5 pages and reports truncation", async () => {
  const urls = [];
  const fetch = async (url) => {
    urls.push(url);
    return json(200, { items: [review({ id: String(urls.length) })], next_cursor: `c${urls.length}` });
  };
  const result = await loadAllReviews(fetch);
  assert.equal(result.status, "ok");
  assert.equal(result.reviews.length, MAX_PAGES);
  assert.equal(result.truncated, true);
  assert.match(urls[0], /page_size=50$/);
  assert.match(urls[1], /cursor=c1$/);
});

test("loadAllReviews discards partial data when a later page fails and passes 401 through", async () => {
  let call = 0;
  const failing = async () => {
    call += 1;
    return call === 1 ? json(200, { items: [review()], next_cursor: "n" }) : json(500, {});
  };
  assert.deepEqual(await loadAllReviews(failing), { status: "error", http: 500 });
  assert.deepEqual(await loadAllReviews(async () => json(401, {})), { status: "unauthorized" });
  assert.deepEqual(await loadAllReviews(async () => json(200, { items: "x" })), { status: "error", http: 200 });
  const done = await loadAllReviews(async () => json(200, { items: [review(), "junk", null], next_cursor: null }));
  assert.equal(done.reviews.length, 1);
  assert.equal(done.truncated, false);
});

// ---- 리스크 점검 ----
test("Given reviews, When the risk sheet opens, Then cards, chart and recent links match the data", async () => {
  const result = await panelReviews(ctx(okReviews(SAMPLE)));
  const root = wrap(result.nodes);
  const text = allText(root);

  assert.match(text, /진행 중인 심사 건은 4건이고, 승인을 기다리는 건이 2건 있습니다\./);
  const cards = byClass(root, "sheet-cards")[0].children.map((card) => allText(card).trim().replace(/\s+/g, " "));
  assert.deepEqual(cards, ["5 전체 심사 건", "4 진행 중", "2 승인 대기", "1 종료"]);
  const figure = byClass(root, "sheet-chart-figure")[0];
  assert.equal(figure.getAttribute("role"), "img");
  assert.match(figure.getAttribute("aria-label"), /상태별 심사 건: 진행 가능 1건, 처리 중 1건, 승인 대기 2건, 저장 중 0건, 종료 1건/);
  // 최근 3건: e, d, c 순
  assert.deepEqual(hrefs(root).filter((href) => href.startsWith("/reviews/")), ["/reviews/e", "/reviews/d", "/reviews/c"]);
  assert.ok(hrefs(root).includes("/reviews"));
  assert.match(allText(root), /심사 건 화면 열기/);
});

test("a truncated list is labelled as the latest 250 reviews", async () => {
  const result = await panelReviews(ctx(okReviews(SAMPLE, true)));
  assert.match(allText(wrap(result.nodes)), /최근 250건 기준/);
});

test("Given no reviews, When the risk sheet opens, Then zero cards, an empty state and a create link show instead of a chart", async () => {
  const result = await panelReviews(ctx(okReviews([])));
  const root = wrap(result.nodes);

  assert.match(allText(root), /아직 심사 건이 없습니다\./);
  assert.equal(byClass(root, "sheet-chart").length, 0);
  assert.ok(hrefs(root).includes("/reviews#create-section"));
  assert.match(allText(root), /새 심사 건 만들기/);
  assert.deepEqual(byClass(root, "sheet-cards")[0].children.map((card) => allText(card).trim().split(/\s+/)[0]), ["0", "0", "0", "0"]);
});

test("401 and failures are reported without server text", async () => {
  assert.deepEqual(await panelReviews(ctx(failReviews("unauthorized"))), { unauthorized: true });
  assert.match((await panelReviews(ctx(failReviews("error", 429)))).error, /요청이 너무 많습니다/);
  assert.match((await panelReviews(ctx(failReviews("error", 503)))).error, /서버에 문제가 있어/);
  assert.match((await panelReviews(ctx(failReviews("error", 400)))).error, /불러오지 못했습니다/);
});

test("server strings are inserted as text, never as HTML", async () => {
  const evil = review({ corp_name: "<img src=x onerror=alert(1)>", status: "awaiting_approval" });
  const result = await panelReviews(ctx(okReviews([evil])));
  assert.match(allText(wrap(result.nodes)), /<img src=x onerror=alert\(1\)>/); // 글자 그대로
  // fake_dom은 innerHTML을 쓰면 던진다 — 여기까지 왔다면 쓰지 않은 것이다.
});

// ---- 승인 현황 ----
test("Given reviews, When the approvals sheet opens, Then counts, donut legend and waiting links are shown", async () => {
  const result = await panelApprovals(ctx(okReviews(SAMPLE)));
  const root = wrap(result.nodes);

  assert.match(allText(root), /승인 대기 2건, 진행 가능 1건, 처리 중 1건, 승인 완료 1건입니다\./);
  const legend = byClass(root, "sheet-legend")[0].children.map((item) => item.textContent);
  assert.deepEqual(legend, ["승인 대기 2건", "진행 가능 1건", "처리 중 1건", "승인 완료 1건"]);
  const donut = byClass(root, "sheet-donut")[0].getAttribute("style");
  assert.match(donut, /^background:conic-gradient\(var\(--gold\) 0\.00% 40\.00%/);
  assert.equal(byClass(root, "sheet-chart-figure")[0].getAttribute("role"), "img");
  assert.deepEqual(hrefs(root).filter((href) => href.startsWith("/reviews/")), ["/reviews/d", "/reviews/c"]);
  assert.ok(hrefs(root).includes("/approvals"));
  assert.match(allText(root), /승인하러 가기/);
});

test("the approvals sheet explains when nothing is waiting, and shows the empty state with no reviews", async () => {
  const onlyClosed = await panelApprovals(ctx(okReviews([review({ status: "closed" })])));
  assert.match(allText(wrap(onlyClosed.nodes)), /승인 대기 중인 심사 건이 없습니다\./);

  const none = await panelApprovals(ctx(okReviews([])));
  const root = wrap(none.nodes);
  assert.equal(byClass(root, "sheet-chart").length, 0);
  assert.ok(hrefs(root).includes("/reviews#create-section"));
});
