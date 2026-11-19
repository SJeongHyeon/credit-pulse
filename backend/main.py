"""FastAPI 진입점.

그래프(backend/graph), 검색(backend/rag), 로그인(backend/auth.py)은 각자 자리에서 만들고
여기서는 엔드포인트로 연결만 합니다. LLM 호출은 services/llm.py 한 곳에서만 합니다.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import psycopg
from psycopg.conninfo import make_conninfo

from backend import auth, db
from backend.api.reviews import queries_router, reviews_router
from backend.api.rules import rules_router
from backend.auth import auth_router, register_error_handlers, validate_auth_config
from backend.rag.vector_store import validate_database_embedding_dimension

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """인증 설정을 검사하고, DB가 연결된 환경에서만 임베딩 차원을 검사한다.

    AUTH_SECRET이 없으면 어떤 환경이든 요청을 받기 전에 중단한다.
    DATABASE_URL이 없으면 차원 검사를 건너뛰지만 /health는 503을 돌려준다.
    DB가 연결됐는데 EMBED_DIM과 컬럼 차원이 다르면 요청을 받기 전에 중단한다.
    """

    validate_auth_config()
    if database_url := os.getenv("DATABASE_URL"):
        try:
            validate_database_embedding_dimension(
                database_url=make_conninfo(database_url, connect_timeout=5)
            )
        except psycopg.OperationalError:
            # DB 장애 중에도 서버를 띄워 /health가 503을 알릴 수 있게 한다.
            logger.warning("DB 연결 실패로 시작 시 임베딩 차원 검사를 건너뜁니다")
    else:
        logger.warning("DATABASE_URL이 없어 임베딩 차원 검사를 건너뜁니다")
    runtime = _start_review_runtime()
    application.state.review_runtime = runtime
    try:
        yield
    finally:
        if runtime is not None:
            runtime.close()


def _start_review_runtime():
    """키·DB·검색 산출물이 모두 있으면 실제 심사 그래프를 한 번 조립한다(backend/api/runtime.py).

    없거나 조립에 실패하면 None: 서버는 뜨고, 질의·승인만 503 SERVICE_UNAVAILABLE_GRAPH를 준다.
    """
    from backend.api.runtime import build_review_runtime, missing_runtime_env

    if os.getenv("REVIEW_RUNTIME", "").strip().lower() == "off":
        logger.warning("REVIEW_RUNTIME=off: 심사 그래프를 띄우지 않습니다")
        return None
    missing = missing_runtime_env()
    if missing:
        logger.warning("심사 그래프를 띄우지 않습니다. 없는 설정: %s", ", ".join(missing))
        return None
    try:
        return build_review_runtime()
    except Exception:
        logger.exception("심사 그래프 조립에 실패해 질의·승인을 503으로 둡니다")
        return None


app = FastAPI(title="3차 프로젝트 API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(auth_router)
app.include_router(reviews_router)
app.include_router(queries_router)
app.include_router(rules_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"review_login_required": request.query_params.get("login_required") == "reviews"},
    )


@app.get("/reviews", include_in_schema=False)
def reviews_page(request: Request):
    headers = {"Cache-Control": "no-store"}
    try:
        auth.current_user_id(request.cookies.get(auth.SESSION_COOKIE_NAME))
    except auth.ApiError as exc:
        if exc.status_code != 401:
            raise
        return RedirectResponse("/?login_required=reviews", status_code=303, headers=headers)
    return templates.TemplateResponse(request=request, name="reviews.html", headers=headers)

@app.get("/approvals", include_in_schema=False)
def approvals_page(request: Request):
    # 쉘 HTML은 공개이고, 로그인 여부는 화면이 부르는 API(401)가 판단한다.
    return templates.TemplateResponse(request=request, name="approvals.html")


@app.get("/signup", include_in_schema=False)
def signup_page(request: Request):
    return templates.TemplateResponse(request=request, name="signup.html")  


@app.get("/reviews/{review_id}", include_in_schema=False)
def review_detail_page(request: Request, review_id: str):
    # 쉘 HTML은 공개이고, 로그인·존재 여부는 화면이 부르는 API(401·404)가 판단한다.
    return templates.TemplateResponse(request=request, name="review_detail.html")


@app.get("/health")
def health() -> dict:
    """평가자와 배포 환경이 서비스 상태를 확인하는 곳. 지우지 마세요."""
    try:
        db.check_database()
    except (db.DatabaseConfigError, psycopg.Error) as exc:
        raise auth.ApiError(503, "SERVICE_UNAVAILABLE_DB", "데이터베이스를 사용할 수 없습니다") from exc
    return {"status": "ok"}


# TODO: 도메인 엔드포인트를 여기에 추가하세요.
#   - 질의 엔드포인트: 로그인한 사용자 id로 thread를 정하고 그래프를 호출합니다
#   - 오류 응답 규약을 정합니다 (검색 결과 없음, 승인 대기 중, 한도 초과 등을 구분)
#   - 요청 ID, 사용 모델, 토큰 수, 지연 시간을 로그에 남깁니다
