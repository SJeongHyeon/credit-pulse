"""서버 재시작 뒤 재개를 실제 서비스(HTTP)로 확인한다(리뷰 코멘트 11/16 ③).

가짜 그래프가 아니라 떠 있는 서비스에 로그인해서 본다. 두 단계를 따로 실행하고, 그 사이에 서버를 다시 띄운다.

  1) pause : 질문을 보내 승인 대기에서 멈춘 심사 건을 만든다. 심사 건 id를 상태 파일에 적는다.
  --- 여기서 서버를 다시 띄운다(로컬: docker compose restart api, 배포: Cloud Run 재배포) ---
  2) resume: 새로 뜬 서버에서 그 심사 건이 여전히 승인 대기이고 초안이 그대로인지 보고, 승인해 메모가 저장되는지,
             같은 승인을 다시 보내면 409로 막히는지 본다.

  $env:SERVICE_EVAL_EMAIL = "<계정 이메일>"; $env:SERVICE_EVAL_PASSWORD = "<비밀번호>"   # 저장소·기록에 남기지 않는다
  python -X utf8 scripts/service_restart_resume.py --base-url <주소> --step pause  --state restart_state.json
  python -X utf8 scripts/service_restart_resume.py --base-url <주소> --step resume --state restart_state.json

계정 값은 출력·저장하지 않는다. 상태 파일에는 심사 건 id와 초안 요약만 든다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_service_eval import error_code, http_sender  # noqa: E402

DEFAULT_QUESTION = "오뚜기의 2025년 말 연결 기준 단기차입금은 얼마인가요?"


def draft_summary(draft: list[dict] | None) -> list[dict]:
    return [{"item": d.get("item"), "verdict": d.get("verdict"), "citations": len(d.get("citations") or [])} for d in draft or []]


def login(send, email: str, password: str) -> None:
    status, body = send("POST", "/api/v1/auth/login", {"email": email, "password": password})
    if status != 204:
        raise SystemExit(f"로그인 실패: HTTP {status} {error_code(body) or ''}")


def pause(send, question: str) -> dict:
    status, body = send("POST", "/api/v1/queries", {"question": question})
    body = body if isinstance(body, dict) else {}
    review = body.get("review") or {}
    record = {"step": "pause", "http": status, "outcome": body.get("outcome"), "review_id": review.get("id"),
              "corp_name": review.get("corp_name"), "draft": draft_summary(body.get("draft"))}
    if status != 200 or body.get("outcome") != "awaiting_approval" or not review.get("id"):
        raise SystemExit(json.dumps({**record, "error": error_code(body) or "승인 대기에 이르지 못했습니다"}, ensure_ascii=False))
    detail_status, detail = send("GET", f"/api/v1/reviews/{review['id']}", None)
    record["status_before_restart"] = (detail or {}).get("status") if detail_status == 200 else f"HTTP {detail_status}"
    return record


def resume(send, state: dict) -> dict:
    review_id = state["review_id"]
    status, detail = send("GET", f"/api/v1/reviews/{review_id}", None)
    detail = detail if isinstance(detail, dict) else {}
    pending = detail.get("pending_draft") or []
    record = {"step": "resume", "review_id": review_id, "detail_http": status, "status_after_restart": detail.get("status"),
              "pending_draft": draft_summary(pending), "draft_same_as_before_restart": draft_summary(pending) == state.get("draft")}
    decisions = [{"item": d["item"], "verdict": d["verdict"], "reason": d.get("reason") or ""} for d in pending]
    approve_status, approved = send("POST", f"/api/v1/reviews/{review_id}/approvals", {"decisions": decisions})
    approved = approved if isinstance(approved, dict) else {}
    record.update({"approve_http": approve_status, "memo_id_saved": bool(approved.get("memo_id")),
                   "approval_seq": approved.get("approval_seq"), "approve_error": error_code(approved) if approve_status != 200 else None})
    again_status, again = send("POST", f"/api/v1/reviews/{review_id}/approvals", {"decisions": decisions})
    record.update({"second_approve_http": again_status, "second_approve_error": error_code(again)})
    final_status, final = send("GET", f"/api/v1/reviews/{review_id}", None)
    record["status_after_approval"] = (final or {}).get("status") if final_status == 200 else f"HTTP {final_status}"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--step", choices=("pause", "resume"), required=True)
    parser.add_argument("--state", type=Path, required=True, help="pause가 쓰고 resume이 읽는 상태 파일")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    email, password = os.environ.get("SERVICE_EVAL_EMAIL"), os.environ.get("SERVICE_EVAL_PASSWORD")
    if not email or not password:
        parser.error("환경변수 SERVICE_EVAL_EMAIL, SERVICE_EVAL_PASSWORD가 필요합니다(값은 출력·저장하지 않습니다)")
    send = http_sender(args.base_url, args.timeout)
    login(send, email, password)
    stamp = {"host": urlparse(args.base_url).hostname, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if args.step == "pause":
        record = {**pause(send, args.question), **stamp}
        args.state.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    else:
        record = {**resume(send, json.loads(args.state.read_text(encoding="utf-8"))), **stamp}
    print(json.dumps(record, ensure_ascii=False))
    ok = record.get("outcome") == "awaiting_approval" if args.step == "pause" else (
        record.get("status_after_restart") == "awaiting_approval" and record.get("approve_http") == 200
        and record.get("memo_id_saved") and record.get("second_approve_http") == 409)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
