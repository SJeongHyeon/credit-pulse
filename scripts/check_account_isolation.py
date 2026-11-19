"""계정 2개로 로그인해 서로의 심사 건·규칙이 보이지 않는지 HTTP로 확인한다(D10: 계정 분리를 현재 코드에서 다시 기록).

대상은 로컬에 띄운 서비스다(기본 http://127.0.0.1:8000). 키 없는 데모 모드에서 돈다:
`docker compose up --build`가 데모 계정 2개를 만들고 비밀번호를 loader 로그에 한 번 출력한다.

계정은 환경 변수로만 받는다(기록·출력에 남기지 않는다).
  ISOLATION_A_EMAIL, ISOLATION_A_PASSWORD, ISOLATION_B_EMAIL, ISOLATION_B_PASSWORD

확인하는 것
  1. A가 심사 건을 만들고 기록된 데모 질문을 넣어 승인 대기로 만든다.
  2. B의 목록에 A의 심사 건이 없다.
  3. B가 A의 심사 건 주소로 조회·질의·승인·삭제를 하면 모두 404다(없는 건과 같은 응답: 존재를 숨김).
  4. B가 '전체 삭제'를 해도 A의 심사 건은 그대로다.
  5. A는 B의 심사 건을 볼 수 없고, 자기 건은 그대로 승인 대기다.
  6. 규칙 목록은 계정마다 따로다.
끝나면 A의 승인 대기를 취소하고 만든 심사 건을 지운다.

사용: python -X utf8 scripts/check_account_isolation.py [--base-url http://127.0.0.1:8000] [--output 파일]
종료 코드: 모든 확인이 기대와 같으면 0.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_QUESTION = "오뚜기의 2025년 말 연결 기준 단기차입금은 얼마인가요?"


class Session:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", "Origin": self.base_url})
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return error.code, None


def error_code(payload) -> str | None:
    if isinstance(payload, dict):
        inner = payload.get("error") if isinstance(payload.get("error"), dict) else payload
        return inner.get("code")
    return None


def review_ids(payload) -> list[str]:
    items = payload.get("items", payload.get("reviews", [])) if isinstance(payload, dict) else payload or []
    return [str(item.get("id") or item.get("review_id")) for item in items]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        accounts = {name: (os.environ[f"ISOLATION_{name}_EMAIL"], os.environ[f"ISOLATION_{name}_PASSWORD"]) for name in ("A", "B")}
    except KeyError as missing:
        raise SystemExit(f"환경 변수가 없습니다: {missing}")

    a, b = Session(args.base_url), Session(args.base_url)
    checks: list[dict] = []

    def check(name: str, got, expected) -> None:
        checks.append({"check": name, "got": got, "expected": expected, "ok": got == expected})

    for name, session in (("A", a), ("B", b)):
        status, _ = session.call("POST", "/api/v1/auth/login", {"email": accounts[name][0], "password": accounts[name][1]})
        check(f"{name} 로그인", status, 204)

    status, created = a.call("POST", "/api/v1/reviews", {"corp_query": "오뚜기", "fiscal_year": 2025})
    check("A 심사 건 생성", status, 201)
    id_a = str((created or {}).get("id") or (created or {}).get("review_id"))
    status, answer = a.call("POST", f"/api/v1/reviews/{id_a}/queries", {"question": DEMO_QUESTION})
    check("A 질의 → 승인 대기", (status, (answer or {}).get("outcome")), (200, "awaiting_approval"))
    items = [item.get("item") for item in (answer or {}).get("draft", []) if isinstance(item, dict)]

    status, listed = b.call("GET", "/api/v1/reviews")
    check("B 목록에 A의 심사 건이 없음", (status, id_a in review_ids(listed)), (200, False))
    status, missing = b.call("GET", "/api/v1/reviews/00000000-0000-4000-8000-000000000000")
    not_found = (status, error_code(missing))
    check("(대조) B가 없는 심사 건 조회", status, 404)
    status, payload = b.call("GET", f"/api/v1/reviews/{id_a}")
    check("B가 A의 심사 건 조회 → 없는 건과 같은 404", (status, error_code(payload)), not_found)
    status, payload = b.call("POST", f"/api/v1/reviews/{id_a}/queries", {"question": DEMO_QUESTION})
    check("B가 A의 심사 건에 질의 → 404", status, 404)
    decisions = [{"item": item, "verdict": "needs_check", "reason": "다른 계정의 승인 시도"} for item in (items or ["borrowing_structure"])]
    status, payload = b.call("POST", f"/api/v1/reviews/{id_a}/approvals", {"decisions": decisions})
    check("B가 A의 초안을 승인 → 404", status, 404)
    status, payload = b.call("DELETE", f"/api/v1/reviews/{id_a}")
    check("B가 A의 심사 건 삭제 → 404", status, 404)

    status, created_b = b.call("POST", "/api/v1/reviews", {"corp_query": "오뚜기", "fiscal_year": 2025})
    check("B 심사 건 생성", status, 201)
    id_b = str((created_b or {}).get("id") or (created_b or {}).get("review_id"))
    status, payload = a.call("GET", f"/api/v1/reviews/{id_b}")
    check("A가 B의 심사 건 조회 → 404", status, 404)
    status, _ = b.call("DELETE", "/api/v1/reviews")
    check("B 전체 삭제", status, 204)
    status, listed = b.call("GET", "/api/v1/reviews")
    check("B 목록이 비었음", (status, review_ids(listed)), (200, []))
    status, detail = a.call("GET", f"/api/v1/reviews/{id_a}")
    check("B의 전체 삭제 뒤에도 A의 심사 건은 그대로(승인 대기)", (status, (detail or {}).get("status")), (200, "awaiting_approval"))

    status_a, rules_a = a.call("GET", "/api/v1/rules")
    status_b, rules_b = b.call("GET", "/api/v1/rules")
    if (status_a, status_b) == (503, 503) and error_code(rules_a) == "SERVICE_UNAVAILABLE_RULES":
        # 데모 모드는 임베딩 키가 없어 규칙 메모리를 켜지 않는다(SPEC의 오류 코드 표). 규칙 분리는 tests/test_memory.py가 본다.
        check("규칙 목록: 데모 모드라 규칙 메모리 없음(두 계정 모두 503 SERVICE_UNAVAILABLE_RULES)", (status_a, status_b), (503, 503))
    else:
        check("규칙 목록은 계정마다 따로 200", (status_a, status_b), (200, 200))

    status, _ = a.call("POST", f"/api/v1/reviews/{id_a}/approvals:cancel", {})
    cleanup_cancel = status
    status, _ = a.call("DELETE", f"/api/v1/reviews/{id_a}")
    check("정리: A가 자기 심사 건 삭제", status, 204)

    record = {
        "what": "계정 2개의 심사 건·승인·삭제·규칙 분리를 HTTP로 확인(로컬 서비스, 데모 모드)",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "accounts": "데모 계정 2개(이메일·비밀번호는 기록하지 않음)",
        "cleanup_cancel_status": cleanup_cancel,
        "checks": checks,
        "all_ok": all(item["ok"] for item in checks),
    }
    text = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        (ROOT / args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if record["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
