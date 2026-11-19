"""데모 계정 2개를 users 테이블에 넣는다. 같은 이메일로 다시 실행하면 값을 갱신한다.

실행: python scripts/seed_users.py
계정 값은 SEED_USER{1,2}_EMAIL / _PASSWORD / _DISPLAY_NAME 환경변수(.env 포함)에서 읽고,
없으면 실행 중에 입력받는다. 계정 정보를 저장소에 적지 않는다.
"""
import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# `python scripts/seed_users.py`로 실행해도 backend를 찾도록 저장소 루트를 경로에 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.auth import hash_password, is_password_length_supported, normalize_email  # noqa: E402

MAX_EMAIL_LENGTH = 254
MAX_DISPLAY_NAME_LENGTH = 50

UPSERT_USER_SQL = """
    INSERT INTO users (email, password_hash, display_name, is_demo)
    VALUES (%s, %s, %s, true)
    ON CONFLICT (email) DO UPDATE SET
        password_hash = EXCLUDED.password_hash,
        display_name = EXCLUDED.display_name,
        is_demo = true
"""


@dataclass(frozen=True)
class SeedAccount:
    email: str
    password: str = field(repr=False)
    display_name: str


def validate_account(email: str, password: str, display_name: str) -> SeedAccount:
    normalized_email = normalize_email(email)
    if "@" not in normalized_email or len(normalized_email) > MAX_EMAIL_LENGTH:
        raise ValueError(f"이메일은 @를 포함하고 {MAX_EMAIL_LENGTH}자 이하여야 합니다")
    if not password:
        raise ValueError("비밀번호가 비어 있습니다")
    if not is_password_length_supported(password):
        raise ValueError("비밀번호는 72바이트 이하여야 합니다")
    if not display_name or len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(f"표시 이름은 1~{MAX_DISPLAY_NAME_LENGTH}자여야 합니다")
    return SeedAccount(normalized_email, password, display_name)


def read_account(number: int) -> SeedAccount:
    prefix = f"SEED_USER{number}"
    email = os.getenv(f"{prefix}_EMAIL") or input(f"계정 {number} 이메일: ")
    password = os.getenv(f"{prefix}_PASSWORD") or getpass.getpass(f"계정 {number} 비밀번호: ")
    display_name = os.getenv(f"{prefix}_DISPLAY_NAME") or input(f"계정 {number} 표시 이름: ")
    return validate_account(email, password, display_name)


def seed_users(database_url: str, accounts: list[SeedAccount]) -> None:
    # with 블록이 정상 종료될 때 commit하므로 두 계정이 함께 들어가거나 함께 실패한다.
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        for account in accounts:
            connection.execute(
                UPSERT_USER_SQL,
                (account.email, hash_password(account.password), account.display_name),
            )


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL이 필요합니다")

    try:
        accounts = [read_account(1), read_account(2)]
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if accounts[0].email == accounts[1].email:
        raise SystemExit("두 계정의 이메일이 같습니다")

    seed_users(database_url, accounts)
    print("데모 계정 2개를 저장했습니다")


if __name__ == "__main__":
    main()
