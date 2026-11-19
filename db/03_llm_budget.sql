-- (2026-11-11부터 쓰지 않음) 예전 누적 LLM 비용 상한(25달러)의 표. 지금 한도는 하루 단위이고 05_daily_limits.sql의
-- daily_usage에 쌓는다. 이미 만들어진 DB의 표와 기록을 지우지 않으려고 파일은 남겨 둔다.
-- compose는 이 폴더를 이름 순서로 적용하고, 운영 DB(Supabase)는 scripts/setup_graph_tables.py가 같은 파일을 실행한다.
CREATE TABLE IF NOT EXISTS llm_budget (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    spent_usd NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO llm_budget (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
