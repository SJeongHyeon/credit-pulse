import assert from "node:assert/strict";
import test from "node:test";

import { COMPANIES } from "../backend/static/landing-data.js";
import { MIN_FONT_PX, createSlideFit, fitFontSize } from "../backend/static/slide-fit.js";

const BASE_PX = 84; // CSS의 기본 기업명 크기(최대)
const PAD_LEFT = 24;
const GLYPH_EM = 0.98; // 한글 한 글자의 폭(자간 -0.02em 반영)

// 글자 수와 글자 크기로 폭이 정해지는 가짜 .slide-name
function fakeName(text, { clientWidth = 533 } = {}) {
  const inline = {};
  const name = {
    text,
    clientWidth,
    style: {
      setProperty: (k, v) => (inline[k] = v),
      removeProperty: (k) => delete inline[k],
      get fontSize() {
        return inline["font-size"];
      },
      set fontSize(v) {
        inline["font-size"] = v;
      },
      get whiteSpace() {
        return inline["white-space"];
      },
      set whiteSpace(v) {
        inline["white-space"] = v;
      },
    },
    inline,
    currentPx: () => (inline["font-size"] ? parseFloat(inline["font-size"]) : BASE_PX),
    // nowrap이면 한 줄의 폭이 곧 scrollWidth. 줄바꿈이 허용되면(white-space: normal) 폭에 맞춰 접힌다.
    get scrollWidth() {
      const oneLine = PAD_LEFT + [...text].length * name.currentPx() * GLYPH_EM;
      return inline["white-space"] === "normal" ? Math.min(oneLine, clientWidth) : oneLine;
    },
    matches: (selector) => selector === ".slide-name",
    nodeType: 1,
  };
  return name;
}

function setup(names, options = {}) {
  const els = names.map((text) => fakeName(text, options));
  const listeners = {};
  let mutationCallback = null;
  const viewport = {
    querySelectorAll: (selector) => (selector === ".slide-name" ? els : []),
  };
  const win = {
    getComputedStyle: (el) => ({ fontSize: `${el.currentPx()}px`, paddingLeft: `${PAD_LEFT}px`, paddingRight: "0px" }),
    MutationObserver: class {
      constructor(callback) {
        mutationCallback = callback;
      }
      observe() {}
    },
    addEventListener: (type, handler) => (listeners[type] = handler),
    document: { fonts: { ready: Promise.resolve() } },
  };
  const document = { getElementById: (id) => (id === "slide-viewport" ? viewport : null) };
  let resizeCallback = null;
  const controller = createSlideFit({ document, window: win, observeResize: options.useResizeObserver ? (element, cb) => (resizeCallback = cb) : undefined });
  return { els, controller, listeners, fire: (records) => mutationCallback(records), resize: () => (resizeCallback ? resizeCallback() : listeners.resize()) };
}

const fits = (el) => el.scrollWidth <= el.clientWidth;

test("fitFontSize leaves a name alone when it already fits or the numbers are unusable", () => {
  assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 500, neededWidth: 500 }), { fontPx: 84, fits: true });
  assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 500, neededWidth: 300 }), { fontPx: 84, fits: true });
  for (const bad of [0, -5, Number.NaN, undefined]) {
    assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: bad, neededWidth: 600 }), { fontPx: 84, fits: true });
    assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 500, neededWidth: bad }), { fontPx: 84, fits: true });
  }
});

test("fitFontSize shrinks in half-pixel steps and respects the floor", () => {
  const result = fitFontSize({ fontPx: 84, availableWidth: 509, neededWidth: 660 });
  assert.equal(result.fits, true);
  assert.ok(result.fontPx < 84 && result.fontPx * 2 === Math.floor(result.fontPx * 2));
  assert.ok((result.fontPx / 84) * 660 <= 509); // 줄인 크기에서 필요한 폭이 사용 가능한 폭 이하
  assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 100, neededWidth: 800 }), { fontPx: MIN_FONT_PX, fits: false });
  // 줄인 크기가 하한보다 작아도 하한 크기에서 들어가면 한 줄을 유지한다(줄바꿈을 허용하지 않는다)
  assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 221, neededWidth: 660 }), { fontPx: MIN_FONT_PX, fits: true });
  assert.deepEqual(fitFontSize({ fontPx: 84, availableWidth: 219, neededWidth: 660 }), { fontPx: MIN_FONT_PX, fits: false });
});

test("a narrow phone-width panel keeps the longest name on one line at the floor without allowing wrap", () => {
  const env = setup(["삼성에피스홀딩스"], { clientWidth: 245 }); // 사용 가능 폭 221px: 줄인 크기는 하한보다 작지만 하한(28px)에서는 들어간다
  const [el] = env.els;
  assert.equal(el.currentPx(), MIN_FONT_PX);
  assert.equal(el.inline["white-space"], undefined);
  assert.ok(fits(el));
});

test("a short name keeps the CSS size (no inline font-size)", () => {
  const env = setup(["모나미"]);
  assert.equal(env.els[0].inline["font-size"], undefined);
  assert.ok(fits(env.els[0]));
});

test("the longest real company name is shrunk to fit on one line", () => {
  const longest = COMPANIES.map((company) => company.name).sort((a, b) => [...b].length - [...a].length)[0];
  assert.ok(longest, "서비스 코퍼스에 기업이 있어야 한다");
  const env = setup([longest]);
  const [el] = env.els;
  assert.ok(el.inline["font-size"], "크기가 줄어야 한다");
  assert.ok(fits(el), `한 줄에 들어가야 한다: ${el.scrollWidth} > ${el.clientWidth}`);
  assert.ok(el.currentPx() < BASE_PX && el.currentPx() >= MIN_FONT_PX);
  assert.equal(el.inline["white-space"], undefined); // 줄바꿈을 허용하지 않는다
});

test("every company name fits on one line at the panel width we ship", () => {
  for (const { name } of COMPANIES) {
    const env = setup([name]);
    assert.ok(fits(env.els[0]), `${name}: ${env.els[0].scrollWidth} > ${env.els[0].clientWidth}`);
  }
});

test("a very narrow panel clamps to the floor and allows wrapping as a last resort", () => {
  const env = setup(["삼성에피스홀딩스"], { clientWidth: 120 });
  const [el] = env.els;
  assert.equal(el.currentPx(), MIN_FONT_PX);
  assert.equal(el.inline["white-space"], "normal");
});

test("refits when a new slide is added and when the panel is resized", () => {
  const env = setup(["삼성에피스홀딩스"]);
  const [el] = env.els;
  const before = el.currentPx();

  // 슬라이드가 바뀌어 새 이름이 들어옴(이전 인라인 값은 지우고 다시 잰다)
  el.inline["font-size"] = "10px";
  env.fire([{ addedNodes: [el] }]);
  assert.equal(el.currentPx(), before);

  // 패널이 넓어지면 더 큰 크기로, 좁아지면 더 작은 크기로
  el.clientWidth = 700;
  env.resize();
  assert.ok(el.currentPx() > before);
  assert.ok(fits(el));
  el.clientWidth = 400;
  env.resize();
  assert.ok(el.currentPx() < before);
  assert.ok(fits(el));
});

test("uses the ResizeObserver hook when given one and ignores non-element nodes", () => {
  const env = setup(["삼성에피스홀딩스"], { useResizeObserver: true });
  const [el] = env.els;
  el.clientWidth = 650;
  env.resize();
  assert.ok(fits(el));
  assert.doesNotThrow(() => env.fire([{ addedNodes: [{ nodeType: 3 }] }]));
});

test("returns null when the slide viewport is missing", () => {
  assert.equal(createSlideFit({ document: { getElementById: () => null }, window: {} }), null);
});
