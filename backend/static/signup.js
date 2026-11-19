const CODE_URL = "/api/v1/auth/signup/verification-codes";
const SIGNUP_URL = "/api/v1/auth/signup";
const PASSWORD_MAX_BYTES = 72;
const MAX_DISPLAY_NAME_LENGTH = 50;
const PASSWORD_LENGTH_ERROR = "비밀번호가 허용량을 넘었습니다. (영문·숫자만 쓰면 최대 72자, 한글만 쓰면 최대 24자)";

const CODE_ERRORS = {
  400: "Gmail 주소를 확인해 주세요. (@gmail.com, @googlemail.com만 가입할 수 있습니다)",
  403: "현재 회원가입이 열려 있지 않습니다.",
  429: "잠시 후 다시 시도해 주세요. 인증코드는 60초에 한 번만 보낼 수 있습니다.",
  503: "인증 메일을 보낼 수 없습니다. 잠시 후 다시 시도해 주세요.",
};

const SIGNUP_ERRORS = {
  400: "인증코드나 입력값을 확인해 주세요. 코드가 만료됐다면 다시 발송해 주세요.",
  403: "현재 회원가입이 열려 있지 않습니다.",
  409: "이미 가입한 이메일입니다.",
  503: "인증 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
};

export function createSignupController({ document, fetch, navigate }) {
  const form = document.getElementById("signup-form");
  const email = document.getElementById("email");
  const displayName = document.getElementById("display-name");
  const password = document.getElementById("password");
  const passwordConfirm = document.getElementById("password-confirm");
  const code = document.getElementById("verification-code");
  const sendButton = document.getElementById("send-code");
  const error = document.getElementById("signup-error");
  const status = document.getElementById("signup-status");
  const submitButton = document.getElementById("signup-submit");

  function showError(message) {
    status.hidden = true;
    error.textContent = message;
    error.hidden = false;
  }

  function showStatus(message) {
    error.hidden = true;
    status.textContent = message;
    status.hidden = false;
  }

  function post(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async function sendCode(event) {
    event.preventDefault();
    if (sendButton.disabled) return;
    sendButton.disabled = true;
    try {
      const response = await post(CODE_URL, { email: email.value });
      if (response.ok) {
        showStatus("인증코드를 보냈습니다. 메일함을 확인해 주세요. (10분 안에 입력)");
      } else {
        showError(CODE_ERRORS[response.status] ?? "인증코드를 보내지 못했습니다.");
      }
    } catch {
      showError("서버에 연결할 수 없습니다. 네트워크를 확인해 주세요.");
    } finally {
      sendButton.disabled = false;
    }
  }

  async function submitSignup(event) {
    event.preventDefault();
    if (submitButton.disabled) return;
    const encoder = new TextEncoder();
    if (encoder.encode(password.value).length > PASSWORD_MAX_BYTES ||
        encoder.encode(passwordConfirm.value).length > PASSWORD_MAX_BYTES) {
      showError(PASSWORD_LENGTH_ERROR);
      return;
    }
    if (!code.value.trim()) {
      showError("이메일로 받은 6자리 인증코드를 입력해 주세요.");
      return;
    }
    if (!passwordConfirm.value || password.value !== passwordConfirm.value) {
      showError("비밀번호가 일치하지 않습니다. 다시 확인해 주세요.");
      return;
    }
    const nickname = displayName.value.trim();
    if (!nickname || [...nickname].length > MAX_DISPLAY_NAME_LENGTH || /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(displayName.value)) {
      showError("닉네임은 줄바꿈 없이 1~50자로 입력해 주세요.");
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = "가입 처리 중…";
    try {
      const response = await post(SIGNUP_URL, {
        email: email.value,
        password: password.value,
        verification_code: code.value.trim(),
        display_name: nickname,
      });
      if (response.ok) {
        navigate("/");
      } else {
        showError(SIGNUP_ERRORS[response.status] ?? "회원가입에 실패했습니다.");
      }
    } catch {
      showError("서버에 연결할 수 없습니다. 네트워크를 확인해 주세요.");
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "회원가입 완료";
    }
  }

  if (sendButton) sendButton.addEventListener("click", sendCode);
  if (form) form.addEventListener("submit", submitSignup);
}

if (typeof window !== "undefined") {
  createSignupController({
    document: window.document,
    fetch: window.fetch.bind(window),
    navigate: (path) => window.location.assign(path),
  });
}
