# G2: 평가자가 `docker compose up` 한 줄로 띄울 수 있어야 합니다.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
# compose loader가 쓰는 표 생성·데모 계정 스크립트와 표 정의(db/03_llm_budget.sql)
COPY scripts/ ./scripts/
COPY db/ ./db/
RUN python scripts/sync_landing_companies.py
# BM25 토큰표(사전 등록 99번)는 운영과 같은 이 이미지 안에서 만든다. Windows의 kiwipiepy는 Linux와
# 토큰이 달라, 로컬에서 만든 표는 서버가 기준 구절 비교로 거부한다. 빌드가 약 3.5분 늘어난다(1 vCPU 215초).
# 검색 산출물(data/service/)이 없는 평가자 compose 빌드에서는 건너뛴다.
RUN if [ -f data/service/chunks.jsonl ]; then python -X utf8 scripts/build_bm25_tokens.py; fi

EXPOSE 8000
# Cloud Run은 https를 받아 http로 넘긴다. 프록시의 X-Forwarded-Proto를 믿어야 템플릿 링크가 https가 된다
# (안 그러면 브라우저가 http 스크립트·스타일을 혼합 콘텐츠로 막아 로그인 버튼이 동작하지 않음, 11/6 배포에서 겪음).
# 컨테이너에는 Cloud Run 앞단만 접근하므로 모든 프록시 주소를 믿어도 된다.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
