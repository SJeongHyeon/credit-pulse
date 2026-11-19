"""EVAL_REPORT 10장: 같은 질문을 '검색 없는 범용 챗봇'에 넣어 우리 서비스와 비교한다.

범용 챗봇 대용은 우리 작성기와 같은 모델(gpt-4o-mini)을 검색·근거 없이 부른 것이다(웹 ChatGPT 화면이 아님).
같은 모델로 해야 차이가 '모델'이 아니라 '근거 검색·인용·거절 구조'에서 온다는 것을 볼 수 있다.
우리 서비스 답은 같은 질문의 평가 실행 결과(91번 반복 측정 파일)에서 가져온다. 모델 호출은 llm.py를 거친다.

사용: python -X utf8 scripts/compare_general_chatbot.py <91번 결과 jsonl> <출력 jsonl> [문항 id ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_IDS = (62, 60, 64, 24, 59)


def main(service_results: str, output_path: str, *ids: str) -> int:
    from dotenv import find_dotenv, load_dotenv
    from pydantic import BaseModel

    from backend.services import llm

    load_dotenv(find_dotenv(usecwd=True), override=False)

    class ChatbotAnswer(BaseModel):
        answer: str
        source: str

    wanted = [int(value) for value in ids] or list(DEFAULT_IDS)
    dataset = {row["id"]: row for row in map(json.loads, Path("evals/dataset_v2.jsonl").read_text(encoding="utf-8").splitlines()) if row}
    service = {row["id"]: row for row in map(json.loads, Path(service_results).read_text(encoding="utf-8").splitlines()) if row}
    with open(output_path, "w", encoding="utf-8", newline="\n") as stream:
        for question_id in wanted:
            question = dataset[question_id]["input"]
            reply = llm.openrouter_structured(
                [
                    {"role": "system", "content": "당신은 범용 AI 어시스턴트입니다. 사용자의 질문에 한국어로 답하세요. "
                     "JSON {\"answer\": 답, \"source\": 답의 출처}만 출력하세요."},
                    {"role": "user", "content": question},
                ],
                model=llm.DEFAULT_GENERATION_MODEL,
                response_model=ChatbotAnswer,
            )
            ours = service[question_id]
            record = {
                "id": question_id,
                "kind": dataset[question_id]["kind"],
                "question": question,
                "expected": dataset[question_id].get("expected"),
                "chatbot": reply.model_dump(),
                "service": {
                    "outcome": ours.get("outcome"),
                    "refusal_reason": ours.get("refusal_reason"),
                    "draft_items": [
                        {key: item.get(key) for key in ("verdict", "reason", "citations")}
                        for item in ours.get("draft_items") or []
                    ],
                },
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(question_id, reply.answer[:120])
    usage = llm.drain_usage()
    print("비용 $", round(sum(float(item.get("cost_usd") or 0) for item in usage), 5))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(*sys.argv[1:]))
