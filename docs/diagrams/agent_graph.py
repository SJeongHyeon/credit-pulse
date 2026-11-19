"""README의 에이전트 그래프 그림(docs/images/agent-graph.png)을 그린다. 채택 구성(사전 등록 104~115번) 기준.
거절 뉴스 참고 분기(118번, NEWS_SEARCH_ENABLED=1일 때만 켜짐)는 점선 칸으로 그린다.
선 규칙은 LangGraph의 draw_mermaid()와 같다: 조건부 edge(Supervisor가 골라서 가는 길)는 점선, 고정 edge는 실선.
재검색·다시 쓰기는 별도 화살표가 아니라 검색 지시·작성 지시 화살표에 횟수로 적는다(같은 edge를 다시 타는 것).

  python -X utf8 docs/diagrams/agent_graph.py     # matplotlib·koreanize-matplotlib가 깔린 파이썬으로 실행

그래프 구조가 바뀌면 이 파일의 글자와 상자를 고쳐 다시 그린다. 좌표는 가로 1360 × 세로 1540 기준(위가 0)이다.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import koreanize_matplotlib  # noqa: F401,E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "images" / "agent-graph.png"
W, H = 1360, 1540
NAVY, BLUE_EDGE, BLUE_FILL, BLUE_TEXT = "#2f3e6e", "#3f73b8", "#e6f0fa", "#1f5fa8"
GRAY_EDGE, GRAY_TEXT, INK = "#a9adb3", "#5f6368", "#202124"
AMBER_FILL, AMBER_EDGE, AMBER_TEXT = "#f8ecd9", "#c9954a", "#8a5a12"
BEIGE_FILL = "#f1eee8"

fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis("off")


def box(x, y, w, h, *, fill="white", edge=GRAY_EDGE, lw=1.3, dashed=False, radius=14):
    patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}", facecolor=fill,
                           edgecolor=edge, linewidth=lw, linestyle=(0, (6, 4)) if dashed else "solid")
    ax.add_patch(patch)


def text(x, y, value, *, size=15, color=INK, weight="normal", ha="left", va="center"):
    ax.text(x, y, value, fontsize=size, color=color, fontweight=weight, ha=ha, va=va)


def tag(x, y, label, *, color=GRAY_TEXT, edge=GRAY_EDGE, fill="white", width=None):
    width = width or (22 + 15 * len(label))
    box(x - width, y - 17, width, 34, fill=fill, edge=edge, lw=1.1, radius=17)
    text(x - width / 2, y, label, size=13, color=color, ha="center")


def arrow(x1, y1, x2, y2, *, color=INK, dashed=False, lw=1.5):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw, linestyle=(0, (5, 4)) if dashed else "solid",
                                shrinkA=0, shrinkB=0, mutation_scale=16))


def step(x, y, w, title, sub, label, *, llm=False):
    """서브에이전트 안의 한 단계. llm이면 파란 칸."""
    box(x, y, w, 76, fill=BLUE_FILL if llm else "white", edge=BLUE_EDGE if llm else GRAY_EDGE)
    text(x + 24, y + 24, title, size=17, color=BLUE_TEXT if llm else INK, weight="bold")
    text(x + 24, y + 56, sub, size=14, color=BLUE_TEXT if llm else GRAY_TEXT)
    tag(x + w - 12, y + 24, label, color=BLUE_TEXT if llm else GRAY_TEXT, edge=BLUE_EDGE if llm else GRAY_EDGE,
        fill=BLUE_FILL if llm else "white")


# ── 위: 질문 입력, 규칙 불러오기, 거절
box(80, 40, 240, 64, fill=NAVY, edge=NAVY, radius=32)
text(200, 72, "질문 입력", size=17, color="white", weight="bold", ha="center")
arrow(200, 104, 200, 142)
box(80, 144, 520, 96)
text(112, 176, "규칙 불러오기", size=17, weight="bold")
text(112, 214, "turn_start · load_rules(Store)", size=14, color=GRAY_TEXT)
tag(580, 176, "코드")
arrow(200, 240, 200, 298)

# 응답 반환(코드로는 END): 거절은 Supervisor가 정한다. 이 칸은 '거절 단계'가 아니라 응답이 나가는 종점이다(아래 '답변'과 짝).
box(960, 40, 320, 92, fill=BEIGE_FILL, edge=GRAY_EDGE, radius=46)
text(1120, 72, "응답 반환", size=17, weight="bold", ha="center")
text(1120, 108, "거절·되묻기 안내(+참고 뉴스)", size=14, color=GRAY_TEXT, ha="center")
# 거절 뉴스 참고(옵션, 기본 꺼짐): Supervisor가 거절을 정한 뒤, 업무 관련 입구 거절에만 뉴스 1회를 붙이고 끝난다.
# 실제 edge: supervisor -.-> news_search(조건부), news_search --> END(고정). Supervisor로 돌아오지 않는다.
box(620, 40, 300, 92, dashed=True)
text(640, 72, "뉴스 참고 첨부", size=17, weight="bold")
text(640, 108, "news_search · 네이버 1회 · 5건", size=14, color=GRAY_TEXT)
tag(908, 72, "도구")
arrow(770, 300, 770, 138, dashed=True)
text(786, 186, "거절 확정 뒤,", size=14, color=GRAY_TEXT)
text(786, 220, "업무 관련 입구 거절만", size=14, color=GRAY_TEXT)
text(786, 254, "(옵션 · 공시 근거 아님)", size=14, color=GRAY_TEXT)
arrow(920, 86, 958, 86)
arrow(1120, 300, 1120, 138, dashed=True)
text(1136, 204, "입구 거절 · 되묻기", size=14, color=GRAY_TEXT)
text(1136, 240, "근거 0건 거절", size=14, color=GRAY_TEXT)

# ── Supervisor
box(80, 300, 1200, 212, fill=BLUE_FILL, edge=BLUE_EDGE)
text(580, 336, "Supervisor", size=20, color=BLUE_TEXT, weight="bold", ha="center")
tag(860, 336, "갈림길에서 LLM", color=GRAY_TEXT, width=190)
text(680, 380, "입구: 코드 규칙 + 주제 분류 LLM(gpt-4o-mini). 범위 밖이면 여기서 거절", size=15, color=BLUE_TEXT, ha="center")
text(680, 416, "검색 뒤: decide_next(gpt-4o-mini)가 판단하고 guard_decision(코드)이 상한을 지킴. 재검색은 꺼 둠(140번)",
     size=15, color=BLUE_TEXT, ha="center")
text(680, 452, "결과가 정해진 방문(검색 전 · 검증 통과 · 검증 실패 · 검색 상한)은 코드가 정함",
     size=15, color=BLUE_TEXT, ha="center")
text(680, 488, "보고는 구조화 값만 받음(청크 본문 없음), LLM이 없으면 코드 규칙으로 대신", size=15, color=BLUE_TEXT, ha="center")

# ── Supervisor ↔ 서브에이전트
# 재검색·다시 쓰기는 따로 있는 길이 아니다. 같은 조건부 edge를 지시(search_query·writer_feedback)만 바꿔 다시 탄다.
arrow(180, 512, 180, 612, dashed=True)
text(194, 546, "1. 검색 지시", size=14, color=INK)
text(194, 582, "첫 검색 1회(재검색 끔)", size=14, color=GRAY_TEXT)
arrow(480, 612, 480, 514)
text(494, 562, "보고", size=14, color=GRAY_TEXT)
arrow(740, 512, 740, 612, dashed=True)
text(754, 546, "2. 작성 지시", size=14, color=INK)
text(754, 582, "다시 쓰기 1회 포함", size=14, color=GRAY_TEXT)
arrow(1040, 612, 1040, 514)
text(1054, 562, "보고", size=14, color=GRAY_TEXT)

# ── 검색 에이전트
box(80, 614, 520, 516)
text(340, 648, "검색 에이전트", size=18, weight="bold", ha="center")
text(340, 682, "search_agent", size=14, color=GRAY_TEXT, ha="center")
step(112, 706, 456, "하이브리드 검색", "retriever · 벡터 + BM25 + 리랭크", "검색")
arrow(340, 782, 340, 804)
step(112, 806, 456, "회사·연도 검사", "scorer", "코드")
arrow(340, 882, 340, 904)
step(112, 906, 456, "임계값 τ + 근거 채점", "grader · gemini-3.5-flash-lite", "코드+LLM", llm=True)
text(340, 1012, "첫 검색은 원래 질문으로, 주제 분류와 동시에 시작", size=14, color=GRAY_TEXT, ha="center")
text(340, 1048, "채점이 부족이어도 재검색 없음 · 부족 항목은 작성기에 알림", size=14, color=GRAY_TEXT, ha="center")
text(340, 1084, "보고: 충분 여부 · 부족 항목 · 개수 · 1위 점수", size=14, color=GRAY_TEXT, ha="center")

# ── 작성 에이전트
box(640, 614, 520, 516)
text(900, 648, "작성 에이전트", size=18, weight="bold", ha="center")
text(900, 682, "writer_agent · 첫 작성은 2번 동시에 실행(보통 + 엄격 지시)", size=14, color=GRAY_TEXT, ha="center")
step(672, 706, 456, "답 쓰기", "writer · deepseek-v4.1-flash", "LLM", llm=True)
arrow(900, 782, 900, 804)
step(672, 806, 456, "인용·숫자 검사", "verifier", "코드")
arrow(900, 882, 900, 904)
step(672, 906, 456, "보고 정리", "report · 초안 문장 제외", "코드")
# 초안 고르기는 서브그래프 안(report)이 아니라 부모 그래프의 어댑터 노드(writer_agent_node)가 한다.
text(900, 1006, "바깥 칸(writer_agent 노드)이 초안 2개 중 고름", size=14, color=GRAY_TEXT, ha="center")
text(900, 1038, "(보통 먼저. 실패했거나 엄격이 1초 넘게 먼저 통과하면 엄격)", size=14, color=GRAY_TEXT, ha="center")
text(900, 1070, "미리 실행: 채점이 끝나면 지시 전에 써 둠", size=14, color=GRAY_TEXT, ha="center")
text(900, 1102, "보고: 판정 · 검사 결과 · 실패 항목", size=14, color=GRAY_TEXT, ha="center")

# ── 승인 단계
arrow(1220, 512, 1220, 1196, dashed=True)
# 승인으로 가는 경우: 검증 통과, 다시 쓰기 뒤에도 검증 실패(확인 필요), 검색·다시 쓰기 상한 도달.
text(1206, 1164, "3. 승인 요청(검사 통과 · 또는 확인 필요로)", size=14, color=GRAY_TEXT, ha="right")
box(80, 1200, 1200, 160)
text(112, 1230, "승인 단계 · 재시작 뒤 같은 thread_id로 재개", size=14, color=GRAY_TEXT)
box(112, 1256, 300, 84)
text(136, 1282, "승인 준비", size=17, weight="bold")
text(136, 1316, "approval_prepare", size=14, color=GRAY_TEXT)
tag(400, 1282, "코드")
arrow(414, 1298, 450, 1298)
box(452, 1256, 360, 84, fill=AMBER_FILL, edge=AMBER_EDGE)
text(476, 1282, "사람 승인", size=17, color=AMBER_TEXT, weight="bold")
text(476, 1316, "human_approval · interrupt", size=14, color=AMBER_TEXT)
arrow(814, 1298, 850, 1298)
box(852, 1256, 240, 84)
text(876, 1282, "메모 저장", size=17, weight="bold")
text(876, 1316, "save_memo", size=14, color=GRAY_TEXT)
tag(1076, 1282, "코드")
arrow(1094, 1298, 1130, 1298)
box(1132, 1270, 132, 56, fill=NAVY, edge=NAVY, radius=28)
text(1198, 1298, "답변", size=17, color="white", weight="bold", ha="center")

# ── 범례
box(80, 1400, 48, 28, fill=BLUE_FILL, edge=BLUE_EDGE, radius=6)
text(140, 1414, "LLM 단계", size=14, color=GRAY_TEXT)
box(300, 1400, 48, 28, radius=6)
text(360, 1414, "코드 단계", size=14, color=GRAY_TEXT)
box(520, 1400, 48, 28, fill=AMBER_FILL, edge=AMBER_EDGE, radius=6)
text(580, 1414, "사람 승인", size=14, color=GRAY_TEXT)
box(760, 1400, 48, 28, dashed=True, radius=6)
text(820, 1414, "선택 기능(기본 꺼짐)", size=14, color=GRAY_TEXT)
ax.plot([80, 136], [1462, 1462], color=INK, lw=1.5)
text(148, 1462, "실선: 항상 가는 길(고정 edge)", size=14, color=GRAY_TEXT)
ax.plot([520, 576], [1462, 1462], color=INK, lw=1.5, linestyle=(0, (5, 4)))
text(588, 1462, "점선: Supervisor가 골라서 가는 길(조건부 edge)", size=14, color=GRAY_TEXT)
text(80, 1508, "번호(1·2·3)는 Supervisor가 결과를 받아 쓰는 순서. 미리 실행 때문에 검색·초안 작성은 지시보다 먼저 시작될 수 있음(입력이 같을 때만 그 결과를 씀)",
     size=13, color=GRAY_TEXT)

fig.savefig(OUT, dpi=100, facecolor="white")
print("저장:", OUT)
