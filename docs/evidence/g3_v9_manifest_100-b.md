# FY2025 100사 v9 manifest·고유 청크 재생성

측정일: 2026-11-06. WBS v4 C3의 11월 9일 작업을 앞당겨 수행한 결과다.

## 입력과 설정

- D1에서 수집한 FY2025 연간 사업보고서 ZIP 100건만 사용했다. 로컬 `data/corpus/`에는 FY2024 원본 30건도 있으므로, 빌드 전 `data/manifest.100-b.jsonl`의 접수번호 100개만 별도 디렉터리에 연결했다.
- 100개 원본의 `metadata.json`과 ZIP SHA-256을 기존 manifest의 접수번호·회사코드·사업연도·`raw_sha256`과 대조했고 불일치가 없었다. 원본 파일은 수정하지 않았다.
- 파서 `dart_xml_v9`, `table_mode=fixed_window`, `max_chars=1000`, `overlap_chars=100`, `embedding_model=unselected`로 빌드했다. `unselected`는 이 단계에서 임베딩을 실행하거나 모델을 확정하지 않았다는 뜻이다. 이후 C4 적재에서 모델을 확정하면 설정을 명시해 코퍼스를 다시 빌드·검증해야 한다.
- 평가 리포트의 v9 실험 코퍼스(`data/experiments/b100-v9-fixed_window`, `corpus-ffc1830e50a11c57749b63b9`)와 이번 코퍼스는 **본문이 같다**(아래 '평가 코퍼스와의 관계'). 코퍼스 ID만 다르다.

## 실행과 검증

`data/corpus/`에 **위 100건만** 있는 작업 디렉터리에서 다음 명령을 실행한다. 압축되지 않은 전체 청크 JSONL은 로컬 산출물로 두고, 평가자가 다시 셀 수 있도록 동일 파일의 압축본을 `data/g3/`에 제공한다.

```powershell
python -X utf8 -m backend.rag.ingest build --corpus-dir data/corpus --manifest data/manifest.jsonl --chunks data/chunks.100-b-v9.jsonl --summary data/ingest_summary.100-b-v9.json --max-chars 1000 --overlap-chars 100 --embedding-model unselected --table-mode fixed_window
python -X utf8 -m backend.rag.ingest validate --manifest data/manifest.jsonl --chunks data/chunks.100-b-v9.jsonl --summary data/ingest_summary.100-b-v9.json
python -X utf8 -m backend.rag.ingest hashes --chunks data/chunks.100-b-v9.jsonl --output data/unique_chunk_hashes.100-b-v9.txt
```

| 항목 | 결과 |
|---|---:|
| 문서 성공 / 실패 | 100 / 0 |
| 파서 / 청킹 | `dart_xml_v9` / 1,000자 고정 창, 100자 겹침 |
| 전체 청크 / 고유 본문 SHA-256 | 31,967 / 31,953 |
| 중복 eligible 청크 / 근접중복 / 대체 문서 | 14 / 0 / 0 |
| `truncated=true` | 0건 |
| 원문 글자 수 합계 / 청크 보존 글자 수 합계 | 28,149,957 / 28,756,538 |
| 코퍼스 ID | `corpus-4867b25d560356df13ab7886` |

`chars_kept`에는 100자 겹침 구간이 반복 집계되므로 `chars_original`보다 클 수 있다. `truncated`는 원문 전체 글자 수와의 단순 대소 비교가 아니라, 보존 대상 블록이 청크에서 누락됐는지 검사한 값이다.

| 파일 | SHA-256 |
|---|---|
| `data/manifest.jsonl` | `510d4737d176b8c662f061dccc5ad40f5db173b03f8d1e29a2bc90d0dc895132` |
| 로컬 `data/chunks.100-b-v9.jsonl` | `bc3c0f72f6bd98720ca41c326d9abb555d76c786c3d781882b22c852ffbdc46d` |
| `data/unique_chunk_hashes.100-b-v9.txt` | `06a26f21943051d9ed165790255007c4aca3ccca520888ed662975445816140d` |

`ingest validate`는 문서·청크 스키마, SHA-256, 참조, 집계와 비밀값 검사를 통과했다. 이전 v6 산출물인 `data/manifest.100-b.jsonl`, `data/ingest_summary.100-b.json`, `data/unique_chunk_hashes.100-b.txt`는 비교용으로 남겼다.

## 평가자 독립 재집계 (11/11 보강)

- [100사 전체 청크 압축본](../../data/g3/chunks.100-b-v9.jsonl.gz)과 [검증 안내·체크섬](../../data/g3/README.md)을 GitHub에 제공한다. 압축 크기 13,692,615바이트, SHA-256 `78deabfcea8c0696a5f968736921fa27f719f6418fb5181dfd7e1df3a7d227a0`이다.
- 로컬 원본 ZIP 100건의 접수번호·회사코드·연도·수집시각·ZIP SHA-256을 커밋된 manifest와 100/100 대조한 뒤, 위 v9 설정으로 다시 빌드했다. 재생성 manifest SHA-256 `510d4737d176b8c662f061dccc5ad40f5db173b03f8d1e29a2bc90d0dc895132`와 청크 원본 SHA-256 `bc3c0f72f6bd98720ca41c326d9abb555d76c786c3d781882b22c852ffbdc46d`가 기존 기록과 일치했다. Windows 체크아웃의 manifest 줄바꿈은 달라질 수 있으므로 검증기는 LF로 정규화해 비교한다.
- 저장소 루트에서 `python -X utf8 scripts/verify_g3_bundle.py`를 실행하면 키·DB·원본 ZIP 없이 압축 무결성, 각 청크 본문 해시, 문서별 청크 수, eligible 고유 본문 해시 집합을 다시 계산한다. 결과: 문서 100건, 청크 31,967개, 고유 31,953개.
- `data/snapshot/chunks.jsonl.gz`는 16사·4,942청크의 데모 부분집합이라 이 100사 검증 입력으로 사용하지 않는다.

## 평가 코퍼스와의 관계 (11/6 확인)

평가(사전 등록 66번 이후 v2 50건)에 쓴 v9 실험 코퍼스와 이번 코퍼스를 대조했다.

| 항목 | 결과 |
|---|---|
| 문서(접수번호) 집합 | 100건 같음 |
| 원본 ZIP `raw_sha256` | 100/100 같음 |
| 청크 수 / 고유 본문 SHA-256 | 31,967 / 31,953으로 같음, 고유 해시 집합이 서로 완전히 같음(한쪽에만 있는 해시 0) |
| 청킹 설정(`dart_xml_v9`, `fixed_window`, 1,000/100자, `embedding_model=unselected`) | 같음 |
| 문서별 `collected_at` | 100건 모두 다름(실험 코퍼스 2026-11-01, 이번 2026-10-30) |

코퍼스 ID는 문서의 `collected_at`을 입력에 넣어 계산하므로(`backend/rag/ingest.py`의 `_corpus_id`) 수집 시각 기록만 달라도 ID가 달라진다. 원본·설정·청크 본문이 같으므로 평가 결과는 이 코퍼스에 그대로 적용된다. 데모 스냅샷은 이 커밋된 manifest(`corpus-4867b25d560356df13ab7886`)를 기준으로 다시 만든다.

## 원문 대조와 남은 확인

[v9 원문 대조 3건](d1_source_verification.100-b-v9.md)은 ZIP에서 읽은 문장과 새 청크의 문장이 모두 일치했다(자동 3/3). 새 v9 청크 ID에 대한 DART 화면 사람 확인은 아직 기록되지 않았다. 2026-11-02의 [v6 사람 확인](d1_source_verification.100-b.md)을 v9 확인으로 대신 표시하지 않는다.

이 변경은 manifest와 G3 증거만 갱신한다. 벡터 DB 적재·활성 코퍼스 전환·데모 스냅샷 교체는 포함하지 않으며, 새 manifest와 현재 DB·스냅샷의 코퍼스 ID가 달라질 수 있다. 팀장 검토 후 C4 적재와 함께 전환해야 한다.
