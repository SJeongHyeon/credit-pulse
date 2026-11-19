"""W2.7 로그인 테스트. DB 없이 가짜 저장소를 의존성 override로 주입한다."""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend import auth
from backend.auth import (
    AuthConfigError,
    PostgresUserRepository,
    UserRecord,
    current_user_id,
    get_user_repository,
    hash_password,
    validate_auth_config,
    verify_password,
)
from backend.main import app
from scripts.seed_users import validate_account

LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"
SIGNUP_URL = "/api/v1/auth/signup"
INVALID_CREDENTIALS_MESSAGE = "email 또는 비밀번호가 맞지 않습니다"

# 값은 실행마다 새로 만들어 저장소에 비밀값이 남지 않게 한다.
USER_EMAIL = "reviewer@example.com"
USER_PASSWORD = f"pw-{secrets.token_hex(16)}"


class FakeUserRepository:
    def __init__(self, users: list[UserRecord]) -> None:
        self._users = users

    def find_by_email(self, email: str) -> UserRecord | None:
        return next((user for user in self._users if user.email == email), None)

    def find_by_id(self, user_id: str) -> UserRecord | None:
        return next((user for user in self._users if user.id == user_id), None)


@pytest.fixture(scope="module")
def seeded_user() -> UserRecord:
    # bcrypt는 일부러 느리므로 모듈에서 한 번만 해시한다.
    return UserRecord(
        id=str(uuid.uuid4()),
        email=USER_EMAIL,
        password_hash=hash_password(USER_PASSWORD),
        display_name="심사역",
        is_demo=True,
    )


@pytest.fixture
def repository(seeded_user):
    return FakeUserRepository([seeded_user])


@pytest.fixture
def client(repository):
    app.dependency_overrides[get_user_repository] = lambda: repository
    # Secure 쿠키는 https에서만 다시 전송되므로 base_url을 https로 둔다.
    # with를 쓰지 않아 lifespan(DB 검사)이 돌지 않는다.
    yield TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    app.dependency_overrides.clear()


def login(client: TestClient, email: str = USER_EMAIL, password: str = USER_PASSWORD):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


@pytest.mark.parametrize("signup_enabled", [None, "true", "0"])
def test_signup_is_disabled_unless_environment_value_is_exactly_one(
    client, monkeypatch, signup_enabled
):
    if signup_enabled is None:
        monkeypatch.delenv("SIGNUP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SIGNUP_ENABLED", signup_enabled)

    response = client.post(
        SIGNUP_URL,
        json={
            "email": "new-reviewer@gmail.com",
            "password": "valid-password",
            "verification_code": "123456",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SIGNUP_DISABLED"


def make_token(user_id: str, key: str | None = None, **claim_overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=1),
        **claim_overrides,
    }
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key or auth._auth_secret(), algorithm="HS256")


def assert_unauthenticated(response) -> None:
    assert response.status_code == 401
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == "AUTH_REQUIRED"


def test_login_sets_session_cookie_with_exact_attributes(client):
    response = login(client)

    assert response.status_code == 204
    assert response.content == b""
    name_value, *attributes = response.headers["set-cookie"].split("; ")
    assert name_value.startswith("session=")
    assert sorted(attributes) == ["HttpOnly", "Max-Age=28800", "Path=/", "SameSite=Lax", "Secure"]


def test_session_token_expires_after_eight_hours(client, seeded_user):
    response = login(client)

    claims = jwt.decode(response.cookies["session"], auth._auth_secret(), algorithms=["HS256"])
    assert claims["sub"] == seeded_user.id
    assert claims["exp"] - claims["iat"] == 28800


def test_login_normalizes_email_case(client):
    assert login(client, email="  Reviewer@EXAMPLE.com ").status_code == 204


def test_me_returns_only_public_fields(client, seeded_user):
    login(client)

    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json() == {
        "id": seeded_user.id,
        "email": USER_EMAIL,
        "display_name": "심사역",
        "is_demo": True,
    }


def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    wrong_password = login(client, password="wrong-password")
    unknown_email = login(client, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    wrong_error = wrong_password.json()["error"]
    unknown_error = unknown_email.json()["error"]
    assert wrong_error["code"] == "AUTH_INVALID_CREDENTIALS"
    assert wrong_error["message"] == INVALID_CREDENTIALS_MESSAGE
    assert wrong_error.pop("request_id") != unknown_error.pop("request_id")
    assert wrong_error == unknown_error
    assert wrong_password.headers.get("set-cookie") is None
    assert unknown_email.headers.get("set-cookie") is None


@pytest.mark.parametrize(
    ("email", "password"),
    [(USER_EMAIL, "wrong-password"), ("nobody@example.com", "any-password")],
)
def test_every_failed_login_compares_hash_exactly_once(client, monkeypatch, email, password):
    calls = []
    real_checkpw = bcrypt.checkpw

    def counting_checkpw(*args):
        calls.append(args)
        return real_checkpw(*args)

    monkeypatch.setattr(auth.bcrypt, "checkpw", counting_checkpw)

    assert login(client, email=email, password=password).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize("password", ["x" * 73, "가" * 100, USER_PASSWORD + "x" * 72])
def test_overlong_password_gets_401_not_500(client, password):
    response = login(client, password=password)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_password_sharing_first_72_bytes_does_not_match():
    password = "a" * 72
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()

    assert verify_password(password, password_hash) is True
    assert verify_password(password + "b", password_hash) is False


def test_lone_surrogate_password_gets_401_not_500(client):
    response = client.post(
        LOGIN_URL,
        content=b'{"email":"reviewer@example.com","password":"\\ud800"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 401


def test_well_formed_token_of_existing_user_is_accepted(client, seeded_user):
    # 아래 거부 테스트의 대조군: 같은 사용자로 정상 토큰을 만들면 통과해야 한다.
    token = make_token(seeded_user.id)

    assert client.get(ME_URL, headers={"cookie": f"session={token}"}).status_code == 200


# 존재하는 사용자 id로 토큰을 만든다. 무작위 id면 서명·만료 검증이 꺼져도
# "없는 사용자"라서 401이 나, 테스트가 검증을 확인하지 못한다.
@pytest.mark.parametrize(
    "token_factory",
    [
        pytest.param(lambda _user_id: None, id="no-cookie"),
        pytest.param(lambda _user_id: "not-a-jwt", id="garbage"),
        pytest.param(
            lambda user_id: make_token(user_id, key=secrets.token_urlsafe(48)),
            id="forged-signature",
        ),
        pytest.param(
            lambda user_id: make_token(
                user_id, exp=datetime.now(timezone.utc) - timedelta(seconds=1)
            ),
            id="expired",
        ),
        pytest.param(lambda user_id: make_token(user_id, exp=None), id="missing-exp"),
        pytest.param(lambda user_id: make_token("not-a-uuid"), id="non-uuid-sub"),
        pytest.param(
            lambda user_id: jwt.encode(
                {
                    "sub": user_id,
                    "iat": datetime.now(timezone.utc),
                    "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                },
                key=None,
                algorithm="none",
            ),
            id="alg-none",
        ),
    ],
)
def test_me_rejects_missing_or_invalid_session(client, seeded_user, token_factory):
    token = token_factory(seeded_user.id)

    headers = {} if token is None else {"cookie": f"session={token}"}
    assert_unauthenticated(client.get(ME_URL, headers=headers))


def test_me_rejects_valid_token_of_deleted_user(client):
    token = make_token(str(uuid.uuid4()))

    assert_unauthenticated(client.get(ME_URL, headers={"cookie": f"session={token}"}))


def test_logout_removes_session(client):
    login(client)
    assert client.get(ME_URL).status_code == 200

    response = client.post(LOGOUT_URL)

    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith('session=""')
    assert "Max-Age=0" in set_cookie
    assert "Secure" in set_cookie
    assert_unauthenticated(client.get(ME_URL))


def test_logout_without_session_is_204(client):
    assert client.post(LOGOUT_URL).status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {"password": USER_PASSWORD},
        {"email": USER_EMAIL},
        {"email": 1, "password": USER_PASSWORD},
        {"email": USER_EMAIL, "password": 12345},
    ],
)
def test_validation_error_uses_error_format_without_echoing_input(client, body):
    response = client.post(LOGIN_URL, json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert set(error) == {"code", "message", "details", "request_id"}
    assert all(set(detail) == {"field", "type"} for detail in error["details"])
    assert USER_PASSWORD not in response.text
    assert "12345" not in response.text


def test_malformed_json_does_not_echo_body(client):
    response = client.post(
        LOGIN_URL,
        content=f'{{"password": "{USER_PASSWORD}"'.encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert USER_PASSWORD not in response.text


def test_password_never_appears_in_responses_or_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    probe_password = f"probe-{secrets.token_hex(16)}"

    responses = [
        login(client, password=probe_password),
        login(client, email="nobody@example.com", password=probe_password),
        client.post(LOGIN_URL, json={"password": probe_password}),
        login(client),
    ]

    for response in responses:
        assert probe_password not in response.text
        assert USER_PASSWORD not in response.text
        assert USER_PASSWORD not in str(response.headers)
    assert probe_password not in caplog.text
    assert USER_PASSWORD not in caplog.text


def test_user_record_repr_hides_password_hash(seeded_user):
    assert seeded_user.password_hash not in repr(seeded_user)


def test_unexpected_error_uses_error_format_without_internal_message(client, repository, monkeypatch):
    def broken_lookup(_email):
        raise RuntimeError("internal connection detail")

    monkeypatch.setattr(repository, "find_by_email", broken_lookup)

    response = login(client)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "internal connection detail" not in response.text


def test_postgres_repository_skips_query_for_email_with_nul(monkeypatch):
    def fail_if_connected(*_args, **_kwargs):
        raise AssertionError("NUL이 든 이메일은 DB로 보내지 않아야 한다")

    monkeypatch.setattr(auth.psycopg, "connect", fail_if_connected)

    assert PostgresUserRepository("postgresql://unused").find_by_email("a\x00@b") is None


@pytest.mark.parametrize("cookie_secure", ["1", "false", ""])
def test_secure_stays_on_unless_exactly_zero(client, monkeypatch, cookie_secure):
    monkeypatch.setenv("COOKIE_SECURE", cookie_secure)

    assert "Secure" in login(client).headers["set-cookie"]


def test_cookie_secure_zero_disables_secure(client, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")

    assert "Secure" not in login(client).headers["set-cookie"]
    assert "Secure" not in client.post(LOGOUT_URL).headers["set-cookie"]


@pytest.mark.parametrize("secret", [None, "", "too-short"])
def test_startup_fails_clearly_without_valid_auth_secret(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("AUTH_SECRET")
    else:
        monkeypatch.setenv("AUTH_SECRET", secret)

    with pytest.raises(AuthConfigError, match="AUTH_SECRET"):
        validate_auth_config()
    with pytest.raises(AuthConfigError, match="AUTH_SECRET"):
        with TestClient(app):
            pass


def test_current_user_id_is_reusable_dependency(client, seeded_user):
    other_app = FastAPI()
    auth.register_error_handlers(other_app)

    @other_app.get("/whoami")
    def whoami(user_id: str = Depends(current_user_id)) -> dict:
        return {"user_id": user_id}

    other_client = TestClient(other_app, base_url="https://testserver")
    token = auth.issue_session_token(seeded_user.id)

    assert other_client.get("/whoami", headers={"cookie": f"session={token}"}).json() == {
        "user_id": seeded_user.id
    }
    assert_unauthenticated(other_client.get("/whoami"))


@pytest.mark.parametrize(
    ("email", "password", "display_name"),
    [
        ("no-at-sign", "password", "이름"),
        ("a@example.com", "", "이름"),
        ("a@example.com", "x" * 73, "이름"),
        ("a@example.com", "password", ""),
        ("a@example.com", "password", "x" * 51),
    ],
)
def test_seed_rejects_invalid_account(email, password, display_name):
    with pytest.raises(ValueError):
        validate_account(email, password, display_name)


def test_seed_lowercases_email_and_hides_password_in_repr():
    account = validate_account("Demo@Example.com", USER_PASSWORD, "데모")

    assert account.email == "demo@example.com"
    assert USER_PASSWORD not in repr(account)


@pytest.mark.parametrize(
    "url, body",
    [
        (SIGNUP_URL, {"email": "new@gmail.com", "password": "valid-password", "verification_code": "123456"}),
        (SIGNUP_URL + "/verification-codes", {"email": "new@gmail.com"}),
    ],
)
def test_disabled_signup_returns_403_before_database_dependency(monkeypatch, url, body):
    monkeypatch.delenv("SIGNUP_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.clear()

    response = TestClient(app, raise_server_exceptions=False).post(url, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SIGNUP_DISABLED"


# ---- Gmail 인증코드 가입 API (docs/specs/gmail-verification-signup.md) ----
from backend.services.email_sender import EmailDeliveryError  # noqa: E402

CODE_URL = SIGNUP_URL + "/verification-codes"
NEW_EMAIL = "new-reviewer@gmail.com"
NEW_PASSWORD = f"pw-{secrets.token_hex(16)}"


class RecordingSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self._error = error

    def send_verification_code(self, to_email: str, code: str) -> None:
        if self._error:
            raise self._error
        self.sent.append((to_email, code))


class FakeVerificationRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def issue(self, email: str, code_hmac: str) -> bool:
        if email in self.rows:
            return False
        self.rows[email] = {"code_hmac": code_hmac, "failed": 0}
        return True

    def consume(self, email: str, code_hmac: str) -> bool:
        row = self.rows.get(email)
        if row and row["failed"] < 5 and row["code_hmac"] == code_hmac:
            del self.rows[email]
            return True
        return False

    def record_failed_attempt(self, email: str) -> bool:
        row = self.rows.get(email)
        if not row:
            return False
        if row["failed"] >= 5:
            return False
        row["failed"] += 1
        return True


class CreatingUserRepository(FakeUserRepository):
    def create_user(self, email, password_hash, display_name):
        if self.find_by_email(email):
            raise auth.psycopg.errors.UniqueViolation()
        user = UserRecord(str(uuid.uuid4()), email, password_hash, display_name, False)
        self._users.append(user)
        return user


@pytest.fixture
def signup_env(monkeypatch, seeded_user):
    monkeypatch.setenv("SIGNUP_ENABLED", "1")
    users = CreatingUserRepository([seeded_user])
    verifications = FakeVerificationRepository()
    sender = RecordingSender()
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[auth.get_verification_repository] = lambda: verifications
    app.dependency_overrides[auth.get_email_sender] = lambda: sender
    yield users, verifications, sender
    app.dependency_overrides.clear()


def signup_client():
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def request_code(email=NEW_EMAIL):
    return signup_client().post(CODE_URL, json={"email": email})


def submit_signup(code, email=NEW_EMAIL, password=NEW_PASSWORD, **fields):
    return signup_client().post(
        SIGNUP_URL, json={"email": email, "password": password, "verification_code": code, **fields}
    )


def test_code_request_for_existing_account_returns_202_without_sending(signup_env):
    users, verifications, sender = signup_env
    users.create_user("taken@gmail.com", "hash", "taken")

    response = request_code("Taken@Gmail.com")

    assert response.status_code == 202
    assert sender.sent == []
    assert verifications.rows == {}


def test_code_request_for_new_address_sends_one_six_digit_code_and_stores_only_hmac(signup_env):
    _users, verifications, sender = signup_env

    assert request_code().status_code == 202

    assert len(sender.sent) == 1
    to_email, code = sender.sent[0]
    assert to_email == NEW_EMAIL
    assert len(code) == 6 and code.isdecimal()
    assert verifications.rows[NEW_EMAIL]["code_hmac"] == auth.signup_code_hmac(NEW_EMAIL, code)
    assert code not in str(verifications.rows)


def test_signup_with_valid_code_creates_non_demo_user_once(signup_env):
    users, _verifications, sender = signup_env
    request_code()
    code = sender.sent[0][1]

    first = submit_signup(code)
    second = submit_signup(code)

    assert first.status_code == 201
    body = first.json()
    assert body["email"] == NEW_EMAIL and body["is_demo"] is False
    assert body["display_name"] == NEW_EMAIL.split("@", 1)[0]
    assert set(body) == {"id", "email", "display_name", "is_demo"}
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "VALIDATION_EMAIL_VERIFICATION"
    assert len([u for u in users._users if u.email == NEW_EMAIL]) == 1
    assert first.headers.get("set-cookie") is None


def test_signup_with_wrong_code_is_400_and_creates_no_user(signup_env):
    users, verifications, sender = signup_env
    request_code()
    wrong = "000000" if sender.sent[0][1] != "000000" else "111111"

    response = submit_signup(wrong)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_EMAIL_VERIFICATION"
    assert users.find_by_email(NEW_EMAIL) is None
    assert verifications.rows[NEW_EMAIL]["failed"] == 1
    assert response.headers.get("set-cookie") is None


def test_signup_for_existing_email_returns_409(signup_env, seeded_user):
    _users, verifications, _sender = signup_env
    verifications.issue(USER_EMAIL.replace("example.com", "gmail.com"), auth.signup_code_hmac(
        USER_EMAIL.replace("example.com", "gmail.com"), "123456"))
    email = USER_EMAIL.replace("example.com", "gmail.com")
    _users.create_user(email, "hash", "dup")

    response = submit_signup("123456", email=email)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_EMAIL_EXISTS"


def test_resend_within_cooldown_is_429_and_sends_nothing_more(signup_env):
    _users, _verifications, sender = signup_env
    assert request_code().status_code == 202

    response = request_code()

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert len(sender.sent) == 1


@pytest.mark.parametrize("email", ["a@example.com", "", "x" * 250 + "@gmail.com"])
def test_code_request_rejects_non_gmail_or_overlong_address(signup_env, email):
    _users, verifications, sender = signup_env

    response = request_code(email)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert sender.sent == [] and verifications.rows == {}


def test_missing_smtp_configuration_is_503_without_leaking(signup_env, monkeypatch):
    for name in ("SMTP_USERNAME", "SMTP_APP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(name, raising=False)
    app.dependency_overrides.pop(auth.get_email_sender)

    response = request_code()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_EMAIL"


def test_smtp_failure_is_503_and_secrets_stay_out_of_response_and_logs(signup_env, caplog):
    _users, _verifications, _sender = signup_env
    app_password = f"app-{secrets.token_hex(8)}"
    failing = RecordingSender(error=EmailDeliveryError(f"auth failed {app_password}"))
    app.dependency_overrides[auth.get_email_sender] = lambda: failing
    caplog.set_level(logging.DEBUG)

    response = request_code()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_EMAIL"
    assert app_password not in response.text
    assert app_password not in caplog.text


def test_password_and_code_never_appear_in_signup_responses_or_logs(signup_env, caplog):
    _users, _verifications, sender = signup_env
    caplog.set_level(logging.DEBUG)
    request_code()
    code = sender.sent[0][1]

    responses = [submit_signup("999999" if code != "999999" else "888888"), submit_signup(code)]

    for response in responses:
        assert NEW_PASSWORD not in response.text
        assert code not in response.text
    assert NEW_PASSWORD not in caplog.text
    assert code not in caplog.text


@pytest.mark.parametrize(
    "url, body",
    [
        (CODE_URL, {"email": "new@gmail.com"}),
        (SIGNUP_URL, {"email": "new@gmail.com", "password": "valid-password", "verification_code": "123456"}),
        (LOGIN_URL, {"email": "new@gmail.com", "password": "valid-password"}),
    ],
)
def test_missing_database_url_is_503_service_unavailable_db(monkeypatch, url, body):
    monkeypatch.setenv("SIGNUP_ENABLED", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app.dependency_overrides.clear()

    response = signup_client().post(url, json=body)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE_DB"


def test_five_wrong_codes_invalidate_the_code_and_keep_the_resend_cooldown(signup_env):
    users, verifications, sender = signup_env
    request_code()
    right = sender.sent[0][1]
    wrong = "000000" if right != "000000" else "111111"

    for _ in range(5):
        assert submit_signup(wrong).status_code == 400
    after_lockout = submit_signup(right)
    resend = request_code()

    assert after_lockout.status_code == 400
    assert after_lockout.json()["error"]["code"] == "VALIDATION_EMAIL_VERIFICATION"
    assert users.find_by_email(NEW_EMAIL) is None
    assert NEW_EMAIL in verifications.rows  # 행이 남아 있어야 쿨다운이 유지된다
    assert resend.status_code == 429
    assert len(sender.sent) == 1


def test_account_created_by_signup_can_log_in_and_is_not_demo(signup_env):
    _users, _verifications, sender = signup_env
    request_code()
    signup_response = submit_signup(sender.sent[0][1], display_name="  승민  ")
    assert signup_response.status_code == 201
    assert signup_response.json()["display_name"] == "승민"
    browser = signup_client()

    login_response = browser.post(LOGIN_URL, json={"email": "  New-Reviewer@GMAIL.com ", "password": NEW_PASSWORD})
    me_response = browser.get(ME_URL)

    assert login_response.status_code == 204
    assert login_response.headers["set-cookie"].startswith("session=")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == NEW_EMAIL
    assert me_response.json()["display_name"] == "승민"
    assert me_response.json()["is_demo"] is False


@pytest.mark.parametrize("nickname", [None, "  ", "가" * 51, "앞\x00뒤", "앞\u2028뒤"])
def test_signup_rejects_invalid_display_name_without_using_the_code(signup_env, nickname):
    users, verifications, sender = signup_env
    request_code()
    code = sender.sent[0][1]

    response = submit_signup(code, display_name=nickname)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert users.find_by_email(NEW_EMAIL) is None
    assert verifications.rows[NEW_EMAIL]["failed"] == 0
    assert submit_signup(code, display_name="가" * 50).status_code == 201


def test_account_created_by_signup_rejects_a_wrong_password(signup_env):
    _users, _verifications, sender = signup_env
    request_code()
    submit_signup(sender.sent[0][1])

    response = signup_client().post(LOGIN_URL, json={"email": NEW_EMAIL, "password": NEW_PASSWORD + "x"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


@pytest.mark.parametrize("password", ["x" * 73, "가" * 25, "가" * 24 + "ab"])
def test_signup_rejects_password_over_72_bytes_without_using_the_code(signup_env, password):
    users, verifications, sender = signup_env
    request_code()
    code = sender.sent[0][1]

    response = submit_signup(code, password=password)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert users.find_by_email(NEW_EMAIL) is None
    assert verifications.rows[NEW_EMAIL]["failed"] == 0  # 코드를 소비하거나 실패로 세지 않는다
    assert submit_signup(code).status_code == 201  # 같은 코드로 올바른 비밀번호를 다시 보낼 수 있다
    assert password not in response.text


@pytest.mark.parametrize("password", ["x" * 72, "가" * 24])
def test_signup_accepts_password_of_exactly_72_bytes(signup_env, password):
    users, _verifications, sender = signup_env
    request_code()

    response = submit_signup(sender.sent[0][1], password=password)

    assert response.status_code == 201
    assert users.find_by_email(NEW_EMAIL) is not None
