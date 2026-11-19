import assert from "node:assert/strict";
import test from "node:test";

import { createPageGlide, easeInOutCubic, glideTarget, wheelPixels } from "../backend/static/page-glide.js";

const SECOND_TOP = 777;

function setup({ scrollY = 0, reduced = false, dialogOpen = false, hasSecond = true } = {}) {
  const listeners = {};
  const calls = [];
  const frames = [];
  let clock = 1000;
  const win = {
    scrollY,
    innerHeight: 776,
    matchMedia: () => ({ matches: reduced }),
    scrollTo(options) {
      calls.push(options);
      this.scrollY = options.top; // 즉시 이동
    },
    addEventListener(type, handler) {
      (listeners[type] ??= []).push(handler);
    },
    removeEventListener(type, handler) {
      listeners[type] = (listeners[type] ?? []).filter((item) => item !== handler);
    },
  };
  const document = {
    getElementById: (id) => (id === "companies" && hasSecond ? { getBoundingClientRect: () => ({ top: SECOND_TOP - win.scrollY }) } : null),
    querySelector: (selector) => (selector === "dialog[open]" && dialogOpen ? {} : null),
  };
  const glide = createPageGlide({ window: win, document, requestFrame: (callback) => frames.push(callback), now: () => clock });

  const wheel = (init) => {
    const event = { deltaY: 100, deltaMode: 0, ctrlKey: false, prevented: false, preventDefault() { this.prevented = true; }, ...init };
    listeners.wheel?.forEach((handler) => handler(event));
    return event;
  };
  // 한 프레임을 흘려보낸다(ms 단위로 시각을 진행한다).
  const advance = (ms) => {
    clock += ms;
    const callback = frames.shift();
    if (callback) callback(clock);
    return Boolean(callback);
  };
  const emit = (type) => listeners[type]?.forEach((handler) => handler({}));
  return { glide, win, calls, wheel, advance, emit, listeners, frames, setClock: (value) => (clock = value) };
}

test("ease in-out: starts and ends gently, passes the middle at half", () => {
  assert.equal(easeInOutCubic(0), 0);
  assert.equal(easeInOutCubic(1), 1);
  assert.equal(easeInOutCubic(0.5), 0.5);
  assert.ok(easeInOutCubic(0.1) < 0.01); // 시작은 천천히
  assert.ok(easeInOutCubic(0.9) > 0.99); // 끝도 천천히
  let previous = 0;
  for (let i = 1; i <= 100; i++) {
    const value = easeInOutCubic(i / 100);
    assert.ok(value >= previous);
    previous = value;
  }
});

test("wheel deltas are converted to pixels for line and page modes", () => {
  assert.equal(wheelPixels({ deltaY: 100, deltaMode: 0 }, 800), 100);
  assert.equal(wheelPixels({ deltaY: 3, deltaMode: 1 }, 800), 120);
  assert.equal(wheelPixels({ deltaY: 1, deltaMode: 2 }, 800), 800);
});

test("glide target: down on page one goes to page two, up near page two goes to the top, else null", () => {
  assert.equal(glideTarget({ deltaY: 100, scrollY: 0, secondTop: SECOND_TOP }), SECOND_TOP);
  assert.equal(glideTarget({ deltaY: 100, scrollY: 300, secondTop: SECOND_TOP }), SECOND_TOP);
  assert.equal(glideTarget({ deltaY: -100, scrollY: SECOND_TOP, secondTop: SECOND_TOP }), 0);
  assert.equal(glideTarget({ deltaY: -100, scrollY: 300, secondTop: SECOND_TOP }), 0);
  // 이미 2페이지 위치이거나 그 안이면 아래로는 기본 스크롤
  assert.equal(glideTarget({ deltaY: 100, scrollY: SECOND_TOP, secondTop: SECOND_TOP }), null);
  assert.equal(glideTarget({ deltaY: 100, scrollY: 800, secondTop: SECOND_TOP }), null);
  // 2페이지 안쪽 깊은 곳에서 위로는 기본 스크롤, 맨 위에서 위로도 할 일 없음
  assert.equal(glideTarget({ deltaY: -100, scrollY: SECOND_TOP + 100, secondTop: SECOND_TOP }), null);
  assert.equal(glideTarget({ deltaY: -100, scrollY: 0, secondTop: SECOND_TOP }), null);
  // 미세한 휠·잘못된 값은 가로채지 않는다
  assert.equal(glideTarget({ deltaY: 3, scrollY: 0, secondTop: SECOND_TOP }), null);
  assert.equal(glideTarget({ deltaY: Number.NaN, scrollY: 0, secondTop: SECOND_TOP }), null);
});

test("one wheel on page one glides smoothly to page two and lands exactly", () => {
  const env = setup();
  const event = env.wheel({ deltaY: 120 });
  assert.equal(event.prevented, true);
  assert.equal(env.glide.isGliding(), true);

  const positions = [];
  while (env.advance(16)) positions.push(env.win.scrollY);
  assert.equal(env.glide.isGliding(), false);
  assert.equal(positions.at(-1), SECOND_TOP);
  assert.ok(positions.length > 50 && positions.length < 80); // 약 1.1초 / 16ms
  assert.ok(positions.every((value, index) => index === 0 || value >= positions[index - 1])); // 되튐 없음
  assert.ok(positions.every((value) => value >= 0 && value <= SECOND_TOP)); // 범위를 넘지 않음
  assert.ok(env.calls.every((call) => call.behavior === "instant" && call.left === 0)); // CSS 부드러움과 겹치지 않음
});

test("a frame time earlier than the start never moves backwards", () => {
  const env = setup();
  env.wheel({});
  env.setClock(900); // rAF 시각이 시작(1000)보다 앞선 경우
  env.advance(0);
  assert.ok(env.win.scrollY >= 0);
  assert.equal(env.calls[0].top, 0);
});

test("wheel events during a glide are swallowed so glides never overlap", () => {
  const env = setup();
  env.wheel({});
  env.advance(100);
  const during = env.wheel({ deltaY: 300 });
  assert.equal(during.prevented, true);
  assert.equal(env.frames.length, 1); // 새 이동이 시작되지 않았다
  while (env.advance(16));
  assert.equal(env.win.scrollY, SECOND_TOP);
});

test("once on page two, further wheels scroll natively; up at the top of page two glides back", () => {
  const env = setup({ scrollY: SECOND_TOP });
  const down = env.wheel({ deltaY: 100 });
  assert.equal(down.prevented, false);
  const up = env.wheel({ deltaY: -100 });
  assert.equal(up.prevented, true);
  while (env.advance(16));
  assert.equal(env.win.scrollY, 0);
  const again = env.wheel({ deltaY: -100 });
  assert.equal(again.prevented, false); // 맨 위에서 더 올릴 곳이 없다
});

test("does not intercept zoom, reduced motion, an open dialog, or tiny wheels", () => {
  assert.equal(setup().wheel({ ctrlKey: true }).prevented, false);
  assert.equal(setup({ reduced: true }).wheel({}).prevented, false);
  assert.equal(setup({ dialogOpen: true }).wheel({}).prevented, false);
  assert.equal(setup().wheel({ deltaY: 2 }).prevented, false);
});

test("a pointer press, key press or touch stops the glide", () => {
  for (const type of ["pointerdown", "keydown", "touchstart"]) {
    const env = setup();
    env.wheel({});
    env.advance(120);
    const stoppedAt = env.win.scrollY;
    env.emit(type);
    assert.equal(env.glide.isGliding(), false, type);
    env.advance(120);
    assert.equal(env.win.scrollY, stoppedAt, type);
  }
});

test("registers a non-passive wheel listener and cleans up on destroy; does nothing without page two", () => {
  const env = setup();
  assert.equal(env.listeners.wheel.length, 1);
  env.glide.destroy();
  assert.equal(env.listeners.wheel.length, 0);
  assert.equal(env.listeners.keydown.length, 0);
  assert.equal(setup({ hasSecond: false }).glide, null);
});
