"""로그인 사용자의 심사 건 생성·조회 API.

라우터는 HTTP·인증 경계만 맡고, 저장소는 의존성으로 주입한다. 이후 질의와
승인 라우트도 같은 소유권 확인 경계를 사용한다.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from backend import db, limit_values
from backend.company_aliases import find_company_matches
from backend.api.limits import QueryGuard
from backend.auth import current_user_id
from backend.errors import ApiError, database_unavailable, resource_not_found, review_closed, upstream_api_error
from backend.graph.approval import (
    ApprovalInputError,
    NotAwaitingApproval,
    pending_approval,
    resume_approval,
    thread_id_for,
)
from backend.graph.checklist import infer_checklist_item
from backend.graph.refusal import clarified_question

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
ReviewSort = Literal["recent", "oldest", "name"]
_REVIEW_NAME_GROUP = """({name} COLLATE "C" !~ '^[가-힣ㄱ-ㅎㅏ-ㅣ]')"""
_REVIEW_NAME_KEY = 'lower(normalize({name})) COLLATE "C"'
NEWS_RESPONSE_KEYS = ("refusal_kind", "news_items", "news_search")  # 거절 응답의 뉴스 참고 필드(공시 근거 아님)
_UNCERTAIN_FOLLOW_UP = re.compile(
    r"확인(?:이)?\s*필요(?:합니다|하다|함|[.!?]|$)|확인할\s*수\s*없|확정할\s*수\s*없"
    r"|명확하지\s*않|불명확"
)
_REPORT_PERIOD = re.compile(r"당기|전기|사업연도|보고기간|기준일|기준\s*시점")
_TABLE_PERIOD = re.compile(r"(?<!\w)(당기|전기)(?:말)?\s*\|\s*\(단위\s*:")
_MONEY_AMOUNT = re.compile(r"(?<![\d,])([\d,]+(?:\.\d+)?)\s*(?:십억원|백만원|천원|억원|만원|원)")
_COMPARISON_PERIOD = re.compile(r"전기|전년|작년|재작년|비교|증감")
_UNCONFIRMED_PERIOD_REASON = (
    "요청한 시점의 금액과 공시 표의 당기·전기 구분을 대조할 수 없어 금액을 확정하지 않았습니다. "
    "인용문에서 해당 기간과 금액을 확인해 주세요."
)


@dataclass(frozen=True)
class ReviewRecord:
    id: str
    user_id: str
    thread_id: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    status: str
    query_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReviewPage:
    items: list[ReviewRecord]
    next_cursor: str | None


def _review_cursor_parts(cursor: str, sort: ReviewSort) -> tuple[str | datetime, uuid.UUID]:
    try:
        cursor_sort, raw_id, key = cursor.split("|", 2)
        if cursor_sort != sort or not key or len(cursor) > 512 or "\x00" in key:
            raise ValueError("invalid review cursor")
        review_id = uuid.UUID(raw_id)
        if sort == "name":
            return key, review_id
        timestamp = datetime.fromisoformat(key)
        if timestamp.utcoffset() is None:
            raise ValueError("missing timezone")
        return timestamp, review_id
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "cursor 형식이 올바르지 않습니다") from exc


class ReviewRepository(Protocol):
    def create_review(self, user_id: str, corp_query: str, fiscal_year: int) -> ReviewRecord: ...

    def list_reviews(
        self, user_id: str, page_size: int, cursor: str | None, sort: ReviewSort = "recent"
    ) -> ReviewPage: ...

    def get_review(self, user_id: str, review_id: str) -> ReviewRecord | None: ...

    def delete_review(self, user_id: str, review_id: str) -> bool: ...

    def delete_all_reviews(self, user_id: str) -> None: ...

    def increment_query_count(self, user_id: str, review_id: str) -> ReviewRecord | None: ...

    def transition_status(
        self, user_id: str, review_id: str, from_status: str, to_status: str
    ) -> ReviewRecord | None:
        """현재 상태가 from_status일 때만 to_status로 바꾼다(compare-and-set). 아니면 None.

        질의(open → running)·승인(awaiting_approval → saving)이 동시에 들어와도 한 요청만 통과해,
        대기 중인 승인 interrupt가 새 질의로 덮이지 않는다(SPEC §3 상태 흐름).
        """
        ...


class ReviewGraphRunner(Protocol):
    """라우터가 그래프에 의존하는 좁은 경계. 실제 실행기는 별도 조립으로 주입한다."""

    def ask(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        question: str,
        thread_id: str,
        review_target: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    def ask_without_review(self, *, user_id: str, question: str) -> dict[str, Any]: ...

    def ask_follow_up(self, *, review: ReviewRecord, user_id: str, question: str) -> dict[str, Any]: ...

    def pending_approval(self, *, review: ReviewRecord, user_id: str) -> dict[str, Any] | None: ...

    def resume(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        decisions: list[dict[str, str]],
        action: Literal["approve", "cancel"],
    ) -> dict[str, Any]: ...


def _trace_summary(final: dict[str, Any]) -> dict[str, Any]:
    """트레이스 출력: 결과·거절 사유·판정·인용 청크 id만(본문 없음, P19 R7)."""

    return {
        "outcome": final.get("outcome"),
        "refusal_reason": final.get("refusal_reason"),
        "verdicts": [item.get("verdict") for item in final.get("draft_items") or []],
        "citation_ids": [citation.get("chunk_id") for citation in final.get("citations") or []],
        "node_events": list(final.get("node_events") or [])[-12:],
    }


class LangGraphReviewRunner:
    """컴파일된 LangGraph의 HTTP 경계 어댑터.

    라우터는 이 클래스를 통해서만 그래프를 실행·재개하므로, 승인 재개가
    ``resume_approval()`` 검증 입구를 건너뛰지 않는다.
    """

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    @staticmethod
    def _config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    def ask(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        question: str,
        thread_id: str,
        review_target: dict[str, Any] | None,
        follow_up: bool = False,
    ) -> dict[str, Any]:
        from backend.services import tracing

        # review_target이 None이면(바로 질문) 그래프가 질문에서 회사·연도를 찾는다. 회사 비교 질문도 이 경로로 나뉜다.
        state: dict[str, Any] = {"user_id": user_id, "question": question}
        if not follow_up:
            state["review_id"] = review.id
        if review_target is not None:
            state["review_target"] = review_target
        # G6: 질문 한 건 = Langfuse 트레이스 하나. 사용자 식별자·청크 본문은 남기지 않는다(P19 R7).
        with tracing.question(
            "review-query", question=question,
            metadata={"review_id": review.id, "corp_code": (review_target or {}).get("corp_code"),
                      "fiscal_year": (review_target or {}).get("fiscal_year"), "direct": review_target is None,
                      "follow_up": follow_up},
            user_id=user_id, session_id=review.id, tags=["service", "query"],
        ) as trace:
            final = self._graph.invoke(state, config=self._config(thread_id))
            trace.finish(_trace_summary(final))
        return self._query_result(final)

    def ask_follow_up(self, *, review: ReviewRecord, user_id: str, question: str) -> dict[str, Any]:
        # 별도 스레드에서 review_id 없이 실행하면 기존 초안을 보존하고 새 승인도 만들지 않는다.
        result = self.ask(
            review=review,
            user_id=user_id,
            question=question,
            thread_id=f"{thread_id_for(user_id, review.id)}:follow-up",
            review_target={"corp_code": review.corp_code, "corp_name": review.corp_name, "fiscal_year": review.fiscal_year},
            follow_up=True,
        )
        if result["outcome"] == "awaiting_approval":
            raise RuntimeError("추가 질문이 승인 대기 상태로 끝났습니다")
        drafts = result["draft"]
        if len(drafts) == 1 and str(drafts[0].get("item", "")).strip() == question.strip():
            item = infer_checklist_item(question)
            if item is not None:
                result["draft"] = [{**drafts[0], "item": item}]
        return result

    def ask_without_review(self, *, user_id: str, question: str) -> dict[str, Any]:
        """질문에서 회사·연도를 정하지 못한 질문(코퍼스 밖 회사·기간, 회사 이름 없음)을 심사 건 없이 돌린다.

        그래프는 입구에서 거절하거나 되묻고 끝난다(검색·작성·승인까지 가지 않는다). 승인 대기가 나오면
        이어 갈 심사 건이 없으므로 오류로 멈춘다. 스레드는 한 번 쓰고 버린다.
        """

        from backend.services import tracing

        thread_id = f"direct-{uuid.uuid4()}"
        with tracing.question(
            "direct-query", question=question, metadata={"review_id": None},
            user_id=user_id, session_id=thread_id, tags=["service", "query", "direct"],
        ) as trace:
            final = self._graph.invoke({"user_id": user_id, "question": question}, config=self._config(thread_id))
            trace.finish(_trace_summary(final))
        if final.get("outcome") == "awaiting_approval" or final.get("__interrupt__"):
            raise RuntimeError("심사 건 없이 돌린 질문이 승인 대기까지 갔습니다")
        result = self._query_result(final)
        # 되묻기('회사명과 사업연도를 포함해…')와 입구 거절의 안내 문구는 화면이 그대로 보여 준다.
        if final.get("refusal_message"):
            result["refusal_message"] = final["refusal_message"]
        if final.get("target_required"):
            result["target_required"] = True
        return result

    @staticmethod
    def _query_result(final: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "outcome": final.get("outcome", "answered"),
            "draft": list(final.get("draft_items") or []),
            # 작성기에 함께 넘긴 이 사용자의 규칙(의미 검색 상위 5, SPEC §3.3·§4). 판정을 바꿨다는 뜻은 아니다.
            "applied_rules": list(final.get("applied_rules") or []),
        }
        for key in ("answer", "refusal_reason"):
            if key in final:
                result[key] = final[key]
        # 거절 뉴스 참고 필드(docs/specs/refusal-news-search.md): 뉴스 분기가 켜진 그래프가 거절한 응답에만 싣는다.
        # 꺼져 있으면 State에 이 키가 없어 응답은 기존과 같다. 답변·승인 대기 응답에는 싣지 않는다.
        if final.get("outcome") == "refused":
            for key in NEWS_RESPONSE_KEYS:
                if key in final:
                    result[key] = final[key]
            # 고정 거절 멘트("뉴스는 공시 근거가 아니다" 안내 포함)는 뉴스 분기가 켜졌을 때만 싣는다(꺼짐 응답 불변).
            if "refusal_kind" in final and final.get("refusal_message"):
                result["refusal_message"] = final["refusal_message"]
        return result

    def pending_approval(self, *, review: ReviewRecord, user_id: str) -> dict[str, Any] | None:
        return pending_approval(self._graph, self._config(thread_id_for(user_id, review.id)))

    def resume(
        self,
        *,
        review: ReviewRecord,
        user_id: str,
        decisions: list[dict[str, str]],
        action: Literal["approve", "cancel"],
    ) -> dict[str, Any]:
        config = self._config(thread_id_for(user_id, review.id))
        payload = pending_approval(self._graph, config)
        if payload is None:
            raise NotAwaitingApproval("대기 중인 승인 요청이 없습니다")
        resume: dict[str, Any] = {"approval_seq": int(payload["approval_seq"]), "action": action}
        if action == "approve":
            resume["decisions"] = decisions
        from backend.services import tracing

        with tracing.question(
            "review-approval", question=f"{action} #{int(payload['approval_seq'])}",
            metadata={"review_id": review.id, "action": action, "items": len(decisions)},
            user_id=user_id, session_id=review.id, tags=["service", "approval"],
        ) as trace:
            final = resume_approval(self._graph, config, resume)
            trace.finish({"status": str(final.get("status", "open")), "memo_saved": bool(final.get("saved_memo_id"))})
        result = {
            "review_id": review.id,
            "approval_seq": int(payload["approval_seq"]),
            "status": str(final.get("status", "open")),
        }
        if action == "approve":
            result["memo_id"] = str(final["saved_memo_id"])
        return result


class PostgresReviewRepository:
    """reviews 표 접근. 단건 조회는 항상 사용자 조건을 함께 둔다."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        # 풀러(6543)에서도 prepared statement를 만들지 않는 공통 연결(#109)에 행 형식만 더한다.
        connection = db.connect_db(self._database_url)
        connection.row_factory = dict_row
        return connection

    @staticmethod
    def _record(row: dict) -> ReviewRecord:
        return ReviewRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            thread_id=row["thread_id"],
            corp_code=row["corp_code"],
            corp_name=row["corp_name"],
            fiscal_year=int(row["fiscal_year"]),
            status=row["status"],
            query_count=int(row["query_count"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _find_corp(self, connection, corp_query: str, fiscal_year: int) -> tuple[str, str]:
        rows = connection.execute(
            """
            SELECT corp_code, min(corp_name) AS corp_name
            FROM documents
            WHERE fiscal_year = %s
            GROUP BY corp_code
            ORDER BY corp_code
            """,
            (fiscal_year,),
        ).fetchall()
        matches = find_company_matches(rows, corp_query)
        if not matches:
            raise ApiError(422, "VALIDATION_CORP_NOT_FOUND", "회사를 찾을 수 없습니다")
        if len(matches) > 1:
            candidates = [
                {"corp_code": str(row["corp_code"]), "corp_name": str(row["corp_name"])}
                for row in matches[:20]
            ]
            raise ApiError(422, "VALIDATION_AMBIGUOUS_CORP", "회사가 둘 이상입니다", candidates)
        return str(matches[0]["corp_code"]), str(matches[0]["corp_name"])

    def create_review(self, user_id: str, corp_query: str, fiscal_year: int) -> ReviewRecord:
        review_id = str(uuid.uuid4())
        thread_id = thread_id_for(user_id, review_id)
        with self._connect() as connection:
            corp_code, corp_name = self._find_corp(connection, corp_query, fiscal_year)
            row = connection.execute(
                """
                INSERT INTO reviews (id, user_id, thread_id, corp_code, fiscal_year)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, user_id, thread_id, corp_code, fiscal_year, status,
                          query_count, created_at, updated_at
                """,
                (review_id, user_id, thread_id, corp_code, fiscal_year),
            ).fetchone()
        return self._record({**row, "corp_name": corp_name})

    def list_reviews(
        self, user_id: str, page_size: int, cursor: str | None, sort: ReviewSort = "recent"
    ) -> ReviewPage:
        after = ""
        params: list[Any] = [user_id]
        if sort == "name":
            name_group = _REVIEW_NAME_GROUP.format(name="d.corp_name")
            name_key = _REVIEW_NAME_KEY.format(name="d.corp_name")
            order = f"{name_group}, {name_key}, r.id"
            if cursor is not None:
                key, review_id = _review_cursor_parts(cursor, sort)
                cursor_group = _REVIEW_NAME_GROUP.format(name="%s::text")
                cursor_key = _REVIEW_NAME_KEY.format(name="%s::text")
                after = f"AND ({order}) > ({cursor_group}, {cursor_key}, %s)"
                params.extend((key, key, review_id))
        else:
            direction = "DESC" if sort == "recent" else "ASC"
            order = f"r.created_at {direction}, r.id {direction}"
            if cursor is not None:
                comparison = "<" if sort == "recent" else ">"
                after = f"AND (r.created_at, r.id) {comparison} (%s, %s)"
                params.extend(_review_cursor_parts(cursor, sort))
        params.append(page_size + 1)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.id, r.user_id, r.thread_id, r.corp_code,
                       d.corp_name, r.fiscal_year, r.status,
                       r.query_count, r.created_at, r.updated_at
                FROM reviews AS r
                JOIN (
                    SELECT corp_code, fiscal_year, min(corp_name) AS corp_name
                    FROM documents
                    GROUP BY corp_code, fiscal_year
                ) AS d ON d.corp_code = r.corp_code AND d.fiscal_year = r.fiscal_year
                WHERE r.user_id = %s {after}
                ORDER BY {order}
                LIMIT %s
                """,
                params,
            ).fetchall()
        items = [self._record(row) for row in rows[:page_size]]
        next_cursor = None
        if len(rows) > page_size:
            last = items[-1]
            key = last.corp_name if sort == "name" else last.created_at.isoformat()
            next_cursor = f"{sort}|{last.id}|{key}"
        return ReviewPage(items=items, next_cursor=next_cursor)

    def get_review(self, user_id: str, review_id: str) -> ReviewRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.id, r.user_id, r.thread_id, r.corp_code,
                       min(d.corp_name) AS corp_name, r.fiscal_year, r.status,
                       r.query_count, r.created_at, r.updated_at
                FROM reviews AS r
                JOIN documents AS d
                  ON d.corp_code = r.corp_code AND d.fiscal_year = r.fiscal_year
                WHERE r.id = %s AND r.user_id = %s
                GROUP BY r.id
                """,
                (review_id, user_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def delete_review(self, user_id: str, review_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, status FROM reviews WHERE id = %s AND user_id = %s FOR UPDATE",
                (review_id, user_id),
            ).fetchone()
            if row is None:
                return False
            self._delete_locked_reviews(connection, user_id, [row])
        return True

    def delete_all_reviews(self, user_id: str) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, status FROM reviews WHERE user_id = %s ORDER BY id FOR UPDATE",
                (user_id,),
            ).fetchall()
            if rows:
                self._delete_locked_reviews(connection, user_id, rows)

    @staticmethod
    def _delete_locked_reviews(connection, user_id: str, rows: list[dict]) -> None:
        if any(row["status"] in ("running", "saving") for row in rows):
            raise ApiError(409, "CONFLICT_REVIEW_IN_PROGRESS", "처리 중인 심사 건은 삭제할 수 없습니다")
        review_ids = [str(row["id"]) for row in rows]
        connection.execute(
            "DELETE FROM review_rule_events WHERE source_memo_id IN "
            "(SELECT id FROM review_memos WHERE review_id = ANY(%s::uuid[]))",
            (review_ids,),
        )
        connection.execute("DELETE FROM review_memos WHERE review_id = ANY(%s::uuid[])", (review_ids,))
        connection.execute(
            "DELETE FROM reviews WHERE id = ANY(%s::uuid[]) AND user_id = %s",
            (review_ids, user_id),
        )

    @staticmethod
    def _with_corp_name(connection, row: dict) -> ReviewRecord:
        corp_name = connection.execute(
            """SELECT min(corp_name) AS corp_name FROM documents
               WHERE corp_code = %s AND fiscal_year = %s""",
            (row["corp_code"], row["fiscal_year"]),
        ).fetchone()["corp_name"]
        return PostgresReviewRepository._record({**row, "corp_name": corp_name})

    def increment_query_count(self, user_id: str, review_id: str) -> ReviewRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE reviews
                SET query_count = query_count + 1, updated_at = now()
                WHERE id = %s AND user_id = %s
                RETURNING id, user_id, thread_id, corp_code, fiscal_year, status,
                          query_count, created_at, updated_at
                """,
                (review_id, user_id),
            ).fetchone()
            return self._with_corp_name(connection, row) if row is not None else None

    def transition_status(
        self, user_id: str, review_id: str, from_status: str, to_status: str
    ) -> ReviewRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE reviews
                SET status = %s, updated_at = now()
                WHERE id = %s AND user_id = %s AND status = %s
                RETURNING id, user_id, thread_id, corp_code, fiscal_year, status,
                          query_count, created_at, updated_at
                """,
                (to_status, review_id, user_id, from_status),
            ).fetchone()
            return self._with_corp_name(connection, row) if row is not None else None


def get_review_repository() -> ReviewRepository:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise database_unavailable()
    return PostgresReviewRepository(database_url)


class UnavailableReviewGraphRunner:
    """그래프 런타임이 아직 주입되지 않은 배포를 안전하게 멈춘다."""

    def ask(self, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다")

    def ask_without_review(self, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다")

    def ask_follow_up(self, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다")

    def resume(self, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다")

    def pending_approval(self, **_kwargs: Any) -> dict[str, Any] | None:
        raise ApiError(503, "SERVICE_UNAVAILABLE_GRAPH", "심사 그래프가 준비되지 않았습니다")


def get_review_graph_runner(request: Request) -> ReviewGraphRunner:
    # 의존성 해석은 라우터 본문보다 먼저 일어난다. 여기서 503을 던지면 타 사용자
    # 심사 건도 404 대신 503이 될 수 있으므로, ask 시점에만 명확히 중단한다.
    # 앱 시작 때 키·DB·검색 산출물이 모두 있으면 backend/api/runtime.py가 실제 실행기를 만들어 둔다.
    runtime = getattr(request.app.state, "review_runtime", None)
    return runtime.runner if runtime is not None else UnavailableReviewGraphRunner()


def get_question_target_resolver(request: Request):
    """질문에서 심사 대상(회사·사업연도)을 찾는 함수. 실행기가 없으면 None(질의 시점에 503)."""

    runtime = getattr(request.app.state, "review_runtime", None)
    return getattr(runtime, "resolve_target", None) if runtime is not None else None


# 실행기가 없을 때도 분당 한도는 지킨다(비용 상한은 DB가 있어야 하므로 실행기와 함께 생긴다).
_DEFAULT_GUARD = QueryGuard()


def get_query_guard(request: Request) -> QueryGuard:
    """SPEC §4 질의 한도(값은 backend/limit_values.py): 사용자 분당·하루, 심사 건 하루, 서비스·사용자 하루 비용."""
    runtime = getattr(request.app.state, "review_runtime", None)
    return runtime.guard if runtime is not None else _DEFAULT_GUARD


def _record_llm_cost(guard: QueryGuard, user_id: str, usage: list[dict[str, Any]]) -> None:
    """이번 요청에서 쓴 LLM·임베딩·리랭크 비용을 오늘 비용에 더한다(실패·시간 초과로 끝난 요청의 비용도 센다).

    usage는 llm.request_scope가 이 요청의 호출만 모은 목록이다(동시에 들어온 다른 사용자의 호출과 섞이지 않는다).
    요청 범위 밖에서 기록된 사용량(요청이 끝난 뒤 끝난 호출 등)은 누구 것인지 알 수 없어 서비스 합계에만 더한다.
    """
    from backend.services import llm

    try:
        guard.record(user_id, list(usage), late_usage=[*llm.drain_late_usage(), *llm.drain_usage()])
    except Exception:  # 비용 기록 실패로 응답을 바꾸지 않는다. 대신 남겨서 운영에서 보이게 한다.
        logger.exception("LLM 비용 누적 기록에 실패했습니다")


class ReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corp_query: str = Field(min_length=1, max_length=100)
    fiscal_year: int = Field(ge=2020, le=2030)


class ReviewQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=limit_values.MAX_QUESTION_CHARS)


class DirectQueryRequest(BaseModel):
    """바로 질문. clarifies: 직전에 되물음(target_required)을 받은 원래 질문. 있으면 question을 그 답으로 보고 합친다(162번)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=limit_values.MAX_QUESTION_CHARS)
    clarifies: str | None = Field(default=None, min_length=1, max_length=limit_values.MAX_QUESTION_CHARS)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    verdict: Literal["applicable", "not_applicable", "needs_check"]
    reason: str


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[ApprovalDecision] = Field(min_length=1)


class ReviewResponse(BaseModel):
    id: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    status: str
    query_count: int
    created_at: datetime


class ReviewPageResponse(BaseModel):
    items: list[ReviewResponse]
    next_cursor: str | None


class ReviewDetailResponse(ReviewResponse):
    pending_draft: list[dict[str, Any]] | None = None
    pending_question: str | None = None
    pending_applied_rules: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalResponse(BaseModel):
    review_id: str
    approval_seq: int
    memo_id: str


class CancelApprovalResponse(BaseModel):
    review_id: str
    approval_seq: int
    status: str


def _response(record: ReviewRecord) -> ReviewResponse:
    return ReviewResponse(
        id=record.id,
        corp_code=record.corp_code,
        corp_name=record.corp_name,
        fiscal_year=record.fiscal_year,
        status=record.status,
        query_count=record.query_count,
        created_at=record.created_at,
    )


Reviews = Annotated[ReviewRepository, Depends(get_review_repository)]
GraphRunner = Annotated[ReviewGraphRunner, Depends(get_review_graph_runner)]
Guard = Annotated[QueryGuard, Depends(get_query_guard)]
TargetResolver = Annotated[Any, Depends(get_question_target_resolver)]
UserId = Annotated[str, Depends(current_user_id)]

reviews_router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])
queries_router = APIRouter(prefix="/api/v1/queries", tags=["queries"])


@reviews_router.post("", status_code=201, response_model=ReviewResponse)
def create_review(request: ReviewCreateRequest, user_id: UserId, repository: Reviews) -> ReviewResponse:
    return _response(repository.create_review(user_id, request.corp_query, request.fiscal_year))


@reviews_router.get("", response_model=ReviewPageResponse)
def list_reviews(
    user_id: UserId,
    repository: Reviews,
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = None,
    sort: ReviewSort = "recent",
) -> ReviewPageResponse:
    page = repository.list_reviews(user_id, page_size, cursor, sort)
    return ReviewPageResponse(items=[_response(item) for item in page.items], next_cursor=page.next_cursor)


@reviews_router.get("/{review_id}", response_model=ReviewDetailResponse)
def get_review(
    review_id: uuid.UUID,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
) -> ReviewDetailResponse:
    review = repository.get_review(user_id, str(review_id))
    if review is None:
        raise resource_not_found()
    pending = (
        graph_runner.pending_approval(review=review, user_id=user_id)
        if review.status == "awaiting_approval"
        else None
    )
    return ReviewDetailResponse(
        **_response(review).model_dump(),
        pending_draft=pending["draft_items"] if pending is not None else None,
        pending_question=pending.get("question") if pending is not None else None,
        pending_applied_rules=(pending.get("applied_rules") or []) if pending is not None else [],
    )


@reviews_router.delete("/{review_id}", status_code=204)
def delete_review(review_id: uuid.UUID, user_id: UserId, repository: Reviews) -> Response:
    if not repository.delete_review(user_id, str(review_id)):
        raise resource_not_found()
    return Response(status_code=204)


@reviews_router.delete("", status_code=204)
def delete_all_reviews(user_id: UserId, repository: Reviews) -> Response:
    repository.delete_all_reviews(user_id)
    return Response(status_code=204)


@reviews_router.post("/{review_id}/queries")
def ask_review(
    review_id: uuid.UUID,
    request: ReviewQueryRequest,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
    guard: Guard,
) -> dict[str, Any]:
    review = repository.get_review(user_id, str(review_id))
    if review is None:
        raise resource_not_found()
    return _ask_in_review(
        review, request.question, user_id, repository, graph_runner, guard,
        review_target={"corp_code": review.corp_code, "corp_name": review.corp_name, "fiscal_year": review.fiscal_year},
    )


@reviews_router.post("/{review_id}/queries:follow-up")
def ask_review_follow_up(
    review_id: uuid.UUID,
    request: ReviewQueryRequest,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
    guard: Guard,
) -> dict[str, Any]:
    review = _awaiting_review_or_409(repository, user_id, str(review_id))
    guard.before_query(user_id, review.id)
    if repository.transition_status(user_id, review.id, "awaiting_approval", "running") is None:
        guard.cancel_query(user_id, review.id)
        raise ApiError(409, "CONFLICT_REVIEW_NOT_OPEN", "다른 질의 또는 승인이 처리 중입니다")
    try:
        result = _guarded_graph_call(
            lambda: graph_runner.ask_follow_up(review=review, user_id=user_id, question=request.question),
            guard, user_id,
        )
    except Exception:
        _restore_status(repository, user_id, review.id, "running", "awaiting_approval")
        raise
    repository.transition_status(user_id, review.id, "running", "awaiting_approval")
    repository.increment_query_count(user_id, review.id)
    response = _query_response(result)
    if response.get("draft"):
        response["draft"] = [
            _check_follow_up_draft(item, request.question, review.fiscal_year) for item in response["draft"]
        ]
    return response


def _cites_previous_period_only(item: dict[str, Any], question: str, fiscal_year: int) -> bool:
    """당기만 물은 질문에서 수치가 명시적으로 전기인 인용에만 나오는지 검사한다."""
    if _COMPARISON_PERIOD.search(question) or not re.search(rf"\b{fiscal_year}\s*년\s*말|당기", question):
        return False
    amounts = {value.replace(",", "") for value in _MONEY_AMOUNT.findall(item.get("reason") or "")}
    if not amounts:
        return False

    previous, other = set(), set()
    for citation in item.get("citations") or []:
        # 표 경계를 잃은 청크는 기간 미지정으로 두며, 명확한 전기 구간만 전기로 해석한다.
        parts = _TABLE_PERIOD.split(str(citation.get("quote") or ""))
        for period, content in zip(["", *parts[1::2]], parts[::2]):
            numbers = {number.replace(",", "") for number in re.findall(r"\d[\d,]*(?:\.\d+)?", content)}
            (previous if period == "전기" else other).update(numbers)
    return bool(amounts & (previous - other))


def _check_follow_up_draft(item: dict[str, Any], question: str, fiscal_year: int) -> dict[str, Any]:
    """추가 답변에서 작성기가 알린 불확실성을 화면 판정과 맞춘다."""
    reason = str(item.get("reason") or "")
    uncertain = [clause for clause in re.split(r"(?<=[.!?])\s+|\n+", reason)
                 if _UNCERTAIN_FOLLOW_UP.search(clause)]
    if any(_REPORT_PERIOD.search(clause) for clause in uncertain) or _cites_previous_period_only(item, question, fiscal_year):
        return {**item, "verdict": "needs_check", "reason": _UNCONFIRMED_PERIOD_REASON}
    if uncertain:
        return {**item, "verdict": "needs_check"}
    return item


def _guarded_graph_call(call, guard: QueryGuard, user_id: str, on_failure=None) -> dict[str, Any]:
    """그래프 호출 하나를 처리 시간 상한·오류 변환·비용 기록으로 감싼다(심사 건 질의와 바로 질문이 함께 쓴다)."""

    from backend.services import llm

    # 질의 처리 시간 상한: 이 범위 안의 외부 호출은 마감이 지나면 시작하지 않고 llm.QueryDeadlineExceeded를 낸다.
    # 그래프가 같은 스레드에서 예외로 멈추므로 응답 뒤에 뒤에서 도는 작업이 없다.
    usage: list[dict[str, Any]] = []
    try:
        with llm.request_scope(timeout_seconds=limit_values.QUERY_TIMEOUT_SECONDS) as usage:
            return call()
    except Exception as exc:
        if on_failure is not None:
            on_failure()
        from backend.services.demo_replay import DemoMiss

        if isinstance(exc, DemoMiss):
            # 데모 모드(키 없음)에서 기록에 없는 질문: 서버는 멀쩡하고 이 질문만 답할 수 없다(SPEC §5.6).
            raise ApiError(
                503, "SERVICE_UNAVAILABLE_GRAPH",
                "데모 모드에서는 기록된 데모 질문만 답할 수 있습니다(README의 데모 질문 참고). API 키를 넣으면 모든 질문에 답합니다",
            ) from exc
        _raise_upstream(exc)
        raise
    finally:
        _record_llm_cost(guard, user_id, usage)


def _query_response(result: dict[str, Any]) -> dict[str, Any]:
    response = {
        key: result[key]
        for key in ("outcome", "answer", "draft", "refusal_reason", "refusal_message", *NEWS_RESPONSE_KEYS)
        if key in result
    }
    # SPEC §3.3: applied_rules는 결과와 관계없이 항상 배열이다. 거절에는 초안이 없으므로 빈 배열.
    response["applied_rules"] = (
        [] if result.get("outcome") == "refused" else list(result.get("applied_rules") or [])
    )
    return response


def _ask_in_review(
    review: ReviewRecord,
    question: str,
    user_id: str,
    repository: ReviewRepository,
    graph_runner: ReviewGraphRunner,
    guard: QueryGuard,
    *,
    review_target: dict[str, Any] | None,
) -> dict[str, Any]:
    if review.status == "closed":
        raise review_closed()
    if review.status != "open":
        raise ApiError(409, "CONFLICT_REVIEW_NOT_OPEN", "열려 있는 심사 건만 질의할 수 있습니다")
    # 한도(분당·하루 횟수·하루 비용)는 잠금(open → running) 전에 본다. 막힌 질의는 상태를 바꾸지 않는다.
    # 통과하면 오늘 횟수에 1회로 센다(실패한 질의도 비용을 쓰므로 그대로 센다).
    guard.before_query(user_id, review.id)

    # open → running 전이가 곧 잠금이다. 동시에 들어온 다른 질의·승인은 여기서 막혀, 승인 대기 중인
    # interrupt를 새 질의가 덮어쓰지 못한다(전이는 현재 상태가 open일 때만 성공).
    if repository.transition_status(user_id, review.id, "open", "running") is None:
        guard.cancel_query(user_id, review.id)  # 그래프를 돌리지 않았으니 방금 센 횟수를 되돌린다
        raise ApiError(409, "CONFLICT_REVIEW_NOT_OPEN", "열려 있는 심사 건만 질의할 수 있습니다")
    result = _guarded_graph_call(
        lambda: graph_runner.ask(
            review=review,
            user_id=user_id,
            question=question,
            thread_id=thread_id_for(user_id, review.id),
            review_target=review_target,
        ),
        guard, user_id,
        # 실패하면 상태를 open으로 되돌려 다음 질의를 받을 수 있게 한다.
        on_failure=lambda: _restore_status(repository, user_id, review.id, "running", "open"),
    )
    # 그래프가 승인 대기로 멈췄으면 awaiting_approval, 아니면(답변·거절) open으로 돌아간다.
    next_status = "awaiting_approval" if result.get("outcome") == "awaiting_approval" else "open"
    repository.transition_status(user_id, review.id, "running", next_status)
    repository.increment_query_count(user_id, review.id)
    return _query_response(result)


def direct_scope_id(user_id: str) -> str:
    """심사 건 없이 끝나는 바로 질문의 하루 한도 칸(심사 건 한도 자리에 사용자별로 하나)."""

    return f"direct:{user_id}"


@queries_router.post("")
def ask_directly(
    request: DirectQueryRequest,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
    guard: Guard,
    resolve_target: TargetResolver,
) -> dict[str, Any]:
    """회사를 고르지 않고 질문만 보낸다(SPEC §3.3 바로 질문).

    질문에서 회사·사업연도가 하나로 정해지면 그 대상의 심사 건을 만들어 기존 질의 경로로 돌린다. 승인 대기로 끝나면
    심사 건을 남기고(승인은 기존 승인 API), 거절로 끝나면 방금 만든 심사 건을 지운다. 대상이 정해지지 않으면
    (코퍼스 밖 회사·기간, 회사 이름 없음) 심사 건 없이 그래프의 거절·되묻기를 그대로 돌려준다.
    """

    question = request.question
    if request.clarifies is not None:
        # 되물음에 대한 답: 원래 질문과 합쳐 처음부터 다시 묻는다(입구 검사·주제 판정·검색을 모두 다시 거친다).
        question = clarified_question(request.clarifies, request.question)
        if len(question) > limit_values.MAX_QUESTION_CHARS:
            raise ApiError(422, "VALIDATION_ERROR", "질문이 너무 깁니다.")
    target = resolve_target(question) if resolve_target is not None else None
    if target is None:
        scope_id = direct_scope_id(user_id)
        guard.before_query(user_id, scope_id)
        result = _guarded_graph_call(
            lambda: graph_runner.ask_without_review(user_id=user_id, question=question), guard, user_id,
        )
        response = _query_response(result)
        if result.get("target_required"):
            response["target_required"] = True
        return {**response, "review": None}

    review = repository.create_review(user_id, str(target["corp_code"]), int(target["fiscal_year"]))
    try:
        # review_target을 주지 않는다: 그래프가 같은 규칙으로 대상을 다시 찾고, 회사 비교 질문은 회사별로 검색한다.
        response = _ask_in_review(
            review, question, user_id, repository, graph_runner, guard, review_target=None,
        )
    except Exception:
        repository.delete_review(user_id, review.id)  # 답을 내지 못한 질문은 빈 심사 건을 남기지 않는다
        raise
    if response.get("outcome") != "awaiting_approval":
        repository.delete_review(user_id, review.id)
        return {**response, "review": None}
    return {
        **response,
        "review": {"id": review.id, "corp_name": review.corp_name, "fiscal_year": review.fiscal_year},
    }


def _raise_upstream(exc: BaseException) -> None:
    """외부 API 장애면 SPEC §6 응답(503·502·504)으로 바꿔 던진다. 아니면 아무것도 하지 않는다(호출부가 그대로 다시 던짐)."""

    mapped = upstream_api_error(exc)
    if mapped is not None:
        logger.warning("외부 API 오류로 %s 응답: %s", mapped.status_code, exc)  # 예외 이름·상태 코드만(키 없음)
        raise mapped from exc


def _restore_status(
    repository: ReviewRepository, user_id: str, review_id: str, from_status: str, to_status: str
) -> None:
    """실패한 요청이 running·saving 상태에 심사 건을 가둬 두지 않도록 되돌린다."""
    try:
        repository.transition_status(user_id, review_id, from_status, to_status)
    except Exception:
        logger.exception("심사 건 상태를 되돌리지 못했습니다: review_id=%s", review_id)


def _awaiting_review_or_409(repository: ReviewRepository, user_id: str, review_id: str) -> ReviewRecord:
    review = repository.get_review(user_id, review_id)
    if review is None:
        raise resource_not_found()
    if review.status != "awaiting_approval":
        raise ApiError(
            409,
            "CONFLICT_NOT_AWAITING_APPROVAL",
            "대기 중인 승인 요청이 없습니다",
        )
    return review


def _resume_review(
    repository: ReviewRepository,
    graph_runner: ReviewGraphRunner,
    *,
    user_id: str,
    review: ReviewRecord,
    decisions: list[dict[str, str]],
    action: Literal["approve", "cancel"],
) -> dict[str, Any]:
    """승인·취소의 공통 경로. awaiting_approval → saving 전이가 잠금이다.

    승인이 끝나면 closed가 되어 더 질의할 수 없다(SPEC §4). 취소는 메모를 저장하지 않으므로 open으로 돌아가 다시 물을 수 있다.
    """
    not_awaiting = ApiError(409, "CONFLICT_NOT_AWAITING_APPROVAL", "대기 중인 승인 요청이 없습니다")
    if repository.transition_status(user_id, review.id, "awaiting_approval", "saving") is None:
        raise not_awaiting
    try:
        result = graph_runner.resume(review=review, user_id=user_id, decisions=decisions, action=action)
    except NotAwaitingApproval as exc:
        # DB는 대기라는데 그래프에는 대기가 없다(이미 처리됨). 열린 상태로 정리하고 409를 돌려준다.
        _restore_status(repository, user_id, review.id, "saving", "open")
        raise not_awaiting from exc
    except ApprovalInputError as exc:
        # 결정이 원본 초안과 맞지 않는다. 대기는 그대로 남아 있으니 다시 보낼 수 있게 되돌린다.
        _restore_status(repository, user_id, review.id, "saving", "awaiting_approval")
        raise ApiError(400, "VALIDATION_ERROR", "승인 결정이 초안과 맞지 않습니다") from exc
    except Exception as exc:
        _restore_status(repository, user_id, review.id, "saving", "awaiting_approval")
        _raise_upstream(exc)
        raise
    repository.transition_status(user_id, review.id, "saving", "closed" if action == "approve" else "open")
    return result


@reviews_router.post("/{review_id}/approvals", response_model=ApprovalResponse)
def approve_review(
    review_id: uuid.UUID,
    request: ApprovalRequest,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
    guard: Guard,
) -> ApprovalResponse:
    review = _awaiting_review_or_409(repository, user_id, str(review_id))
    from backend.services import llm

    with llm.request_scope() as usage:  # 승인에는 처리 시간 상한을 두지 않는다(메모 저장을 중간에 끊지 않는다)
        try:
            result = _resume_review(
                repository,
                graph_runner,
                user_id=user_id,
                review=review,
                decisions=[decision.model_dump() for decision in request.decisions],
                action="approve",
            )
        finally:
            _record_llm_cost(guard, user_id, usage)  # 승인 때 고친 규칙을 Store에 넣으며 임베딩을 부른다
    return ApprovalResponse(
        review_id=str(result["review_id"]),
        approval_seq=int(result["approval_seq"]),
        memo_id=str(result["memo_id"]),
    )


@reviews_router.post("/{review_id}/approvals:cancel", response_model=CancelApprovalResponse)
def cancel_review_approval(
    review_id: uuid.UUID,
    user_id: UserId,
    repository: Reviews,
    graph_runner: GraphRunner,
) -> CancelApprovalResponse:
    review = _awaiting_review_or_409(repository, user_id, str(review_id))
    result = _resume_review(
        repository, graph_runner, user_id=user_id, review=review, decisions=[], action="cancel"
    )
    return CancelApprovalResponse(
        review_id=str(result["review_id"]),
        approval_seq=int(result["approval_seq"]),
        status=str(result["status"]),
    )
