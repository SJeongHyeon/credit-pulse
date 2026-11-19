// 2페이지 탭형 무대(docs/specs/main-page-design.md AC-57·58).
// 탭을 바꿀 때 나가는 패널은 위로 흐려지고 들어오는 패널의 요소가 하나씩 올라오며, 탭은 자동으로 차례로 바뀐다.
// 업무 로직(API, 폼)과는 무관하다. 움직이는 것은 transform과 opacity뿐이고, 모션 줄이기에서는 바로 바뀐다.
const AUTO_MS = 6000; // 한 탭이 머무는 시간(스펙의 제안값)
const HOVER_INTENT_MS = 110; // 스치듯 지나가면 바뀌지 않게 잠깐 기다린다
const LEAVE_MS = 300;
const LEAVE_RISE_PX = -20;
const FADE_MS = 280;
const FADE_DELAY_MS = 100;
const RISE_MS = 700;
const RISE_PX = 44;
const RISE_BASE_DELAY_MS = 120;
const RISE_STEP_MS = 60;
const SETTLE_PAD_MS = 60;
const VISIBLE_RATIO = 0.35;
const SPRING = "cubic-bezier(0.16, 1, 0.3, 1)"; // theme.css의 --ease-spring과 같다

// 들어오는 패널에서 i번째 요소가 올라오기 시작하는 시각
export const riseDelay = (index) => RISE_BASE_DELAY_MS + index * RISE_STEP_MS;

// 전환 한 번이 끝나는 시각(ms). 요소가 많을수록 길어진다.
export function transitionDuration(riserCount) {
  const rises = riserCount > 0 ? riseDelay(riserCount - 1) + RISE_MS : 0;
  return Math.max(LEAVE_MS, FADE_DELAY_MS + FADE_MS, rises);
}

export const nextIndex = (index, length) => (index + 1) % length;

// 방향키·Home·End가 가리키는 탭. 해당 키가 아니면 null.
export function keyTarget(key, index, length) {
  const last = length - 1;
  const target = { ArrowDown: index + 1, ArrowRight: index + 1, ArrowUp: index - 1, ArrowLeft: index - 1, Home: 0, End: last }[key];
  return target === undefined ? null : (target + length) % length;
}

export function createStageTabs({
  document,
  window,
  timers,
  prefersReducedMotion = false,
  observeVisibility,
}) {
  const root = document.getElementById("companies");
  const toggle = document.getElementById("stage-toggle");
  const wrap = root?.querySelector(".stage-wrap");
  const body = root?.querySelector(".stage-body");
  if (!root || !toggle || !wrap || !body) return null;

  const tabs = [...root.querySelectorAll(".stage-tab")];
  const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));
  if (tabs.length === 0 || panels.some((panel) => !panel)) return null;
  const count = tabs.length;
  const canAnimate = !prefersReducedMotion && typeof panels[0].animate === "function";

  let current = 0; // 지금 보이는 패널
  let wanted = 0; // 사용자가 가리킨 탭(표시는 즉시 따라가고 패널은 전환이 끝난 뒤 맞춘다)
  let busy = false;
  let autoTimer = null;
  const hoverTimers = new Map();

  // 자동 전환의 멈춤 이유. 직접 멈춤(userPaused)은 따로 두고 모션 줄이기에서는 멈춘 채로 시작한다.
  let userPaused = prefersReducedMotion;
  const paused = { hover: false, focus: false, hidden: Boolean(document.hidden), offscreen: false };

  function markTabs() {
    tabs.forEach((tab, i) => {
      tab.setAttribute("aria-selected", String(i === wanted));
      tab.tabIndex = i === wanted ? 0 : -1;
    });
  }

  function swap(index) {
    panels.forEach((panel, i) => (i === index ? panel.setAttribute("data-active", "") : panel.removeAttribute("data-active")));
    current = index;
  }

  const risers = (panel) => [...panel.querySelectorAll("[data-rise]")];

  function rise(node, delay) {
    return node.animate(
      [
        { opacity: 0, transform: `translateY(${RISE_PX}px)` },
        { opacity: 1, transform: "none" },
      ],
      { duration: RISE_MS, delay, easing: SPRING, fill: "both" },
    );
  }

  // 올라오기 + 차례로(AC-57): 나가는 패널은 위로 흐려지고, 들어오는 패널의 요소가 하나씩 올라온다.
  function playTransition(from, to) {
    const outgoing = panels[from];
    const incoming = panels[to];
    const animations = [
      outgoing.animate(
        [
          { opacity: 1, transform: "none" },
          { opacity: 0, transform: `translateY(${LEAVE_RISE_PX}px)` },
        ],
        { duration: LEAVE_MS, easing: SPRING, fill: "forwards" },
      ),
      incoming.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FADE_MS, delay: FADE_DELAY_MS, easing: SPRING, fill: "both" }),
    ];
    const nodes = risers(incoming);
    nodes.forEach((node, i) => animations.push(rise(node, riseDelay(i))));
    return { animations, total: transitionDuration(nodes.length) };
  }

  // 전환이 끝날 때마다 원하는 탭과 맞는지 다시 보므로, 빠르게 여러 번 바꿔도 마지막으로 가리킨 탭에서 멈춘다.
  function settle() {
    if (busy || wanted === current) return;
    if (!canAnimate) {
      swap(wanted);
      return;
    }
    busy = true;
    const from = current;
    const to = wanted;
    panels[from].setAttribute("data-leaving", "");
    swap(to);
    const { animations, total } = playTransition(from, to);
    timers.setTimeout(() => {
      animations.forEach((animation) => animation.cancel());
      panels[from].removeAttribute("data-leaving");
      busy = false;
      settle();
    }, total + SETTLE_PAD_MS);
  }

  // ---- 자동 전환 ----
  const isPaused = () => userPaused || paused.hover || paused.focus || paused.hidden || paused.offscreen;

  function updateAutoUi() {
    // 자동으로 바뀌는 동안에는 화면 낭독기가 매번 읽지 않도록 aria-live를 끈다.
    body.setAttribute("aria-live", autoTimer === null ? "polite" : "off");
    toggle.hidden = false;
    toggle.setAttribute("aria-checked", String(!userPaused));
  }

  function stopAuto() {
    if (autoTimer === null) return;
    timers.clearTimeout(autoTimer);
    autoTimer = null;
  }

  // 멈춤 이유가 바뀌거나 직접 골랐을 때마다 시간을 처음부터 다시 센다.
  function syncAuto() {
    stopAuto();
    if (!isPaused()) {
      autoTimer = timers.setTimeout(() => {
        autoTimer = null;
        go(nextIndex(wanted, count));
        syncAuto();
      }, AUTO_MS);
    }
    updateAutoUi();
  }

  function go(index, manual = false) {
    wanted = index;
    markTabs();
    settle();
    if (manual) syncAuto();
  }

  // ---- 조작 ----
  tabs.forEach((tab, i) => {
    tab.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "touch") return;
      hoverTimers.set(i, timers.setTimeout(() => go(i, true), HOVER_INTENT_MS));
    });
    tab.addEventListener("pointerleave", () => timers.clearTimeout(hoverTimers.get(i)));
    tab.addEventListener("click", () => go(i, true));
    tab.addEventListener("focus", () => go(i, true));
    tab.addEventListener("keydown", (event) => {
      const target = keyTarget(event.key, i, count);
      if (target === null) return;
      event.preventDefault();
      tabs[target].focus();
    });
  });

  // 검색을 하면 결과가 보이는 첫 패널로 돌아간다(검색 처리는 landing.js가 한다).
  document.getElementById("company-search")?.addEventListener("submit", () => timers.setTimeout(() => go(0, true), 0));
  // 내비의 「기능」 링크는 03 패널을 연다.
  for (const link of document.querySelectorAll('a[href="#features"]')) link.addEventListener("click", () => go(count - 1, true));

  // 마우스가 무대 위에 있거나 포커스가 안에 있으면(검색 입력 중 포함) 자동 전환을 멈춘다.
  wrap.addEventListener("pointerenter", (event) => {
    if (event.pointerType === "touch") return;
    paused.hover = true;
    syncAuto();
  });
  wrap.addEventListener("pointerleave", () => {
    paused.hover = false;
    syncAuto();
  });
  wrap.addEventListener("focusin", (event) => {
    // 키보드 포커스(검색 입력 포함)일 때만 멈춘다. 마우스로 누른 버튼에 남은 포커스는 멈춤 이유가 아니다.
    paused.focus = Boolean(event.target?.matches?.(":focus-visible"));
    syncAuto();
  });
  wrap.addEventListener("focusout", (event) => {
    if (event.relatedTarget && wrap.contains(event.relatedTarget)) return;
    paused.focus = false;
    syncAuto();
  });
  document.addEventListener("visibilitychange", () => {
    paused.hidden = Boolean(document.hidden);
    syncAuto();
  });
  observeVisibility?.(root, (visible) => {
    paused.offscreen = !visible;
    syncAuto();
  });

  toggle.addEventListener("click", () => {
    userPaused = !userPaused;
    syncAuto();
  });

  // 주소가 #features면 처음부터 03을 보여 준다(애니메이션 없이).
  if (window.location?.hash === "#features") {
    wanted = count - 1;
    swap(count - 1);
  }
  markTabs();
  if (current === 0) swap(0);
  syncAuto();

  return {
    go: (index) => go(index, true),
    state: () => ({ current, wanted, busy, autoRunning: autoTimer !== null, userPaused }),
  };
}

// 2페이지가 35% 이상 보일 때만 보이는 것으로 본다(IntersectionObserver가 없으면 항상 보이는 것으로 본다).
function observeWithIntersection(window) {
  if (typeof window.IntersectionObserver !== "function") return undefined;
  return (element, onChange) => {
    const observer = new window.IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      onChange(entry.isIntersecting && entry.intersectionRatio >= VISIBLE_RATIO);
    }, { threshold: [0, VISIBLE_RATIO, 1] });
    observer.observe(element);
  };
}

if (typeof window !== "undefined" && window.document?.getElementById("stage-toggle")) {
  createStageTabs({
    document: window.document,
    window,
    timers: { setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window) },
    prefersReducedMotion: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false,
    observeVisibility: observeWithIntersection(window),
  });
}
