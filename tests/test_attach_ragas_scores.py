"""scripts/attach_ragas_scores.py: 질문 매칭과 '이미 있으면 건너뛰기'(Langfuse 없이 가짜 데이터로)."""
import json
from types import SimpleNamespace

import pytest

from scripts import attach_ragas_scores as ars

DATASET = [
    {"id": "1", "input": "오뚜기의 단기차입금은 얼마인가요?"},
    {"id": "2", "input": "오늘 서울 날씨 알려줘"},
    {"id": "3", "input": "LG화학의 사채 잔액은?"},
]
TRACES = [
    {"trace_id": "t-a", "question": "오뚜기의 단기차입금은 얼마인가요?"},
    {"trace_id": "t-b", "question": "오늘 서울 날씨 알려줘"},
    {"trace_id": "t-c", "question": "LG화학의 사채 잔액은?"},
]
RAGAS = [{"id": 1, "faithfulness": 1.0, "answer_relevancy": 0.3418}, {"id": 3, "faithfulness": 0.75, "answer_relevancy": 0.0}]


def test_questions_are_matched_literally_one_trace_per_question():
    assert ars.match_traces(DATASET, TRACES) == {1: "t-a", 2: "t-b", 3: "t-c"}


def test_a_question_that_differs_by_one_character_is_not_matched():
    """글자 그대로 맞춘다. 공백 하나만 달라도 같은 질문으로 보지 않는다(추측으로 고르지 않는다)."""

    traces = [*TRACES[:2], {"trace_id": "t-c", "question": "LG화학의 사채 잔액은? "}]
    with pytest.raises(ars.MatchError) as error:
        ars.match_traces(DATASET, traces)
    assert "찾지 못한 문항 1건: [3]" in str(error.value)


def test_two_traces_for_one_question_stop_with_both_ids_listed():
    traces = [*TRACES, {"trace_id": "t-a2", "question": "오뚜기의 단기차입금은 얼마인가요?"}]
    with pytest.raises(ars.MatchError) as error:
        ars.match_traces(DATASET, traces)
    assert "같은 질문의 트레이스가 2개인 문항 1: ['t-a', 't-a2']" in str(error.value)


def test_missing_and_duplicated_are_reported_together():
    traces = [TRACES[0], {"trace_id": "t-a2", "question": TRACES[0]["question"]}, TRACES[1]]
    with pytest.raises(ars.MatchError) as error:
        ars.match_traces(DATASET, traces)
    assert "찾지 못한 문항 1건: [3]" in str(error.value) and "문항 1: ['t-a', 't-a2']" in str(error.value)


def test_the_same_trace_listed_twice_is_one_trace():
    """페이지가 겹쳐 같은 트레이스가 두 번 읽혀도 서로 다른 트레이스로 세지 않는다."""

    assert ars.match_traces(DATASET, [*TRACES, TRACES[0]])[1] == "t-a"


def test_trace_input_is_read_from_a_json_string_or_a_dict():
    assert ars.trace_question(json.dumps({"question": "가나다"}, ensure_ascii=False)) == "가나다"
    assert ars.trace_question({"question": "가나다"}) == "가나다"
    assert ars.trace_question("질문만 있는 글") is None and ars.trace_question(None) is None
    assert ars.trace_question('{"other": 1}') is None


def test_planned_scores_are_two_per_question_with_fixed_names():
    send, skip = ars.planned_scores(RAGAS, {1: "t-a", 2: "t-b", 3: "t-c"}, {})

    assert skip == []
    assert send == [
        {"id": 1, "trace_id": "t-a", "name": "ragas_faithfulness", "value": 1.0},
        {"id": 1, "trace_id": "t-a", "name": "ragas_answer_relevancy", "value": 0.3418},
        {"id": 3, "trace_id": "t-c", "name": "ragas_faithfulness", "value": 0.75},
        {"id": 3, "trace_id": "t-c", "name": "ragas_answer_relevancy", "value": 0.0},
    ]


def test_scores_already_on_the_trace_are_skipped_so_a_second_run_sends_nothing():
    trace_ids = {1: "t-a", 2: "t-b", 3: "t-c"}
    partly = {"t-a": {"correct_citation", "ragas_faithfulness"}}
    send, skip = ars.planned_scores(RAGAS, trace_ids, partly)
    assert [(item["id"], item["name"]) for item in skip] == [(1, "ragas_faithfulness")]
    assert [(item["id"], item["name"]) for item in send] == [
        (1, "ragas_answer_relevancy"), (3, "ragas_faithfulness"), (3, "ragas_answer_relevancy")]

    done = {"t-a": {"ragas_faithfulness", "ragas_answer_relevancy"}, "t-c": {"ragas_faithfulness", "ragas_answer_relevancy"}}
    send, skip = ars.planned_scores(RAGAS, trace_ids, done)
    assert send == [] and len(skip) == 4


def test_a_missing_value_is_not_sent_and_an_unknown_question_stops():
    send, _ = ars.planned_scores([{"id": 1, "faithfulness": None, "answer_relevancy": 0.5}], {1: "t-a"}, {})
    assert [item["name"] for item in send] == ["ragas_answer_relevancy"]

    with pytest.raises(ars.MatchError) as error:
        ars.planned_scores([{"id": 9, "faithfulness": 1.0, "answer_relevancy": 1.0}], {1: "t-a"}, {})
    assert "[9]" in str(error.value)


class FakeLangfuse:
    """observations·scores 조회를 페이지로 나눠 돌려주는 가짜(cursor로 다음 페이지)."""

    def __init__(self, observation_pages, score_pages=None):
        self.observation_calls, self.score_calls = [], []
        pages = {"observations": observation_pages, "scores": score_pages or [[]]}

        def paged(kind, calls):
            def get_many(**kwargs):
                calls.append(kwargs)
                index = int(kwargs.get("cursor") or 0)
                rows = pages[kind][index]
                cursor = str(index + 1) if index + 1 < len(pages[kind]) else None
                return SimpleNamespace(data=rows, meta=SimpleNamespace(cursor=cursor))

            return get_many

        self.api = SimpleNamespace(
            observations=SimpleNamespace(get_many=paged("observations", self.observation_calls)),
            scores=SimpleNamespace(get_many=paged("scores", self.score_calls)),
        )


def _observation(trace_id, question, tags):
    return SimpleNamespace(trace_id=trace_id, input=json.dumps({"question": question}, ensure_ascii=False), tags=tags)


def test_fetch_traces_follows_the_cursor_and_keeps_only_this_runs_eval_traces():
    run = "w146_now_r1_multi_d3-55_v9_v2"
    client = FakeLangfuse([
        [_observation("t-a", "질문 1", ["eval", run]), _observation("t-x", "다른 것", ["service", "query"])],
        [_observation("t-b", "질문 2", ["eval", run]), _observation("t-y", "다른 회차", ["eval", "w140_new_r1"])],
    ])

    traces = ars.fetch_traces(client, run)

    assert traces == [{"trace_id": "t-a", "question": "질문 1"}, {"trace_id": "t-b", "question": "질문 2"}]
    assert [call.get("cursor") for call in client.observation_calls] == [None, "1"]
    assert all(call["session_id"] == run and call["is_root_observation"] is True for call in client.observation_calls)


def test_fetch_score_names_reads_every_page():
    client = FakeLangfuse([[]], score_pages=[[SimpleNamespace(name="correct_citation")],
                                             [SimpleNamespace(name="ragas_faithfulness")]])

    assert ars.fetch_score_names(client, "t-a") == ["correct_citation", "ragas_faithfulness"]
    assert [call["trace_id"] for call in client.score_calls] == ["t-a", "t-a"]
