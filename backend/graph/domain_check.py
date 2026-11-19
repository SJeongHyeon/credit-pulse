"""Supervisor 주제 밖 판정 (사전 등록 59번).

두 단계로 거른다(2주19 라우팅, 3주15 R4: 도메인 밖 질문이 도메인 에이전트로 새지 않게).
  1. 코드 규칙: '예측' 요청은 검색 전에 거절한다. 근거는 SPEC §1 범위 밖
     ("재무비율·등급·부도 예측", 최종 신용 결정)이다. 평가셋 문장에서 낱말을 뽑지 않는다.
  2. 규칙을 통과하고 회사·연도가 확정된 질문만 LLM 분류 1회(backend/services/llm.py classify_domain).
     공시되지 않았을 세부 항목은 주제 안으로 본다(그것은 검색 뒤 ITEM_NOT_DISCLOSED의 몫).
기본은 켜짐(domain_check_enabled=True, 11/6 59번 채택 뒤). 평가 러너는 --domain-check 값을 명시적으로 넘겨
58·64번 같은 이전 측정을 그대로 재현한다. 키가 없으면 LLM 분류는 건너뛰고 규칙만 쓴다.
"""
from __future__ import annotations

PREDICTION_MARKERS = ("예측",)


def is_prediction_request(question: str) -> bool:
    """SPEC §1이 범위 밖으로 정한 '예측' 요청인가(낱말 규칙, 결정적)."""

    return any(marker in question for marker in PREDICTION_MARKERS)


def default_domain_classifier(model: str):
    """llm.classify_domain을 감싼 기본 분류기. 반환: "in_domain" / "out_of_domain" / "not_disclosed_item"(72번), None 건너뜀.

    API 키가 없는 실행(키 없는 데모, G2)에서는 LLM 분류를 건너뛰고 None을 돌려준다.
    supervisor는 이때 '예측' 규칙만 적용하고, 건너뛴 사실을 State의 domain_check에 남긴다.
    키 없음 말고 다른 오류(형식 오류, HTTP 오류)는 숨기지 않고 그대로 올린다.
    """

    from backend.services import llm

    def classify(question: str) -> str | None:
        try:
            return llm.classify_domain(question=question, model=model)
        except llm.ExternalApiKeyMissing:
            return None

    return classify


def default_refusal_kind_classifier(model: str):
    """llm.classify_refusal_kind를 감싼 기본 분류기. 반환: "unrelated" / "work_related", 키가 없으면 None.

    None이면 거절 종류를 알 수 없으므로 뉴스를 부르지 않고 State에 건너뜀을 남긴다(조용히 숨기지 않음).
    키 없음 말고 다른 오류는 숨기지 않고 그대로 올린다.
    """

    from backend.services import llm

    def classify(question: str) -> str | None:
        try:
            return llm.classify_refusal_kind(question=question, model=model)
        except llm.ExternalApiKeyMissing:
            return None

    return classify
