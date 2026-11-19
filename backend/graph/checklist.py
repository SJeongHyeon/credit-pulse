"""질문이 분명히 가리키는 단일 심사 체크리스트 항목을 찾는다."""
from __future__ import annotations

import re

# SPEC §3.1의 여섯 코드. 여러 주제가 섞이면 임의로 하나를 고르지 않는다.
_TOPICS = {
    "litigation": re.compile(r"소송|분쟁|피소|계류"),
    "guarantee_collateral": re.compile(r"담보|저당|질권|채무보증|지급보증|보증(?!금)"),
    "customer_concentration": re.compile(
        r"(?:주요|상위|대형)\s*고객|고객.{0,20}(?:매출|비중|의존|집중)"
        r"|거래처.{0,20}(?:매출|비중|의존|집중)|매출.{0,20}고객"
    ),
    "fx_raw_material": re.compile(r"환율|외화|외환|환위험|원재료|원자재"),
    "borrowing_structure": re.compile(r"차입|사채|대출|금리|이자율|만기|리스부채"),
    "contingent_liability": re.compile(r"우발부채|우발채무|우발손실|충당부채"),
}


def infer_checklist_item(question: str) -> str | None:
    """명확히 한 주제만 발견되면 정식 코드를 돌려준다."""
    matches = [item for item, pattern in _TOPICS.items() if pattern.search(question)]
    return matches[0] if len(matches) == 1 else None
