// landing-feature-pages: 보고서 시트(종료 = 승인·저장된 심사 건)
import assert from "node:assert/strict";
import test from "node:test";

import { panelReports } from "../backend/static/sheet-panels.js";
import { allText, byClass, createDocument, failReviews, hrefs, okReviews, review, wrap } from "./fake_dom.mjs";

const document = createDocument();
const ctx = (reviews) => ({ document, reviews });

test("Given closed reviews, When the reports sheet opens, Then only closed ones are listed with summary numbers", async () => {
  const data = [
    review({ id: "1", status: "closed", corp_code: "00000001", corp_name: "삼성전자", fiscal_year: 2025, created_at: "2026-11-10T05:20:00Z" }),
    review({ id: "2", status: "closed", corp_code: "00000001", corp_name: "삼성전자", fiscal_year: 2024, created_at: "2026-11-02T08:05:00Z" }),
    review({ id: "3", status: "closed", corp_code: "00000002", corp_name: "LG화학", fiscal_year: 2025, created_at: "2026-11-08T00:41:00Z" }),
    review({ id: "4", status: "open", corp_name: "열린건" }),
    review({ id: "5", status: "awaiting_approval", corp_name: "대기건" }),
  ];
  const root = wrap((await panelReports(ctx(okReviews(data)))).nodes);
  const text = allText(root);

  assert.match(text, /승인·저장된 심사 메모가 3건이고 기업은 2곳입니다\./);
  const cards = byClass(root, "sheet-cards")[0].children.map((card) => allText(card).trim().replace(/\s+/g, " "));
  assert.deepEqual(cards, ["3건 저장된 메모", "2곳 기업 수", "2026-11-10 가장 최근 생성일"]);
  assert.match(byClass(root, "sheet-chart-figure")[0].getAttribute("aria-label"), /사업연도별 저장 건: 2025년 2건, 2024년 1건/);
  // 최근 생성순, 종료 건만
  assert.deepEqual(hrefs(root), ["/reviews/1", "/reviews/3", "/reviews/2"]);
  assert.doesNotMatch(text, /열린건|대기건/);
  // 목록 API에는 저장 시각이 없으므로 "저장"이 아니라 "생성"이라고만 적는다.
  assert.match(text, /생성 2026-11-10 14:20/);
  assert.doesNotMatch(text, /저장 2026|저장 시각/);
});

test("more than 10 closed reviews show 10 and point to the reviews page for the rest", async () => {
  const many = Array.from({ length: 13 }, (_, index) => review({ id: `r${index}`, status: "closed", created_at: `2026-10-${String(index + 1).padStart(2, "0")}T00:00:00Z` }));
  const root = wrap((await panelReports(ctx(okReviews(many)))).nodes);
  assert.equal(hrefs(root).length, 10);
  assert.match(allText(root), /외 3건은 심사 건 화면에서 확인하세요\./);
});

test("Given no closed reviews, When the reports sheet opens, Then an empty state with a create link shows and no chart", async () => {
  for (const data of [[], [review({ status: "open" })]]) {
    const root = wrap((await panelReports(ctx(okReviews(data)))).nodes);
    assert.match(allText(root), /아직 승인·저장된 심사 메모가 없습니다\./);
    assert.equal(byClass(root, "sheet-chart").length, 0);
    assert.deepEqual(hrefs(root), ["/reviews#create-section"]);
    assert.match(allText(root), /새 심사 건 만들기/);
  }
});

test("reports sheet passes 401 and shows fixed messages for failures", async () => {
  assert.deepEqual(await panelReports(ctx(failReviews("unauthorized"))), { unauthorized: true });
  assert.match((await panelReports(ctx(failReviews("error", 500)))).error, /서버에 문제가 있어/);
});

test("report rows render HTML-looking names as text", async () => {
  const evil = review({ status: "closed", corp_name: "<script>alert(1)</script>", id: "../x" });
  const root = wrap((await panelReports(ctx(okReviews([evil])))).nodes);
  assert.match(allText(root), /<script>alert\(1\)<\/script>/);
  assert.deepEqual(hrefs(root), ["/reviews/..%2Fx"]); // id는 인코딩된다
});
