import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createSignupController } from "../backend/static/signup.js";

function element(values = {}) {
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
  };
}

function setup(respond) {
  const ids = ["signup-form", "email", "display-name", "password", "password-confirm", "verification-code", "send-code", "signup-error", "signup-status", "signup-submit"];
  const els = Object.fromEntries(ids.map((id) => [id, element()]));
  els["display-name"].value = "심사역";
  const calls = [];
  const navigations = [];
  createSignupController({
    document: { getElementById: (id) => els[id] },
    fetch: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body), method: options.method });
      return respond(url);
    },
    navigate: (path) => navigations.push(path),
  });
  return { els, calls, navigations };
}

const click = (els, id) => els[id].listeners.click({ preventDefault() {} });
const submit = (els) => els["signup-form"].listeners.submit({ preventDefault() {} });

test("코드 발송은 이메일만 상대 경로로 보내고 안내를 보여 준다", async () => {
  const { els, calls } = setup(() => ({ ok: true, status: 202 }));
  els.email.value = "reviewer@gmail.com";
  els.password.value = "secret-pw";

  await click(els, "send-code");

  assert.deepEqual(calls, [
    { url: "/api/v1/auth/signup/verification-codes", method: "POST", body: { email: "reviewer@gmail.com" } },
  ]);
  assert.equal(els["signup-status"].hidden, false);
  assert.match(els["signup-status"].textContent, /인증코드/);
  assert.equal(els["send-code"].disabled, false);
});

test("가입은 이메일·비밀번호·인증코드와 공백을 정리한 닉네임을 보내고 201이면 이동한다", async () => {
  const { els, calls, navigations } = setup(() => ({ ok: true, status: 201 }));
  els.email.value = "reviewer@gmail.com";
  els.password.value = "secret-pw";
  els["password-confirm"].value = "secret-pw";
  els["verification-code"].value = "123456";
  els["display-name"].value = "  승민  ";

  await submit(els);

  assert.deepEqual(calls, [
    {
      url: "/api/v1/auth/signup",
      method: "POST",
      body: { email: "reviewer@gmail.com", password: "secret-pw", verification_code: "123456", display_name: "승민" },
    },
  ]);
  assert.deepEqual(navigations, ["/"]);
});

test("잘못된 닉네임은 가입 요청 전에 안내한다", async () => {
  for (const nickname of ["  ", "가".repeat(51), "앞\u2028뒤"]) {
    const { els, calls } = setup(() => ({ ok: true, status: 201 }));
    els.email.value = "reviewer@gmail.com";
    els["display-name"].value = nickname;
    els.password.value = els["password-confirm"].value = "test-password";
    els["verification-code"].value = "123456";

    await submit(els);

    assert.equal(calls.length, 0);
    assert.match(els["signup-error"].textContent, /닉네임.*1~50자/);
  }
});

test("인증코드가 비어 있으면 서버에 요청하지 않는다", async () => {
  const { els, calls } = setup(() => ({ ok: true, status: 201 }));
  els.email.value = "reviewer@gmail.com";
  els.password.value = "secret-pw";

  await submit(els);

  assert.equal(calls.length, 0);
  assert.equal(els["signup-error"].hidden, false);
});

for (const confirmation of ["", "different-pw"]) {
  test(`비밀번호 확인이 ${confirmation ? "불일치하면" : "비어 있으면"} 가입 요청을 보내지 않는다`, async () => {
    const { els, calls, navigations } = setup(() => ({ ok: true, status: 201 }));
    els.email.value = "reviewer@gmail.com";
    els.password.value = "secret-pw";
    els["password-confirm"].value = confirmation;
    els["verification-code"].value = "123456";

    await submit(els);

    assert.equal(calls.length, 0);
    assert.deepEqual(navigations, []);
    assert.equal(els["signup-error"].hidden, false);
    assert.match(els["signup-error"].textContent, /비밀번호.*일치/);
    assert.equal(els["signup-submit"].disabled, false);
  });
}

for (const [label, password, confirmation] of [
  ["영문 73자", "a".repeat(73), "a".repeat(73)],
  ["한글 25자", "한".repeat(25), "한".repeat(25)],
  ["확인 칸만 73바이트", "safe-password", "a".repeat(73)],
]) {
  test(`${label}는 가입 요청 전에 길이 제한을 안내한다`, async () => {
    const { els, calls } = setup(() => ({ ok: false, status: 400 }));
    els.email.value = "reviewer@gmail.com";
    els.password.value = password;
    els["password-confirm"].value = confirmation;
    els["verification-code"].value = "123456";

    await submit(els);

    assert.equal(calls.length, 0);
    assert.equal(els["signup-error"].hidden, false);
    assert.match(els["signup-error"].textContent, /비밀번호가 허용량을 넘었습니다/);
    assert.doesNotMatch(els["signup-error"].textContent, /인증코드나 입력값/);
  });
}

for (const [label, password] of [
  ["영문 72자", "a".repeat(72)],
  ["한글 24자", "한".repeat(24)],
  ["영문·한글 혼합 72바이트", "a".repeat(69) + "한"],
]) {
  test(`${label}는 길이 때문에 차단되지 않는다`, async () => {
    const { els, calls } = setup(() => ({ ok: true, status: 201 }));
    els.email.value = "reviewer@gmail.com";
    els.password.value = password;
    els["password-confirm"].value = password;
    els["verification-code"].value = "123456";

    await submit(els);

    assert.equal(calls.length, 1);
    assert.equal(calls[0].body.password, password);
  });
}

for (const [status, pattern] of [
  [429, /잠시 후/],
  [403, /가입.*(열려|비활성)/],
  [503, /메일/],
  [400, /확인/],
]) {
  test(`코드 발송 ${status} 응답은 오류 안내를 보여 준다`, async () => {
    const { els } = setup(() => ({ ok: false, status }));
    els.email.value = "reviewer@gmail.com";

    await click(els, "send-code");

    assert.equal(els["signup-error"].hidden, false);
    assert.match(els["signup-error"].textContent, pattern);
  });
}

test("가입 400·409 응답은 서로 다른 안내를 보여 준다", async () => {
  const first = setup(() => ({ ok: false, status: 400 }));
  first.els.email.value = "a@gmail.com";
  first.els.password.value = "pw";
  first.els["password-confirm"].value = "pw";
  first.els["verification-code"].value = "123456";
  await submit(first.els);

  const second = setup(() => ({ ok: false, status: 409 }));
  second.els.email.value = "a@gmail.com";
  second.els.password.value = "pw";
  second.els["password-confirm"].value = "pw";
  second.els["verification-code"].value = "123456";
  await submit(second.els);

  assert.notEqual(first.els["signup-error"].textContent, second.els["signup-error"].textContent);
  assert.match(second.els["signup-error"].textContent, /이미/);
});

test("signup.js에 하드코딩된 배포 URL이 없다", () => {
  const source = readFileSync(new URL("../backend/static/signup.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /https?:\/\//);
  assert.doesNotMatch(source, /run\.app/);
});

test("signup.html에 코드 발송 버튼과 코드 입력란이 있다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  for (const id of ["send-code", "verification-code", "signup-status"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("signup.html에 필수 비밀번호 확인 칸이 있다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  assert.match(html, /<label for="password-confirm">비밀번호 확인<\/label>/);
  assert.match(html, /<input[^>]*type="password"[^>]*id="password-confirm"[^>]*required/);
});

test("두 비밀번호 칸에서 영문·숫자와 한글 입력 한도를 확인할 수 있다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  assert.match(html, /id="password-length-hint"[^>]*>비밀번호는 영문·숫자만 쓰면 최대 72자, 한글만 쓰면 최대 24자입니다\.<\/p>/);
  for (const id of ["password", "password-confirm"]) {
    assert.match(html, new RegExp(`<input[^>]*id="${id}"[^>]*aria-describedby="password-length-hint"`));
  }
});

test("signup.html에 필수 닉네임 입력란이 있다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  assert.match(html, /<label for="display-name">닉네임<\/label>/);
  assert.match(html, /<input[^>]*id="display-name"[^>]*required/);
});

test("signup.html은 전용 CSS를 쓰고 인라인 style이 없다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  assert.match(html, /path='signup\.css'/);
  assert.doesNotMatch(html, /\sstyle="/);
});

test("이메일 입력과 코드 발송 버튼은 같은 한 행에 있다", () => {
  const html = readFileSync(new URL("../backend/templates/signup.html", import.meta.url), "utf8");
  const row = html.match(/<div class="field-row">([\s\S]*?)<\/div>/);
  assert.ok(row, "field-row가 있어야 한다");
  assert.match(row[1], /id="email"/);
  assert.match(row[1], /id="send-code"/);
  assert.doesNotMatch(row[1], /verification-code/);
});
