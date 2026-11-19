// 기능 시트 5개의 본문. 각 함수는 { nodes } | { error } | { unauthorized: true }를 돌려준다.
// ctx: { document, fetch, companies, reviews(), replace(nodes), onUnauthorized() }
// 서버·데이터 문자열은 textContent로만 넣는다(HTML로 해석하지 않는다).
import { barChart, donutChart, el, emptyState, leadText, link, statCards } from "./charts.js";
import {
  IN_PROGRESS,
  MAX_REVIEWS,
  STATUS_LABELS,
  countByStatus,
  countOf,
  formatKst,
  isObject,
  latestFirst,
  reviewHref,
} from "./review-data.js";
import { ITEM_LABELS, UNKNOWN, own } from "./review-shared.js";

export const MESSAGES = Object.freeze({
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  loadFailed: "불러오지 못했습니다. 다시 시도해 주세요.",
  noReviews: "아직 심사 건이 없습니다.",
  noReports: "아직 승인·저장된 심사 메모가 없습니다.",
  noRules: "저장된 판정 규칙이 없습니다. 승인 때 판정·사유를 고치면 여기에 쌓입니다.",
  rulesDemo: "이 환경에서는 판정 규칙 기억을 쓰지 않습니다(데모 모드).",
  ruleDeleteFailed: "규칙을 삭제하지 못했습니다. 다시 시도해 주세요.",
  ruleAlreadyDeleted: "이미 삭제된 규칙입니다.",
  noSearchResult: "검색 결과가 없습니다.",
});
export const CREATE_HREF = "/reviews#create-section";
export const NEW_REVIEW_LABEL = "새 심사 건 만들기";
const SEARCH_LIMIT = 20;
const RECENT_LIMIT = 3;
const APPROVAL_LIMIT = 5;
const REPORT_LIMIT = 10;
const TRUNCATED_NOTE = ` (최근 ${MAX_REVIEWS}건 기준)`;

export function messageForStatus(status) {
  if (status === 429) return MESSAGES.tooMany;
  if (status >= 500) return MESSAGES.server;
  return MESSAGES.loadFailed;
}

function span(document, className, text) {
  return el(document, "span", className, text);
}

// 목록 한 줄: 왼쪽 제목(링크 또는 글자), 오른쪽 보조 글자
function row(document, title, meta, href) {
  const item = el(document, "li");
  if (href) {
    const anchor = el(document, "a", "", title);
    anchor.setAttribute("href", href);
    item.append(anchor);
  } else {
    item.append(span(document, "sheet-row-title", title));
  }
  item.append(el(document, "small", "", meta));
  return item;
}

// 심사 건 기반 시트의 공통 틀: 불러오기 결과를 오류·401·본문으로 나눈다.
async function withReviews(ctx, build) {
  const result = await ctx.reviews();
  if (result.status === "unauthorized") return { unauthorized: true };
  if (result.status !== "ok") return { error: messageForStatus(result.http) };
  return { nodes: build(result.reviews, result.truncated) };
}

function actionRow(document, anchor) {
  const wrap = el(document, "p", "sheet-actions");
  wrap.append(anchor);
  return wrap;
}

function reviewEmpty(document, message = MESSAGES.noReviews) {
  return emptyState(document, { message, actionLabel: NEW_REVIEW_LABEL, href: CREATE_HREF });
}

// ---- 리스크 점검 ----
export function panelReviews(ctx) {
  const { document } = ctx;
  return withReviews(ctx, (reviews, truncated) => {
    const counts = countByStatus(reviews);
    const inProgress = countOf(counts, ...IN_PROGRESS);
    const awaiting = countOf(counts, "awaiting_approval");
    const note = truncated ? TRUNCATED_NOTE : "";
    const nodes = [
      leadText(document, reviews.length === 0
        ? MESSAGES.noReviews
        : `진행 중인 심사 건은 ${inProgress}건이고, 승인을 기다리는 건이 ${awaiting}건 있습니다.${note}`),
      statCards(document, [
        { label: "전체 심사 건", value: `${reviews.length}${truncated ? "+" : ""}` },
        { label: "진행 중", value: inProgress },
        { label: "승인 대기", value: awaiting },
        { label: "종료", value: countOf(counts, "closed") },
      ]),
    ];
    if (reviews.length === 0) {
      nodes.push(reviewEmpty(document));
    } else {
      const rows = Object.keys(STATUS_LABELS).map((status) => ({ label: STATUS_LABELS[status], value: counts[status] ?? 0 }));
      const known = rows.reduce((sum, item) => sum + item.value, 0);
      if (known < reviews.length) rows.push({ label: "기타", value: reviews.length - known });
      nodes.push(barChart(document, { title: "상태별 심사 건", rows, unit: "건" }));
      const list = el(document, "ul", "sheet-rows");
      list.setAttribute("aria-label", "최근 심사 건");
      for (const review of latestFirst(reviews).slice(0, RECENT_LIMIT)) {
        list.append(row(
          document,
          `${review.corp_name ?? "-"} · ${review.fiscal_year ?? "-"}`,
          `${STATUS_LABELS[review.status] ?? UNKNOWN} · 질의 ${review.query_count ?? 0}건`,
          reviewHref(review),
        ));
      }
      nodes.push(el(document, "h3", "sheet-sub", "최근 심사 건"), list);
    }
    nodes.push(actionRow(document, link(document, "심사 건 화면 열기", "/reviews", { solid: true })));
    return nodes;
  });
}

// ---- 승인 현황 ----
export function panelApprovals(ctx) {
  const { document } = ctx;
  return withReviews(ctx, (reviews, truncated) => {
    const counts = countByStatus(reviews);
    const rows = [
      { label: "승인 대기", value: countOf(counts, "awaiting_approval") },
      { label: "진행 가능", value: countOf(counts, "open") },
      { label: "처리 중", value: countOf(counts, "running") },
      { label: "승인 완료", value: countOf(counts, "closed") },
    ];
    const shown = rows.reduce((sum, item) => sum + item.value, 0);
    const note = truncated ? TRUNCATED_NOTE : "";
    const nodes = [
      leadText(document, reviews.length === 0
        ? MESSAGES.noReviews
        : `승인 대기 ${rows[0].value}건, 진행 가능 ${rows[1].value}건, 처리 중 ${rows[2].value}건, 승인 완료 ${rows[3].value}건입니다.${note}`),
      statCards(document, rows),
    ];
    if (reviews.length === 0) {
      nodes.push(reviewEmpty(document));
    } else {
      if (shown > 0) nodes.push(donutChart(document, { title: "상태 비율", rows, unit: "건" }));
      const waiting = latestFirst(reviews.filter((review) => review.status === "awaiting_approval"));
      nodes.push(el(document, "h3", "sheet-sub", "승인 대기 건"));
      if (waiting.length === 0) {
        nodes.push(el(document, "p", "sheet-note", "승인 대기 중인 심사 건이 없습니다."));
      } else {
        const list = el(document, "ul", "sheet-rows");
        for (const review of waiting.slice(0, APPROVAL_LIMIT)) {
          list.append(row(document, `${review.corp_name ?? "-"} · ${review.fiscal_year ?? "-"}`, "승인하러 가기", reviewHref(review)));
        }
        nodes.push(list);
      }
    }
    nodes.push(actionRow(document, link(document, "승인 현황 화면 열기", "/approvals", { solid: true })));
    return nodes;
  });
}

// ---- 보고서 ----
export function panelReports(ctx) {
  const { document } = ctx;
  return withReviews(ctx, (reviews, truncated) => {
    const closed = latestFirst(reviews.filter((review) => review.status === "closed"));
    const corpCount = new Set(closed.map((review) => review.corp_code)).size;
    const note = truncated ? TRUNCATED_NOTE : "";
    const nodes = [
      leadText(document, closed.length === 0
        ? MESSAGES.noReports
        : `승인·저장된 심사 메모가 ${closed.length}건이고 기업은 ${corpCount}곳입니다.${note}`),
      statCards(document, [
        { label: "저장된 메모", value: `${closed.length}건` },
        { label: "기업 수", value: `${corpCount}곳` },
        { label: "가장 최근 생성일", value: closed.length ? formatKst(closed[0].created_at).slice(0, 10) : "-" },
      ]),
    ];
    if (closed.length === 0) {
      nodes.push(reviewEmpty(document, MESSAGES.noReports));
      return nodes;
    }
    const byYear = new Map();
    for (const review of closed) byYear.set(review.fiscal_year, (byYear.get(review.fiscal_year) ?? 0) + 1);
    const rows = [...byYear.entries()].sort((a, b) => Number(b[0]) - Number(a[0])).map(([year, value]) => ({ label: `${year}년`, value }));
    nodes.push(barChart(document, { title: "사업연도별 저장 건", rows, unit: "건" }));
    const list = el(document, "ul", "sheet-rows");
    for (const review of closed.slice(0, REPORT_LIMIT)) {
      list.append(row(document, `${review.corp_name ?? "-"} · ${review.fiscal_year ?? "-"}`, `생성 ${formatKst(review.created_at)}`, reviewHref(review)));
    }
    nodes.push(el(document, "h3", "sheet-sub", "저장된 심사 메모"), list);
    if (closed.length > REPORT_LIMIT) {
      nodes.push(el(document, "p", "sheet-note", `외 ${closed.length - REPORT_LIMIT}건은 심사 건 화면에서 확인하세요.`));
    }
    return nodes;
  });
}

// ---- 공시 검색 ----
const normalize = (text) => String(text ?? "").trim().replace(/\s+/g, " ").toLowerCase();

export function panelSearch(ctx) {
  const { document } = ctx;
  const companies = Array.isArray(ctx.companies) ? ctx.companies : [];

  const form = el(document, "form", "sheet-search");
  form.setAttribute("role", "search");
  form.setAttribute("novalidate", "");
  const inputId = ctx.inputId ?? "sheet-company-query"; // 미리보기와 시트가 동시에 있어도 id가 겹치지 않게 한다
  const label = el(document, "label", "visually-hidden", "기업 검색");
  label.setAttribute("for", inputId);
  const input = el(document, "input");
  input.setAttribute("id", inputId);
  input.setAttribute("name", "q");
  input.setAttribute("type", "search");
  input.setAttribute("maxlength", "50");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("placeholder", "기업명 또는 corp_code 검색");
  form.append(label, input);

  const results = el(document, "div", "sheet-search-results");

  function draw() {
    const needle = normalize(input.value);
    const matched = needle
      ? companies.filter((company) => normalize(company.name).includes(needle) || String(company.corp_code).includes(needle))
      : companies;
    const latest = companies.reduce((max, company) => (String(company.filed_at) > max ? String(company.filed_at) : max), "");
    const nodes = [
      leadText(document, `보유 기업 ${companies.length}곳 중 ${matched.length}곳이 검색되었습니다.`),
      statCards(document, [
        { label: "보유 기업", value: `${companies.length}곳` },
        { label: "검색 결과", value: `${matched.length}곳` },
        { label: "최신 공시일", value: latest || "-" },
      ]),
    ];
    if (matched.length === 0) {
      nodes.push(el(document, "p", "sheet-note", MESSAGES.noSearchResult));
    } else {
      const byYear = new Map();
      for (const company of matched) byYear.set(company.fiscal_year, (byYear.get(company.fiscal_year) ?? 0) + 1);
      const rows = [...byYear.entries()].sort((a, b) => Number(b[0]) - Number(a[0])).map(([year, value]) => ({ label: `${year}년`, value }));
      nodes.push(barChart(document, { title: "사업연도별 기업 수", rows, unit: "곳" }));
      const list = el(document, "ul", "sheet-rows");
      for (const company of matched.slice(0, SEARCH_LIMIT)) {
        const item = row(document, String(company.name), `corp_code ${company.corp_code} · ${company.fiscal_year} 사업보고서 · 공시 ${company.filed_at}`);
        item.append(link(document, "심사 건 만들기", CREATE_HREF));
        list.append(item);
      }
      nodes.push(list);
      if (matched.length > SEARCH_LIMIT) {
        nodes.push(el(document, "p", "sheet-note", `외 ${matched.length - SEARCH_LIMIT}곳. 검색어로 좁혀 보세요.`));
      }
    }
    results.replaceChildren(...nodes);
  }

  input.addEventListener("input", draw);
  input.addEventListener("search", draw);
  form.addEventListener("submit", (event) => {
    event?.preventDefault?.();
    draw();
  });
  draw();
  return Promise.resolve({ nodes: [form, results] });
}

// ---- 내 판정 규칙 ----
function isRulesUnavailable(status, body) {
  return status === 503 && isObject(body) && isObject(body.error) && body.error.code === "SERVICE_UNAVAILABLE_RULES";
}

export async function panelRules(ctx) {
  const { document } = ctx;
  const response = await ctx.fetch("/api/v1/rules", { credentials: "same-origin" });
  if (response.status === 401) return { unauthorized: true };
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (isRulesUnavailable(response.status, body)) {
    return { nodes: [leadText(document, MESSAGES.rulesDemo)] };
  }
  if (response.status !== 200 || !isObject(body) || !Array.isArray(body.items)) {
    return { error: messageForStatus(response.status) };
  }

  let rules = body.items.filter(isObject);
  let message = "";

  async function deleteRule(rule) {
    message = "";
    try {
      const result = await ctx.fetch(`/api/v1/rules/${encodeURIComponent(String(rule.rule_id ?? ""))}`, {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (result.status === 401) {
        ctx.onUnauthorized();
        return;
      }
      if (result.status === 204 || result.status === 404) {
        rules = rules.filter((item) => item !== rule);
        if (result.status === 404) message = MESSAGES.ruleAlreadyDeleted;
      } else if (result.status === 429) {
        message = MESSAGES.tooMany;
      } else if (result.status >= 500) {
        message = MESSAGES.server;
      } else {
        message = MESSAGES.ruleDeleteFailed;
      }
    } catch {
      message = MESSAGES.network;
    }
    ctx.replace(build());
  }

  // 두 단계 삭제: 「삭제」 → 「정말 삭제」(+「취소」). 브라우저 confirm()은 쓰지 않는다.
  function ruleRow(rule) {
    const item = el(document, "li", "sheet-rule");
    const head = el(document, "div", "sheet-rule-head");
    head.append(span(document, "sheet-row-title", own(ITEM_LABELS, rule.item) ?? UNKNOWN), el(document, "small", "", `메모 ${String(rule.source_memo_id ?? "-")}`));
    const instruction = el(document, "p", "sheet-rule-text", String(rule.instruction ?? ""));
    const remove = el(document, "button", "sheet-btn", "삭제");
    remove.setAttribute("type", "button");
    const cancel = el(document, "button", "sheet-btn", "취소");
    cancel.setAttribute("type", "button");
    cancel.hidden = true;
    let confirming = false;
    const reset = () => {
      confirming = false;
      remove.textContent = "삭제";
      remove.disabled = false;
      cancel.hidden = true;
      cancel.disabled = false;
    };
    remove.addEventListener("click", async () => {
      if (remove.disabled) return;
      if (!confirming) {
        confirming = true;
        remove.textContent = "정말 삭제";
        cancel.hidden = false;
        return;
      }
      remove.disabled = true;
      cancel.disabled = true;
      await deleteRule(rule);
    });
    cancel.addEventListener("click", reset);
    const actions = el(document, "div", "sheet-actions-inline");
    actions.append(remove, cancel);
    item.append(head, instruction, actions);
    return item;
  }

  function build() {
    ctx.setStat?.("rules", `${rules.length}개`); // 목록 쪽 수치도 함께 맞춘다
    const itemCounts = new Map();
    for (const rule of rules) {
      const name = own(ITEM_LABELS, rule.item) ?? UNKNOWN;
      itemCounts.set(name, (itemCounts.get(name) ?? 0) + 1);
    }
    const nodes = [
      leadText(document, rules.length === 0
        ? MESSAGES.noRules
        : `저장된 판정 규칙은 ${rules.length}개이고 항목은 ${itemCounts.size}종류입니다. 승인 때 고친 판정·사유가 다음 질의부터 규칙으로 쓰입니다.`),
      statCards(document, [
        { label: "규칙", value: `${rules.length}개` },
        { label: "항목 종류", value: `${itemCounts.size}개` },
      ]),
    ];
    if (message) {
      const alert = el(document, "p", "sheet-alert", message);
      alert.setAttribute("role", "alert");
      nodes.push(alert);
    }
    if (rules.length > 0) {
      const rows = [...itemCounts.entries()].map(([label, value]) => ({ label, value }));
      nodes.push(barChart(document, { title: "항목별 규칙 수", rows, unit: "개" }));
      const list = el(document, "ul", "sheet-rows sheet-rules");
      for (const rule of rules) list.append(ruleRow(rule));
      nodes.push(list);
    }
    return nodes;
  }

  return { nodes: build() };
}

export const PANELS = Object.freeze({
  search: panelSearch,
  reviews: panelReviews,
  reports: panelReports,
  approvals: panelApprovals,
  rules: panelRules,
});
