import { createApprovalPanel } from "./approval-panel.js?v=citation-status";
import { COMMON_MESSAGES, ITEM_LABELS, UNKNOWN, VERDICT_LABELS, isObject, own } from "./review-shared.js";
import { serviceStatusMessage } from "./service-errors.js";
import { formatReviewReason, renderReviewReason } from "./review-reason.js";

const MAX_QUESTION_LENGTH = 500;
const ASK_LABEL = "추가 질문 보내기";
const ASKING_LABEL = "분석 중…";
const REVIEW_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// 서버 오류 본문(details, request_id, message)은 화면에 쓰지 않는다. 상태 코드만 본다.
const MESSAGES = {
  ...COMMON_MESSAGES,
  badInput: "입력을 확인한 뒤 다시 시도해 주세요.",
  loadFailed: "심사 건을 불러오지 못했습니다.",
  queryFailed: "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  question: "질문을 1~500자로 입력하세요",
  // 409: 다른 처리 중이거나, 이 심사 건의 하루 질의 한도(서버가 센다)를 넘었거나, 승인이 끝나 닫힌 건이다
  conflict: "지금은 질문할 수 없습니다. 처리 중이거나 이 심사 건의 오늘 질의 한도를 넘었습니다. 화면을 새로 불러옵니다",
  tooMany: "질의가 너무 잦거나 오늘 질의 한도를 모두 썼습니다. 잠시 후 다시 시도해 주세요.",
  logout: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
};

const STATUS_LABELS = {
  open: "진행 가능",
  running: "처리 중",
  awaiting_approval: "승인 대기",
  saving: "저장 중",
  closed: "종료",
};

const BLOCKED_HINTS = {
  running: "처리 중인 질의가 있어 지금은 질문할 수 없습니다.",
  saving: "저장 중입니다. 잠시 후 화면을 새로 불러와 주세요.",
  closed: "승인이 끝나 종료된 심사 건입니다. 더 질문하려면 새 심사 건을 만들어 주세요.",
};
const UNKNOWN_STATE_HINT = "지금은 질문할 수 없는 상태입니다.";

const REFUSAL_REASONS = {
  OUT_OF_DOMAIN: "기업여신 심사 체크리스트와 관련 없는 질문입니다.",
  CORP_NOT_IN_CORPUS: "이 회사의 공시가 아직 수집되지 않았습니다.",
  ITEM_NOT_DISCLOSED: "해당 항목에 대한 공시 근거를 찾지 못했습니다.",
  PERIOD_NOT_IN_CORPUS: "요청한 사업연도의 공시가 수집되지 않았습니다.",
  PRIOR_YEAR_NOT_IN_CORPUS: "비교할 전년도 공시가 수집되지 않았습니다.",
  INJECTION_SUSPECTED: "요청을 처리할 수 없습니다. 회사, 사업연도, 체크리스트 항목을 적어 업무 질문으로 다시 입력해 주세요.",
};
const REFUSED_BASE = "답변할 수 없는 질문입니다.";
const APPROVAL_NOTICE = "이 질문으로 검토할 초안을 만들었습니다.";
const UNKNOWN_OUTCOME_NOTICE = "알 수 없는 응답입니다. 화면을 새로 불러온 뒤 다시 시도해 주세요.";


function normalizeQuestion(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}


// UTC ISO 8601을 한국 시간 "YYYY-MM-DD HH:mm"으로 바꾼다. 해석할 수 없으면 "-".
function formatKst(isoText) {
  const date = new Date(isoText);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type).value;
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
}


// 질문할 수 없는 이유. 질문할 수 있으면 null. 하루 질의 한도는 서버가 세고 409·429로 알려 준다(화면은 세지 않는다).
function blockedHint(review) {
  if (review.status !== "open" && review.status !== "awaiting_approval") {
    return own(BLOCKED_HINTS, review.status) ?? UNKNOWN_STATE_HINT;
  }
  return null;
}


export function createReviewDetailController({ document, fetch, navigate, reviewId, confirm = () => false }) {
  const el = (id) => document.getElementById(id);
  const loading = el("detail-loading");
  const currentUser = el("current-user");
  const logoutButton = el("logout-button");
  const pageError = el("detail-error");
  const reloadButton = el("detail-reload");
  const missing = el("detail-missing");
  const body = el("detail-body");
  const querySection = el("query-section");
  const form = el("query-form");
  const question = el("question");
  const submitButton = el("query-submit");
  const queryError = el("query-error");
  const queryHint = el("query-hint");
  const queryScope = el("query-scope");
  const conversation = el("review-conversation");
  const turnTemplate = el("review-turn-template");
  const approvalRules = el("approval-rules");
  const approvalRulesList = el("approval-rules-list");

  const reviewPath = reviewId ? `/api/v1/reviews/${encodeURIComponent(reviewId)}` : null;
  let currentReview = null;
  let asking = false;
  let lastDraft = null;
  let loadFailureMessage = MESSAGES.loadFailed;

  // 401이면 로그인 화면으로 보내고 null을 돌려준다. 호출한 쪽은 null이면 바로 멈춘다.
  async function call(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    if (response.status === 401) {
      navigate("/");
      return null;
    }
    return response;
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  const approvalPanel = createApprovalPanel({
    document,
    request: call,
    confirm,
    renderCitation,
    approvalsPath: reviewPath ? `${reviewPath}/approvals` : "",
    onSettled: settleApproval,
    onMissing: () => showMissing(),
  });

  function showPageError(message, withReload = false) {
    pageError.textContent = message;
    pageError.hidden = false;
    reloadButton.hidden = !withReload;
  }

  function clearPageError() {
    pageError.textContent = "";
    pageError.hidden = true;
    reloadButton.hidden = true;
  }

  function showQueryError(message) {
    queryError.textContent = message;
    queryError.hidden = false;
  }

  function clearQueryError() {
    queryError.textContent = "";
    queryError.hidden = true;
  }

  function showMissing() {
    missing.hidden = false;
    body.hidden = true;
    querySection.hidden = true;
    conversation.hidden = true;
    conversation.replaceChildren();
    approvalPanel.hide();
    loading.hidden = true;
  }

  // 질문 입력 상태를 현재 심사 건에 맞춘다. 요청 중에는 asking이 버튼을 따로 막는다.
  function syncQueryControls() {
    const hint = currentReview ? blockedHint(currentReview) : UNKNOWN_STATE_HINT;
    question.disabled = asking || hint !== null;
    submitButton.disabled = hint !== null || asking;
    queryHint.textContent = hint ?? (currentReview?.status === "awaiting_approval"
      ? "추가 답변을 참고해 위의 초안을 검토하고 최종 승인하세요."
      : "");
    queryHint.hidden = !queryHint.textContent;
  }

  function showReview(review) {
    currentReview = review;
    el("review-name").textContent = String(review.corp_name ?? "-");
    el("review-code").textContent = String(review.corp_code ?? "");
    el("review-year").textContent = `${review.fiscal_year ?? "-"} 사업연도`;
    el("review-status").textContent = own(STATUS_LABELS, review.status) ?? UNKNOWN;
    el("review-count").textContent = `질의 ${Number(review.query_count) || 0}회`;
    el("review-created").textContent = `생성 ${formatKst(review.created_at)}`;
    body.hidden = false;
    missing.hidden = true;
    querySection.hidden = false;
    queryScope.textContent = `${review.corp_name ?? "-"} · ${review.fiscal_year ?? "-"} 사업연도`;
    syncQueryControls();
    if (review.status === "awaiting_approval") {
      const pending = review.pending_draft == null ? lastDraft : {
        items: review.pending_draft,
        question: review.pending_question,
        rules: review.pending_applied_rules,
      };
      approvalPanel.show(pending?.items, pending?.question);
      showAppliedRules(approvalRules, approvalRulesList, pending?.rules);
      if (conversation.hidden && typeof pending?.question === "string" && pending.question.trim()) {
        showResult(pending.question, { outcome: "awaiting_approval", draft: pending.items });
      }
    } else {
      lastDraft = null;
      approvalPanel.hide();
      showAppliedRules(approvalRules, approvalRulesList, []);
    }
  }

  // 승인·취소·충돌 뒤 심사 건을 다시 불러온다. 승인 요청은 다시 보내지 않는다.
  async function settleApproval() {
    lastDraft = null;
    const state = await loadReview();
    if (state === "missing") showMissing();
    else if (state === "failed") showPageError(loadFailureMessage, true);
    return state;
  }

  // 'ok' | 'missing' | 'failed' | 'unauthorized'
  async function loadReview() {
    if (!reviewPath) return "missing";
    loadFailureMessage = MESSAGES.loadFailed;
    try {
      const response = await call(reviewPath);
      if (!response) return "unauthorized";
      if (response.status === 404) return "missing";
      if (response.status !== 200) {
        loadFailureMessage = serviceStatusMessage(response.status) ?? MESSAGES.loadFailed;
        return "failed";
      }
      const review = await readJson(response);
      if (!isObject(review)) return "failed";
      showReview(review);
      return "ok";
    } catch {
      return "failed";
    }
  }

  async function showUserName(response) {
    const data = await readJson(response);
    const name = isObject(data) && typeof data.display_name === "string" ? data.display_name : "";
    currentUser.textContent = name ? `${name}님` : "";
  }

  // 세션 확인 → 심사 건 불러오기. 「다시 불러오기」도 같은 길을 다시 탄다.
  async function start() {
    loading.hidden = false;
    clearPageError();
    try {
      const me = await call("/api/v1/auth/me");
      if (!me) return;
      if (me.status !== 200) {
        showPageError(serviceStatusMessage(me.status) ?? MESSAGES.loadFailed, true);
        return;
      }
      await showUserName(me);
      const state = await loadReview();
      if (state === "missing") showMissing();
      else if (state === "failed") showPageError(loadFailureMessage, true);
    } catch {
      showPageError(MESSAGES.loadFailed, true);
    } finally {
      loading.hidden = true;
    }
  }

  function textElement(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function renderCitation(citation) {
    const item = document.createElement("li");
    item.className = "citation";

    const parts = [];
    if (citation.chapter) parts.push(`장 ${citation.chapter}`);
    if (citation.note_no) parts.push(`주석 ${citation.note_no}`);
    if (citation.rcept_no) parts.push(String(citation.rcept_no));
    const meta = textElement("span", "citation-meta", parts.join(" · "));

    const verification = isObject(citation.verification) ? citation.verification : null;
    let checkText = "인용 검증 정보 없음";
    let checkClass = "citation-check";
    if (verification && verification.passed === true) {
      checkText = "인용 검증 통과";
      checkClass += " check-pass";
    } else if (verification && verification.passed === false) {
      const failed = Array.isArray(verification.failed_checks) ? verification.failed_checks.length : 0;
      checkText = `인용 검증 실패 (${failed}건)`;
      checkClass += " check-fail";
    }
    const check = textElement("span", checkClass, checkText);

    const details = document.createElement("details");
    details.className = "citation-quote";
    details.append(textElement("summary", "citation-summary", "인용문 보기"), textElement("p", "quote", String(citation.quote ?? "")));

    item.append(meta, check, details);
    return item;
  }

  function renderDraft(draft, followUpQuestion) {
    const item = document.createElement("li");
    item.className = "draft-item";

    const verdictLabel = draft.verdict === "needs_check" ? "확인 필요" : own(VERDICT_LABELS, draft.verdict);
    const verdictClass = verdictLabel ? `verdict-${draft.verdict.replace(/_/g, "-")}` : "verdict-unknown";
    const head = document.createElement("div");
    head.className = "draft-head";
    head.append(
      textElement("span", "draft-item-name", own(ITEM_LABELS, draft.item)
        ?? (draft.item === followUpQuestion && followUpQuestion ? "추가 질문 답변" : UNKNOWN)),
      textElement("span", `draft-verdict ${verdictClass}`, verdictLabel ?? UNKNOWN),
    );

    const citations = Array.isArray(draft.citations) ? draft.citations.filter(isObject) : [];
    const list = document.createElement("ul");
    list.className = "citation-list";
    list.append(...citations.map(renderCitation));

    const reason = renderReviewReason(document, formatReviewReason(draft.reason, citations));
    reason.className += " draft-reason";
    item.append(head, reason, list);
    return item;
  }

  function showAnswer(turn, data, followUpQuestion) {
    const resultAnswer = turn("answer");
    const answer = typeof data.answer === "string" ? data.answer : "";
    resultAnswer.textContent = answer;
    resultAnswer.hidden = answer === "";
    const drafts = Array.isArray(data.draft) ? data.draft.filter(isObject) : [];
    turn("drafts").append(...drafts.map((draft) => renderDraft(draft, followUpQuestion)));
    if (data.outcome !== "awaiting_approval") {
      showAppliedRules(turn("rules"), turn("rules-list"), data.applied_rules);
    }
  }

  // 작성기에 함께 넘긴 내 규칙(질문과 의미가 가까운 상위 5개). 판정을 바꿨다는 뜻이 아니라 "참고"로만 보인다.
  function showAppliedRules(section, list, rules) {
    const items = Array.isArray(rules) ? rules.filter(isObject) : [];
    list.replaceChildren(
      ...items.map((rule) => {
        const item = document.createElement("li");
        item.append(
          textElement("span", "applied-rule-name", own(ITEM_LABELS, rule.item) ?? UNKNOWN),
          textElement("span", "applied-rule-instruction", String(rule.instruction ?? "")),
        );
        return item;
      }),
    );
    section.hidden = items.length === 0;
  }

  // 뉴스 링크는 http·https 주소만 연다(javascript: 같은 주소는 링크로 만들지 않는다).
  function safeNewsUrl(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch {
      return null;
    }
  }

  // 네이버가 준 영문 날짜("Wed, 23 Sep 2026 07:02:00 +0900")를 한국 시간 YYYY-MM-DD로 바꾼다. 못 읽으면 원문 그대로.
  function formatNewsDate(value) {
    if (typeof value !== "string" || value === "") return "";
    const time = Date.parse(value);
    if (Number.isNaN(time)) return value;
    return new Date(time).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
  }

  const NEWS_SUMMARY_MAX = 120;

  // 거절 응답에 붙은 참고 뉴스(최대 5건). 공시 근거가 아니므로 초안·인용과 다른 영역에 둔다.
  function showNews(turn, items) {
    const news = (Array.isArray(items) ? items.filter(isObject) : []).slice(0, 5);
    turn("news-list").append(
      ...news.map((entry) => {
        const item = document.createElement("li");
        const title = String(entry.title ?? "") || UNKNOWN;
        const href = safeNewsUrl(entry.originallink) ?? safeNewsUrl(entry.link);
        if (href) {
          const link = textElement("a", "news-reference-title", title);
          link.href = href;
          link.setAttribute("target", "_blank");
          link.setAttribute("rel", "noopener noreferrer");
          item.append(link);
        } else {
          item.append(textElement("span", "news-reference-title", title));
        }
        const meta = [entry.source_domain, formatNewsDate(entry.pub_date)].filter((v) => typeof v === "string" && v !== "").join(" · ");
        if (meta) item.append(textElement("span", "news-reference-meta", meta));
        const summary = typeof entry.description === "string" ? entry.description.trim() : "";
        if (summary) {
          const cut = summary.length > NEWS_SUMMARY_MAX ? `${summary.slice(0, NEWS_SUMMARY_MAX)}…` : summary;
          item.append(textElement("span", "news-reference-summary", cut));
        }
        return item;
      }),
    );
    turn("news").hidden = news.length === 0;
  }

  // 서버가 준 모든 값은 textContent로만 넣는다. HTML로 해석하지 않는다.
  function showResult(text, data, followUp = false) {
    const fragment = turnTemplate.content.cloneNode(true);
    const turn = (name) => fragment.querySelector(`[data-result="${name}"]`);
    turn("question").textContent = text;
    const notice = turn("notice");
    if (data.outcome === "answered" || data.outcome === "awaiting_approval") {
      showAnswer(turn, data, followUp ? text : undefined);
      if (data.outcome === "awaiting_approval") notice.textContent = APPROVAL_NOTICE;
    } else if (data.outcome === "refused") {
      const reason = own(REFUSAL_REASONS, data.refusal_reason);
      // 뉴스 분기가 켜진 서버는 거절 종류별 안내 문구(refusal_message)를 함께 준다. 없으면 기존 문구.
      const message = typeof data.refusal_message === "string" ? data.refusal_message.trim() : "";
      notice.textContent = message || (reason ? `${REFUSED_BASE} ${reason}` : REFUSED_BASE);
      showNews(turn, data.news_items);
    } else {
      notice.textContent = UNKNOWN_OUTCOME_NOTICE;
    }
    notice.hidden = !notice.textContent;
    conversation.hidden = false;
    conversation.append(fragment);
  }

  async function showQueryFailure(response) {
    const status = response.status;
    if (status === 409) {
      showQueryError(MESSAGES.conflict);
      await loadReview();
    } else if (status === 429) showQueryError(MESSAGES.tooMany);
    else if (status === 400) showQueryError(MESSAGES.badInput);
    else if (status >= 500) showQueryError(serviceStatusMessage(status) ?? MESSAGES.server);
    else showQueryError(MESSAGES.queryFailed);
  }

  async function ask(text) {
    const followUp = currentReview.status === "awaiting_approval";
    asking = true;
    approvalPanel.setAsking(true);
    clearQueryError();
    approvalPanel.clearDone();
    submitButton.textContent = ASKING_LABEL;
    syncQueryControls();
    try {
      const response = await call(`${reviewPath}/queries${followUp ? ":follow-up" : ""}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      if (!response) return;
      if (response.status !== 200) {
        await showQueryFailure(response);
        return;
      }
      const data = await readJson(response);
      if (!isObject(data)) {
        showQueryError(MESSAGES.queryFailed);
        return;
      }
      showResult(text, data, followUp);
      question.value = "";
      // 새로 불러오지 못해도 승인 대기 결과면 더 묻지 못하게 먼저 잠근다.
      if (!followUp && data.outcome === "awaiting_approval" && currentReview) {
        lastDraft = { items: data.draft, question: text, rules: data.applied_rules };
        showReview({ ...currentReview, status: "awaiting_approval" });
      }
      await loadReview();
    } catch {
      showQueryError(MESSAGES.network);
    } finally {
      asking = false;
      approvalPanel.setAsking(false);
      submitButton.textContent = ASK_LABEL;
      syncQueryControls();
    }
  }

  async function submitQuestion(event) {
    event.preventDefault();
    if (asking || approvalPanel.isBusy() || !currentReview || blockedHint(currentReview) !== null) return;
    clearQueryError();

    const text = normalizeQuestion(question.value);
    if (text.length < 1 || text.length > MAX_QUESTION_LENGTH) {
      showQueryError(MESSAGES.question);
      question.focus();
      return;
    }
    await ask(text);
  }

  async function logout() {
    try {
      const response = await call("/api/v1/auth/logout", { method: "POST" });
      if (!response) return;
      if (response.status !== 204) {
        showPageError(MESSAGES.logout);
        return;
      }
      navigate("/");
    } catch {
      showPageError(MESSAGES.logout);
    }
  }

  form.addEventListener("submit", submitQuestion);
  reloadButton.addEventListener("click", start);
  logoutButton.addEventListener("click", logout);

  return { initialize: start };
}


function reviewIdFromPath(pathname) {
  const match = /^\/reviews\/([^/]+)\/?$/.exec(pathname);
  if (!match) return null;
  let id;
  try {
    id = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  return REVIEW_ID_PATTERN.test(id) ? id : null;
}


if (typeof window !== "undefined") {
  createReviewDetailController({
    document: window.document,
    fetch: window.fetch.bind(window),
    navigate: (path) => window.location.assign(path),
    reviewId: reviewIdFromPath(window.location.pathname),
    confirm: (message) => window.confirm(message),
  }).initialize();
}
