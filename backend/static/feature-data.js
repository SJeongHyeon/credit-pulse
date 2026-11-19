// 03 패널의 띠와 기능 시트가 함께 쓰는 데이터: 심사 건 집계를 한 번만 불러와 공유하고, 현재 수치를 만든다.
import { COMPANIES } from "./landing-data.js";
import { IN_PROGRESS, countByStatus, countOf, isObject, loadAllReviews } from "./review-data.js";

export function createFeatureData({ fetch, companies = COMPANIES }) {
  let reviewsPromise = null;

  // 실패한 결과는 재사용하지 않는다(다시 불러오기가 새로 받는다).
  function reviews() {
    reviewsPromise ??= loadAllReviews(fetch).then((result) => {
      if (result.status !== "ok") reviewsPromise = null;
      return result;
    });
    return reviewsPromise;
  }

  function reset() {
    reviewsPromise = null;
  }

  // setStat(기능, "진행 6건", "6")을 기능별로 부른다. 실패하면 그 수치만 건너뛴다(오류 문구는 본문이 알린다).
  async function loadStats(setStat) {
    setStat("search", `${companies.length}곳`, String(companies.length));
    try {
      const result = await reviews();
      if (result.status === "ok") {
        const counts = countByStatus(result.reviews);
        const more = result.truncated ? "+" : "";
        const inProgress = countOf(counts, ...IN_PROGRESS);
        const closed = countOf(counts, "closed");
        const waiting = countOf(counts, "awaiting_approval");
        setStat("reviews", `진행 ${inProgress}${more}건`, `${inProgress}${more}`);
        setStat("reports", `저장 ${closed}${more}건`, `${closed}${more}`);
        setStat("approvals", `대기 ${waiting}${more}건`, `${waiting}${more}`);
      }
    } catch {
      // 수치만 비운다.
    }
    try {
      const response = await fetch("/api/v1/rules", { credentials: "same-origin" });
      if (response.status === 200) {
        const body = await response.json();
        if (isObject(body) && Array.isArray(body.items)) setStat("rules", `${body.items.length}개`, String(body.items.length));
      }
    } catch {
      // 수치만 비운다.
    }
  }

  return { reviews, reset, loadStats };
}

let shared = null;

// 브라우저에서 두 컨트롤러가 같은 인스턴스를 쓰게 한다.
export function sharedFeatureData() {
  shared ??= createFeatureData({ fetch: window.fetch.bind(window) });
  return shared;
}
