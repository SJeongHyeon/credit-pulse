import assert from "node:assert/strict";
import test from "node:test";

import { createStageTabs, keyTarget, nextIndex, riseDelay, transitionDuration } from "../backend/static/stage-tabs.js";

const SPRING = "cubic-bezier(0.16, 1, 0.3, 1)";
const RISER_COUNTS = [3, 4, 7]; // 패널 0·1·2의 data-rise 개수(index.html과 같다)

function fakeTimers() {
  let now = 0;
  let id = 0;
  const tasks = new Map();
  return {
    setTimeout(callback, ms) {
      id += 1;
      tasks.set(id, { at: now + ms, callback });
      return id;
    },
    clearTimeout(taskId) {
      tasks.delete(taskId);
    },
    advance(ms) {
      const target = now + ms;
      for (;;) {
        const due = [...tasks.entries()].filter(([, task]) => task.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!due) break;
        now = due[1].at;
        tasks.delete(due[0]);
        due[1].callback();
      }
      now = target;
    },
    pending: () => tasks.size,
  };
}

function node(extra = {}) {
  const attrs = new Map();
  const listeners = {};
  const self = {
    attrs,
    listeners,
    tabIndex: 0,
    hidden: false,
    textContent: "",
    animations: [],
    focused: 0,
    setAttribute: (name, value) => attrs.set(name, String(value)),
    getAttribute: (name) => (attrs.has(name) ? attrs.get(name) : null),
    removeAttribute: (name) => attrs.delete(name),
    hasAttribute: (name) => attrs.has(name),
    addEventListener: (type, handler) => (listeners[type] ??= []).push(handler),
    dispatch(type, init = {}) {
      const event = { prevented: false, preventDefault() { this.prevented = true; }, ...init };
      (listeners[type] ?? []).forEach((handler) => handler(event));
      return event;
    },
    focus() {
      self.focused += 1;
      self.dispatch("focus");
    },
    animate(keyframes, options) {
      const animation = { keyframes, options, canceled: false, cancel() { this.canceled = true; } };
      self.animations.push(animation);
      return animation;
    },
    ...extra,
  };
  return self;
}

function setup({ reduced = false, hash = "", withObserver = true, withAnimate = true } = {}) {
  const timers = fakeTimers();
  const tabs = [0, 1, 2].map((i) => {
    const tab = node();
    tab.setAttribute("aria-controls", ["stage-panel-0", "stage-panel-1", "features"][i]);
    tab.setAttribute("aria-selected", String(i === 0));
    return tab;
  });
  const risers = RISER_COUNTS.map((n) => Array.from({ length: n }, () => node()));
  const panels = risers.map((list) => {
    const panel = node({ querySelectorAll: (selector) => (selector === "[data-rise]" ? list : []) });
    if (!withAnimate) delete panel.animate;
    return panel;
  });
  const toggle = node({ hidden: true, textContent: "ON OFF 자동 전환" });
  const body = node();
  const search = node();
  const link = node();
  const members = new Set([...tabs, toggle, body]);
  const wrap = node({ contains: (other) => members.has(other) });
  const root = node({
    querySelector: (selector) => ({ ".stage-wrap": wrap, ".stage-body": body }[selector] ?? null),
    querySelectorAll: (selector) => (selector === ".stage-tab" ? tabs : []),
  });
  const byId = { companies: root, "stage-toggle": toggle, "company-search": search, "stage-panel-0": panels[0], "stage-panel-1": panels[1], features: panels[2] };
  const document = {
    hidden: false,
    getElementById: (id) => byId[id] ?? null,
    querySelectorAll: (selector) => (selector === 'a[href="#features"]' ? [link] : []),
    listeners: {},
    addEventListener(type, handler) {
      (this.listeners[type] ??= []).push(handler);
    },
  };
  let visibilityCallback = null;
  const controller = createStageTabs({
    document,
    window: { location: { hash } },
    timers,
    prefersReducedMotion: reduced,
    observeVisibility: withObserver ? (element, callback) => (visibilityCallback = callback) : undefined,
  });
  const helpers = {
    controller,
    timers,
    tabs,
    panels,
    risers,
    toggle,
    body,
    wrap,
    search,
    link,
    document,
    setVisible: (visible) => visibilityCallback(visible),
    setHidden(value) {
      document.hidden = value;
      (document.listeners.visibilitychange ?? []).forEach((handler) => handler());
    },
    selected: () => tabs.map((tab) => tab.getAttribute("aria-selected")[0]).join(""),
    active: () => panels.findIndex((panel) => panel.hasAttribute("data-active")),
    leaving: () => panels.findIndex((panel) => panel.hasAttribute("data-leaving")),
  };
  return helpers;
}

const keyboardFocus = { matches: (selector) => selector === ":focus-visible" };
const mouseFocus = { matches: () => false };

const onlyOpacityAndTransform = (keyframes) => keyframes.every((frame) => Object.keys(frame).every((key) => key === "opacity" || key === "transform"));

test("helpers: rise delays, transition length, wraparound and key targets", () => {
  assert.deepEqual([0, 1, 2, 6].map(riseDelay), [120, 180, 240, 480]);
  assert.equal(transitionDuration(0), 380);
  assert.equal(transitionDuration(3), 940);
  assert.equal(transitionDuration(7), 1180);
  assert.deepEqual([0, 1, 2].map((i) => nextIndex(i, 3)), [1, 2, 0]);
  assert.equal(keyTarget("ArrowDown", 0, 3), 1);
  assert.equal(keyTarget("ArrowRight", 2, 3), 0);
  assert.equal(keyTarget("ArrowUp", 0, 3), 2);
  assert.equal(keyTarget("ArrowLeft", 1, 3), 0);
  assert.equal(keyTarget("Home", 2, 3), 0);
  assert.equal(keyTarget("End", 0, 3), 2);
  assert.equal(keyTarget("a", 0, 3), null);
});

test("returns null when the stage markup is missing", () => {
  const empty = { getElementById: () => null, querySelectorAll: () => [], addEventListener() {} };
  assert.equal(createStageTabs({ document: empty, window: { location: { hash: "" } }, timers: fakeTimers() }), null);
});

test("starts on the first tab with the toggle shown and aria-live off while auto-rotating", () => {
  const env = setup();
  assert.equal(env.selected(), "tff");
  assert.equal(env.active(), 0);
  assert.equal(env.toggle.hidden, false);
  assert.equal(env.toggle.textContent, "ON OFF 자동 전환");
  assert.equal(env.toggle.getAttribute("aria-checked"), "true");
  assert.equal(env.body.getAttribute("aria-live"), "off");
  assert.deepEqual(env.tabs.map((tab) => tab.tabIndex), [0, -1, -1]);
  assert.equal(env.controller.state().autoRunning, true);
});

test("switching plays the rise-in-order transition: outgoing fades up, incoming fades in, pieces rise one by one", () => {
  const env = setup();
  env.tabs[1].dispatch("click");
  assert.equal(env.selected(), "ftf"); // 표시는 즉시
  assert.equal(env.active(), 1);
  assert.equal(env.leaving(), 0); // 나가는 패널은 전환이 끝날 때까지 남는다

  const [out] = env.panels[0].animations;
  assert.equal(out.options.duration, 300);
  assert.equal(out.options.easing, SPRING);
  assert.ok(onlyOpacityAndTransform(out.keyframes));
  assert.equal(out.keyframes[1].transform, "translateY(-20px)");
  assert.equal(out.keyframes[1].opacity, 0);

  const [fade] = env.panels[1].animations;
  assert.deepEqual(fade.keyframes.map((frame) => Object.keys(frame)), [["opacity"], ["opacity"]]);
  assert.equal(fade.options.duration, 280);
  assert.equal(fade.options.delay, 100);

  const rises = env.risers[1].map((piece) => piece.animations[0]);
  assert.equal(rises.length, 4);
  assert.deepEqual(rises.map((a) => a.options.delay), [120, 180, 240, 300]);
  for (const animation of rises) {
    assert.equal(animation.options.duration, 700);
    assert.equal(animation.options.easing, SPRING);
    assert.ok(onlyOpacityAndTransform(animation.keyframes));
    assert.equal(animation.keyframes[0].transform, "translateY(44px)");
    assert.equal(animation.keyframes[1].transform, "none");
  }

  env.timers.advance(1000 + 59); // 전환 길이(1000ms) + 여유(60ms) 직전
  assert.equal(env.leaving(), 0);
  env.timers.advance(1);
  assert.equal(env.leaving(), -1);
  assert.ok([...env.panels[0].animations, ...env.panels[1].animations, ...env.risers[1].flatMap((piece) => piece.animations)].every((a) => a.canceled));
});

test("rapid changes always settle on the last tab pointed at", () => {
  const env = setup();
  env.tabs[1].dispatch("click");
  env.tabs[0].dispatch("click");
  env.tabs[2].dispatch("click");
  assert.equal(env.selected(), "fft"); // 표시는 마지막으로 가리킨 탭
  assert.equal(env.active(), 1); // 패널은 첫 전환이 끝나야 따라간다
  env.timers.advance(1060 + 1240 + 10);
  assert.equal(env.active(), 2);
  assert.equal(env.leaving(), -1);
  assert.equal(env.controller.state().busy, false);
  assert.equal(env.selected(), "fft");

  const env2 = setup();
  env2.tabs[2].dispatch("click");
  env2.tabs[0].dispatch("click");
  env2.timers.advance(3000);
  assert.equal(env2.active(), 0);
  assert.equal(env2.selected(), "tff");
});

test("hover intent: switches after 110ms for a mouse, ignores a quick pass and touch", () => {
  const env = setup();
  env.tabs[1].dispatch("pointerenter", { pointerType: "mouse" });
  env.timers.advance(109);
  assert.equal(env.selected(), "tff");
  env.timers.advance(1);
  assert.equal(env.selected(), "ftf");

  const quick = setup();
  quick.tabs[2].dispatch("pointerenter", { pointerType: "mouse" });
  quick.timers.advance(60);
  quick.tabs[2].dispatch("pointerleave");
  quick.timers.advance(500);
  assert.equal(quick.selected(), "tff");

  const touch = setup();
  touch.tabs[2].dispatch("pointerenter", { pointerType: "touch" });
  touch.timers.advance(500);
  assert.equal(touch.selected(), "tff");
});

test("arrow keys, Home and End move focus (activation follows focus); other keys are left alone", () => {
  const env = setup();
  const down = env.tabs[0].dispatch("keydown", { key: "ArrowDown" });
  assert.equal(down.prevented, true);
  assert.equal(env.tabs[1].focused, 1);
  assert.equal(env.selected(), "ftf");
  env.tabs[1].dispatch("keydown", { key: "End" });
  assert.equal(env.tabs[2].focused, 1);
  env.tabs[2].dispatch("keydown", { key: "ArrowRight" });
  assert.equal(env.tabs[0].focused, 1); // 끝에서 처음으로
  assert.equal(env.tabs[0].dispatch("keydown", { key: "a" }).prevented, false);
});

test("searching returns to the companies tab; the nav link opens the features tab", () => {
  const env = setup();
  env.link.dispatch("click");
  assert.equal(env.selected(), "fft");
  env.timers.advance(5000);
  env.search.dispatch("submit");
  env.timers.advance(0);
  assert.equal(env.selected(), "tff");
  env.timers.advance(3000);
  assert.equal(env.active(), 0);
});

test("auto rotation advances every 6s, wraps around, and a manual pick restarts the clock", () => {
  const env = setup();
  env.timers.advance(5999);
  assert.equal(env.selected(), "tff");
  env.timers.advance(1);
  assert.equal(env.selected(), "ftf");
  env.timers.advance(6000);
  assert.equal(env.selected(), "fft");
  env.timers.advance(6000);
  assert.equal(env.selected(), "tff"); // 03 다음은 01

  const manual = setup();
  manual.timers.advance(5000);
  manual.tabs[1].dispatch("click");
  manual.timers.advance(5999);
  assert.equal(manual.selected(), "ftf"); // 직접 고른 뒤 처음부터 다시 센다
  manual.timers.advance(1);
  assert.equal(manual.selected(), "fft");
});

test("auto rotation pauses for hover, focus inside, hidden document, off-screen section and resumes after", () => {
  const hover = setup();
  hover.wrap.dispatch("pointerenter", { pointerType: "mouse" });
  assert.equal(hover.controller.state().autoRunning, false);
  assert.equal(hover.body.getAttribute("aria-live"), "polite");
  hover.timers.advance(30000);
  assert.equal(hover.selected(), "tff");
  hover.wrap.dispatch("pointerleave");
  assert.equal(hover.controller.state().autoRunning, true);
  hover.timers.advance(6000);
  assert.equal(hover.selected(), "ftf");

  const touchHover = setup();
  touchHover.wrap.dispatch("pointerenter", { pointerType: "touch" }); // 터치는 호버로 보지 않는다
  assert.equal(touchHover.controller.state().autoRunning, true);

  const focus = setup();
  focus.wrap.dispatch("focusin", { target: keyboardFocus });
  focus.timers.advance(30000);
  assert.equal(focus.selected(), "tff");
  focus.wrap.dispatch("focusout", { relatedTarget: focus.tabs[1] }); // 무대 안에서 옮겨 다님
  assert.equal(focus.controller.state().autoRunning, false);
  focus.wrap.dispatch("focusout", { relatedTarget: null });
  assert.equal(focus.controller.state().autoRunning, true);

  const hidden = setup();
  hidden.setHidden(true);
  hidden.timers.advance(30000);
  assert.equal(hidden.selected(), "tff");
  hidden.setHidden(false);
  hidden.timers.advance(6000);
  assert.equal(hidden.selected(), "ftf");

  const offscreen = setup();
  offscreen.setVisible(false);
  offscreen.timers.advance(30000);
  assert.equal(offscreen.selected(), "tff");
  offscreen.setVisible(true);
  offscreen.timers.advance(6000);
  assert.equal(offscreen.selected(), "ftf");
});

test("the switch pauses and resumes auto rotation without changing its label", () => {
  const env = setup();
  env.toggle.dispatch("click");
  assert.equal(env.toggle.textContent, "ON OFF 자동 전환");
  assert.equal(env.toggle.getAttribute("aria-checked"), "false");
  assert.equal(env.controller.state().userPaused, true);
  env.timers.advance(30000);
  assert.equal(env.selected(), "tff");
  env.toggle.dispatch("click");
  assert.equal(env.toggle.textContent, "ON OFF 자동 전환");
  assert.equal(env.toggle.getAttribute("aria-checked"), "true");
  env.timers.advance(6000);
  assert.equal(env.selected(), "ftf");
  // 마우스가 벗어나도 직접 멈춘 것은 풀리지 않는다
  env.toggle.dispatch("click");
  env.wrap.dispatch("pointerenter", { pointerType: "mouse" });
  env.wrap.dispatch("pointerleave");
  assert.equal(env.controller.state().autoRunning, false);
});

test("reduced motion: starts paused, switches without animation", () => {
  const env = setup({ reduced: true });
  assert.equal(env.toggle.getAttribute("aria-checked"), "false");
  assert.equal(env.body.getAttribute("aria-live"), "polite");
  assert.equal(env.controller.state().autoRunning, false);
  env.timers.advance(60000);
  assert.equal(env.selected(), "tff");
  env.tabs[2].dispatch("click");
  assert.equal(env.active(), 2);
  assert.equal(env.leaving(), -1);
  assert.ok(env.panels.every((panel) => panel.animations.length === 0));
  assert.ok(env.risers.flat().every((piece) => piece.animations.length === 0));
});

test("without the Web Animations API it switches immediately", () => {
  const env = setup({ withAnimate: false });
  env.tabs[1].dispatch("click");
  assert.equal(env.active(), 1);
  assert.equal(env.leaving(), -1);
});

test("an address ending in #features opens the features tab without animation", () => {
  const env = setup({ hash: "#features" });
  assert.equal(env.selected(), "fft");
  assert.equal(env.active(), 2);
  assert.ok(env.panels.every((panel) => panel.animations.length === 0));
  assert.deepEqual(env.tabs.map((tab) => tab.tabIndex), [-1, -1, 0]);
});

test("works without IntersectionObserver support", () => {
  const env = setup({ withObserver: false });
  env.timers.advance(6000);
  assert.equal(env.selected(), "ftf");
});

test("focus left on a button after a mouse click does not keep auto rotation paused", () => {
  const env = setup();
  env.toggle.dispatch("click"); // 마우스로 「일시정지」를 눌렀다가
  env.toggle.dispatch("click"); // 다시 「재생」을 누른다
  env.wrap.dispatch("focusin", { target: mouseFocus }); // 눌린 버튼에 포커스가 남는다
  assert.equal(env.controller.state().autoRunning, true);
  env.timers.advance(6000);
  assert.equal(env.selected(), "ftf");

  // 키보드 포커스로 옮겨 가면 멈추고, 마우스 포커스로 바뀌면 다시 돈다
  env.wrap.dispatch("focusin", { target: keyboardFocus });
  assert.equal(env.controller.state().autoRunning, false);
  env.wrap.dispatch("focusin", { target: mouseFocus });
  assert.equal(env.controller.state().autoRunning, true);
});
