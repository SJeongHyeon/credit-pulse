import { serviceStatusMessage } from "./service-errors.js";

const MESSAGES = {
  invalidCredentials: "이메일 또는 비밀번호가 맞지 않습니다.",
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 로그인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  sessionNotKept: "로그인은 되었지만 세션을 유지하지 못했습니다. 브라우저의 쿠키 설정을 확인하고 다시 시도해 주세요.",
  generic: "로그인 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  tooManyAttempts: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
  loginRejected: "로그인하지 못했습니다. 입력을 확인한 뒤 다시 시도해 주세요.",
  redirecting: "심사 건 화면으로 이동하는 중…",
  logoutFailed: "로그아웃하지 못했습니다. 다시 시도해 주세요.",
};
const REVIEWS_PATH = "/reviews";
const SUBMIT_LABEL = "로그인";
const SUBMITTING_LABEL = "로그인 중…";
const SHOW_PASSWORD_LABEL = "표시";
const HIDE_PASSWORD_LABEL = "숨김";


// 서버가 준 오류 본문(details, request_id)은 읽지 않는다. 상태 코드만으로 문구를 고른다.
// 세션 확인(/me)이 예상 밖 응답을 줬을 때의 문구.
function messageForStatus(status) {
  return serviceStatusMessage(status) ?? (status >= 500 ? MESSAGES.server : MESSAGES.generic);
}

// 로그인 시도(401 제외)가 실패했을 때의 문구. 세션 확인 문구와 구분한다.
function messageForLoginFailure(status) {
  if (status === 429) return MESSAGES.tooManyAttempts;
  const serviceMessage = serviceStatusMessage(status);
  if (serviceMessage) return serviceMessage;
  if (status >= 500) return MESSAGES.server;
  return MESSAGES.loginRejected;
}


// 랜딩의 링크 칸(feature-login.js)이 적어 둔 로그인 뒤 도착지. 허용 목록에 있는 값만 따른다.
const NEXT_KEY = "login-next";
// feature-login.js의 ALLOWED와 항상 같아야 한다.
const ALLOWED_NEXT = [REVIEWS_PATH, "/?open=search", "/?open=reviews", "/?open=reports", "/?open=approvals", "/?open=rules"];

function hasNextPath(storage) {
  try {
    return ALLOWED_NEXT.includes(storage?.getItem(NEXT_KEY));
  } catch {
    return false;
  }
}

function takeNextPath(storage) {
  try {
    const value = storage?.getItem(NEXT_KEY);
    storage?.removeItem(NEXT_KEY);
    return ALLOWED_NEXT.includes(value) ? value : REVIEWS_PATH;
  } catch {
    return REVIEWS_PATH;
  }
}


export function createLoginController({ document, fetch, navigate, storage }) {
  const form = document.getElementById("login-form");
  const email = document.getElementById("email");
  const password = document.getElementById("password");
  const error = document.getElementById("login-error");
  const status = document.getElementById("login-status");
  const submitButton = document.getElementById("login-submit");
  const togglePasswordButton = document.getElementById("toggle-password");
  const loginButtons = [document.getElementById("open-login"), document.getElementById("hero-login")];
  const startupStatus = document.getElementById("service-startup");
  // 로그인 상태 표시용 요소(랜딩에만 있다). 없으면 건너뛴다.
  const logoutButtons = [document.getElementById("site-logout"), document.getElementById("hero-logout")];
  const siteUser = document.getElementById("site-user");
  const loginHint = document.getElementById("login-required-hint");
  const logoutError = document.getElementById("logout-error");
  const dialog = document.getElementById("login-dialog");
  const reviewLink = document.getElementById("hero-reviews");
  const reviewLoginNotice = document.getElementById("review-login-notice");

  function showStartupStatus(message) {
    if (!startupStatus) return;
    startupStatus.textContent = message;
    startupStatus.hidden = false;
  }

  function hideStartupStatus() {
    if (startupStatus) startupStatus.hidden = true;
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function clearError() {
    error.textContent = "";
    error.hidden = true;
  }

  // 다른 스크립트(feature-login.js)가 /me를 다시 부르지 않고 상태를 읽도록 <html>에 적는다.
  function setAuthState(state, displayName = "") {
    document.documentElement?.setAttribute?.("data-auth", state);
    reviewLink?.setAttribute("aria-haspopup", state === "anonymous" ? "dialog" : "false");
    const signedIn = state === "authenticated";
    for (const button of logoutButtons) if (button) button.hidden = !signedIn;
    if (loginHint) loginHint.hidden = signedIn;
    if (siteUser) {
      siteUser.textContent = signedIn && displayName ? `${displayName}님` : "";
      siteUser.hidden = !siteUser.textContent;
    }
    if (logoutError) {
      logoutError.textContent = "";
      logoutError.hidden = true;
    }
    // 화면 반영이 끝난 뒤에 알린다: 시트가 로그아웃 버튼 위치를 재서 폭을 맞춘다.
    // 시트(feature-sheet.js)가 /me를 다시 부르지 않고 상태를 알게 하는 용도이기도 하다.
    if (typeof CustomEvent === "function") document.dispatchEvent?.(new CustomEvent("auth-state", { detail: { state } }));
  }

  function showLoginForm() {
    form.hidden = false;
    for (const button of loginButtons) if (button) button.hidden = false;
    setAuthState("anonymous");
  }

  function showAuthenticatedHome(displayName = "") {
    form.hidden = true;
    clearError();
    status.hidden = true;
    for (const button of loginButtons) if (button) button.hidden = true;
    setAuthState("authenticated", displayName);
    // 확인 중에 로그인 창이 먼저 열렸다면 빈 박스를 남기지 않는다: 저장된 도착지로 가거나 창을 닫는다.
    if (dialog?.open) {
      if (hasNextPath(storage)) navigate(takeNextPath(storage));
      else dialog.close?.();
    }
  }

  async function readDisplayName(response) {
    try {
      const body = await response.json();
      return typeof body?.display_name === "string" ? body.display_name : "";
    } catch {
      return "";
    }
  }

  // 로그인 완료 직후에는 심사 건 목록으로 간다. 이미 로그인한 사용자의 첫 화면 방문은 유지한다.
  function redirectToReviews() {
    form.hidden = true;
    clearError();
    status.textContent = MESSAGES.redirecting;
    status.hidden = false;
    navigate(takeNextPath(storage));
  }

  function setSubmitting(isSubmitting) {
    submitButton.disabled = isSubmitting;
    submitButton.textContent = isSubmitting ? SUBMITTING_LABEL : SUBMIT_LABEL;
  }

  function hidePassword() {
    password.type = "password";
    togglePasswordButton.textContent = SHOW_PASSWORD_LABEL;
  }

  function togglePassword() {
    const willShow = password.type === "password";
    password.type = willShow ? "text" : "password";
    togglePasswordButton.textContent = willShow ? HIDE_PASSWORD_LABEL : SHOW_PASSWORD_LABEL;
  }

  // "ok" | "signed-out" | "error" 중 하나를 돌려준다. 네트워크 예외는 호출한 쪽에서 처리한다.
  async function checkSession({ redirectOnSuccess = false } = {}) {
    const response = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    if (response.status === 401) {
      showLoginForm();
      return "signed-out";
    }
    if (response.status !== 200) {
      showLoginForm();
      showError(messageForStatus(response.status));
      return "error";
    }

    if (redirectOnSuccess) redirectToReviews();
    else showAuthenticatedHome(await readDisplayName(response));
    return "ok";
  }

  async function submitLogin(event) {
    event.preventDefault();
    if (submitButton.disabled) return;

    setSubmitting(true);
    clearError();
    hideStartupStatus();
    let focusPassword = false;
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: email.value, password: password.value }),
        credentials: "same-origin",
      });

      if (response.status === 204) {
        const result = await checkSession({ redirectOnSuccess: true });
        if (result === "signed-out") showError(MESSAGES.sessionNotKept);
        return;
      }
      if (response.status === 401) {
        showError(MESSAGES.invalidCredentials);
        focusPassword = true;
        return;
      }
      showError(messageForLoginFailure(response.status));
    } catch {
      showError(MESSAGES.network);
    } finally {
      password.value = "";
      hidePassword();
      setSubmitting(false);
      if (focusPassword) password.focus();
    }
  }

  async function initialize() {
    if (reviewLoginNotice?.getAttribute("data-open") === "true") reviewLoginNotice.showModal();
    showStartupStatus("서비스에 연결하는 중입니다. 첫 요청은 잠시 걸릴 수 있습니다.");
    try {
      const outcome = await checkSession();
      if (outcome === "error") showStartupStatus(error.textContent);
      else hideStartupStatus();
    } catch {
      showLoginForm();
      showError(MESSAGES.network);
      showStartupStatus(MESSAGES.network);
    } finally {
      status.hidden = true;
    }
  }

  // 랜딩의 로그아웃: 성공(204)이나 이미 세션 없음(401)이면 비로그인 화면으로 돌린다. 실패하면 로그인 상태를 유지한다.
  async function logout() {
    try {
      const response = await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
      if (response.status === 204 || response.status === 401) {
        clearError();
        status.hidden = true;
        showLoginForm();
        return;
      }
    } catch {
      // 아래 오류 문구로 이어진다.
    }
    if (logoutError) {
      logoutError.textContent = MESSAGES.logoutFailed;
      logoutError.hidden = false;
    }
  }

  form.addEventListener("submit", submitLogin);
  reviewLink?.addEventListener("click", (event) => {
    if (!reviewLoginNotice || document.documentElement?.getAttribute("data-auth") !== "anonymous") return;
    event.preventDefault();
    if (!reviewLoginNotice.open) reviewLoginNotice.showModal();
  });
  for (const button of logoutButtons) button?.addEventListener("click", logout);
  togglePasswordButton.addEventListener("click", togglePassword);

  return { initialize };
}


if (typeof window !== "undefined") {
  createLoginController({
    document: window.document,
    fetch: window.fetch,
    navigate: (path) => window.location.assign(path),
    storage: (() => {
      try {
        return window.sessionStorage;
      } catch {
        return undefined;
      }
    })(),
  }).initialize();
}
