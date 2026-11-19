import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createLandingController, createLoginDialogController } from "../backend/static/landing.js";
import { COMPANIES } from "../backend/static/landing-data.js";

const NO_RESULTS = "검색 결과가 없습니다.";

const company = (name, corp_code, overrides = {}) => ({
  name,
  corp_code,
  fiscal_year: 2025,
  filed_at: "2026-03-01",
  ...overrides,
});

const SAMPLE = [
  company("알파전자", "00000001"),
  company("베타화학", "00000002", { filed_at: "2026-03-02" }),
  company("F&F 홀딩스", "00000003", { filed_at: "2026-03-03" }),
];

function createElement(tagName = "div", values = {}) {
  const attributes = {};
  return {
    tagName: tagName.toUpperCase(),
    children: [],
    hidden: false,
    disabled: false,
    textContent: "",
    className: "",
    style: {},
    value: "",
    listeners: {},
    ...values,
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      this.children = nodes;
    },
    setAttribute(name, value) {
      attributes[name] = value;
    },
    getAttribute(name) {
      return attributes[name];
    },
    // 기업 정보·검색어를 HTML로 해석하는 코드가 있으면 테스트가 즉시 실패하게 한다.
    set innerHTML(_) {
      throw new Error("innerHTML must not be used");
    },
  };
}

function allText(element) {
  return [element.textContent, ...element.children.map(allText)].join(" ");
}

function descendants(element) {
  return element.children.flatMap((child) => [child, ...descendants(child)]);
}

function createFakeTimers() {
  let nextId = 1;
  const active = new Map();
  const log = [];
  return {
    setInterval(callback, ms) {
      const id = nextId++;
      active.set(id, callback);
      log.push(["set", ms]);
      return id;
    },
    clearInterval(id) {
      active.delete(id);
      log.push(["clear"]);
    },
    tick() {
      for (const callback of [...active.values()]) callback();
    },
    activeCount: () => active.size,
    log,
  };
}

function createPage(companies = SAMPLE, options = {}) {
  const elements = {
    "slide-viewport": createElement("div"),
    "slide-prev": createElement("button"),
    "slide-next": createElement("button"),
    "slide-position": createElement("p"),
    "slide-progress": createElement("div"),
    "slide-progress-fill": createElement("span"),
    "slide-empty": createElement("p", { hidden: true }),
    "company-search": createElement("form"),
    "company-query": createElement("input"),
    "info-panel": createElement("div"),
    "slide-toggle": createElement("button", { hidden: true }),
  };
  const document = {
    getElementById: (id) => elements[id],
    createElement: (tag) => createElement(tag),
  };
  const controller = createLandingController({ document, companies, ...options });
  controller.initialize();
  return {
    elements,
    panel: elements["info-panel"],
    toggle: elements["slide-toggle"],
    slideText: () => allText(elements["slide-viewport"]),
    progressWidth: () => elements["slide-progress-fill"].style.width,
    next: () => elements["slide-next"].listeners.click(),
    prev: () => elements["slide-prev"].listeners.click(),
    search(value) {
      elements["company-query"].value = value;
      elements["company-query"].listeners.input();
    },
    submit(value) {
      elements["company-query"].value = value;
      let prevented = false;
      elements["company-search"].listeners.submit({ preventDefault: () => (prevented = true) });
      return prevented;
    },
  };
}

test("AC-10 first company is shown with its code, report and filing date", () => {
  const page = createPage();

  const text = page.slideText();
  assert.match(text, /알파전자/);
  assert.match(text, /corp_code 00000001/);
  assert.match(text, /2025 사업보고서 · 공시 2026-03-01/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 3");
  assert.equal(page.elements["slide-viewport"].children.length, 1);
  assert.equal(page.elements["slide-empty"].hidden, true);
});

test("AC-10 next and previous move between companies and wrap around", () => {
  const page = createPage();
  const first = page.elements["slide-viewport"].children[0];

  page.next();
  assert.match(page.slideText(), /베타화학/);
  assert.equal(page.elements["slide-position"].textContent, "2 / 3");
  // 이동할 때마다 새 요소로 바뀌어야 CSS 슬라이드 효과가 다시 재생된다.
  assert.notEqual(page.elements["slide-viewport"].children[0], first);
  assert.match(page.elements["slide-viewport"].children[0].className, /slide-in-next/);

  page.next();
  page.next();
  assert.match(page.slideText(), /알파전자/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 3");

  page.prev();
  assert.match(page.slideText(), /F&F 홀딩스/);
  assert.equal(page.elements["slide-position"].textContent, "3 / 3");
  assert.match(page.elements["slide-viewport"].children[0].className, /slide-in-prev/);
});

test("AC-10 one progress track follows the current company", () => {
  const page = createPage();

  assert.equal(page.progressWidth(), `${(1 / 3) * 100}%`);
  assert.equal(page.elements["slide-progress"].getAttribute("aria-valuenow"), "1");
  assert.equal(page.elements["slide-progress"].getAttribute("aria-valuemax"), "3");

  page.next();
  assert.equal(page.progressWidth(), `${(2 / 3) * 100}%`);
  page.next();
  assert.equal(page.progressWidth(), "100%");
  page.next();
  assert.equal(page.progressWidth(), `${(1 / 3) * 100}%`);

  page.prev();
  assert.equal(page.progressWidth(), "100%");
  assert.equal(page.elements["slide-progress"].getAttribute("aria-valuenow"), "3");
});

test("AC-10 the 100-company track advances by exactly one percent", () => {
  const page = createPage(COMPANIES);
  assert.equal(COMPANIES.length, 100);
  assert.equal(page.progressWidth(), "1%");
  page.next();
  assert.equal(page.progressWidth(), "2%");
  page.prev();
  page.prev();
  assert.equal(page.progressWidth(), "100%");
});

test("AC-11 search narrows by name (case-insensitive) or corp_code and resets on clear", () => {
  const page = createPage();

  page.search("베타");
  assert.match(page.slideText(), /베타화학/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");

  page.search("00000003");
  assert.match(page.slideText(), /F&F 홀딩스/);

  page.search("  f&f   홀딩스 ");
  assert.match(page.slideText(), /F&F 홀딩스/, "앞뒤 공백 제거, 연속 공백은 하나로 본다");

  page.search("0000");
  assert.equal(page.elements["slide-position"].textContent, "1 / 3");
  assert.equal(page.progressWidth(), `${(1 / 3) * 100}%`);

  page.next();
  page.search("");
  assert.match(page.slideText(), /알파전자/, "검색을 지우면 첫 기업부터 다시 보인다");
  assert.equal(page.elements["slide-position"].textContent, "1 / 3");
  assert.equal(page.progressWidth(), `${(1 / 3) * 100}%`);
});

test("company aliases find the same 100 cards without changing their displayed names", () => {
  const page = createPage(COMPANIES);
  page.search("kctc");
  assert.match(page.slideText(), /케이씨티시/);
  assert.doesNotMatch(page.slideText(), /KCTC/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");

  page.search("ＮＰＣ");
  assert.match(page.slideText(), /엔피씨/);
  assert.doesNotMatch(page.slideText(), /NPC/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");

  page.search("네이버");
  assert.match(page.slideText(), /NAVER/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");

  page.search("e-world");
  assert.match(page.slideText(), /이월드/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");

  page.search("엘지");
  assert.equal(page.elements["slide-position"].textContent, "1 / 2");
  page.search("엘지화학");
  assert.match(page.slideText(), /LG화학/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");
  page.search("");
  assert.equal(page.elements["slide-position"].textContent, `1 / ${COMPANIES.length}`);
});

test("AC-11 submitting the search form does not reload the page and uses the current input", () => {
  const page = createPage();

  const prevented = page.submit("알파");

  assert.equal(prevented, true);
  assert.match(page.slideText(), /알파전자/);
  assert.equal(page.elements["slide-position"].textContent, "1 / 1");
});

test("AC-12 no match empties the slider and says so; navigation is disabled", () => {
  const page = createPage();

  page.search("없는회사");

  assert.equal(page.elements["slide-viewport"].children.length, 0);
  assert.equal(page.elements["slide-progress"].hidden, true);
  assert.equal(page.progressWidth(), "0%");
  assert.equal(page.elements["slide-position"].textContent, "");
  assert.equal(page.elements["slide-empty"].hidden, false);
  assert.equal(page.elements["slide-empty"].textContent, NO_RESULTS);
  assert.equal(page.elements["slide-prev"].disabled, true);
  assert.equal(page.elements["slide-next"].disabled, true);

  page.search("알파");
  assert.equal(page.elements["slide-empty"].hidden, true);
  assert.equal(page.elements["slide-empty"].textContent, "", "결과가 생기면 안내 문구를 지운다");
  assert.equal(page.elements["slide-progress"].hidden, false);
  assert.equal(page.progressWidth(), "100%");
});

test("AC-12 a single result disables previous and next; several enable them", () => {
  const page = createPage();
  assert.equal(page.elements["slide-prev"].disabled, false);
  assert.equal(page.elements["slide-next"].disabled, false);

  page.search("알파");
  assert.equal(page.elements["slide-prev"].disabled, true);
  assert.equal(page.elements["slide-next"].disabled, true);

  page.search("");
  assert.equal(page.elements["slide-prev"].disabled, false);
  assert.equal(page.elements["slide-next"].disabled, false);
});

test("AC-12 next and previous do nothing when there is no result", () => {
  const page = createPage();
  page.search("없는회사");

  assert.doesNotThrow(() => {
    page.next();
    page.prev();
  });
  assert.equal(page.elements["slide-viewport"].children.length, 0);
});

test("AC-16 HTML-looking company names and queries stay plain text", () => {
  const evil = company("<img src=x onerror=alert(1)>", "00000009");
  const page = createPage([...SAMPLE, evil]);

  page.search("<b>");
  assert.equal(page.elements["slide-viewport"].children.length, 0);
  assert.equal(page.elements["slide-empty"].textContent, NO_RESULTS, "검색어는 안내 문구에 섞이지 않는다");

  page.search("<img");
  const slide = page.elements["slide-viewport"].children[0];
  assert.match(allText(slide), /<img src=x onerror=alert\(1\)>/);
  for (const node of descendants(slide)) {
    assert.ok(["ARTICLE", "P", "H3", "SPAN"].includes(node.tagName), `${node.tagName}은 허용되지 않는다`);
  }
});

test("AC-14 service company list is well formed", () => {
  const manifest = readFileSync(new URL("../data/manifest.jsonl", import.meta.url), "utf8")
    .trim().split("\n").map((line) => JSON.parse(line));
  const activeCodes = new Set(manifest.filter((row) =>
    row.status === "success" && row.report_type === "annual" && !row.superseded,
  ).map((row) => row.corp_code));
  assert.equal(COMPANIES.length, activeCodes.size);
  const codes = COMPANIES.map((entry) => entry.corp_code);
  assert.equal(new Set(codes).size, codes.length, "corp_code는 중복이 없어야 한다");
  for (const entry of COMPANIES) {
    assert.match(entry.corp_code, /^\d{8}$/);
    assert.ok(entry.name.length > 0);
    assert.match(entry.filed_at, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(entry.fiscal_year, 2025);
  }
});

// ---- AC-25 로그인 팝업 ----
function createDialogPage({ withShowModal = true } = {}) {
  const calls = [];
  const dialog = createElement("dialog", {
    close() {
      calls.push("close");
    },
  });
  if (withShowModal) {
    dialog.showModal = () => calls.push("showModal");
  }
  const elements = {
    "login-dialog": dialog,
    "open-login": createElement("button"),
    "hero-login": createElement("button"),
    "login-close": createElement("button"),
  };
  const document = { getElementById: (id) => elements[id] };
  createLoginDialogController({ document }).initialize();
  return { elements, dialog, calls };
}

test("AC-25 상단 버튼과 히어로 버튼은 팝업을 연다", () => {
  const { elements, calls } = createDialogPage();
  elements["open-login"].listeners.click({});
  elements["hero-login"].listeners.click({});
  assert.deepEqual(calls, ["showModal", "showModal"]);
});

test("AC-25 닫기 버튼은 팝업을 닫는다", () => {
  const { elements, calls } = createDialogPage();
  elements["login-close"].listeners.click({});
  assert.deepEqual(calls, ["close"]);
});

test("AC-25 팝업 안쪽과 배경 클릭은 로그인 창을 닫지 않는다", () => {
  const { dialog, calls } = createDialogPage();
  dialog.listeners.click?.({ target: createElement("input") });
  dialog.listeners.click?.({ target: dialog });
  assert.deepEqual(calls, []);
});

test("AC-25 Esc에 의한 기본 닫기를 취소한다", () => {
  const { dialog, calls } = createDialogPage();
  const event = new Event("cancel", { cancelable: true });
  dialog.listeners.cancel(event);
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(calls, []);
});

test("AC-25 showModal이 없으면 open 속성으로 연다", () => {
  const { elements, dialog, calls } = createDialogPage({ withShowModal: false });
  elements["open-login"].listeners.click({});
  assert.deepEqual(calls, []);
  assert.equal(dialog.getAttribute("open"), "");
});

// ---- AC-31 자동 넘김 ----
function createAutoplayPage(options = {}) {
  const timers = createFakeTimers();
  const page = createPage(SAMPLE, { timers, intervalMs: 5000, ...options });
  return { timers, ...page };
}

test("AC-31 5초마다 다음 기업으로 넘어가고 순환한다", () => {
  const { timers, slideText, elements, toggle } = createAutoplayPage();
  assert.deepEqual(timers.log[0], ["set", 5000]);
  assert.match(slideText(), /알파전자/);
  timers.tick();
  assert.match(slideText(), /베타화학/);
  timers.tick();
  timers.tick();
  assert.match(slideText(), /알파전자/);
  assert.equal(elements["slide-viewport"].getAttribute("aria-live"), "off");
  assert.equal(toggle.hidden, false);
  assert.equal(toggle.textContent, "일시정지");
});

test("AC-31 마우스를 올리거나 포커스가 들어오면 멈추고 벗어나면 다시 시작한다", () => {
  const { timers, panel, elements } = createAutoplayPage();
  panel.listeners.mouseenter();
  assert.equal(timers.activeCount(), 0);
  assert.equal(elements["slide-viewport"].getAttribute("aria-live"), "polite");
  panel.listeners.mouseleave();
  assert.equal(timers.activeCount(), 1);
  panel.listeners.focusin();
  assert.equal(timers.activeCount(), 0);
  panel.listeners.focusout();
  assert.equal(timers.activeCount(), 1);
});

test("AC-31 직접 멈추면 마우스·포커스가 벗어나도 다시 시작하지 않는다", () => {
  const { timers, panel, toggle } = createAutoplayPage();
  toggle.listeners.click();
  assert.equal(timers.activeCount(), 0);
  assert.equal(toggle.textContent, "재생");
  panel.listeners.mouseenter();
  panel.listeners.mouseleave();
  panel.listeners.focusin();
  panel.listeners.focusout();
  assert.equal(timers.activeCount(), 0);
  toggle.listeners.click();
  assert.equal(timers.activeCount(), 1);
  assert.equal(toggle.textContent, "일시정지");
});

test("AC-31 이전·다음·검색 조작을 하면 5초를 다시 센다", () => {
  const { timers, next, search } = createAutoplayPage();
  const before = timers.log.length;
  next();
  assert.deepEqual(timers.log.slice(before), [["clear"], ["set", 5000]]);
  const mid = timers.log.length;
  search("베타");
  assert.deepEqual(timers.log.slice(mid), [["clear"], ["set", 5000]]);
  assert.equal(timers.activeCount(), 1);
});

test("AC-31 결과가 1곳이면 넘어가지 않는다", () => {
  const { timers, search, elements } = createAutoplayPage();
  search("베타화학");
  const slide = elements["slide-viewport"].children[0];
  timers.tick();
  assert.equal(elements["slide-viewport"].children[0], slide);
});

test("AC-31 동작 줄이기가 켜져 있으면 멈춘 채 시작하고 버튼으로만 시작한다", () => {
  const { timers, toggle, elements, slideText } = createAutoplayPage({ prefersReducedMotion: true });
  assert.equal(timers.activeCount(), 0);
  assert.equal(toggle.textContent, "재생");
  assert.equal(elements["slide-viewport"].getAttribute("aria-live"), "polite");
  timers.tick();
  assert.match(slideText(), /알파전자/);
  toggle.listeners.click();
  assert.equal(timers.activeCount(), 1);
  timers.tick();
  assert.match(slideText(), /베타화학/);
});
