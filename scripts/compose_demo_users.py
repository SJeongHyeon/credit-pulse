"""compose 데모용 계정 2개를 처음 한 번만 만든다(G2: 키 없이 clone → `docker compose up`).

비밀번호는 저장소에 적지 않는다(AGENTS.md 4번). 처음 만들 때 무작위로 정해 이 실행의 로그에만 한 번 출력한다.
  docker compose logs loader   # 계정 확인
이미 계정이 있으면 건드리지 않는다(재시작해도 비밀번호가 바뀌지 않음). 운영 계정은 scripts/seed_users.py로 만든다.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from backend.auth import hash_password  # noqa: E402

DEMO_ACCOUNTS = (("demo1@creditpulse.local", "데모 심사역 1"), ("demo2@creditpulse.local", "데모 심사역 2"))


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL이 필요합니다", file=sys.stderr)
        return 2
    created = []
    with psycopg.connect(database_url, autocommit=True) as connection:
        for email, display_name in DEMO_ACCOUNTS:
            exists = connection.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone()
            if exists:
                continue
            password = secrets.token_urlsafe(12)
            connection.execute(
                "INSERT INTO users (email, password_hash, display_name, is_demo) VALUES (%s, %s, %s, true)",
                (email, hash_password(password), display_name),
            )
            created.append((email, password))
    if created:
        print("[demo] 데모 계정을 만들었습니다. 이 비밀번호는 지금 한 번만 보입니다:")
        for email, password in created:
            print(f"[demo]   {email} / {password}")
    else:
        print("[demo] 데모 계정이 이미 있습니다(비밀번호는 처음 실행 로그에 있습니다. 다시 만들려면 docker compose down -v)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
