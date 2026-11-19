import assert from "node:assert/strict";
import test from "node:test";

import { renderHeroStats } from "../backend/static/hero-stats.js";

function fakeDocument() {
  const nodes = { "stat-companies": { textContent: "15" }, "stat-year": { textContent: "2025" } };
  return { nodes, document: { getElementById: (id) => nodes[id] ?? null } };
}

test("fills the company count and the single fiscal year from the list", () => {
  const { nodes, document } = fakeDocument();
  const companies = [
    { name: "가", fiscal_year: 2025 },
    { name: "나", fiscal_year: 2025 },
    { name: "다", fiscal_year: 2025 },
  ];
  renderHeroStats({ document, companies });
  assert.equal(nodes["stat-companies"].textContent, "3");
  assert.equal(nodes["stat-year"].textContent, "2025");
});

test("keeps the printed year when the list mixes fiscal years", () => {
  const { nodes, document } = fakeDocument();
  renderHeroStats({
    document,
    companies: [
      { name: "가", fiscal_year: 2024 },
      { name: "나", fiscal_year: 2025 },
    ],
  });
  assert.equal(nodes["stat-companies"].textContent, "2");
  assert.equal(nodes["stat-year"].textContent, "2025");
});

test("leaves the printed values alone when the list is empty or invalid", () => {
  for (const companies of [[], null, undefined, "x"]) {
    const { nodes, document } = fakeDocument();
    renderHeroStats({ document, companies });
    assert.equal(nodes["stat-companies"].textContent, "15");
    assert.equal(nodes["stat-year"].textContent, "2025");
  }
});

test("leaves the printed values alone when an entry is malformed", () => {
  const malformedLists = [
    [{}],
    [{ name: "가" }],
    [null],
    ["x"],
    [{ fiscal_year: "2025" }],
    [{ fiscal_year: Number.NaN }],
    [{ fiscal_year: 2025.5 }],
    // 올바른 항목이 섞여 있어도 하나가 틀리면 기업 수와 연도 모두 HTML 값을 유지한다
    [{ name: "가", fiscal_year: 2025 }, {}],
    [{ name: "가", fiscal_year: 2025 }, null],
  ];
  for (const companies of malformedLists) {
    const { nodes, document } = fakeDocument();
    renderHeroStats({ document, companies });
    assert.equal(nodes["stat-companies"].textContent, "15", JSON.stringify(companies));
    assert.equal(nodes["stat-year"].textContent, "2025", JSON.stringify(companies));
  }
});

test("tolerates a page without the stats strip", () => {
  assert.doesNotThrow(() =>
    renderHeroStats({ document: { getElementById: () => null }, companies: [{ fiscal_year: 2025 }] }),
  );
});
