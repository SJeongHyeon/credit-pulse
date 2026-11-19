// landing-feature-pages: 공시 검색 시트(보유 기업 COMPANIES, API 없음)
import assert from "node:assert/strict";
import test from "node:test";

import { COMPANIES } from "../backend/static/landing-data.js";
import { panelSearch } from "../backend/static/sheet-panels.js";
import { allText, byClass, byTag, createDocument, fire, hrefs, wrap } from "./fake_dom.mjs";

const document = createDocument();
const SMALL = [
  { name: "LG화학", corp_code: "00356361", fiscal_year: 2025, filed_at: "2026-03-10" },
  { name: "삼성전자", corp_code: "00126380", fiscal_year: 2025, filed_at: "2026-03-12" },
  { name: "구형기업", corp_code: "00000009", fiscal_year: 2024, filed_at: "2025-03-01" },
];

async function open(companies) {
  const result = await panelSearch({ document, companies });
  const [form, results] = result.nodes;
  const input = byTag(form, "input")[0];
  const search = async (value) => {
    input.value = value;
    await fire(input, "input");
    return allText(results);
  };
  return { form, results, input, search, root: wrap(result.nodes) };
}

test("Given the company list, When the search sheet opens, Then totals, latest filing date and a role=img chart show", async () => {
  const { results } = await open(SMALL);
  const cards = byClass(results, "sheet-cards")[0].children.map((card) => allText(card).trim().replace(/\s+/g, " "));
  assert.deepEqual(cards, ["3곳 보유 기업", "3곳 검색 결과", "2026-03-12 최신 공시일"]);
  assert.equal(byClass(results, "sheet-chart-figure")[0].getAttribute("role"), "img");
  assert.match(byClass(results, "sheet-chart-figure")[0].getAttribute("aria-label"), /사업연도별 기업 수: 2025년 2곳, 2024년 1곳/);
  assert.match(allText(results), /보유 기업 3곳 중 3곳이 검색되었습니다\./);
});

test("search matches names and corp_code, ignoring case and extra spaces", async () => {
  const { search, results } = await open(SMALL);
  assert.match(await search("  lg화학 "), /1곳이 검색되었습니다/);
  assert.match(await search("126380"), /삼성전자/);
  assert.doesNotMatch(await search("126380"), /LG화학/);
  assert.match(await search("   "), /3곳이 검색되었습니다/); // 공백뿐이면 전체
  assert.match(await search("없는회사"), /검색 결과가 없습니다\./);
  assert.equal(byClass(results, "sheet-chart").length, 0);
});

test("each result offers a create-review link and the form submit does not reload", async () => {
  const { form, results } = await open(SMALL);
  assert.equal(form.getAttribute("role"), "search");
  assert.deepEqual(hrefs(results), ["/reviews#create-section", "/reviews#create-section", "/reviews#create-section"]);
  const event = await fire(form, "submit");
  assert.equal(event.prevented, true);
});

test("the real 100-company list shows 20 and asks to narrow the search", async () => {
  const { results } = await open(COMPANIES);
  assert.equal(byTag(results, "li").filter((li) => allText(li).includes("corp_code")).length, 20);
  assert.match(allText(results), new RegExp(`외 ${COMPANIES.length - 20}곳\\. 검색어로 좁혀 보세요\\.`));
});

test("company names are rendered as text only", async () => {
  const { results } = await open([{ name: "<b onclick=x>악성</b>", corp_code: "00000001", fiscal_year: 2025, filed_at: "2026-01-01" }]);
  assert.match(allText(results), /<b onclick=x>악성<\/b>/);
});

test("an empty or missing company list still renders without error", async () => {
  const { results } = await open([]);
  assert.match(allText(results), /보유 기업 0곳 중 0곳이 검색되었습니다\./);
  const missing = await panelSearch({ document, companies: undefined });
  assert.ok(Array.isArray(missing.nodes));
});
