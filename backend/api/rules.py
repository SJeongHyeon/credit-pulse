"""로그인 사용자의 규칙 메모리 목록·삭제 API(G4, SPEC §6).

승인 때 심사역이 고친 항목은 Store namespace ("memories", user_id)에 규칙으로 쌓인다(#114).
틀린 기억은 삭제로 고친다(WBS v4: 규칙 수정·메모 되돌리기는 구현하지 않음).
다른 사용자의 규칙은 없는 규칙과 같은 404로 숨긴다.
"""
from __future__ import annotations

from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, Request, Response

from backend.auth import current_user_id
from backend.errors import ApiError, resource_not_found
from backend.memory import RuleNotFound


class RuleRepository(Protocol):
    def list_rules(self, user_id: str) -> list[dict[str, Any]]: ...

    def delete_rule(self, user_id: str, rule_id: str) -> None: ...


def rules_unavailable() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE_RULES", "규칙 메모리를 사용할 수 없습니다")


class UnavailableRuleStore:
    """Store가 조립되지 않은 배포(그래프 런타임 없음, 데모 모드)를 안전하게 멈춘다."""

    def list_rules(self, user_id: str) -> list[dict[str, Any]]:
        raise rules_unavailable()

    def delete_rule(self, user_id: str, rule_id: str) -> None:
        raise rules_unavailable()


def get_rule_store(request: Request) -> RuleRepository:
    # reviews.py의 get_review_graph_runner와 같은 패턴: 앱 시작 때 runtime.py가 만든 것을 꺼내고,
    # 없으면 호출 시점에 503을 던지는 대체 객체를 준다(인증 401이 먼저 걸리도록 의존성에서는 던지지 않음).
    runtime = getattr(request.app.state, "review_runtime", None)
    rules = getattr(runtime, "rules", None) if runtime is not None else None
    return rules if rules is not None else UnavailableRuleStore()


UserId = Annotated[str, Depends(current_user_id)]
Rules = Annotated[RuleRepository, Depends(get_rule_store)]

rules_router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


@rules_router.get("")
def list_rules(user_id: UserId, rules: Rules) -> dict[str, list[dict[str, Any]]]:
    return {"items": rules.list_rules(user_id)}


@rules_router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: str, user_id: UserId, rules: Rules) -> Response:
    try:
        rules.delete_rule(user_id, rule_id)
    except RuleNotFound as exc:
        raise resource_not_found() from exc
    return Response(status_code=204)
