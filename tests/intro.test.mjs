import assert from "node:assert/strict";
import test from "node:test";

import { SEEN_KEY, isSafeLogoPath, playIntro, shouldPlayIntro, staggerDelays, tileBackground } from "../backend/static/intro.js";

// 값이 고정된 가짜 난수: 같은 순서로 0..1을 돌려준다.
function seeded(seed = 7) {
  let state = seed;
  return () => {
    state = (state * 48271) % 2147483647;
    return state / 2147483647;
  };
}

function fakeElement(tag = "div") {
  const listeners = {};
  const element = {
    tag,
    className: "",
    children: [],
    dataset: {},
    style: {
      props: {},
      setProperty(name, value) {
        this.props[name] = value;
      },
    },
    animations: [],
    append(...nodes) {
      this.children.push(...nodes);
    },
    replaceChildren() {
      this.children = [];
    },
    animate(keyframes, options) {
      const animation = { keyframes, options, onfinish: null };
      this.animations.push(animation);
      return animation;
    },
    addEventListener(type, handler) {
      listeners[type] = handler;
    },
    removeEventListener(type) {
      delete listeners[type];
    },
    fire(type) {
      listeners[type]?.();
    },
    listeners,
  };
  return element;
}

function setup({ pending = true, reduced = false, seen = false, logo = "/static/credit-pulse-logo-white.png", storageWorks = true } = {}) {
  const classes = new Set(pending ? ["intro-pending"] : []);
  const stage = fakeElement();
  const intro = fakeElement();
  intro.dataset.logo = logo;
  intro.querySelector = (selector) => (selector === ".intro-stage" ? stage : null);

  const store = new Map(seen ? [[SEEN_KEY, "1"]] : []);
  const timers = [];
  const win = fakeElement();
  win.matchMedia = () => ({ matches: reduced });
  win.sessionStorage = storageWorks
    ? { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, value) }
    : {
        getItem() {
          throw new Error("blocked");
        },
        setItem() {
          throw new Error("blocked");
        },
      };
  win.setTimeout = (callback, ms) => {
    timers.push({ callback, ms });
    return timers.length;
  };
  win.clearTimeout = () => {};

  const document = {
    documentElement: {
      classList: {
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
        contains: (name) => classes.has(name),
      },
    },
    getElementById: (id) => (id === "intro" ? intro : null),
    createElement: (tag) => fakeElement(tag),
  };
  return { document, window: win, intro, stage, classes, store, timers };
}

test("plays only on a fresh, motion-allowed session with usable storage", () => {
  const fresh = { getItem: () => null };
  assert.equal(shouldPlayIntro({ storage: fresh, reducedMotion: false }), true);
  assert.equal(shouldPlayIntro({ storage: fresh, reducedMotion: true }), false);
  assert.equal(shouldPlayIntro({ storage: { getItem: () => "1" }, reducedMotion: false }), false);
  assert.equal(shouldPlayIntro({ storage: null, reducedMotion: false }), false);
  assert.equal(
    shouldPlayIntro({
      storage: {
        getItem() {
          throw new Error("blocked");
        },
      },
      reducedMotion: false,
    }),
    false,
  );
});

test("accepts only our own static logo path", () => {
  assert.equal(isSafeLogoPath("/static/credit-pulse-logo-white.png"), true);
  for (const bad of ["https://evil.example/logo.png", "//evil.example/x.png", "/static/../secret.png", '/static/a.png"); x', "", null, undefined, 3]) {
    assert.equal(isSafeLogoPath(bad), false, String(bad));
  }
});

test("stagger delays are a shuffled, even spread over the span", () => {
  const delays = staggerDelays(48, 450, seeded());
  assert.equal(delays.length, 48);
  assert.equal(new Set(delays).size, 48); // 모든 조각의 출발 시점이 서로 다르다
  assert.ok(Math.min(...delays) >= 0 && Math.max(...delays) < 450);
  const sorted = [...delays].sort((a, b) => a - b);
  sorted.forEach((value, index) => assert.ok(Math.abs(value - (index * 450) / 48) < 1e-9));
  assert.notDeepEqual(delays, sorted); // 섞여 있다(순서대로가 아니다)
});

test("tile backgrounds map the grid corners to the logo corners", () => {
  assert.deepEqual(tileBackground(0, 0), { size: "1600% 300%", position: "0% 0%" });
  assert.deepEqual(tileBackground(2, 15), { size: "1600% 300%", position: "100% 100%" });
  assert.equal(tileBackground(1, 0).position, "0% 50%");
});

test("does nothing when the page did not mark the intro as pending", () => {
  const env = setup({ pending: false });
  assert.equal(playIntro({ document: env.document, window: env.window }), null);
  assert.equal(env.stage.children.length, 0);
  assert.equal(env.store.size, 0);
});

test("lifts the cover without playing for reduced motion, a seen session, a bad logo, or blocked storage", () => {
  for (const options of [{ reduced: true }, { seen: true }, { logo: "https://evil.example/x.png" }, { storageWorks: false }]) {
    const env = setup(options);
    assert.equal(playIntro({ document: env.document, window: env.window }), null, JSON.stringify(options));
    assert.equal(env.classes.has("intro-pending"), false, JSON.stringify(options));
    assert.equal(env.stage.children.length, 0, JSON.stringify(options));
  }
});

test("builds 48 tiles plus the settled logo and the gold tint, and remembers the session", () => {
  const env = setup();
  const controller = playIntro({ document: env.document, window: env.window, random: seeded() });
  assert.ok(controller);
  assert.equal(env.store.get(SEEN_KEY), "1");
  assert.equal(env.stage.style.props["--intro-logo"], 'url("/static/credit-pulse-logo-white.png")');

  const tiles = env.stage.children.filter((node) => node.className === "intro-tile");
  assert.equal(tiles.length, 48);
  assert.equal(env.stage.children.filter((node) => node.className === "intro-full").length, 1);
  assert.equal(env.stage.children.filter((node) => node.className === "intro-gold").length, 1);

  // 조각은 transform·opacity만 움직인다
  for (const tile of tiles) {
    const [flight, settle] = tile.animations;
    for (const frame of flight.keyframes) assert.deepEqual(Object.keys(frame).sort(), ["opacity", "transform"]);
    assert.equal(flight.options.easing, "cubic-bezier(0.16, 1, 0.3, 1)");
    assert.deepEqual(Object.keys(settle.keyframes[0]), ["opacity"]);
  }

  const gold = env.stage.children.find((node) => node.className === "intro-gold");
  const peaks = gold.animations[0].keyframes.map((frame) => frame.opacity);
  assert.deepEqual(peaks, [0, 0.6, 0.6, 0]); // 흰색 → 은은한 금색 → 흰색

  // 끝나는 시각: 전체 2800ms에서 마지막 500ms는 페이드아웃
  assert.equal(env.timers.at(-1).ms, 2800 - 500);
});

test("fades out after the hold, then lifts the cover and clears the stage", () => {
  const env = setup();
  playIntro({ document: env.document, window: env.window, random: seeded() });
  env.timers.at(-1).callback();
  assert.equal(env.intro.animations.length, 1);
  assert.equal(env.intro.animations[0].options.duration, 500);
  assert.equal(env.classes.has("intro-pending"), true); // 페이드 중에는 아직 덮고 있다
  env.intro.animations[0].onfinish();
  assert.equal(env.classes.has("intro-pending"), false);
  assert.equal(env.stage.children.length, 0);
  assert.equal(Object.keys(env.intro.listeners).length, 0);
  assert.equal(Object.keys(env.window.listeners).length, 0);
});

test("a click or any key skips with a short fade, only once", () => {
  const clicked = setup();
  playIntro({ document: clicked.document, window: clicked.window, random: seeded() });
  clicked.intro.fire("pointerdown");
  clicked.intro.fire("pointerdown");
  clicked.window.fire("keydown");
  assert.equal(clicked.intro.animations.length, 1);
  assert.equal(clicked.intro.animations[0].options.duration, 150);
  clicked.intro.animations[0].onfinish();
  assert.equal(clicked.classes.has("intro-pending"), false);

  const keyed = setup();
  playIntro({ document: keyed.document, window: keyed.window, random: seeded() });
  keyed.window.fire("keydown");
  assert.equal(keyed.intro.animations[0].options.duration, 150);
});
