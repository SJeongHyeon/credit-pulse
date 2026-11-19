"""사전 등록 20번: 질문마다 검색할 장(II·IV·XI·NOTES) 하나를 LLM 라우터로 고르고 파일로 남긴다.

강의 2주19강 라우터 패턴(Route 스키마, P12 R8 '라우터는 답이 아니라 검색 전략을 고른다')을
장 필터 선택에 응용한다. 정답 청크·검색 결과는 읽지 않는다. 라우터에 주는 것은
질문 원문, 그 회사·연도 보고서에 실제로 있는 장 목록, DART 서식의 장 설명뿐이다.

실행: python -X utf8 scripts/route_chapters.py --dry-run   # 호출 없이 프롬프트 수만 확인
      python -X utf8 scripts/route_chapters.py             # 라우팅 결과와 장 필터 파일 생성
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import find_dotenv, load_dotenv  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

CHAPTERS = ("II", "IV", "XI", "NOTES")
SYSTEM_PROMPT = """너는 한국 DART 사업보고서 검색 라우터다. 질문의 근거가 가장 많이 있을 장 하나를 고른다. 답을 쓰지 않고 검색할 장만 고른다.
장 설명(DART 사업보고서 서식의 장·절 제목 기준):
- II: 'II. 사업의 내용' - 사업의 개요, 주요 제품 및 서비스, 원재료 및 생산설비, 매출 및 수주상황, 위험관리 및 파생거래, 주요계약 및 연구개발활동 등
- IV: 'IV. 이사의 경영진단 및 분석의견' - 경영진이 설명하는 재무상태 및 영업실적, 유동성 및 자금조달과 지출 등
- XI: 'XI. 그 밖에 투자자 보호를 위하여 필요한 사항' - 공시내용 진행 및 변경사항, 우발부채 등에 관한 사항(중요한 소송사건, 채무보증 등), 제재 등과 관련된 사항
- NOTES: 연결·별도 재무제표 주석 - 회계정책과 계정별 세부 내역(차입금, 유형자산, 담보제공자산, 우발사항과 약정사항, 특수관계자, 고객과의 계약에서 생기는 수익, 금융위험관리 등)
반드시 사용자가 알려 준 '이 보고서에 있는 장' 중에서만 고른다.
JSON 하나로만 답한다: {"chapter": "II|IV|XI|NOTES 중 하나", "reason": "한국어 한 문장"}"""


class ChapterRoute(BaseModel):
    """라우터 출력. Literal로 묶어 네 장 밖의 값을 막는다(강의 Route 스키마 주석의 권고)."""

    chapter: Literal["II", "IV", "XI", "NOTES"]
    reason: str = Field(min_length=1)


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def available_chapters(chunks_path: str | Path) -> dict[tuple[str, int], list[str]]:
    seen: dict[tuple[str, int], set[str]] = {}
    with open(chunks_path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                seen.setdefault((str(row["corp_code"]), int(row["fiscal_year"])), set()).add(str(row["chapter"]))
    return {key: [c for c in CHAPTERS if c in value] for key, value in seen.items()}


def build_messages(question: str, chapters: list[str]) -> list[dict[str, str]]:
    user = f"이 보고서에 있는 장: {', '.join(chapters)}\n질문: {question}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default="evals/dataset_v1.jsonl", help="질문 원문(동결 v1)")
    parser.add_argument("--filters", default="evals/filters_v1.jsonl", help="문항별 회사·연도 필터")
    parser.add_argument("--chunks", default="data/experiments/b100-v8-fixed_window/chunks.jsonl")
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--output", default="evals/routing/chapter_router_v1.jsonl")
    parser.add_argument("--filters-output", default="evals/filters_v1.chapter_router.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    questions = {int(row["id"]): str(row["input"]) for row in read_jsonl(args.dataset)}
    filters = read_jsonl(args.filters)
    chapters_by_doc = available_chapters(args.chunks)
    prompt_sha = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    plans = []
    for item in filters:
        key = (str(item["corp_code"]), int(item["fiscal_year"]))
        if key not in chapters_by_doc:
            raise SystemExit(f"id={item['id']} 회사·연도 청크가 없습니다 {key}")
        plans.append((item, chapters_by_doc[key]))
    if args.dry_run:
        print(json.dumps({"dry_run": True, "requests": len(plans), "model": args.model, "prompt_sha256": prompt_sha}, ensure_ascii=False))
        return 0

    load_dotenv(find_dotenv(usecwd=True))
    from backend.services.llm import openrouter_structured

    routes, routed_filters = [], []
    for item, chapters in plans:
        route = openrouter_structured(
            build_messages(questions[int(item["id"])], chapters), model=args.model, response_model=ChapterRoute
        )
        if route.chapter not in chapters:
            raise SystemExit(f"id={item['id']} 라우터가 보고서에 없는 장 {route.chapter}를 골랐습니다")
        routes.append({"id": int(item["id"]), "chapter": route.chapter, "reason": route.reason,
                       "available": chapters, "model": args.model, "prompt_sha256": prompt_sha})
        routed_filters.append({**item, "chapter": route.chapter})
    for path, rows in ((args.output, routes), (args.filters_output, routed_filters)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8"))
    print(json.dumps({"routed": len(routes), "output": args.output, "filters_output": args.filters_output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
