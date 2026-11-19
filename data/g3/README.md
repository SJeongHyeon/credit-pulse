# G3 FY2025 100사 청크 검증 묶음

평가자가 원본 ZIP, DART API 키, DB 없이 **고유 청크 31,953개**를 직접 다시 셀 수 있도록 전체 청크를 압축해 넣었다. 16사 데모 스냅샷과는 다른 100사 전체 코퍼스다.

## 파일

| 파일 | 역할 |
|---|---|
| [chunks.100-b-v9.jsonl.gz](chunks.100-b-v9.jsonl.gz) | v9 전체 청크 31,967행. 압축 크기 13,692,615바이트 |
| [checksums.json](checksums.json) | 압축본·압축 해제본·manifest·고유 해시 목록 SHA-256과 기대 수치 |
| [../manifest.jsonl](../manifest.jsonl) | 동일 `corpus_id`의 FY2025 사업보고서 100건과 문서별 청크 수 |
| [../ingest_summary.100-b-v9.json](../ingest_summary.100-b-v9.json) | 파서·청킹 설정과 전체·고유 청크 수 |
| [../unique_chunk_hashes.100-b-v9.txt](../unique_chunk_hashes.100-b-v9.txt) | eligible 청크의 고유 본문 SHA-256 31,953개 |

## 직접 확인

저장소 루트에서 실행한다. 표준 라이브러리만 사용한다.

```powershell
python -X utf8 scripts/verify_g3_bundle.py
```

기대 출력: `documents: 100`, `chunks: 31967`, `unique_chunks: 31953`, `corpus_id: corpus-4867b25d560356df13ab7886`. 종료 코드가 0이 아니면 해시·본문·문서별 수치 중 무엇이 달라졌는지 출력한다.

검증기는 압축 파일 SHA-256을 확인하고, 압축을 순차적으로 풀면서 각 행의 본문을 NFKC·공백 정규화해 `content_sha256`을 다시 계산한다. `near_dup=false`와 `superseded=false`인 청크만 고유 본문 해시 집합에 넣고, 문서별 청크 수와 manifest를 대조한다. 마지막으로 압축 해제 원본 파일의 SHA-256과 고유 해시 목록을 비교한다. 청크의 원문 텍스트를 읽어 확인할 수도 있다.

재생성 설정과 원본 ZIP 대조 기록은 [G3 증거 문서](../../docs/evidence/g3_v9_manifest_100-b.md)에 있다. 전체 청크 JSONL 원본은 이 압축 파일을 풀면 얻을 수 있다. 압축본을 새로 만들거나 다른 `corpus_id`의 청크와 혼용하지 않는다.
