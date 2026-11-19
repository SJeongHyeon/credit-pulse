// 시트가 쓰는 심사 건 전체 불러오기와 집계. 새 API 없이 GET /api/v1/reviews를 최대 5쪽(250건) 받는다.
export const PAGE_SIZE = 50;
export const MAX_PAGES = 5;
export const MAX_REVIEWS = PAGE_SIZE * MAX_PAGES;

export const STATUS_LABELS = Object.freeze({
  open: "진행 가능",
  running: "처리 중",
  awaiting_approval: "승인 대기",
  saving: "저장 중",
  closed: "종료",
});
// 진행 중 = 종료가 아닌 것
export const IN_PROGRESS = Object.freeze(["open", "running", "awaiting_approval", "saving"]);

export const isObject = (value) => typeof value === "object" && value !== null;

function listPath(cursor) {
  const base = `/api/v1/reviews?page_size=${PAGE_SIZE}`;
  return cursor ? `${base}&cursor=${encodeURIComponent(cursor)}` : base;
}

// 돌려주는 값: { status: "ok", reviews, truncated } | { status: "unauthorized" } | { status: "error", http }
// 중간에 실패하면 받은 건도 버린다(건수가 틀려 보이는 것을 막는다). 네트워크 예외는 호출한 쪽이 처리한다.
export async function loadAllReviews(fetch) {
  const all = [];
  let cursor = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await fetch(listPath(cursor), { credentials: "same-origin" });
    if (response.status === 401) return { status: "unauthorized" };
    if (response.status !== 200) return { status: "error", http: response.status };
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!isObject(body) || !Array.isArray(body.items)) return { status: "error", http: 200 };
    all.push(...body.items.filter(isObject));
    cursor = typeof body.next_cursor === "string" && body.next_cursor ? body.next_cursor : null;
    if (!cursor) break;
  }
  return { status: "ok", reviews: all, truncated: cursor !== null };
}

export function countByStatus(reviews) {
  const counts = {};
  for (const review of reviews) counts[review.status] = (counts[review.status] ?? 0) + 1;
  return counts;
}

export const countOf = (counts, ...statuses) => statuses.reduce((sum, status) => sum + (counts[status] ?? 0), 0);

// created_at 내림차순(해석할 수 없는 값은 맨 뒤). 원본은 바꾸지 않는다.
export function latestFirst(reviews) {
  const time = (review) => {
    const value = new Date(review.created_at).getTime();
    return Number.isNaN(value) ? -Infinity : value;
  };
  return [...reviews].sort((a, b) => time(b) - time(a));
}

// UTC ISO 8601을 한국 시간 "YYYY-MM-DD HH:mm"으로 바꾼다. 해석할 수 없으면 "-".
export function formatKst(isoText) {
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

export const reviewHref = (review) => `/reviews/${encodeURIComponent(String(review.id ?? ""))}`;
