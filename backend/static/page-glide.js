// 1페이지 ↔ 2페이지 미끄러짐(docs/specs/main-page-design.md AC-54).
// 휠을 한 번 굴리면 두 페이지 사이를 한 번에 부드럽게 오간다. 업무 로직(API, 폼)과는 무관하다.
// 모션 줄이기, 확대(ctrl), 열린 대화상자, 미세한 휠에서는 가로채지 않고 기본 스크롤에 맡긴다.
const GLIDE_MS = 1100;
const MIN_WHEEL_PX = 4; // 이보다 작은 휠은 가로채지 않는다
const EDGE_PX = 4; // 페이지 경계로 보는 오차
const SECOND_TOLERANCE_PX = 6;

// 시작과 끝이 모두 부드러운 곡선(0 → 1)
export function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;
}

const clamp01 = (value) => Math.max(0, Math.min(1, value));

// 휠 값을 픽셀로 바꾼다(줄·쪽 단위 휠 포함).
export function wheelPixels(event, viewportHeight) {
  const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? viewportHeight : 1;
  return event.deltaY * unit;
}

// 어디로 미끄러질지 정한다. 가로채지 않을 때는 null을 돌려준다.
// 1페이지(맨 위 ~ 2페이지 위)에서 아래로 → 2페이지 맨 위, 2페이지 맨 위 부근에서 위로 → 맨 위.
export function glideTarget({ deltaY, scrollY, secondTop }) {
  if (!Number.isFinite(deltaY) || Math.abs(deltaY) < MIN_WHEEL_PX) return null;
  if (deltaY > 0 && scrollY < secondTop - EDGE_PX) return secondTop;
  if (deltaY < 0 && scrollY > EDGE_PX && scrollY <= secondTop + SECOND_TOLERANCE_PX) return 0;
  return null;
}

export function createPageGlide({ window, document, requestFrame, now }) {
  const second = document.getElementById("companies");
  if (!second) return null;

  let animation = null; // { from, to, start }

  const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  const secondTop = () => second.getBoundingClientRect().top + window.scrollY;

  // 프레임마다 위치를 직접 정하므로 CSS의 scroll-behavior: smooth를 건너뛰고 즉시 이동한다.
  // (건너뛰지 않으면 프레임마다 새 부드러운 스크롤이 시작되어 서로 덮어쓴다.)
  function jump(top) {
    window.scrollTo({ top, left: 0, behavior: "instant" });
  }

  function step(time) {
    if (!animation) return;
    const { from, to, start } = animation;
    // requestAnimationFrame의 시각이 시작 시각보다 앞설 수 있어 0 아래로는 내리지 않는다(첫 프레임 되튐 방지).
    const progress = clamp01((time - start) / GLIDE_MS);
    jump(from + (to - from) * easeInOutCubic(progress));
    if (progress < 1) requestFrame(step);
    else animation = null;
  }

  function begin(to) {
    animation = { from: window.scrollY, to, start: now() };
    requestFrame(step);
  }

  function stop() {
    animation = null;
  }

  function onWheel(event) {
    if (event.ctrlKey || reducedMotion()) return;
    if (document.querySelector("dialog[open]")) return;
    if (animation) {
      event.preventDefault(); // 이동 중에 들어오는 휠은 삼켜 겹치지 않게 한다
      return;
    }
    const target = glideTarget({
      deltaY: wheelPixels(event, window.innerHeight),
      scrollY: window.scrollY,
      secondTop: secondTop(),
    });
    if (target === null) return;
    event.preventDefault();
    begin(target);
  }

  const interrupts = ["pointerdown", "keydown", "touchstart"];
  window.addEventListener("wheel", onWheel, { passive: false });
  for (const type of interrupts) window.addEventListener(type, stop, { passive: true });

  return {
    isGliding: () => animation !== null,
    destroy() {
      window.removeEventListener("wheel", onWheel);
      for (const type of interrupts) window.removeEventListener(type, stop);
      animation = null;
    },
  };
}

if (typeof window !== "undefined" && window.document?.getElementById("companies")) {
  createPageGlide({
    window,
    document: window.document,
    requestFrame: window.requestAnimationFrame.bind(window),
    now: () => window.performance.now(),
  });
}
