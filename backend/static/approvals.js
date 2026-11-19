// 승인 현황 화면(approvals-web-ui Spec). 새 API 없이 GET /api/v1/reviews를 받아 상태별로 거른다.
const PAGE_SIZE = 50;
const MAX_PAGES = 5;

const MESSAGES = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  loadFailed: "승인 현황을 불러오지 못했습니다.",
  logout: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
};

// 화면에 보이는 세 상태. 그 밖의 status(저장 중·종료·알 수 없음)는 어느 쪽에도 넣지 않는다.
const STATUSES = [
  { key: "awaiting_approval", label: "승인 대기", empty: "승인 대기 중인 심사 건이 없습니다.", open: "승인하러 가기" },
  { key: "open", label: "진행 가능", empty: "진행 가능한 심사 건이 없습니다.", open: "열기" },
  { key: "running", label: "처리 중", empty: "처리 중인 심사 건이 없습니다.", open: "열기" },
];

const isObject = (value) => typeof value === "object" && value !== null;

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

function messageFor(status) {
  if (status === 429) return MESSAGES.tooMany;
  if (status >= 500) return MESSAGES.server;
  return MESSAGES.loadFailed;
}

function listPath(cursor) {
  const base = `/api/v1/reviews?page_size=${PAGE_SIZE}`;
  return cursor ? `${base}&cursor=${encodeURIComponent(cursor)}` : base;
}


export function createApprovalsController({ document, fetch, navigate }) {
  const loading = document.getElementById("approvals-loading");
  const currentUser = document.getElementById("current-user");
  const pageError = document.getElementById("approvals-error");
  const reloadButton = document.getElementById("reload-button");
  const notice = document.getElementById("approvals-notice");
  const statusLine = document.getElementById("approvals-status");
  const empty = document.getElementById("approvals-empty");
  const list = document.getElementById("approvals-list");
  const logoutButton = document.getElementById("logout-button");
  const buttons = new Map(STATUSES.map((item) => [item.key, document.getElementById(`filter-${item.key}`)]));

  let reviews = [];
  let truncated = false;
  let selected = STATUSES[0].key;
  let loadingNow = false;

  // 401이면 로그인 화면으로 보내고 null을 돌려준다.
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

  function span(className, text) {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
  }

  // 서버가 준 모든 값은 textContent로만 넣는다.
  function renderReview(review, config) {
    const item = document.createElement("li");
    item.className = "review-item approval-item";

    const title = document.createElement("div");
    title.className = "review-title";
    title.append(span("approval-name", String(review.corp_name ?? "-")), span("review-code", String(review.corp_code ?? "")));

    const meta = document.createElement("div");
    meta.className = "review-meta";
    meta.append(
      span("review-year", `${review.fiscal_year ?? "-"} 사업연도`),
      span("review-status", config.label),
      span("review-count", `질의 ${review.query_count ?? 0}건`),
      span("review-created", `생성 ${formatKst(review.created_at)}`),
    );

    const open = document.createElement("a");
    open.className = "approval-open";
    open.setAttribute("href", `/reviews/${encodeURIComponent(String(review.id ?? ""))}`);
    open.textContent = config.open;

    item.append(title, meta, open);
    return item;
  }

  function countOf(key) {
    return reviews.filter((review) => review.status === key).length;
  }

  function countText(config) {
    return `${countOf(config.key)}${truncated ? "건 이상" : "건"}`;
  }

  function render() {
    const config = STATUSES.find((item) => item.key === selected);
    for (const item of STATUSES) {
      const button = buttons.get(item.key);
      button.textContent = `${item.label} ${countOf(item.key)}${truncated ? " 이상" : ""}`;
      button.setAttribute("aria-pressed", item.key === selected ? "true" : "false");
    }
    const rows = reviews.filter((review) => review.status === selected);
    list.replaceChildren(...rows.map((review) => renderReview(review, config)));
    empty.textContent = config.empty;
    empty.hidden = rows.length > 0;
    statusLine.textContent = `${config.label} ${countText(config)}`;
    statusLine.hidden = false;
    notice.hidden = !truncated;
  }

  function showNothing() {
    reviews = [];
    truncated = false;
    list.replaceChildren();
    empty.hidden = true;
    statusLine.textContent = "";
    statusLine.hidden = true;
    notice.hidden = true;
    for (const item of STATUSES) {
      const button = buttons.get(item.key);
      button.textContent = item.label;
      button.setAttribute("aria-pressed", item.key === selected ? "true" : "false");
    }
  }

  // 모든 쪽을 받은 뒤에만 보인다. 중간에 실패하면 받은 건도 버린다(건수가 틀려 보이는 것을 막는다).
  // 돌려주는 값: "ok" | "stop"(401 또는 오류 표시 완료)
  async function loadAll() {
    const all = [];
    let cursor = null;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const response = await call(listPath(cursor));
      if (!response) return "stop";
      if (response.status !== 200) {
        showNothing();
        showPageError(messageFor(response.status), true);
        return "stop";
      }
      const body = await readJson(response);
      if (!isObject(body) || !Array.isArray(body.items)) {
        showNothing();
        showPageError(MESSAGES.loadFailed, true);
        return "stop";
      }
      all.push(...body.items.filter(isObject));
      cursor = typeof body.next_cursor === "string" && body.next_cursor ? body.next_cursor : null;
      if (!cursor) break;
    }
    reviews = all;
    truncated = cursor !== null;
    return "ok";
  }

  async function start() {
    if (loadingNow) return;
    loadingNow = true;
    reloadButton.disabled = true;
    loading.hidden = false;
    clearPageError();
    try {
      const me = await call("/api/v1/auth/me");
      if (!me) return;
      if (me.status !== 200) {
        showNothing();
        showPageError(messageFor(me.status), true);
        return;
      }
      const body = await readJson(me);
      const name = isObject(body) && typeof body.display_name === "string" ? body.display_name : "";
      currentUser.textContent = name ? `${name}님` : "";
      if ((await loadAll()) === "ok") render();
    } catch {
      showNothing();
      showPageError(MESSAGES.network, true);
    } finally {
      loading.hidden = true;
      loadingNow = false;
      reloadButton.disabled = false;
    }
  }

  function select(key) {
    selected = key;
    // 불러온 데이터가 없으면(오류·로딩 중) 선택 표시만 맞춘다.
    if (!statusLine.hidden) render();
    else showNothing();
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

  for (const item of STATUSES) buttons.get(item.key).addEventListener("click", () => select(item.key));
  reloadButton.addEventListener("click", start);
  logoutButton.addEventListener("click", logout);

  return { initialize: start };
}


if (typeof window !== "undefined") {
  createApprovalsController({
    document: window.document,
    fetch: window.fetch,
    navigate: (path) => window.location.assign(path),
  }).initialize();
}
