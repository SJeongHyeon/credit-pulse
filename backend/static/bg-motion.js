// 배경 사진이 마우스를 따라 미세하게 움직이게 한다. CSS 변수(--mx, --my, --spot)만 바꾸며
// 업무 로직(API, 폼)과는 무관하다. 모션 줄이기 설정이나 터치 기기에서는 아무것도 하지 않는다.
export function startBackgroundMotion({ document, window, requestFrame, cancelFrame }) {
  const root = document.documentElement;
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const fine = window.matchMedia?.("(hover: hover) and (pointer: fine)");
  if (!fine?.matches || reduced?.matches) return () => {};

  const target = { x: 0.5, y: 0.5, spot: 0 };
  const current = { x: 0.5, y: 0.5, spot: 0 };
  let frame = null;

  function paint() {
    frame = null;
    // 목표 위치로 천천히 다가가 부드럽게 따라온다.
    current.x += (target.x - current.x) * 0.08;
    current.y += (target.y - current.y) * 0.08;
    current.spot += (target.spot - current.spot) * 0.08;
    root.style.setProperty("--mx", current.x.toFixed(4));
    root.style.setProperty("--my", current.y.toFixed(4));
    root.style.setProperty("--spot", current.spot.toFixed(3));
    const moving =
      Math.abs(target.x - current.x) > 0.001 ||
      Math.abs(target.y - current.y) > 0.001 ||
      Math.abs(target.spot - current.spot) > 0.002;
    if (moving) frame = requestFrame(paint);
  }

  function schedule() {
    if (frame === null) frame = requestFrame(paint);
  }

  function onMove(event) {
    target.x = event.clientX / Math.max(window.innerWidth, 1);
    target.y = event.clientY / Math.max(window.innerHeight, 1);
    target.spot = 1;
    schedule();
  }

  function onLeave() {
    target.spot = 0;
    schedule();
  }

  window.addEventListener("pointermove", onMove, { passive: true });
  document.documentElement.addEventListener("pointerleave", onLeave);
  return () => {
    window.removeEventListener("pointermove", onMove);
    document.documentElement.removeEventListener("pointerleave", onLeave);
    if (frame !== null) cancelFrame(frame);
  };
}

if (typeof window !== "undefined" && window.document?.querySelector(".page-bg")) {
  startBackgroundMotion({
    document: window.document,
    window,
    requestFrame: window.requestAnimationFrame.bind(window),
    cancelFrame: window.cancelAnimationFrame.bind(window),
  });
}
