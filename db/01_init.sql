-- 컨테이너가 처음 만들어질 때 한 번 실행됩니다 (db/ 아래 .sql은 이름 순서대로).
-- 스냅샷 적재(스키마와 데이터)는 02_schema.sql, 03_snapshot.sql 식으로 추가하세요.
CREATE EXTENSION IF NOT EXISTS vector;
