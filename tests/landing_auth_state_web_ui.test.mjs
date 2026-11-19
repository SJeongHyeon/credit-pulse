// docs/specs/landing-feature-pages.md AC-01~06: 랜딩의 로그인 상태 반영
import assert from "node:assert/strict";
import test from "node:test";

import { createFeatureLoginController } from "../backend/static/feature-login.js";
import { createLoginController } from "../backend/static/login.js";

function createElement(values = {}) {
  const attributes = {};
  return {
    hidden: false,
    disabled: false,
    textContent: "",
    value: "",
    listeners: {},
    ...values,
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    focus() {},
    setAttribute(name, value) {
      attributes[name] = value;
    },
    getAttribute(name) {
      return name in attributes ? attributes[name] : (this.attrs?.[name] ?? null);
    },
  };
}

// 실제 login.html의 첫 상태: 로그아웃 버튼·이메일·로그아웃 오류는 숨김, 안내 문구는 보임.
function createPage({ dialogOpen = false } = {}) {
  const root = createElement();
  const elements = {
    "login-form": createElement({ hidden: true }),
    email: createElement(),
    password: createElement({ type: "password" }),
    "login-error": createElement({ hidden: true }),
    "login-status": createElement(),
    "login-submit": createElement(),
    "toggle-password": createElement(),
    "open-login": createElement(),
    "hero-login": createElement(),
    "site-logout": createElement({ hidden: true }),
    "hero-logout": createElement({ hidden: true }),
    "site-user": createElement({ hidden: true }),
    "login-required-hint": createElement({ textContent: "로그인 후 사용 가능합니다." }),
    "logout-error": createElement({ hidden: true }),
    "login-dialog": createElement({
      open: dialogOpen,
      close() {
        this.open = false;
      },
    }),
  };
  return { root, elements, document: { documentElement: root, getElementById: (id) => elements[id] } };
}

function createFetch({ me, logout = { status: 204 } }) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    calls.push({ path, method: options.method ?? "GET" });
    if (path === "/api/v1/auth/me") return typeof me === "function" ? me() : me;
    if (path === "/api/v1/auth/logout") return typeof logout === "function" ? logout() : logout;
    throw new Error(`unexpected ${path}`);
  };
  return { fetch, calls };
}

const SIGNED_IN = { status: 200, json: async () => ({ email: "reviewer@example.com", display_name: "심사역" }) };

function boot({ me, logout, dialogOpen, storage } = {}) {
  const page = createPage({ dialogOpen });
  const net = createFetch({ me, logout });
  const navigations = [];
  const controller = createLoginController({
    document: page.document,
    fetch: net.fetch,
    navigate: (path) => navigations.push(path),
    storage,
  });
  return { ...page, ...net, navigations, controller };
}

// AC-01
test("Given signed out, When the landing loads, Then login buttons and the hint are visible and logout is hidden", async () => {
  const { elements, root, controller } = boot({ me: { status: 401 } });
  await controller.initialize();

  assert.equal(root.getAttribute("data-auth"), "anonymous");
  assert.equal(elements["open-login"].hidden, false);
  assert.equal(elements["hero-login"].hidden, false);
  assert.equal(elements["login-required-hint"].hidden, false);
  assert.equal(elements["site-logout"].hidden, true);
  assert.equal(elements["hero-logout"].hidden, true);
  assert.equal(elements["site-user"].hidden, true);
});

// AC-02
test("Given signed in, When the landing loads, Then logout shows, the hint hides, and /me is called once", async () => {
  const { elements, root, calls, controller } = boot({ me: SIGNED_IN });
  await controller.initialize();

  assert.equal(root.getAttribute("data-auth"), "authenticated");
  assert.equal(elements["site-logout"].hidden, false);
  assert.equal(elements["hero-logout"].hidden, false);
  assert.equal(elements["open-login"].hidden, true);
  assert.equal(elements["hero-login"].hidden, true);
  assert.equal(elements["login-required-hint"].hidden, true);
  assert.equal(elements["site-user"].textContent, "심사역님");
  assert.equal(elements["site-user"].hidden, false);
  assert.deepEqual(calls.map((call) => call.path), ["/api/v1/auth/me"]);
});

// EC-01
test("a /me body without a display name keeps the signed-in state and hides the greeting", async () => {
  for (const body of [{ email: "reviewer@example.com" }, { display_name: 7 }, { display_name: "" }, null]) {
    const { elements, root, controller } = boot({ me: { status: 200, json: async () => body } });
    await controller.initialize();
    assert.equal(root.getAttribute("data-auth"), "authenticated");
    assert.equal(elements["site-user"].hidden, true);
    assert.equal(elements["site-user"].textContent, "");
  }
  const { root, controller } = boot({ me: { status: 200 } }); // json() 자체가 없는 응답
  await controller.initialize();
  assert.equal(root.getAttribute("data-auth"), "authenticated");
});

// EC-02
test("a failing /me leaves the signed-out screen", async () => {
  const page = boot({
    me: () => {
      throw new Error("offline");
    },
  });
  await page.controller.initialize();
  assert.equal(page.root.getAttribute("data-auth"), "anonymous");
  assert.equal(page.elements["site-logout"].hidden, true);
  assert.equal(page.elements["login-required-hint"].hidden, false);
});

// AC-04
test("Given signed in, When logout is pressed, Then POST /logout is called and the signed-out screen returns", async () => {
  for (const id of ["site-logout", "hero-logout"]) {
    const { elements, root, calls, controller } = boot({ me: SIGNED_IN });
    await controller.initialize();

    await elements[id].listeners.click();

    assert.deepEqual(calls.map((call) => `${call.method} ${call.path}`), [
      "GET /api/v1/auth/me",
      "POST /api/v1/auth/logout",
    ]);
    assert.equal(root.getAttribute("data-auth"), "anonymous");
    assert.equal(elements["open-login"].hidden, false);
    assert.equal(elements["hero-login"].hidden, false);
    assert.equal(elements["site-logout"].hidden, true);
    assert.equal(elements["hero-logout"].hidden, true);
    assert.equal(elements["login-required-hint"].hidden, false);
    assert.equal(elements["site-user"].textContent, "");
    assert.equal(elements["login-form"].hidden, false);
  }
});

// EC-04
test("a 401 on logout also returns to the signed-out screen", async () => {
  const { elements, root, controller } = boot({ me: SIGNED_IN, logout: { status: 401 } });
  await controller.initialize();
  await elements["site-logout"].listeners.click();
  assert.equal(root.getAttribute("data-auth"), "anonymous");
});

// EC-03
test("a failed logout keeps the signed-in state and shows a message", async () => {
  const failures = [
    { status: 500 },
    () => {
      throw new Error("offline");
    },
  ];
  for (const logout of failures) {
    const { elements, root, controller } = boot({ me: SIGNED_IN, logout });
    await controller.initialize();
    await elements["hero-logout"].listeners.click();

    assert.equal(root.getAttribute("data-auth"), "authenticated");
    assert.equal(elements["site-logout"].hidden, false);
    assert.equal(elements["logout-error"].hidden, false);
    assert.equal(elements["logout-error"].textContent, "로그아웃하지 못했습니다. 다시 시도해 주세요.");
  }
});

// AC-06
test("Given the login dialog opened before /me answered, When /me is 200, Then no empty box remains", async () => {
  const withNext = boot({
    me: SIGNED_IN,
    dialogOpen: true,
    storage: { getItem: (key) => (key === "login-next" ? "/?open=reports" : null), removeItem() {} },
  });
  await withNext.controller.initialize();
  assert.deepEqual(withNext.navigations, ["/?open=reports"]);

  const withoutNext = boot({ me: SIGNED_IN, dialogOpen: true, storage: { getItem: () => null, removeItem() {} } });
  await withoutNext.controller.initialize();
  assert.deepEqual(withoutNext.navigations, []);
  assert.equal(withoutNext.elements["login-dialog"].open, false);
});

// AC-03 / AC-05
function createTiles(hrefs, auth) {
  const tiles = hrefs.map((href) => createElement({ attrs: { href } }));
  const loginOpener = createElement();
  loginOpener.clicked = 0;
  loginOpener.click = function click() {
    this.clicked += 1;
  };
  const elements = { "open-login": loginOpener, "login-dialog": createElement() };
  const documentElement = createElement();
  if (auth) documentElement.setAttribute("data-auth", auth);
  const stored = new Map();
  createFeatureLoginController({
    document: { documentElement, getElementById: (id) => elements[id], querySelectorAll: () => tiles },
    storage: { setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key) },
  }).initialize();
  return { tiles, elements, stored };
}

const press = (tile) => {
  const event = {
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
  tile.listeners.click(event);
  return event;
};

const TILE_PATHS = ["/?open=search", "/?open=reviews", "/?open=reports", "/?open=approvals", "/?open=rules"];

test("Given signed in, When any of the 5 tiles is clicked, Then the login dialog is not opened (the sheet opens instead)", () => {
  const { tiles, elements, stored } = createTiles(TILE_PATHS, "authenticated");
  for (const tile of tiles) assert.equal(press(tile).prevented, false, tile.getAttribute("href"));
  assert.equal(elements["open-login"].clicked, 0);
  assert.equal(stored.size, 0);
});

test("Given signed out, When any of the 5 tiles is clicked, Then the dialog opens and that tile's page is remembered", () => {
  for (const auth of ["anonymous", undefined]) {
    const { tiles, elements, stored } = createTiles(TILE_PATHS, auth);
    TILE_PATHS.forEach((path, index) => {
      assert.equal(press(tiles[index]).prevented, true);
      assert.equal(stored.get("login-next"), path);
    });
    assert.equal(elements["open-login"].clicked, 5);
    elements["login-dialog"].listeners.close();
    assert.equal(stored.has("login-next"), false); // 창을 닫으면 도착지 초기화
  }
});
