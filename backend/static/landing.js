import { COMPANIES } from "./landing-data.js";

const MESSAGES = Object.freeze({
  empty: "검색 결과가 없습니다.",
  pause: "일시정지",
  play: "재생",
});
const DEFAULT_INTERVAL_MS = 5000;

function normalize(text) {
  return String(text ?? "").normalize("NFKC").toLowerCase().replace(/\s+/g, "");
}

// timers를 주면 슬라이드를 intervalMs마다 자동으로 넘긴다(없으면 직접 넘기기만 한다).
export function createLandingController({
  document,
  companies,
  timers,
  intervalMs = DEFAULT_INTERVAL_MS,
  prefersReducedMotion = false,
}) {
  const viewport = document.getElementById("slide-viewport");
  const previousButton = document.getElementById("slide-prev");
  const nextButton = document.getElementById("slide-next");
  const position = document.getElementById("slide-position");
  const progress = document.getElementById("slide-progress");
  const progressFill = document.getElementById("slide-progress-fill");
  const emptyMessage = document.getElementById("slide-empty");
  const searchForm = document.getElementById("company-search");
  const searchInput = document.getElementById("company-query");
  const panel = document.getElementById("info-panel");
  const toggle = document.getElementById("slide-toggle");

  let query = "";
  let index = 0;

  // 자동 넘김 상태: 직접 멈춤(userPaused)과 마우스·포커스로 인한 멈춤(hoverPaused)을 따로 둔다.
  const autoplayEnabled = Boolean(timers);
  let timerId = null;
  let userPaused = prefersReducedMotion;
  let hoverPaused = false;

  function stopTimer() {
    if (timerId === null) return;
    timers.clearInterval(timerId);
    timerId = null;
  }

  function updateAutoplayUi() {
    // 자동으로 바뀌는 동안에는 화면 낭독기가 매번 읽지 않도록 aria-live를 끈다.
    viewport.setAttribute("aria-live", timerId === null ? "polite" : "off");
    if (!toggle) return;
    toggle.hidden = false;
    toggle.textContent = userPaused ? MESSAGES.play : MESSAGES.pause;
  }

  function syncAutoplay() {
    if (!autoplayEnabled) return;
    stopTimer();
    if (!userPaused && !hoverPaused) {
      timerId = timers.setInterval(() => move(1, "next"), intervalMs);
    }
    updateAutoplayUi();
  }

  // 직접 조작하면 5초를 처음부터 다시 센다.
  function restartAutoplay() {
    if (autoplayEnabled && !userPaused && !hoverPaused) syncAutoplay();
  }

  function visibleCompanies() {
    const needle = normalize(query);
    if (!needle) return companies;
    return companies.filter(
      (company) => [company.name, company.corp_code, ...(company.aliases ?? [])]
        .some((value) => normalize(value).includes(needle)),
    );
  }

  // 서버·데이터 문자열은 textContent로만 넣는다(HTML로 해석하지 않는다).
  function textElement(tag, className, text) {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  }

  function buildSlide(company, direction) {
    const slide = document.createElement("article");
    slide.className = `slide slide-in-${direction}`;
    const name = textElement("h3", "slide-name", company.name);
    slide.append(
      name,
      textElement("p", "slide-code", `corp_code ${company.corp_code}`),
      textElement("p", "slide-meta", `${company.fiscal_year} 사업보고서 · 공시 ${company.filed_at}`),
    );
    return slide;
  }

  // direction: 새 슬라이드가 들어오는 방향. 처음 표시와 검색 결과는 효과 없이("none") 보인다.
  function render(direction) {
    const list = visibleCompanies();
    if (index >= list.length) index = 0;
    const empty = list.length === 0;

    previousButton.disabled = list.length <= 1;
    nextButton.disabled = list.length <= 1;
    emptyMessage.hidden = !empty;
    emptyMessage.textContent = empty ? MESSAGES.empty : "";
    if (empty) {
      viewport.replaceChildren();
      position.textContent = "";
      progress.hidden = true;
      progressFill.style.width = "0%";
      progress.setAttribute("aria-valuemax", "0");
      progress.setAttribute("aria-valuenow", "0");
      return;
    }

    viewport.replaceChildren(buildSlide(list[index], direction));
    position.textContent = `${index + 1} / ${list.length}`;
    progress.hidden = false;
    progressFill.style.width = `${((index + 1) / list.length) * 100}%`;
    progress.setAttribute("aria-valuemax", String(list.length));
    progress.setAttribute("aria-valuenow", String(index + 1));
  }

  function move(step, direction) {
    const total = visibleCompanies().length;
    if (total <= 1) return;
    index = (index + step + total) % total;
    render(direction);
  }

  function search() {
    query = searchInput.value;
    index = 0;
    render("none");
    restartAutoplay();
  }

  function initialize() {
    previousButton.addEventListener("click", () => {
      move(-1, "prev");
      restartAutoplay();
    });
    nextButton.addEventListener("click", () => {
      move(1, "next");
      restartAutoplay();
    });
    searchInput.addEventListener("input", search);
    // 검색 칸의 지우기(x) 버튼은 input 대신 search 이벤트를 보내는 브라우저가 있다.
    searchInput.addEventListener("search", search);
    searchForm.addEventListener("submit", (event) => {
      event?.preventDefault?.();
      search();
    });
    render("none");
    if (autoplayEnabled) {
      const pause = () => {
        hoverPaused = true;
        syncAutoplay();
      };
      const resume = () => {
        hoverPaused = false;
        syncAutoplay();
      };
      panel?.addEventListener("mouseenter", pause);
      panel?.addEventListener("mouseleave", resume);
      panel?.addEventListener("focusin", pause);
      panel?.addEventListener("focusout", resume);
      toggle?.addEventListener("click", () => {
        userPaused = !userPaused;
        syncAutoplay();
      });
      syncAutoplay();
    }
  }

  return { initialize };
}

// 로그인 팝업은 상단·히어로 버튼으로 열고 닫기 버튼으로 취소한다.
export function createLoginDialogController({ document }) {
  const dialog = document.getElementById("login-dialog");
  const openers = ["open-login", "hero-login"].map((id) => document.getElementById(id));
  const closeButton = document.getElementById("login-close");

  function open() {
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function close() {
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function initialize() {
    for (const opener of openers) opener.addEventListener("click", open);
    closeButton.addEventListener("click", close);
    // closedby를 지원하지 않는 브라우저에서도 Esc에 의한 기본 닫기를 막는다.
    dialog.addEventListener("cancel", (event) => event.preventDefault());
  }

  return { initialize };
}

if (typeof window !== "undefined" && window.document?.getElementById("login-dialog")) {
  createLoginDialogController({ document: window.document }).initialize();
}

if (typeof window !== "undefined" && window.document?.getElementById("slide-viewport")) {
  createLandingController({
    document: window.document,
    companies: COMPANIES,
    timers: { setInterval: window.setInterval.bind(window), clearInterval: window.clearInterval.bind(window) },
    prefersReducedMotion: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  }).initialize();
}
