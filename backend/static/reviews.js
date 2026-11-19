import { ITEM_LABELS, UNKNOWN, own } from "./review-shared.js";
import { serviceStatusMessage } from "./service-errors.js";
import { bindSortMenu } from "./sort-menu.js";

const PAGE_SIZE = 20;
const MAX_QUESTION_LENGTH = 500;
const CLARIFY_HINT = "회사 이름과 사업연도만 적어 보내면 방금 질문에 이어서 답합니다.";
const MAX_NEWS = 5;
const ASK_LABEL = "질문하기";
const ASKING_LABEL = "분석 중…";

// 서버 오류 본문(details, request_id, message)은 화면에 쓰지 않는다. 상태 코드와 error.code만 본다.
const MESSAGES = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  listFailed: "심사 건 목록을 불러오지 못했습니다.",
  askFailed: "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  askUnknown: "알 수 없는 응답입니다. 화면을 새로 불러온 뒤 다시 시도해 주세요.",
  badInput: "입력을 확인한 뒤 다시 시도해 주세요.",
  question: "질문을 1~500자로 입력하세요",
  logout: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
  empty: "아직 심사 건이 없습니다. 위에서 질문하면 심사 건이 만들어집니다.",
  deleteFailed: "심사 건을 삭제하지 못했습니다. 다시 시도해 주세요.",
  deleteInProgress: "처리 중인 심사 건은 삭제할 수 없습니다. 잠시 후 다시 시도해 주세요.",
};
// 거절 사유 문구(심사 건 화면 review-detail.js와 같은 문구).
const REFUSAL_REASONS = {
  OUT_OF_DOMAIN: "기업여신 심사 체크리스트와 관련 없는 질문입니다.",
  CORP_NOT_IN_CORPUS: "이 회사의 공시가 아직 수집되지 않았습니다.",
  ITEM_NOT_DISCLOSED: "해당 항목에 대한 공시 근거를 찾지 못했습니다.",
  PERIOD_NOT_IN_CORPUS: "요청한 사업연도의 공시가 수집되지 않았습니다.",
  PRIOR_YEAR_NOT_IN_CORPUS: "비교할 전년도 공시가 수집되지 않았습니다.",
  INJECTION_SUSPECTED: "요청을 처리할 수 없습니다. 회사, 사업연도, 체크리스트 항목을 적어 업무 질문으로 다시 입력해 주세요.",
};
const REFUSED_BASE = "답변할 수 없는 질문입니다.";

// 내 판정 규칙(rules-web-ui Spec). 규칙 섹션 실패는 심사 건 목록 오류 영역을 쓰지 않는다.
const RULE_MESSAGES = {
  empty: "저장된 판정 규칙이 없습니다. 승인 때 판정·사유를 고치면 여기에 쌓입니다.",
  unavailable: "이 환경에서는 판정 규칙 기억을 쓰지 않습니다(데모 모드).",
  listFailed: "판정 규칙을 불러오지 못했습니다.",
  deleteFailed: "규칙을 삭제하지 못했습니다. 다시 시도해 주세요.",
  alreadyDeleted: "이미 삭제된 규칙입니다.",
};
const DELETE_LABEL = "삭제";
const CONFIRM_DELETE_LABEL = "정말 삭제";

const STATUS_LABELS = {
  open: "진행 가능",
  running: "처리 중",
  awaiting_approval: "승인 대기",
  saving: "저장 중",
  closed: "종료",
};
const UNKNOWN_STATUS_LABEL = "알 수 없음";

const isObject = (value) => typeof value === "object" && value !== null;


function normalizeQuery(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}


// 뉴스 링크는 http(s)만 건다(서버가 준 문자열을 그대로 href에 넣지 않는다).
function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
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


function listMessageFor(status) {
  return serviceStatusMessage(status) ?? (status === 429 ? MESSAGES.tooMany : MESSAGES.listFailed);
}


export function createReviewsController({ document, fetch, navigate }) {
  const loading = document.getElementById("reviews-loading");
  const currentUser = document.getElementById("current-user");
  const pageError = document.getElementById("reviews-error");
  const reloadButton = document.getElementById("reload-button");
  const empty = document.getElementById("reviews-empty");
  const list = document.getElementById("reviews-list");
  const deleteAllButton = document.getElementById("review-delete-all");
  const sortButton = document.getElementById("reviews-sort");
  const sortStatus = document.getElementById("reviews-sort-status");
  const deleteDialog = document.getElementById("review-delete-dialog");
  const deleteQuestion = document.getElementById("review-delete-question");
  const deleteError = document.getElementById("review-delete-error");
  const deleteYes = document.getElementById("review-delete-yes");
  const deleteNo = document.getElementById("review-delete-no");
  const moreButton = document.getElementById("more-button");
  const form = document.getElementById("ask-form");
  const question = document.getElementById("ask-question");
  const submitButton = document.getElementById("ask-submit");
  const askError = document.getElementById("ask-error");
  const askResult = document.getElementById("ask-result");
  const askResultMessage = document.getElementById("ask-result-message");
  const askNews = document.getElementById("ask-result-news");
  const logoutButton = document.getElementById("logout-button");
  const rulesError = document.getElementById("rules-error");
  const rulesStatus = document.getElementById("rules-status");
  const rulesList = document.getElementById("rules-list");

  let nextCursor = null;
  let sort = "recent";
  let asking = false;
  let signedOut = false;
  let pendingDelete = null;
  let deleting = false;
  let ruleItems = [];

  // 401이면 로그인 화면으로 보내고 null을 돌려준다. 호출한 쪽은 null이면 바로 멈춘다.
  async function call(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    if (response.status === 401) {
      signedOut = true;
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

  // display_name이 문자열일 때만 보여 준다. 텍스트로만 넣는다.
  async function showUserName(response) {
    const body = await readJson(response);
    const name = isObject(body) && typeof body.display_name === "string" ? body.display_name : "";
    currentUser.textContent = name ? `${name}님` : "";
  }

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

  function showAskError(message) {
    askError.textContent = message;
    askError.hidden = false;
  }

  function clearAskError() {
    askError.textContent = "";
    askError.hidden = true;
  }

  function setNextCursor(cursor) {
    nextCursor = cursor;
    moreButton.hidden = !cursor;
  }

  function updateListControls() {
    const busy = signedOut || !loading.hidden || moreButton.disabled || asking || deleting;
    deleteAllButton.disabled = busy;
    sortButton.setAttribute("aria-disabled", String(busy));
  }

  function span(className, text) {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
  }

  // 서버가 준 모든 값은 textContent로만 넣는다. HTML로 해석하지 않는다.
  function renderReview(review) {
    const item = document.createElement("li");
    item.className = "review-item";

    const title = document.createElement("div");
    title.className = "review-title";
    // 상세 화면 주소는 문자열로만 조립하고 id는 인코딩한다.
    const name = document.createElement("a");
    name.className = "review-name";
    name.setAttribute("href", `/reviews/${encodeURIComponent(String(review.id ?? ""))}`);
    name.textContent = String(review.corp_name ?? "-");
    title.append(name, span("review-code", String(review.corp_code ?? "")));

    const meta = document.createElement("div");
    meta.className = "review-meta";
    meta.append(
      span("review-year", `${review.fiscal_year ?? "-"} 사업연도`),
      span("review-status", STATUS_LABELS[review.status] ?? UNKNOWN_STATUS_LABEL),
      span("review-count", `질의 ${review.query_count ?? 0}건`),
      span("review-created", `생성 ${formatKst(review.created_at)}`),
    );

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "review-delete-button secondary";
    deleteButton.setAttribute("aria-label", `${String(review.corp_name ?? "심사 건")} 심사 건 삭제`);
    deleteButton.textContent = "🗑";
    deleteButton.addEventListener("click", () => {
      openDelete(`/api/v1/reviews/${encodeURIComponent(String(review.id))}`, item);
    });

    item.append(title, meta, deleteButton);
    return item;
  }

  function openDelete(path, item) {
    pendingDelete = { path, item };
    deleteQuestion.textContent = item ? "해당 심사건 내역을 삭제하겠습니까?" : "전체 심사건 내역을 삭제하시겠습니까?";
    deleteError.hidden = true;
    deleteError.textContent = "";
    deleteDialog.showModal();
    deleteNo.focus();
  }

  function cancelDelete() {
    if (deleting) return;
    deleteDialog.close();
    pendingDelete = null;
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return;
    deleting = true;
    deleteYes.disabled = true;
    deleteNo.disabled = true;
    deleteError.hidden = true;
    const { path, item } = pendingDelete;
    try {
      const response = await call(path, { method: "DELETE" });
      if (!response) return;
      if (response.status === 204) {
        if (item) {
          list.replaceChildren(...Array.from(list.children).filter((element) => element !== item));
        } else {
          list.replaceChildren();
          setNextCursor(null);
          clearPageError();
        }
        empty.textContent = MESSAGES.empty;
        empty.hidden = list.children.length > 0;
        deleteDialog.close();
        pendingDelete = null;
      } else {
        deleteError.textContent = response.status === 409 ? MESSAGES.deleteInProgress
          : response.status === 429 ? MESSAGES.tooMany
          : response.status >= 500 ? MESSAGES.server : MESSAGES.deleteFailed;
        deleteError.hidden = false;
      }
    } catch {
      deleteError.textContent = MESSAGES.network;
      deleteError.hidden = false;
    } finally {
      deleting = false;
      deleteYes.disabled = false;
      deleteNo.disabled = false;
      updateListControls();
    }
  }

  async function readPage(response) {
    const body = await readJson(response);
    if (!isObject(body) || !Array.isArray(body.items)) return null;
    return {
      items: body.items.filter(isObject),
      nextCursor: typeof body.next_cursor === "string" && body.next_cursor ? body.next_cursor : null,
    };
  }

  function listPath(cursor) {
    const sortQuery = sort === "recent" ? "" : `&sort=${sort}`;
    const base = `/api/v1/reviews?page_size=${PAGE_SIZE}${sortQuery}`;
    return cursor ? `${base}&cursor=${encodeURIComponent(cursor)}` : base;
  }

  async function loadFirstPage() {
    try {
      const response = await call(listPath(null));
      if (!response) return;
      if (response.status !== 200) {
        showPageError(listMessageFor(response.status), true);
        return;
      }
      const page = await readPage(response);
      if (!page) {
        showPageError(MESSAGES.listFailed, true);
        return;
      }
      list.replaceChildren(...page.items.map(renderReview));
      empty.textContent = MESSAGES.empty;
      empty.hidden = page.items.length > 0;
      setNextCursor(page.nextCursor);
      return true;
    } catch {
      showPageError(MESSAGES.listFailed, true);
    }
  }

  async function changeSort(key, label) {
    if (key === sort) return;
    sort = key;
    sortStatus.textContent = "";
    loading.hidden = false;
    updateListControls();
    clearPageError();
    list.replaceChildren();
    empty.hidden = true;
    setNextCursor(null);
    try {
      if (await loadFirstPage()) sortStatus.textContent = `${label}으로 정렬했습니다.`;
    } finally {
      loading.hidden = true;
      updateListControls();
    }
  }

  function showRulesError(message) {
    rulesError.textContent = message;
    rulesError.hidden = false;
  }

  function clearRulesError() {
    rulesError.textContent = "";
    rulesError.hidden = true;
  }

  function showRulesStatus(message) {
    rulesStatus.textContent = message;
    rulesStatus.hidden = !message;
  }

  function showRuleItems(items) {
    ruleItems = items;
    rulesList.replaceChildren(...ruleItems);
    showRulesStatus(ruleItems.length === 0 ? RULE_MESSAGES.empty : "");
  }

  // 규칙 하나 = 카드 하나. 서버 값은 textContent로만 넣는다.
  function renderRule(rule) {
    const item = document.createElement("li");
    item.className = "review-item rule-item";

    const title = document.createElement("div");
    title.className = "review-title";
    title.append(
      span("rule-item-label", own(ITEM_LABELS, rule.item) ?? UNKNOWN),
      span("review-code", `메모 ${String(rule.source_memo_id ?? "-")}`),
    );

    const instruction = document.createElement("p");
    instruction.className = "rule-instruction";
    instruction.textContent = String(rule.instruction ?? "");

    // 두 단계 삭제: 「삭제」 → 「정말 삭제」(+「취소」). 브라우저 confirm()은 쓰지 않는다.
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "secondary";
    deleteButton.textContent = DELETE_LABEL;
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "secondary";
    cancelButton.textContent = "취소";
    cancelButton.hidden = true;

    const controls = { deleteButton, cancelButton, confirming: false };
    const resetControls = () => {
      controls.confirming = false;
      deleteButton.textContent = DELETE_LABEL;
      deleteButton.disabled = false;
      cancelButton.hidden = true;
      cancelButton.disabled = false;
    };
    deleteButton.addEventListener("click", async () => {
      if (deleteButton.disabled) return;
      if (!controls.confirming) {
        controls.confirming = true;
        deleteButton.textContent = CONFIRM_DELETE_LABEL;
        cancelButton.hidden = false;
        return;
      }
      await deleteRule(rule, item, resetControls, controls);
    });
    cancelButton.addEventListener("click", resetControls);

    const actions = document.createElement("div");
    actions.className = "rule-actions";
    actions.append(deleteButton, cancelButton);

    item.append(title, instruction, actions);
    return item;
  }

  async function deleteRule(rule, item, resetControls, controls) {
    controls.deleteButton.disabled = true;
    controls.cancelButton.disabled = true;
    clearRulesError();
    try {
      const response = await call(`/api/v1/rules/${encodeURIComponent(String(rule.rule_id ?? ""))}`, {
        method: "DELETE",
      });
      if (!response) return;
      if (response.status === 204 || response.status === 404) {
        showRuleItems(ruleItems.filter((element) => element !== item));
        if (response.status === 404) showRulesError(RULE_MESSAGES.alreadyDeleted);
        return;
      }
      if (response.status === 429) showRulesError(MESSAGES.tooMany);
      else if (response.status >= 500) showRulesError(serviceStatusMessage(response.status) ?? MESSAGES.server);
      else showRulesError(RULE_MESSAGES.deleteFailed);
    } catch {
      showRulesError(MESSAGES.network);
    }
    resetControls();
  }

  async function loadRules() {
    clearRulesError();
    try {
      const response = await call("/api/v1/rules");
      if (!response) return;
      const body = await readJson(response);
      if (response.status === 503 && isObject(body) && isObject(body.error)
          && body.error.code === "SERVICE_UNAVAILABLE_RULES") {
        ruleItems = [];
        rulesList.replaceChildren();
        showRulesStatus(RULE_MESSAGES.unavailable);
        return;
      }
      if (response.status !== 200 || !isObject(body) || !Array.isArray(body.items)) {
        showRulesStatus("");
        showRulesError(serviceStatusMessage(response.status) ?? (response.status === 429 ? MESSAGES.tooMany : RULE_MESSAGES.listFailed));
        return;
      }
      showRuleItems(body.items.filter(isObject).map(renderRule));
    } catch {
      showRulesStatus("");
      showRulesError(RULE_MESSAGES.listFailed);
    }
  }

  // 세션 확인 → 첫 쪽 불러오기 → 내 판정 규칙. 「다시 불러오기」도 같은 길을 다시 탄다.
  async function start() {
    loading.hidden = false;
    updateListControls();
    clearPageError();
    try {
      const me = await call("/api/v1/auth/me");
      if (!me) return;
      if (me.status !== 200) {
        showPageError(listMessageFor(me.status), true);
        return;
      }
      await showUserName(me);
      await loadFirstPage();
      // 규칙 섹션은 심사 건 목록이 실패해도 따로 불러온다. 401로 로그인 화면에 보냈으면 멈춘다.
      if (!signedOut) await loadRules();
    } catch {
      showPageError(MESSAGES.listFailed, true);
    } finally {
      loading.hidden = true;
      updateListControls();
    }
  }

  async function loadMore() {
    if (moreButton.disabled || asking || !loading.hidden || !nextCursor) return;
    moreButton.disabled = true;
    updateListControls();
    clearPageError();
    try {
      const response = await call(listPath(nextCursor));
      if (!response) return;
      if (response.status !== 200) {
        showPageError(listMessageFor(response.status));
        return;
      }
      const page = await readPage(response);
      if (!page) {
        showPageError(MESSAGES.listFailed);
        return;
      }
      list.append(...page.items.map(renderReview));
      setNextCursor(page.nextCursor);
    } catch {
      showPageError(MESSAGES.listFailed);
    } finally {
      moreButton.disabled = false;
      updateListControls();
    }
  }

  function showAskNews(items) {
    const news = (Array.isArray(items) ? items.filter(isObject) : []).slice(0, MAX_NEWS);
    askNews.replaceChildren(
      ...news.map((entry) => {
        const item = document.createElement("li");
        const title = String(entry.title ?? "") || UNKNOWN;
        const href = safeHttpUrl(entry.originallink) ?? safeHttpUrl(entry.link);
        if (href) {
          const link = document.createElement("a");
          link.textContent = title;
          link.href = href;
          link.setAttribute("target", "_blank");
          link.setAttribute("rel", "noopener noreferrer");
          item.append(link);
        } else {
          item.append(span("news-reference-title", title));
        }
        return item;
      }),
    );
    askNews.hidden = news.length === 0;
  }

  // 심사 건 없이 끝난 응답(거절, 되묻기)을 이 화면에 보여 준다. 서버가 준 안내 문구가 있으면 그것을 쓴다.
  function showAskResult(data) {
    const message = typeof data.refusal_message === "string" ? data.refusal_message.trim() : "";
    const reason = own(REFUSAL_REASONS, data.refusal_reason);
    if (message) askResultMessage.textContent = message;
    else if (data.outcome === "refused") askResultMessage.textContent = reason ? `${REFUSED_BASE} ${reason}` : REFUSED_BASE;
    else askResultMessage.textContent = MESSAGES.askUnknown;
    showAskNews(data.outcome === "refused" ? data.news_items : []);
    askResult.hidden = false;
  }

  function clearAskResult() {
    askResultMessage.textContent = "";
    askNews.replaceChildren();
    askNews.hidden = true;
    askResult.hidden = true;
  }

  function showAskFailure(status) {
    if (status === 400 || status === 422) showAskError(MESSAGES.badInput);
    else if (status === 429) showAskError(MESSAGES.tooMany);
    else if (status >= 500) showAskError(serviceStatusMessage(status) ?? MESSAGES.server);
    else showAskError(MESSAGES.askFailed);
  }

  // 되물음(target_required)을 받은 원래 질문. 있으면 다음 질문을 그 답으로 함께 보낸다(서버가 합친다, 162번).
  let pendingClarification = "";

  // 질문만 보낸다. 서버가 질문에서 회사·연도를 찾아 심사 건을 만들었으면 그 심사 건 화면(승인)으로 간다.
  async function askDirectly(text) {
    if (asking || moreButton.disabled || !loading.hidden) return;
    asking = true;
    updateListControls();
    submitButton.disabled = true;
    submitButton.textContent = ASKING_LABEL;
    clearAskError();
    clearAskResult();
    try {
      const response = await call("/api/v1/queries", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(pendingClarification ? { question: text, clarifies: pendingClarification } : { question: text }),
      });
      if (!response) return;
      if (response.status !== 200) {
        showAskFailure(response.status);
        return;
      }
      const clarified = pendingClarification;
      pendingClarification = "";
      const data = await readJson(response);
      if (!isObject(data)) {
        showAskError(MESSAGES.askFailed);
        return;
      }
      const reviewId = isObject(data.review) && typeof data.review.id === "string" ? data.review.id : "";
      if (reviewId) {
        navigate(`/reviews/${encodeURIComponent(reviewId)}`);
        return;
      }
      showAskResult(data);
      if (data.target_required === true) {
        // 답에도 회사가 없어 다시 되물음을 받으면 처음 질문을 그대로 기억한다.
        pendingClarification = clarified || text;
        askResultMessage.textContent = `${askResultMessage.textContent} ${CLARIFY_HINT}`;
      }
    } catch {
      showAskError(MESSAGES.network);
    } finally {
      asking = false;
      updateListControls();
      submitButton.disabled = false;
      submitButton.textContent = ASK_LABEL;
    }
  }

  async function submitAsk(event) {
    event.preventDefault();
    if (asking) return;
    clearAskError();

    const text = normalizeQuery(question.value);
    if (text.length < 1 || text.length > MAX_QUESTION_LENGTH) {
      showAskError(MESSAGES.question);
      question.focus();
      return;
    }
    await askDirectly(text);
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

  form.addEventListener("submit", submitAsk);
  for (const example of form.querySelectorAll("[data-question-example]")) {
    example.addEventListener("click", () => {
      if (asking) return;
      question.value = example.dataset.questionExample;
      clearAskError();
      clearAskResult();
      question.focus();
    });
  }
  bindSortMenu(document, "reviews-sort", changeSort);
  deleteAllButton.addEventListener("click", () => {
    if (!deleteAllButton.disabled) openDelete("/api/v1/reviews", null);
  });
  deleteYes.addEventListener("click", confirmDelete);
  deleteNo.addEventListener("click", cancelDelete);
  deleteDialog.addEventListener("close", () => { pendingDelete = null; });
  deleteDialog.addEventListener("cancel", (event) => {
    if (deleting) event.preventDefault();
    else pendingDelete = null;
  });
  moreButton.addEventListener("click", loadMore);
  reloadButton.addEventListener("click", start);
  logoutButton.addEventListener("click", logout);

  return { initialize: start };
}


if (typeof window !== "undefined") {
  createReviewsController({
    document: window.document,
    fetch: window.fetch,
    navigate: (path) => window.location.assign(path),
  }).initialize();
}
