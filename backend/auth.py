"""로그인과 사용자 식별 (G4). Spec: docs/specs/w2.7-auth-login.md

평가자는 조가 비공개로 제출한 평가용 계정 2개로 로그인해, 두 사용자의 대화(thread)와
장기 기억(Store namespace)이 섞이지 않는지 봅니다. 계정 정보를 저장소에 적지 않습니다.

다른 라우터는 `Depends(current_user_id)`로 로그인한 사용자 id를 얻습니다.
"""
import logging
import os
import uuid
import hmac
import hashlib
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated, Any, Protocol

import bcrypt
import jwt
import psycopg
from fastapi import APIRouter, Cookie, Depends, Response
from psycopg.rows import dict_row
from pydantic import BaseModel

from backend.errors import ApiError, register_error_handlers
from backend.services.email_sender import EmailDeliveryError, GmailSmtpSender

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
SESSION_TTL_SECONDS = 28800
JWT_ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12
BCRYPT_MAX_PASSWORD_BYTES = 72
VERIFICATION_CODE_LENGTH = 6
MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 50
VERIFICATION_MAX_FAILED_ATTEMPTS = 5
MIN_AUTH_SECRET_BYTES = 32  # RFC 7518 §3.2: HS256 키는 해시 출력(256비트) 이상


class AuthConfigError(RuntimeError):
    """인증 설정이 잘못돼 앱을 띄울 수 없다."""


def _invalid_credentials() -> ApiError:
    # 틀린 비밀번호와 없는 계정이 같은 오류를 쓰도록 한 곳에서 만든다(AC-019).
    return ApiError(401, "AUTH_INVALID_CREDENTIALS", "email 또는 비밀번호가 맞지 않습니다")


def _unauthenticated() -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다")


def _auth_secret() -> str:
    secret = os.getenv("AUTH_SECRET", "")
    if not secret:
        raise AuthConfigError("AUTH_SECRET 환경변수가 없습니다. .env.example의 설명을 보고 설정하세요")
    if len(secret.encode("utf-8")) < MIN_AUTH_SECRET_BYTES:
        raise AuthConfigError(f"AUTH_SECRET은 {MIN_AUTH_SECRET_BYTES}바이트 이상이어야 합니다")
    return secret


def validate_auth_config() -> None:
    """기동 시 호출한다. 첫 로그인이 더미 해시를 만드느라 느려지지 않게 미리 만들어 둔다."""

    _auth_secret()
    _dummy_password_hash()


def _cookie_secure() -> bool:
    # 정확히 "0"일 때만 끈다. "false" 같은 오타는 안전한 쪽(켜짐)으로 남는다.
    return os.getenv("COOKIE_SECURE", "1").strip() != "0"


def _password_bytes(password: str) -> bytes:
    # JSON의 "\ud800" 같은 짝 없는 surrogate도 str로 들어오므로 500이 나지 않게 처리한다.
    return password.encode("utf-8", "surrogatepass")


def is_password_length_supported(password: str) -> bool:
    return len(_password_bytes(password)) <= BCRYPT_MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    if not is_password_length_supported(password):
        raise ValueError(f"비밀번호는 {BCRYPT_MAX_PASSWORD_BYTES}바이트 이하여야 합니다")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_password_bytes(password), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    # bcrypt 5는 72바이트 초과 입력에 ValueError를 낸다. 응답 시간을 맞추려 잘라서 비교는
    # 수행하되, 앞 72바이트만 같은 긴 비밀번호가 통과하지 않도록 길이 조건을 함께 건다.
    encoded = _password_bytes(password)
    matched = bcrypt.checkpw(encoded[:BCRYPT_MAX_PASSWORD_BYTES], password_hash.encode("ascii"))
    return matched and is_password_length_supported(password)


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    # 계정이 없을 때 비교할 해시. 같은 cost로 만들어야 비교 시간이 비슷해진다.
    return hash_password(uuid.uuid4().hex)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_signup_gmail_address(email: str) -> bool:
    normalized = normalize_email(email)
    local, separator, domain = normalized.rpartition("@")
    return (
        len(normalized) <= MAX_EMAIL_LENGTH
        and bool(local and separator and domain in {"gmail.com", "googlemail.com"})
    )


def is_verification_code_format_valid(code: str) -> bool:
    return len(code) == VERIFICATION_CODE_LENGTH and code.isascii() and code.isdecimal()


def signup_code_hmac(email: str, code: str) -> str:
    value = f"{normalize_email(email)}:{code}".encode("utf-8")
    return hmac.new(_auth_secret().encode("utf-8"), value, hashlib.sha256).hexdigest()


def verify_signup_code(email: str, code: str, stored_hmac: str) -> bool:
    if not is_verification_code_format_valid(code):
        return False
    return hmac.compare_digest(signup_code_hmac(email, code), stored_hmac)


@dataclass(frozen=True)
class SignupVerification:
    email: str
    code_hmac: str
    expires_at: datetime
    failed_attempts: int
    consumed: bool


def can_consume_signup_verification(
    record: SignupVerification, email: str, code: str, now: datetime
) -> bool:
    return (
        not record.consumed
        and record.failed_attempts < VERIFICATION_MAX_FAILED_ATTEMPTS
        and now < record.expires_at
        and normalize_email(email) == record.email
        and verify_signup_code(email, code, record.code_hmac)
    )


def issue_session_token(user_id: str) -> str:
    issued_at = datetime.now(timezone.utc)
    claims = {
        "sub": user_id,
        "iat": issued_at,
        "exp": issued_at + timedelta(seconds=SESSION_TTL_SECONDS),
    }
    return jwt.encode(claims, _auth_secret(), algorithm=JWT_ALGORITHM)


def current_user_id(session: Annotated[str | None, Cookie()] = None) -> str:
    """세션 쿠키를 검증해 사용자 id를 돌려주는 FastAPI 의존성.

    매개변수 이름 `session`이 곧 쿠키 이름이다(SESSION_COOKIE_NAME).
    DB를 조회하지 않으므로 삭제된 사용자의 토큰도 만료 전까지 통과한다.
    """

    if session is None:
        raise _unauthenticated()
    try:
        # algorithms를 고정해 토큰이 스스로 고른 alg(none 등)를 믿지 않는다.
        claims = jwt.decode(
            session,
            _auth_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
        return str(uuid.UUID(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise _unauthenticated() from exc


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    password_hash: str = field(repr=False)  # 로그에 찍히지 않도록 repr에서 뺀다
    display_name: str
    is_demo: bool


class UserRepository(Protocol):
    """사용자 조회 인터페이스. 테스트는 가짜 구현을 의존성 override로 주입한다."""

    def find_by_email(self, email: str) -> UserRecord | None: ...

    def find_by_id(self, user_id: str) -> UserRecord | None: ...

    def create_user(self, email: str, password_hash: str, display_name: str) -> UserRecord: ...


class PostgresUserRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _fetch_one(self, query: str, value: str) -> UserRecord | None:
        # prepare_threshold=None: Supabase transaction pooler 호환(SPEC §5.2)
        with psycopg.connect(
            self._database_url, prepare_threshold=None, row_factory=dict_row
        ) as connection:
            row = connection.execute(query, (value,)).fetchone()
        if row is None:
            return None
        return UserRecord(
            id=str(row["id"]),
            email=row["email"],
            password_hash=row["password_hash"],
            display_name=row["display_name"],
            is_demo=row["is_demo"],
        )

    def find_by_email(self, email: str) -> UserRecord | None:
        if "\x00" in email:  # PostgreSQL 문자열에 NUL을 넣을 수 없어 psycopg가 오류를 낸다
            return None
        return self._fetch_one(
            "SELECT id, email, password_hash, display_name, is_demo FROM users WHERE email = %s",
            email,
        )

    def find_by_id(self, user_id: str) -> UserRecord | None:
        return self._fetch_one(
            "SELECT id, email, password_hash, display_name, is_demo FROM users WHERE id = %s",
            user_id,
        )

    def create_user(self, email: str, password_hash: str, display_name: str) -> UserRecord:
        with psycopg.connect(self._database_url, prepare_threshold=None, row_factory=dict_row) as connection:
            row = connection.execute(
                """INSERT INTO users (email, password_hash, display_name, is_demo)
                   VALUES (%s, %s, %s, false)
                   RETURNING id, email, password_hash, display_name, is_demo""",
                (email, password_hash, display_name),
            ).fetchone()
        return UserRecord(id=str(row["id"]), email=row["email"], password_hash=row["password_hash"], display_name=row["display_name"], is_demo=row["is_demo"])


class PostgresVerificationRepository:
    """인증코드 원문 없이 HMAC과 발송 시각만 보관한다."""

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def issue(self, email: str, code_hmac: str) -> bool:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO signup_verifications (email, code_hmac, expires_at, sent_at, failed_attempts)
                    VALUES (%s, %s, now() + interval '10 minutes', now(), 0)
                    ON CONFLICT (email) DO UPDATE SET
                      code_hmac = EXCLUDED.code_hmac,
                      expires_at = EXCLUDED.expires_at,
                      sent_at = EXCLUDED.sent_at,
                      failed_attempts = 0
                    WHERE signup_verifications.sent_at <= now() - interval '60 seconds'
                    RETURNING email
                    """,
                    (normalize_email(email), code_hmac),
                )
                return cursor.fetchone() is not None

    def consume(self, email: str, code_hmac: str) -> bool:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM signup_verifications
                    WHERE email = %s
                      AND code_hmac = %s
                      AND expires_at > now()
                      AND failed_attempts < 5
                    RETURNING email
                    """,
                    (normalize_email(email), code_hmac),
                )
                return cursor.fetchone() is not None

    def record_failed_attempt(self, email: str) -> bool:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE signup_verifications
                    SET failed_attempts = failed_attempts + 1
                    WHERE email = %s AND expires_at > now() AND failed_attempts < 5
                    RETURNING email
                    """,
                    (normalize_email(email),),
                )
                # 5회에 닿으면 failed_attempts < 5 조건이 consume을 막아 코드가 무효화된다(EC-06).
                # 행은 지우지 않는다: sent_at이 남아 60초 재발송 제한이 유지된다.
                return cursor.fetchone() is not None


def get_user_repository() -> UserRepository:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise ApiError(503, "SERVICE_UNAVAILABLE_DB", "데이터베이스를 사용할 수 없습니다")
    return PostgresUserRepository(database_url)


def get_verification_repository() -> PostgresVerificationRepository:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise ApiError(503, "SERVICE_UNAVAILABLE_DB", "데이터베이스를 사용할 수 없습니다")
    return PostgresVerificationRepository(lambda: psycopg.connect(database_url, prepare_threshold=None))


class LoginRequest(BaseModel):
    # 길이·형식은 일부러 검사하지 않는다. 400으로 갈라지지 않고 모두 같은 401 흐름을 탄다.
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    verification_code: str
    display_name: str | None = None


class VerificationCodeRequest(BaseModel):
    email: str


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_demo: bool


def get_email_sender() -> GmailSmtpSender:
    try:
        return GmailSmtpSender.from_environment()
    except EmailDeliveryError as exc:
        raise ApiError(503, "SERVICE_UNAVAILABLE_EMAIL", "인증 메일 서비스를 사용할 수 없습니다") from exc


Users = Annotated[UserRepository, Depends(get_user_repository)]
Senders = Annotated[GmailSmtpSender, Depends(get_email_sender)]
Verifications = Annotated[PostgresVerificationRepository, Depends(get_verification_repository)]

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def signup_enabled() -> bool:
    return os.getenv("SIGNUP_ENABLED", "").strip() == "1"


def require_signup_enabled() -> None:
    # 라우트 dependencies로 걸어 DB·SMTP 의존성보다 먼저 평가되게 한다(EC-01).
    if not signup_enabled():
        raise ApiError(403, "SIGNUP_DISABLED", "회원가입이 비활성화되어 있습니다")


@auth_router.post("/signup", status_code=201, dependencies=[Depends(require_signup_enabled)])
def signup(request: SignupRequest, users: Users, verifications: Verifications) -> CurrentUserResponse:
    if not signup_enabled():
        raise ApiError(403, "SIGNUP_DISABLED", "회원가입이 비활성화되어 있습니다")
    email = normalize_email(request.email)
    if not is_signup_gmail_address(email) or not is_password_length_supported(request.password):
        raise ApiError(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다")
    if "display_name" in request.model_fields_set:
        raw_name = request.display_name or ""
        display_name = raw_name.strip()
        if not 1 <= len(display_name) <= MAX_DISPLAY_NAME_LENGTH or any(
            unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in raw_name
        ):
            raise ApiError(400, "VALIDATION_ERROR", "닉네임은 줄바꿈 없이 1~50자로 입력해 주세요")
    else:
        display_name = email.split("@", 1)[0][:MAX_DISPLAY_NAME_LENGTH]
    if not verifications.consume(email, signup_code_hmac(email, request.verification_code)):
        verifications.record_failed_attempt(email)
        raise ApiError(400, "VALIDATION_EMAIL_VERIFICATION", "인증코드가 올바르지 않습니다")
    try:
        user = users.create_user(email, hash_password(request.password), display_name)
    except psycopg.errors.UniqueViolation as exc:
        raise ApiError(409, "CONFLICT_EMAIL_EXISTS", "이미 사용 중인 이메일입니다") from exc
    return CurrentUserResponse(id=user.id, email=user.email, display_name=user.display_name, is_demo=user.is_demo)


@auth_router.post(
    "/signup/verification-codes", status_code=202, dependencies=[Depends(require_signup_enabled)]
)
def request_signup_verification_code(
    request: VerificationCodeRequest,
    users: Users,
    verifications: Verifications,
    sender: Senders,
) -> None:
    email = normalize_email(request.email)
    if not is_signup_gmail_address(email):
        raise ApiError(400, "VALIDATION_ERROR", "요청 형식이 올바르지 않습니다")
    if users.find_by_email(email) is not None:
        return  # EC-08: 계정 존재 여부를 숨기려고 같은 202를 주되 발송하지 않는다
    code = f"{secrets.randbelow(1_000_000):06d}"
    if not verifications.issue(email, signup_code_hmac(email, code)):
        raise ApiError(429, "RATE_LIMITED", "잠시 후 다시 시도하세요")
    try:
        sender.send_verification_code(email, code)
    except EmailDeliveryError as exc:
        raise ApiError(503, "SERVICE_UNAVAILABLE_EMAIL", "인증 메일 서비스를 사용할 수 없습니다") from exc


# async가 아닌 def: bcrypt와 psycopg가 블로킹이라 FastAPI가 스레드풀에서 돌리게 한다.
@auth_router.post("/login", status_code=204)
def login(credentials: LoginRequest, response: Response, users: Users) -> None:
    user = users.find_by_email(normalize_email(credentials.email))
    stored_hash = user.password_hash if user else _dummy_password_hash()
    password_matched = verify_password(credentials.password, stored_hash)
    if user is None or not password_matched:
        logger.info("로그인 실패")  # 이메일(개인정보)과 비밀번호는 남기지 않는다
        raise _invalid_credentials()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issue_session_token(user.id),
        max_age=SESSION_TTL_SECONDS,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="Lax",
    )


@auth_router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    # 브라우저는 발급 때와 같은 속성이어야 쿠키를 지운다.
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="Lax",
    )


@auth_router.get("/me")
def me(
    user_id: Annotated[str, Depends(current_user_id)],
    users: Users,
) -> CurrentUserResponse:
    user = users.find_by_id(user_id)
    if user is None:
        raise _unauthenticated()
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_demo=user.is_demo,
    )
