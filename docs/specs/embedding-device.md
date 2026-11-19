# Spec — 임베딩 장치 선택 옵션

## Why

- **페르소나**: 로컬에서 임베딩을 다시 만드는 팀원
- **상황**: `LocalEmbedder`의 기본 장치가 `cpu`이고 `embedding build`에는 장치를 고르는 옵션이 없다.
- **문제**: GPU가 있어도 쓸 수 없다. 이 PC의 bge-m3 8,759건에 1시간 7분이 걸렸다.
- **측정 지표**: 같은 입력의 초당 처리 건수, CPU 벡터와의 코사인

## Goal

- **해결 목표**: `embedding build --device cuda`로 GPU를 쓸 수 있게 한다.
- **성공 기준**: 옵션을 주지 않으면 config 해시와 산출물이 기존과 같다. 같은 입력의 GPU 벡터와 CPU 벡터의 코사인이 0.9999 이상이다.
- **Out of Scope**: `offline_eval`의 질의 임베딩 장치, 혼합 정밀도(fp16), 다중 GPU, 기존 산출물 재생성, Docker 이미지

## What

**Happy Path**

1. `--device cuda`로 `embedding build`를 실행한다.
2. 모델과 입력이 GPU로 올라가고, 결과 벡터는 기존 형식으로 저장된다.
3. metadata에 `device`가 남는다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | 옵션 없음 | `cpu`. config에 키를 추가하지 않아 해시가 그대로다 |
| EC-02 | `cuda`인데 GPU를 쓸 수 없음 | 모델을 읽기 전에 오류. 조용히 CPU로 바꾸지 않는다 |
| EC-03 | API 모델에 `cuda` 지정 | 오류. 장치를 쓰지 않는 모델에 옵션이 무시되면 기록이 틀어진다 |
| EC-04 | CPU로 시작한 작업을 `cuda`로 재개 | config 해시가 달라 기존 progress 오류로 거부. 한 파일에 두 장치의 벡터가 섞이지 않는다 |

## How

```text
python -m backend.rag.embedding build ... --device {cpu,cuda}
```

- `build_embeddings(device="cpu")`, `create_embedder(device="cpu")`로 전달한다.
- `device`가 `cpu`가 아닐 때만 `config["device"]`에 기록한다. 기존 `pooling`, `corp_codes`와 같은 방식이다.
- 벡터는 지금처럼 `float32`로 CPU 메모리에 옮겨 저장한다.
- GPU와 CPU 벡터는 부동소수점 수준에서 다를 수 있다. 측정을 비교할 때는 같은 장치의 산출물을 쓰거나 차이를 기록한다.

## AC (Given-When-Then)

**AC-DV-01 · 기본값 호환**
- GIVEN: 같은 입력
- WHEN: 옵션 없이 실행
- THEN: metadata에 `device`가 없고 config 해시가 `device="cpu"`를 명시한 실행과 같다

**AC-DV-02 · 장치 전달**
- GIVEN: `--device cuda`
- WHEN: CLI 실행
- THEN: `create_embedder`와 `LocalEmbedder`까지 `cuda`가 전달되고 metadata에 기록된다

**AC-DV-03 · GPU 없음**
- GIVEN: `torch.cuda.is_available()`가 False
- WHEN: `LocalEmbedder(device="cuda")`
- THEN: 모델을 읽지 않고 `EmbeddingBuildError`로 중단한다

**AC-DV-04 · API 모델 거부**
- GIVEN: Upstage 모델과 `cuda`
- WHEN: `create_embedder` 호출
- THEN: `EmbeddingBuildError`로 중단한다

**AC-DV-05 · 재개 차단**
- GIVEN: CPU로 만든 progress
- WHEN: 같은 출력에 `cuda`로 재개
- THEN: progress 오류로 중단하고 기존 파일을 바꾸지 않는다
