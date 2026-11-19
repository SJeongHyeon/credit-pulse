import assert from "node:assert/strict";
import test from "node:test";

import { formatReviewReason, renderReviewReason } from "../backend/static/review-reason.js";

const citation = (quote) => ({ quote });

test("converts the cited thousand-won table value to the full won amount", () => {
  const formatted = formatReviewReason("163,578,745", [citation("(단위 : 천원) 유동 은행차입금 | 163,578,745")]);
  assert.deepEqual(formatted, { kind: "amount", editorText: "163,578,745,000원" });
});

test("keeps each company and its converted amount together as one display entry", () => {
  const reason = "고려종합국제운송(주) 86,373,776 | (주)에이치엠티메가라인 26,292,472 | 케이씨티시에이엔티(주) 214,669";
  const formatted = formatReviewReason(reason, [citation(`(단위 : 천원) ${reason}`)]);
  assert.equal(formatted.kind, "pairs");
  assert.deepEqual(formatted.entries, [
    { company: "고려종합국제운송(주)", amount: "86,373,776,000원" },
    { company: "(주)에이치엠티메가라인", amount: "26,292,472,000원" },
    { company: "케이씨티시에이엔티(주)", amount: "214,669,000원" },
  ]);
  const document = { createElement: (tagName) => ({ tagName, children: [], append(...children) { this.children.push(...children); } }) };
  const display = renderReviewReason(document, formatted);
  assert.equal(display.children.length, 3);
  assert.deepEqual(display.children.map((pair) => pair.children.map((part) => part.textContent)), [
    ["고려종합국제운송(주)", "86,373,776,000원"],
    ["(주)에이치엠티메가라인", "26,292,472,000원"],
    ["케이씨티시에이엔티(주)", "214,669,000원"],
  ]);
});

test("never invents a won unit for uncited, ambiguous, or unrelated numbers", () => {
  assert.deepEqual(formatReviewReason("163,578,745", [citation("유동차입금 163,578,745")]),
    { kind: "plain", editorText: "163,578,745" });
  assert.deepEqual(formatReviewReason("163,578,745", [citation("(단위: 천원) 163,578,745"), citation("(단위: 원) 163,578,745")]),
    { kind: "plain", editorText: "163,578,745" });
  assert.deepEqual(formatReviewReason("2025", [citation("(단위: 천원) 2025")]),
    { kind: "plain", editorText: "2025" });
  assert.deepEqual(formatReviewReason("16.3%", [citation("(단위: 천원) 16.3%")]),
    { kind: "plain", editorText: "16.3%" });
});
