import os
import secrets

# 앱 기동(lifespan)이 AUTH_SECRET을 요구한다. 값을 저장소에 적지 않도록 실행마다 새로 만든다.
os.environ["AUTH_SECRET"] = secrets.token_urlsafe(48)
os.environ.pop("COOKIE_SECURE", None)
# 시험 중 앱 기동이 실제 실행기(DB 연결·스냅샷 풀기)를 만들지 않게 한다. 실행기 시험은 직접 조립한다.
os.environ["REVIEW_RUNTIME"] = "off"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_llm_keys(monkeypatch):
    """시험은 실제 API를 부르지 않는다. 셸에 키가 있어도 지운다(59번 주제 판정이 기본 켜짐).

    가짜 키가 필요한 시험은 시험 안에서 monkeypatch.setenv로 다시 넣는다.
    """

    # Langfuse 키도 지운다: 시험이 실제 트레이스를 보내지 않게(G6 어댑터는 키가 없으면 아무것도 하지 않음).
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "VOYAGE_API_KEY", "UPSTAGE_API_KEY",
                 "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "ANTHROPIC_API_KEY",
                 "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _restore_process_environment():
    """시험이 프로세스 환경을 바꿔도 다음 시험으로 새지 않게 되돌린다.

    CLI `main()`은 `load_dotenv`로 상위 폴더의 .env(DATABASE_URL 등)를 환경에 올린다. 되돌리지 않으면
    이후 앱 기동 시험이 로컬 DB에 붙어 임베딩 차원 검사에서 실패한다.
    """

    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _fresh_default_query_guard(monkeypatch):
    """런타임이 없을 때 쓰는 모듈 공용 한도(_DEFAULT_GUARD)를 시험마다 새로 만든다.

    분당 5회·하루 한도를 프로세스 메모리에 세므로, 그대로 두면 앞 시험의 질의가 뒤 시험의 한도를 깎는다.
    """

    from backend.api import reviews
    from backend.api.limits import QueryGuard

    monkeypatch.setattr(reviews, "_DEFAULT_GUARD", QueryGuard())
