// 첫 방문 로고 인트로(A안, docs/specs/main-page-design.md AC-50·51).
// 로고 PNG를 16×3 조각으로 나눠 흩어진 조각이 날아와 합쳐지고, 끝나면 은은한 금색이 번졌다가 흰색으로 돌아온다.
// 업무 로직(API, 폼)과는 무관하다. 움직이는 것은 transform과 opacity뿐이다.
const COLS = 16;
const ROWS = 3;
const TOTAL_MS = 2800;
const FADE_MS = 500;
const QUICK_FADE_MS = 150;
const SCATTER_SPAN_MS = 450; // 조각마다 0~450ms 사이의 서로 다른 시점에 출발한다
const TILE_MS = 900;
const TILE_EXTRA_MS = 250;
const SETTLE_MS = 1400; // 조립이 끝나 조각을 통짜 로고로 바꾸는 때
const SETTLE_FADE_MS = 160;
const GOLD_DELAY_MS = 1450;
const GOLD_MS = 1100;
const GOLD_PEAK = 0.6;
const SPRING = "cubic-bezier(0.16, 1, 0.3, 1)"; // theme.css의 --ease-spring과 같다

// <head>의 인라인 스크립트와 같은 키여야 한다(tests/test_login_web_ui.py가 확인).
export const SEEN_KEY = "credit-pulse-intro-seen";

// 이미 본 세션이거나 모션 줄이기이거나 저장소를 쓸 수 없으면 재생하지 않는다.
export function shouldPlayIntro({ storage, reducedMotion }) {
  if (reducedMotion) return false;
  try {
    return storage?.getItem(SEEN_KEY) === null;
  } catch {
    return false;
  }
}

// 로고 주소는 우리 서버의 정적 파일일 때만 쓴다.
export function isSafeLogoPath(path) {
  return typeof path === "string" && /^\/static\/[\w./-]+$/.test(path) && !path.includes("..");
}

// 칸 순서를 섞어 0..span 사이로 고르게 나눈 무작위 지연. 모든 칸의 지연이 서로 다르다.
export function staggerDelays(count, span, random = Math.random) {
  const order = [...Array(count).keys()];
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  const delays = new Array(count).fill(0);
  order.forEach((cell, position) => {
    delays[cell] = (position * span) / count;
  });
  return delays;
}

// (row, col) 조각이 보여 줄 로고 부분. 모서리 조각은 0%와 100%를 가리킨다.
export function tileBackground(row, col) {
  return {
    size: `${COLS * 100}% ${ROWS * 100}%`,
    position: `${(col / (COLS - 1)) * 100}% ${(row / (ROWS - 1)) * 100}%`,
  };
}

const between = (random, min, max) => min + random() * (max - min);

export function playIntro({ document, window, random = Math.random }) {
  const root = document.documentElement;
  const intro = document.getElementById("intro");
  if (!intro || !root.classList.contains("intro-pending")) return null;

  const stage = intro.querySelector(".intro-stage");
  const logo = intro.dataset.logo ?? "";
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  let storage = null;
  try {
    storage = window.sessionStorage;
  } catch {
    storage = null;
  }

  if (!stage || !isSafeLogoPath(logo) || typeof stage.animate !== "function" || !shouldPlayIntro({ storage, reducedMotion: reduced })) {
    root.classList.remove("intro-pending");
    return null;
  }
  try {
    storage.setItem(SEEN_KEY, "1"); // 재생을 시작할 때 기록해 새로고침해도 다시 나오지 않게 한다
  } catch {
    root.classList.remove("intro-pending");
    return null;
  }

  stage.style.setProperty("--intro-logo", `url("${logo}")`);

  const delays = staggerDelays(COLS * ROWS, SCATTER_SPAN_MS, random);
  const tiles = [];
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      const tile = document.createElement("div");
      tile.className = "intro-tile";
      const background = tileBackground(row, col);
      tile.style.backgroundSize = background.size;
      tile.style.backgroundPosition = background.position;
      tile.style.gridColumn = String(col + 1);
      tile.style.gridRow = String(row + 1);
      stage.append(tile);

      const from = `translate(${between(random, -70, 70)}vw, ${between(random, -70, 70)}vh) rotate(${between(random, -160, 160)}deg) scale(${between(random, 0.3, 1.6)})`;
      tile.animate(
        [
          { transform: from, opacity: 0 },
          { transform: "translate(0, 0) rotate(0deg) scale(1)", opacity: 1 },
        ],
        { duration: TILE_MS + random() * TILE_EXTRA_MS, delay: delays[row * COLS + col], easing: SPRING, fill: "both" },
      );
      tiles.push(tile);
    }
  }

  const full = document.createElement("div");
  full.className = "intro-full";
  stage.append(full);
  full.animate([{ opacity: 0 }, { opacity: 1 }], { duration: SETTLE_FADE_MS, delay: SETTLE_MS, fill: "both" });
  for (const tile of tiles) {
    tile.animate([{ opacity: 1 }, { opacity: 0 }], { duration: SETTLE_FADE_MS, delay: SETTLE_MS, fill: "forwards" });
  }

  const gold = document.createElement("div");
  gold.className = "intro-gold";
  stage.append(gold);
  gold.animate(
    [
      { opacity: 0, offset: 0 },
      { opacity: GOLD_PEAK, offset: 0.4 },
      { opacity: GOLD_PEAK, offset: 0.55 },
      { opacity: 0, offset: 1 },
    ],
    { duration: GOLD_MS, delay: GOLD_DELAY_MS, easing: "linear", fill: "both" },
  );

  let finished = false;
  let timer = null;

  function cleanup() {
    root.classList.remove("intro-pending");
    stage.replaceChildren();
    window.removeEventListener("keydown", skip);
    intro.removeEventListener("pointerdown", skip);
  }

  function finish(quick) {
    if (finished) return;
    finished = true;
    window.clearTimeout(timer);
    const fade = intro.animate([{ opacity: 1 }, { opacity: 0 }], { duration: quick ? QUICK_FADE_MS : FADE_MS, fill: "forwards" });
    fade.onfinish = cleanup;
  }

  function skip() {
    finish(true);
  }

  timer = window.setTimeout(() => finish(false), TOTAL_MS - FADE_MS);
  intro.addEventListener("pointerdown", skip);
  window.addEventListener("keydown", skip);
  return { skip };
}

if (typeof window !== "undefined" && window.document?.getElementById("intro")) {
  playIntro({ document: window.document, window });
}
