// 첫 화면 핵심 수치 띠의 숫자를 landing-data.js(보유 기업 목록)에서 세어 채운다.
// HTML에도 같은 값이 미리 적혀 있어 이 스크립트가 없어도 화면은 비지 않는다(tests/test_login_web_ui.py가 일치를 확인).
import { COMPANIES } from "./landing-data.js";

// 항목이 객체이고 사업연도가 정수일 때만 쓸 수 있는 목록이다. 하나라도 틀리면 목록 전체를 믿지 않는다.
function isUsableList(companies) {
  return (
    Array.isArray(companies) &&
    companies.length > 0 &&
    companies.every(
      (company) => company !== null && typeof company === "object" && Number.isInteger(company.fiscal_year),
    )
  );
}

export function renderHeroStats({ document, companies }) {
  // 목록이 비었거나 형식이 틀리면 HTML에 적힌 값을 그대로 둔다(docs/specs/main-page-design.md AC-49).
  if (!isUsableList(companies)) return;

  const count = document.getElementById("stat-companies");
  if (count) count.textContent = String(companies.length);

  // 사업연도는 목록 전체가 한 해일 때만 바꾼다. 섞여 있으면 HTML의 값을 그대로 둔다.
  const years = new Set(companies.map((company) => company.fiscal_year));
  const year = document.getElementById("stat-year");
  if (year && years.size === 1) year.textContent = String([...years][0]);
}

if (typeof window !== "undefined") {
  renderHeroStats({ document: window.document, companies: COMPANIES });
}
