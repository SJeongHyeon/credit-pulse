// 긴 기업명도 한 줄에 놓이게 한다(docs/specs/main-page-design.md AC-59).
// 이름이 넘칠 때만 글자 크기를 줄이므로 짧은 이름은 CSS의 큰 크기 그대로다. landing.js는 바꾸지 않는다.
export const MIN_FONT_PX = 28;
const SAFETY = 0.99; // 글자 모양 오차로 한 줄이 다시 넘치지 않게 조금 여유를 둔다
const MAX_PASSES = 3;

// 넘치면 폭에 맞춘 크기(0.5px 단위 내림)를 돌려준다. 하한에서도 넘치면 fits: false.
export function fitFontSize({ fontPx, availableWidth, neededWidth, minPx = MIN_FONT_PX }) {
  if (!(availableWidth > 0) || !(neededWidth > 0) || neededWidth <= availableWidth) return { fontPx, fits: true };
  const scaled = Math.floor(((fontPx * availableWidth) / neededWidth) * SAFETY * 2) / 2;
  if (scaled < minPx) {
    // 하한까지 줄여도 되는지 확인한다: 하한 크기에서 들어가면 줄바꿈 없이 한 줄을 유지한다.
    const neededAtFloor = (neededWidth * minPx) / fontPx;
    return { fontPx: minPx, fits: neededAtFloor <= availableWidth };
  }
  return { fontPx: scaled, fits: true };
}

export function createSlideFit({ document, window, observeResize }) {
  const viewport = document.getElementById("slide-viewport");
  if (!viewport) return null;

  function fit(name) {
    // 인라인으로 준 값을 지우고 CSS의 기본 크기에서 다시 잰다.
    name.style.removeProperty("font-size");
    name.style.removeProperty("white-space");
    for (let pass = 0; pass < MAX_PASSES; pass++) {
      const style = window.getComputedStyle(name);
      const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
      const result = fitFontSize({
        fontPx: parseFloat(style.fontSize),
        availableWidth: name.clientWidth - padding,
        neededWidth: name.scrollWidth - padding,
      });
      if (result.fontPx === parseFloat(style.fontSize) && result.fits) return; // 이미 한 줄에 들어간다
      name.style.fontSize = `${result.fontPx}px`;
      if (!result.fits) {
        name.style.whiteSpace = "normal"; // 하한에서도 넘치면 줄바꿈을 허용한다
        return;
      }
    }
  }

  const fitAll = () => viewport.querySelectorAll(".slide-name").forEach(fit);

  // 슬라이드가 바뀔 때(landing.js가 새 슬라이드를 넣을 때)마다 맞춘다.
  new window.MutationObserver((records) => {
    for (const record of records) {
      for (const added of record.addedNodes) {
        if (added.nodeType !== 1) continue;
        if (added.matches?.(".slide-name")) fit(added);
        else added.querySelectorAll?.(".slide-name").forEach(fit);
      }
    }
  }).observe(viewport, { childList: true, subtree: true });

  // 창·패널 크기가 바뀌거나 글꼴이 로드되면 다시 맞춘다.
  if (observeResize) observeResize(viewport, fitAll);
  else window.addEventListener("resize", fitAll);
  window.document?.fonts?.ready?.then(fitAll);

  fitAll();
  return { fitAll };
}

function observeWithResizeObserver(window) {
  if (typeof window.ResizeObserver !== "function") return undefined;
  return (element, callback) => {
    let queued = false;
    new window.ResizeObserver(() => {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(() => {
        queued = false;
        callback();
      });
    }).observe(element);
  };
}

if (typeof window !== "undefined" && window.document?.getElementById("slide-viewport")) {
  createSlideFit({ document: window.document, window, observeResize: observeWithResizeObserver(window) });
}
