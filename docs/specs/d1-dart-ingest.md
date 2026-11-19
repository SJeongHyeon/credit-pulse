# D1 Spec - OpenDART 수집·파싱·청킹·manifest

## Why

- **페르소나**: 기업여신 심사용 검색 품질을 책임지는 검색·데이터 담당자
- **상황**: FY2025 유가증권 사업보고서의 위험 근거를 검색 가능한 코퍼스로 만들어야 한다.
- **문제**:
  1. OpenDART 원문은 ZIP 안의 XML이라 그대로는 검색할 수 없다.
  2. 장·절·주석과 표의 위치를 잃으면 인용 검증과 재검색을 할 수 없다.
  3. 중복 청크를 그대로 세면 G3의 고유 청크 1만 개를 증명할 수 없다.
- **측정 지표**: 원문 대조 통과 수, 서로 다른 문서 수, 본문 SHA-256 기준 고유 청크 수, 파싱 실패 수

## Goal

- OpenDART `document.xml` ZIP을 로컬 스냅샷으로 수집한다.
- 기업개황 `induty_code`를 로컬에 캐시하고 금융업을 제외한 FY2025 100사 후보안을 seed `20261030`으로 재현한다.
- XML에서 II장, IV장, XI장, 연결재무제표 주석을 구조와 출처 메타데이터를 보존해 추출한다.
- 문단은 약 1,000자 경계를 지키고, 표는 제목·헤더·여러 행을 1,000자 이내로 묶어 청크를 생성한다.
- 문단·SECTION·표 행 경계를 찾지 못한 긴 본문은 1,000자 고정 크기와 100자 overlap으로 분할한다.
- 문서 manifest와 청크 JSONL을 원자적으로 기록한다.
- 같은 입력을 다시 실행해도 같은 청크 id와 해시가 나오게 한다.
- 최소 3개 문서를 원문과 대조할 수 있는 검증 명령과 테스트를 제공한다.
- 대상 문서·수집일·청킹 설정·임베딩 모델을 해시한 `corpus_id`로 산출물 버전을 고정한다.

**성공 기준**

- 파서·청커 AC 테스트 전부 통과
- 문서별 `rcept_no`, `corp_code`, 사업연도, 원본 SHA-256, 청크 수 기록
- 문서별 구분자 빈도와 의도하지 않은 잘림 여부 기록
- 청크별 `corpus_id`, 장·절·주석·표 제목·순서·본문 SHA-256, `near_dup`, `superseded` 기록
- G3 고유 청크 수가 `near_dup=false`이고 `superseded=false`인 본문 SHA-256 집합 크기와 일치
- API 키가 코드·manifest·로그에 기록되지 않음

**Out of Scope**

- 임베딩 생성과 pgvector 적재(D2 이후)
- BM25, 하이브리드 검색, 리랭킹(D3)
- 생성된 FY2025 100사 후보안의 팀 승인과 FY2024 비교 30사 최종 확정
- 본문 정정공시의 자동 대체 판정과 증분 스케줄링. 단, 원문 API에 본문이 없는 첨부 전용 공시는 후보에서 제외하고 팀이 확정한 `superseded` 표시는 스키마와 G3 집계에 반영한다.
- PDF OCR과 스캔 문서 파싱

## What

**Happy Path**

1. 키 소유자가 후보 공시 목록을 조회하거나 승인된 대상 CSV를 준비한다.
2. 수집기가 각 `rcept_no`의 원본 ZIP을 `data/corpus/<rcept_no>/document.zip`에 저장한다.
3. 원본 바이트 SHA-256과 비밀이 없는 metadata를 함께 저장한다.
4. 파서가 ZIP 안 XML을 읽고 대상 장·주석의 문단과 표 행을 순서대로 추출한다.
5. 중요한 회계정책 주석을 제외하고, 여러 회사에서 반복되는 상용구를 제거한다.
6. 청커가 문단 경계를 지키며 약 1,000자로 묶고 표는 제목·헤더·여러 행을 1,000자 이내로 묶는다.
7. manifest와 chunks JSONL, 통계 JSON을 원자적으로 기록한다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | API 키가 없음 | 네트워크 요청 전에 명확한 오류. 로컬 ZIP ingest는 계속 가능 |
| EC-02 | OpenDART가 오류 status를 반환 | 키와 URL을 출력하지 않고 status/message만 오류로 변환 |
| EC-03 | ZIP이 깨졌거나 XML이 없음 | 해당 문서를 실패로 기록하고 다른 문서 처리는 계속 |
| EC-04 | XML 선언과 실제 바이트 인코딩이 다름 | 선언을 믿지 않고 UTF-8 → CP949 순으로 실제 디코딩. 둘 다 실패하면 manifest 실패 |
| EC-05 | 주석 제목에 SECTION 태그가 없음 | 짧은 번호형 제목을 주석 경계로 인식 |
| EC-06 | 표 셀이 비어 있거나 병합됨 | 빈 셀은 버리고 남은 셀을 ` | `로 연결 |
| EC-07 | 한 문단·표 행이 1,000자 초과 | 문장·공백 경계로 결정적으로 분할하고 표 제목·헤더 문맥을 보존 |
| EC-08 | 같은 원본을 다시 처리 | 결정적 chunk id와 해시를 재생성하고 파일을 원자적으로 교체 |
| EC-09 | 3개사 이상에 같은 20자 이상 문단 | 공통 상용구로 제외 |
| EC-10 | 대상 장이 하나도 없음 | 빈 성공으로 숨기지 않고 파싱 실패로 기록 |
| EC-11 | 긴 본문에 문장·공백 경계가 없음 | 고정 1,000자 + 100자 overlap으로 분할 |
| EC-12 | 정정 전 원본으로 팀이 표시한 문서 | `superseded=true`를 청크까지 전파하고 G3 고유 청크 집계에서 제외 |
| EC-13 | 같은 문서·설정으로 재실행 | 같은 `corpus_id` 생성. 대상·수집일·청킹 설정·임베딩 모델이 달라지면 다른 ID |
| EC-14 | `[첨부정정]`·`[첨부추가]` 사업보고서 | 본문 원문 후보가 아니므로 목록 단계에서 제외. 원본 또는 `[기재정정]` 공시를 사용 |
| EC-15 | OpenDART `.xml`이 닫힘 태그 불일치 등 SGML/HTML형 문법을 포함 | 외부 엔터티와 네트워크를 끈 `lxml` 복구 모드로 구조를 복원하고, 복구 후에도 대상 본문이 없으면 실패 |
| EC-16 | 후보 회사의 기업개황 또는 `induty_code`가 없음 | 누락 회사를 표시하고 100사 후보 생성을 중단 |
| EC-17 | 필수 포함 4사가 금융업 제외 후보에 없음 | 누락 기업명을 표시하고 후보 생성을 중단 |

## How

**CLI**

```bash
# 후보 공시 목록 저장(키 필요)
python -m backend.rag.ingest list \
  --begin-date 20260301 --end-date 20260430 \
  --output data/candidates.jsonl

# 후보 기업개황·업종코드 캐시(키 필요, 중단 후 같은 명령으로 재개)
python -m backend.rag.ingest profiles \
  --candidates data/candidates.jsonl \
  --output data/company_profiles.jsonl \
  --request-interval-seconds 0.5

# 금융업 제외·업종 비례 FY2025 100사 후보안(키 불필요)
python -m backend.rag.ingest select \
  --candidates data/candidates.jsonl \
  --profiles data/company_profiles.jsonl \
  --output data/targets.proposed.100.csv \
  --summary data/targets.proposed.100.summary.json \
  --count 100 --seed 20261030

# 팀 승인 후 후보안을 data/targets.csv로 확정하고 원본 수집(키 필요)
python -m backend.rag.ingest collect \
  --targets data/targets.csv --corpus-dir data/corpus

# 로컬 ZIP/XML을 파싱·청킹(키 불필요)
python -m backend.rag.ingest build \
  --corpus-dir data/corpus \
  --manifest data/manifest.jsonl \
  --chunks data/chunks.jsonl \
  --summary data/ingest_summary.json \
  --max-chars 1000 --overlap-chars 100 \
  --embedding-model unselected

# 산출물 계약 검증
python -m backend.rag.ingest validate \
  --manifest data/manifest.jsonl \
  --chunks data/chunks.jsonl \
  --summary data/ingest_summary.json

# 원문 대조 3건 초안 생성(사람 확인 체크는 별도)
python -m backend.rag.ingest verify \
  --corpus-dir data/corpus \
  --chunks data/chunks.jsonl \
  --output docs/evidence/d1_source_verification.md
```

**환경변수**

- `DART_API_KEY`: OpenDART 키. `.env`에만 저장하고 CLI 인자로 받지 않는다.

**수집 파일 구조**

```text
data/corpus/<rcept_no>/
  document.zip
  metadata.json
```

`metadata.json`에는 키와 키가 들어간 URL을 저장하지 않는다.

**청크 스키마 핵심 필드**

```json
{
  "chunk_id": "결정적 문자열",
  "corpus_id": "corpus-결정적 해시",
  "rcept_no": "14자리 접수번호",
  "corp_code": "8자리 고유번호",
  "corp_name": "회사명",
  "fiscal_year": 2025,
  "chapter": "II | IV | XI | NOTES",
  "section_path": ["장", "절", "주석 제목"],
  "note_no": "33",
  "table_title": null,
  "ordinal": 1,
  "content": "본문",
  "content_sha256": "64자리",
  "is_table": false,
  "near_dup": false,
  "superseded": false
}
```

**제약**

- 본문 정규화는 Unicode NFKC와 연속 공백 1칸으로 통일한다.
- `corpus_id`는 정렬한 문서 식별자·원본 해시·수집일·`superseded`와 parser·청킹·임베딩 설정의 canonical JSON SHA-256으로 만든다.
- 비정형 XML 복구, 대상 장·재무제표 주석 종료 규칙, ZIP 재번호 매김 시 원본 표 그룹 보존과 B 표 그룹 청킹을 반영한 현재 파서 식별자는 `dart_xml_v7`이며, 파싱·청킹 규칙이 바뀌면 식별자를 올린다.
- manifest의 `truncated`는 의도적으로 제외한 회계정책·상용구가 아니라, 남겨야 할 파싱 블록 조각이 청크에서 사라졌을 때만 true다.
- manifest와 chunks JSONL은 임시 파일을 완성한 뒤 교체한다.
- 네트워크 재시도는 최대 3회다.
- 서비스 런타임은 OpenDART API를 호출하지 않는다. 이 CLI만 수집한다.
- `profiles`는 OpenDART 기업개황의 공개 `induty_code`만 캐시하며 API 키와 키가 포함된 URL은 저장하지 않는다.
- `select`는 회사별 최신 원본·기재정정 1건을 남기고 업종코드 앞 두 자리 64·65·66을 금융업으로 제외한다. 업종별 quota는 최대 나머지 방식, 회사 선택 순서는 seed와 `corp_code`의 SHA-256 순서다.
- 필수 포함 기업은 효성중공업, 한일철강, 한화오션, 대한조선이다. 생성 파일은 팀 승인 전까지 후보안이다.
- 원본 ZIP/XML이 있는 `data/corpus/`는 로컬 전용이며 저장소에 커밋하지 않는다.

## AC (Given-When-Then)

**AC-01 · 대상 장과 위치 보존**
- GIVEN: II·IV·XI장과 연결주석이 있는 XML
- WHEN: 파서를 실행
- THEN: 대상 장만 순서대로 나오고 각 블록에 `chapter`, `section_path`, `ordinal`이 있다.

**AC-02 · SECTION 없는 주석 제목**
- GIVEN: `33. 우발부채 및 약정사항`이 일반 문단 태그로 있는 XML
- WHEN: 파서를 실행
- THEN: 뒤 문단의 `note_no`가 `33`이고 section path에 제목이 포함된다.

**AC-03 · 표 그룹 청킹**
- GIVEN: 제목과 2개 행을 가진 표
- WHEN: 파싱·청킹
- THEN: 제목·헤더·여러 행이 1,000자 이내의 `is_table=true` 청크로 묶이고 모든 원문 행이 보존된다.

**AC-04 · 결정적 고유성**
- GIVEN: 같은 문서를 두 번 실행
- WHEN: chunks JSONL을 비교
- THEN: chunk id, ordinal, content SHA-256이 모두 같다.

**AC-05 · 상용구 제거**
- GIVEN: 서로 다른 3개 회사에 같은 20자 이상 문단
- WHEN: 코퍼스 정제
- THEN: 해당 문단은 모든 회사의 청크에서 제외된다.

**AC-06 · 회계정책 제외**
- GIVEN: 주석의 `중요한 회계정책` 절과 `우발부채` 절
- WHEN: 청킹
- THEN: 회계정책 절은 없고 우발부채 절은 남는다.

**AC-07 · manifest 집계**
- GIVEN: 정상 문서 2개와 실패 문서 1개
- WHEN: build 명령 실행
- THEN: 문서별 상태·청크 수가 기록되고 summary의 문서·고유 청크·실패 수가 실제 JSONL과 일치한다.

**AC-08 · 비밀 미기록**
- GIVEN: `DART_API_KEY`가 설정됨
- WHEN: list/collect/build 실행
- THEN: 생성 파일과 예외 메시지에 키가 포함되지 않는다.

**AC-09 · 실제 인코딩 우선**
- GIVEN: 선언은 EUC-KR이지만 실제 바이트는 UTF-8인 XML과 UTF-8·CP949 모두 아닌 바이트
- WHEN: 파서를 실행
- THEN: 전자는 한글이 보존되고 후자는 대체문자 본문 대신 디코딩 실패가 된다.

**AC-10 · 경계 없는 본문 fallback**
- GIVEN: 문장부호와 공백이 없는 2,500자 본문
- WHEN: max 1,000자, overlap 100자로 청킹
- THEN: 모든 청크는 1,000자 이하이고 인접 청크가 100자를 겹치며 보존 검사가 통과한다.

**AC-11 · 구분자와 잘림 기록**
- GIVEN: 줄바꿈·문단·SECTION·표 행이 있는 원문
- WHEN: build를 실행
- THEN: manifest에 각 구분자 수와 실제 보존 검사 결과인 `truncated`가 기록된다.

**AC-12 · corpus_id 결정성**
- GIVEN: 같은 원문·메타데이터·설정과, overlap 또는 임베딩 모델만 바꾼 설정
- WHEN: 각각 build를 실행
- THEN: 같은 설정은 같은 `corpus_id`, 다른 설정은 다른 `corpus_id`를 만든다.

**AC-13 · superseded G3 제외**
- GIVEN: 정상 문서와 `superseded=true` 문서
- WHEN: 코퍼스를 build·validate
- THEN: 두 문서의 청크는 보존되지만 G3 `unique_chunks`는 정상 문서의 eligible 청크만 센다.

**AC-14 · 첨부 전용 공시 제외**
- GIVEN: 원본·`[기재정정]`·`[첨부정정]`·`[첨부추가]` 사업보고서
- WHEN: 후보 목록을 조회
- THEN: 원본과 `[기재정정]`만 남고 첨부 전용 두 유형은 제외된다.

**AC-15 · 비정형 OpenDART XML 복구**
- GIVEN: 닫힘 태그가 일부 누락되거나 불일치하지만 대상 장과 문단이 남아 있는 OpenDART XML
- WHEN: 파서를 실행
- THEN: 안전한 복구 모드로 대상 본문을 추출하고 장·절·순서를 보존한다.

**AC-16 · 업종 층화 후보 재현**
- GIVEN: FY2025 후보와 모든 후보의 `induty_code`
- WHEN: count 100, seed `20261030`으로 선정
- THEN: 회사 100곳이 고유하고 금융업 64·65·66은 없으며 필수 4사가 포함되고, 입력 순서를 바꿔도 같은 CSV가 생성된다.
