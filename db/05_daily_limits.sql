-- SPEC §4 하루 단위 한도(2026-11-11): 질의 횟수와 LLM 비용을 (대상, 한국 날짜)별로 센다.
-- scope: user(사용자), review(심사 건), service(서비스 전체, scope_id는 'all').
-- 서버 재시작·재배포에도 이어지도록 프로세스 메모리가 아니라 DB에 둔다. 날짜가 바뀌면 새 행이 생겨 0부터 센다.
-- compose는 이 폴더를 이름 순서로 적용하고, 운영 DB(Supabase)는 scripts/setup_graph_tables.py가 같은 파일을 실행한다.
CREATE TABLE IF NOT EXISTS daily_usage (
    scope VARCHAR(10) NOT NULL CHECK (scope IN ('user', 'review', 'service')),
    scope_id TEXT NOT NULL CHECK (char_length(btrim(scope_id)) > 0),
    day DATE NOT NULL,
    queries INTEGER NOT NULL DEFAULT 0 CHECK (queries >= 0),
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_id, day)
);

-- 심사 건의 query_count는 누적 표시용으로만 남긴다. 평생 50회 상한(CHECK ... BETWEEN 0 AND 50)을 없앤다.
ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_query_count_check;
ALTER TABLE reviews ADD CONSTRAINT reviews_query_count_check CHECK (query_count >= 0);
