CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS corpus_versions (
    corpus_id TEXT PRIMARY KEY,
    parser TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    vector_dimension INTEGER NOT NULL CHECK (vector_dimension > 0),
    manifest_sha256 CHAR(64) NOT NULL,
    chunks_sha256 CHAR(64) NOT NULL,
    embeddings_sha256 CHAR(64) NOT NULL,
    documents_count INTEGER NOT NULL CHECK (documents_count >= 0),
    chunks_count INTEGER NOT NULL CHECK (chunks_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    rcept_no CHAR(14) PRIMARY KEY,
    corp_code CHAR(8) NOT NULL,
    corp_name VARCHAR(100) NOT NULL,
    fiscal_year SMALLINT NOT NULL CHECK (fiscal_year BETWEEN 2020 AND 2030),
    report_type VARCHAR(20) NOT NULL CHECK (
        report_type IN ('annual', 'quarterly', 'half', 'major_event')
    ),
    filed_at DATE NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'superseded')
    ),
    collected_at TIMESTAMPTZ NOT NULL,
    raw_sha256 CHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    corpus_id TEXT NOT NULL REFERENCES corpus_versions(corpus_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    rcept_no CHAR(14) NOT NULL REFERENCES documents(rcept_no),
    corp_code CHAR(8) NOT NULL,
    corp_name VARCHAR(100) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    chapter VARCHAR(10) NOT NULL CHECK (chapter IN ('II', 'IV', 'XI', 'NOTES')),
    section_path TEXT[] NOT NULL,
    note_no VARCHAR(20),
    table_title TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 1200),
    content_sha256 CHAR(64) NOT NULL,
    is_table BOOLEAN NOT NULL,
    is_numeric_heavy BOOLEAN NOT NULL,
    near_dup BOOLEAN NOT NULL DEFAULT false,
    superseded BOOLEAN NOT NULL DEFAULT false,
    bm25_tokens TEXT[] NOT NULL DEFAULT '{}',
    embedding VECTOR(1536) NOT NULL,
    PRIMARY KEY (corpus_id, chunk_id),
    UNIQUE (corpus_id, rcept_no, ordinal)
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(50) NOT NULL,
    is_demo BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    thread_id TEXT NOT NULL UNIQUE,
    corp_code CHAR(8) NOT NULL,
    fiscal_year SMALLINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'running', 'awaiting_approval', 'saving', 'closed')
    ),
    query_count SMALLINT NOT NULL DEFAULT 0 CHECK (query_count >= 0),  -- 누적 표시용. 한도는 하루 단위(05_daily_limits.sql)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_memos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id UUID NOT NULL REFERENCES reviews(id),
    approval_seq INTEGER NOT NULL CHECK (approval_seq > 0),
    items JSONB NOT NULL CHECK (jsonb_typeof(items) = 'array'),
    approved_by UUID NOT NULL REFERENCES users(id),
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reverted_at TIMESTAMPTZ,
    UNIQUE (review_id, approval_seq)
);

CREATE TABLE IF NOT EXISTS review_rule_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    rule_id TEXT NOT NULL,
    action VARCHAR(6) NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    source_memo_id UUID REFERENCES review_memos(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reviews_user_id_created_at
    ON reviews (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_memos_review_id_approved_at
    ON review_memos (review_id, approved_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_rule_events_user_id_created_at
    ON review_rule_events (user_id, created_at DESC);

-- 사용자별 심사 규칙은 LangGraph 범용 Store와 분리해, 사용자당 200건 상한을
-- 같은 DB 트랜잭션 안에서 보장한다. rule_id는 사용자 namespace 안에서만 유일하다.
CREATE TABLE IF NOT EXISTS user_memory_rules (
    user_id TEXT NOT NULL CHECK (char_length(btrim(user_id)) > 0),
    rule_id TEXT NOT NULL CHECK (char_length(btrim(rule_id)) > 0),
    item TEXT NOT NULL CHECK (char_length(btrim(item)) > 0),
    instruction TEXT NOT NULL CHECK (char_length(btrim(instruction)) > 0),
    source_memo_id TEXT NOT NULL CHECK (char_length(btrim(source_memo_id)) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_rules_user_id
    ON user_memory_rules (user_id);

CREATE INDEX IF NOT EXISTS idx_documents_corp_code_fiscal_year
    ON documents (corp_code, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_chunks_corp_code_fiscal_year
    ON chunks (corpus_id, corp_code, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_chunks_rcept_no_note_no
    ON chunks (corpus_id, rcept_no, note_no);
CREATE INDEX IF NOT EXISTS idx_chunks_content_sha256
    ON chunks (corpus_id, content_sha256);
-- 운영은 정확 검색(회사·연도 B-tree 뒤 전수 계산). HNSW는 필터 뒤 근사라 결과가 달라지고 약 230MB를 더 쓴다(ARCHITECTURE 결정 기록 11/7).
-- 이미 인덱스가 있는 DB에서도 이 파일을 다시 적용하면 빠지도록 DROP으로 둔다.
DROP INDEX IF EXISTS idx_chunks_embedding;
