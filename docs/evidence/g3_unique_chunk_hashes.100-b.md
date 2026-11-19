# B-100 G3 고유 청크 해시 재현

측정일: 2026-11-02

WBS: W1.4 `manifest·G3 집계`

목적: 대용량 청크 본문을 Git에 넣지 않고도 G3 고유 청크 수를 재현할 수 있게 한다.

## 실행 명령

```powershell
python -m backend.rag.ingest hashes `
  --chunks data/chunks.100-b.jsonl `
  --output data/unique_chunk_hashes.100-b.txt
```

이 명령은 `near_dup=false`, `superseded=false`인 청크만 선택하고, 각 본문을 다시 SHA-256으로 계산해 기록된 `content_sha256`과 일치하는지 검사한다. 검사를 통과한 해시를 중복 제거하고 오름차순으로 저장한다.

## 결과

| 항목 | 결과 |
|---|---:|
| eligible 청크 | 33,996 |
| 고유 본문 SHA-256 | 32,467 |
| 중복 eligible 청크 | 1,529 |
| 원본 `chunks.100-b.jsonl` SHA-256 | `9da1d274e91cab63db962b51c0ca10791d89e30c33eb949b8493df3758e48f20` |
| 해시 목록 SHA-256 | `7feb2a2b0eaf83f33fe1517457dfe5df0bf1f960458fde96747eae185dcf78c0` |

`data/unique_chunk_hashes.100-b.txt`는 32,467줄이며 한 줄에 하나의 64자리 소문자 SHA-256이 있다. 이 값은 `data/ingest_summary.100-b.json`의 `unique_chunks=32467`과 일치한다.

## 남은 확인

- 이 목록은 결정적 용량 측정용 B-100 표본의 증거다. 최종 업종별 100사 목록이 확정되면 같은 명령으로 다시 생성한다.
- 자동 원문 대조 3건의 DART 화면 사람 확인은 별도 완료해야 한다.
- 긴 표 행 보존 검사 수정 반영 후 100건을 다시 빌드한 결과 `truncated=true`는 81건에서 1건으로 감소했다. 남은 1건은 KG케미칼(`20260323001613`)이며 원인을 추가 확인해야 한다.
