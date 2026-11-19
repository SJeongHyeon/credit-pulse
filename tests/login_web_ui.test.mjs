import assert from "node:assert/strict";
import test from "node:test";

import { createLoginController } from "../backend/static/login.js";

const MESSAGES = {
  invalid: "이메일 또는 비밀번호가 맞지 않습니다.",
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 로그인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  sessionNotKept: "로그인은 되었지만 세션을 유지하지 못했습니다. 브라우저의 쿠키 설정을 확인하고 다시 시도해 주세요.",
  generic: "로그인 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  logout: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
  tooManyAttempts: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  loginRejected: "로그인하지 못했습니다. 입력을 확인한 뒤 다시 시도해 주세요.",
  502: "서버 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.",
  503: "서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  504: "서버 응답 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.",
};


function createElement(values = {}) {
  const attributes = {};
  return {
    hidden: false,
    disabled: false,
    textContent: "",
    value: "",
    focused: false,
    listeners: {},
    ...values,
    addEventListener(eventName, listener) {
      this.listeners[eventName] = listener;
    },
    focus() {
      this.focused = true;
    },
    setAttribute(name, value) {
      attributes[name] = value;
    },
    getAttribute(name) {
      return attributes[name];
    },
  };
}


// 실제 login.html의 첫 상태와 같게 만든다: 폼은 숨김, 확인 중 표시는 보임.
function createPage() {
  const elements = {
    "login-form": createElement({ hidden: true }),
    email: createElement({ value: "reviewer@example.com" }),
    password: createElement({ value: "not-a-real-password", type: "password" }),
    "login-error": createElement({ hidden: true }),
    "login-status": createElement({ hidden: false }),
    "login-submit": createElement({ textContent: "로그인" }),
    "toggle-password": createElement({ textContent: "표시" }),
    "open-login": createElement({ textContent: "로그인" }),
    "hero-login": createElement({ textContent: "로그인" }),
    "hero-reviews": createElement({ textContent: "심사 건 확인" }),
    "review-login-notice": createElement({
      open: false,
      showModal() { this.open = true; },
    }),
    "service-startup": createElement({ hidden: false }),
  };
  return {
    elements,
    document: { documentElement: createElement(), getElementById: (id) => elements[id] },
  };
}


const submit = (elements) => elements["login-form"].listeners.submit({ preventDefault() {} });


// AC-02
test("successful login navigates to the reviews page and clears the password", async () => {
  const { document, elements } = createPage();
  const responses = [
    { status: 401 },
    { status: 204 },
    {
      status: 200,
      json: async () => ({
        id: "user-1",
        email: "reviewer@example.com",
        display_name: "심사역",
        is_demo: true,
      }),
    },
  ];
  const calls = [];
  const navigations = [];
  const fetch = async (path, options = {}) => {
    calls.push({ path, options });
    return responses.shift();
  };

  const controller = createLoginController({ document, fetch, navigate: (path) => navigations.push(path) });
  await controller.initialize();
  assert.deepEqual(navigations, []);
  await submit(elements);

  assert.deepEqual(navigations, ["/reviews"]);
  assert.equal(elements.password.value, "");
  assert.equal(elements["login-form"].hidden, true);
  assert.equal(elements["login-status"].hidden, false);
  assert.equal(elements["login-status"].textContent, "심사 건 화면으로 이동하는 중…");
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[1], {
    path: "/api/v1/auth/login",
    options: {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: "reviewer@example.com", password: "not-a-real-password" }),
      credentials: "same-origin",
    },
  });
});


// AC-05
test("an existing session can stay on the homepage and use the reviews link", async () => {
  const { document, elements } = createPage();
  const navigations = [];
  const calls = [];
  const fetch = async (path) => {
    calls.push(path);
    return { status: 200, json: async () => ({ display_name: "심사역" }) };
  };

  await createLoginController({ document, fetch, navigate: (path) => navigations.push(path) }).initialize();

  assert.deepEqual(navigations, []);
  assert.deepEqual(calls, ["/api/v1/auth/me"]);
  assert.equal(elements["login-form"].hidden, true);
  assert.equal(elements["login-status"].hidden, true);
  assert.equal(elements["open-login"].hidden, true);
  assert.equal(elements["hero-login"].hidden, true);
  assert.equal(elements["service-startup"].hidden, true);
  elements["hero-reviews"].listeners.click({ preventDefault: () => assert.fail("the reviews link should navigate") });
  assert.equal(elements["review-login-notice"].open, false);
});


test("the reviews link opens a notice on the homepage when signed out", async () => {
  const { document, elements } = createPage();
  const navigations = [];
  await createLoginController({
    document,
    fetch: async () => ({ status: 401 }),
    navigate: (path) => navigations.push(path),
  }).initialize();

  let prevented = false;
  elements["hero-reviews"].listeners.click({ preventDefault() { prevented = true; } });

  assert.equal(prevented, true);
  assert.equal(elements["review-login-notice"].open, true);
  assert.deepEqual(navigations, []);
});


test("a server redirect from the reviews page opens the notice on arrival", async () => {
  const { document, elements } = createPage();
  elements["review-login-notice"].setAttribute("data-open", "true");
  await createLoginController({
    document,
    fetch: async () => ({ status: 401 }),
    navigate: () => assert.fail("the notice belongs on the homepage"),
  }).initialize();

  assert.equal(elements["review-login-notice"].open, true);
});

test("the homepage shows a visible status while the first session request is pending", async () => {
  const { document, elements } = createPage();
  let release;
  const fetch = () => new Promise((resolve) => { release = resolve; });
  const pending = createLoginController({ document, fetch, navigate: () => {} }).initialize();

  assert.equal(elements["service-startup"].hidden, false);
  assert.match(elements["service-startup"].textContent, /첫 요청은 잠시 걸릴 수 있습니다/);

  release({ status: 401 });
  await pending;
  assert.equal(elements["service-startup"].hidden, true);
});

test("first session lookup distinguishes 502, 503 and 504 without showing server details", async () => {
  for (const status of [502, 503, 504]) {
    const { document, elements } = createPage();
    await createLoginController({
      document,
      fetch: async () => ({ status, json: async () => ({ details: "secret-request-id" }) }),
      navigate: () => {},
    }).initialize();

    assert.equal(elements["service-startup"].hidden, false);
    assert.equal(elements["service-startup"].textContent, MESSAGES[status]);
    assert.equal(elements["login-error"].textContent, MESSAGES[status]);
    assert.doesNotMatch(elements["service-startup"].textContent, /secret-request-id/);
  }
});


test("no navigation happens when the user is signed out or login fails", async () => {
  const { document, elements } = createPage();
  const navigations = [];
  const fetch = async (path) => (path === "/api/v1/auth/login" ? { status: 401 } : { status: 401 });

  const controller = createLoginController({ document, fetch, navigate: (path) => navigations.push(path) });
  await controller.initialize();
  await submit(elements);

  assert.deepEqual(navigations, []);
  assert.equal(elements["login-form"].hidden, false);
});


test("failed login clears the password and shows no server error details", async () => {
  const { document, elements } = createPage();
  const fetch = async (path) => {
    if (path === "/api/v1/auth/me") return { status: 401 };
    return {
      status: 401,
      json: async () => ({ error: { details: "do not show this", request_id: "secret-request-id" } }),
    };
  };

  const controller = createLoginController({ document, fetch, navigate: () => {} });
  await controller.initialize();
  await submit(elements);

  assert.equal(elements.password.value, "");
  assert.equal(elements["login-error"].hidden, false);
  assert.equal(elements["login-error"].textContent, MESSAGES.invalid);
  assert.doesNotMatch(elements["login-error"].textContent, /details|secret-request-id/);
});


test("initial session lookup failure shows a network error instead of rejecting", async () => {
  const { document, elements } = createPage();
  const fetch = async () => {
    throw new Error("network unavailable");
  };

  const controller = createLoginController({ document, fetch, navigate: () => {} });

  await assert.doesNotReject(controller.initialize());
  assert.equal(elements["login-form"].hidden, false);
  assert.equal(elements["login-error"].hidden, false);
  assert.equal(elements["login-error"].textContent, MESSAGES.network);
});


// AC-07
test("submit button shows progress while waiting and is restored afterwards", async () => {
  const { document, elements } = createPage();
  let releaseLogin;
  const fetch = async (path) => {
    if (path === "/api/v1/auth/me") return { status: 401 };
    await new Promise((resolve) => {
      releaseLogin = resolve;
    });
    return { status: 401 };
  };

  const controller = createLoginController({ document, fetch, navigate: () => {} });
  await controller.initialize();
  const pending = submit(elements);
  await Promise.resolve();

  assert.equal(elements["login-submit"].disabled, true);
  assert.equal(elements["login-submit"].textContent, "로그인 중…");

  releaseLogin();
  await pending;

  assert.equal(elements["login-submit"].disabled, false);
  assert.equal(elements["login-submit"].textContent, "로그인");
});


// AC-08
test("status indicator is hidden after the first session lookup", async () => {
  const outcomes = [
    { fetch: async () => ({ status: 401 }), formHidden: false, statusHidden: true, navigations: [] },
    { fetch: async () => ({ status: 500 }), formHidden: false, statusHidden: true, navigations: [] },
    {
      fetch: async () => {
        throw new Error("network unavailable");
      },
      formHidden: false,
      statusHidden: true,
      navigations: [],
    },
    {
      fetch: async () => ({ status: 200, json: async () => ({ display_name: "심사역" }) }),
      formHidden: true,
      statusHidden: true,
      navigations: [],
    },
  ];

  for (const outcome of outcomes) {
    const { document, elements } = createPage();
    const navigations = [];
    assert.equal(elements["login-status"].hidden, false);

    await createLoginController({
      document,
      fetch: outcome.fetch,
      navigate: (path) => navigations.push(path),
    }).initialize();

    assert.equal(elements["login-status"].hidden, outcome.statusHidden);
    assert.equal(elements["login-form"].hidden, outcome.formHidden);
    assert.deepEqual(navigations, outcome.navigations);
  }
});


// AC-09
test("failed login moves focus to the password input", async () => {
  const { document, elements } = createPage();
  const fetch = async () => ({ status: 401 });

  const controller = createLoginController({ document, fetch, navigate: () => {} });
  await controller.initialize();
  await submit(elements);

  assert.equal(elements.password.focused, true);
});


// AC-10
test("login accepted but session not kept shows the cookie guidance", async () => {
  const { document, elements } = createPage();
  const fetch = async (path) => (path === "/api/v1/auth/login" ? { status: 204 } : { status: 401 });

  const controller = createLoginController({ document, fetch, navigate: () => {} });
  await controller.initialize();
  await submit(elements);

  assert.equal(elements["login-form"].hidden, false);
  assert.equal(elements["login-error"].hidden, false);
  assert.equal(elements["login-error"].textContent, MESSAGES.sessionNotKept);
});


// AC-11
test("login errors are told apart without leaking input or server details", async () => {
  const cases = [
    {
      login: async () => {
        throw new Error("network unavailable reviewer@example.com");
      },
      message: MESSAGES.network,
    },
    {
      login: async () => ({
        status: 503,
        json: async () => ({ error: { details: "db password=hunter2", request_id: "req-1" } }),
      }),
      message: MESSAGES[503],
    },
    { login: async () => ({ status: 400 }), message: MESSAGES.loginRejected },
  ];

  for (const { login, message } of cases) {
    const { document, elements } = createPage();
    const fetch = async (path) => (path === "/api/v1/auth/me" ? { status: 401 } : login());

    const controller = createLoginController({ document, fetch, navigate: () => {} });
    await controller.initialize();
    await submit(elements);

    const text = elements["login-error"].textContent;
    assert.equal(text, message);
    assert.doesNotMatch(text, /reviewer@example\.com|not-a-real-password|hunter2|req-1/);
    assert.equal(elements["login-submit"].disabled, false);
  }
});


// AC-12
test("password toggle only flips the input type and resets after a request", async () => {
  const { document, elements } = createPage();
  const fetch = async () => ({ status: 401 });

  const controller = createLoginController({ document, fetch, navigate: () => {} });
  await controller.initialize();
  const toggle = elements["toggle-password"];

  toggle.listeners.click();
  assert.equal(elements.password.type, "text");
  assert.equal(toggle.textContent, "숨김");

  toggle.listeners.click();
  assert.equal(elements.password.type, "password");
  assert.equal(toggle.textContent, "표시");

  toggle.listeners.click();
  await submit(elements);

  assert.equal(elements.password.type, "password");
  assert.equal(toggle.textContent, "표시");
});


// AC-14
test("login attempts rejected with 4xx get login-specific messages", async () => {
  const cases = [
    { status: 429, message: MESSAGES.tooManyAttempts },
    { status: 403, message: MESSAGES.loginRejected },
    { status: 404, message: MESSAGES.loginRejected },
  ];

  for (const { status, message } of cases) {
    const { document, elements } = createPage();
    const fetch = async (path) => (path === "/api/v1/auth/me" ? { status: 401 } : { status });

    const controller = createLoginController({ document, fetch, navigate: () => {} });
    await controller.initialize();
    await submit(elements);

    assert.equal(elements["login-error"].textContent, message);
    assert.notEqual(elements["login-error"].textContent, MESSAGES.generic);
    assert.equal(elements["login-submit"].disabled, false);
  }
});


test("401 keeps the credentials message and an unexpected session check keeps the status message", async () => {
  const login = createPage();
  const loginFetch = async (path) => (path === "/api/v1/auth/me" ? { status: 401 } : { status: 401 });
  await createLoginController({ document: login.document, fetch: loginFetch, navigate: () => {} }).initialize();
  await submit(login.elements);
  assert.equal(login.elements["login-error"].textContent, MESSAGES.invalid);

  const lookup = createPage();
  await createLoginController({ document: lookup.document, fetch: async () => ({ status: 403 }), navigate: () => {} }).initialize();
  assert.equal(lookup.elements["login-error"].textContent, MESSAGES.generic);
});


// 개정 8 AC-65: 링크 칸에서 시작한 로그인은 그 칸의 도착지로 간다
function sessionPage(storageValue, storageOverrides = {}) {
  const { document, elements } = createPage();
  const removed = [];
  const storage = {
    getItem: (key) => (key === "login-next" ? storageValue : null),
    removeItem: (key) => removed.push(key),
    ...storageOverrides,
  };
  const navigations = [];
  // 이동은 로그인 제출이 성공한 뒤에만 한다(이미 로그인된 화면을 열 때는 이동하지 않는다).
  const fetch = async (url) =>
    String(url).includes("/auth/login")
      ? { status: 204 }
      : { status: 200, json: async () => ({ display_name: "심사역" }) };
  const controller = createLoginController({ document, fetch, navigate: (path) => navigations.push(path), storage });
  return { controller, navigations, removed, signIn: () => submit(elements) };
}

test("login-next in storage sends the user to the clicked tile destination once", async () => {
  for (const next of ["/reviews", "/?open=search", "/?open=reviews", "/?open=reports", "/?open=approvals", "/?open=rules"]) {
    const { navigations, removed, signIn } = sessionPage(next);
    await signIn();
    assert.deepEqual(navigations, [next]);
    assert.deepEqual(removed, ["login-next"]);
  }
});

test("a missing, unknown or external login-next falls back to the reviews page", async () => {
  for (const value of [null, "", "/other", "//evil.example", "https://evil.example/approvals", "/approvals?x=1", "/approvals", "/?open=other", "/?open=rules&x=1", "javascript:1"]) {
    const { navigations, signIn } = sessionPage(value);
    await signIn();
    assert.deepEqual(navigations, ["/reviews"], String(value));
  }
});

test("a storage that throws still logs in and goes to the reviews page", async () => {
  const { navigations, signIn } = sessionPage("/approvals", {
    getItem: () => {
      throw new Error("blocked");
    },
    removeItem: () => {
      throw new Error("blocked");
    },
  });
  await signIn();
  assert.deepEqual(navigations, ["/reviews"]);
});
