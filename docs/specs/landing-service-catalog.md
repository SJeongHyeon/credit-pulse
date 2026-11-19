# 첫 화면 보유 기업 목록: 서비스 코퍼스 기준

## Why

- 첫 화면의 100곳이라는 숫자와 실제 슬라이드 목록 16곳이 달라 사용자가 보유 기업 범위를 잘못 이해한다.
- 현재 서비스 코퍼스 `data/manifest.jsonl`에는 FY2025 사업보고서 기업 100곳이 있다. 키 없는 데모 검색 스냅샷 16곳과 구분한다.

## Goal

- 첫 화면의 숫자·기업 슬라이드·검색 목록을 서비스 코퍼스의 사용 가능한 **고유 기업 수**와 일치시킨다(현재 100곳).
- 새 기업을 manifest에 추가한 뒤 이미지를 빌드하면 수동으로 숫자를 고치지 않아도 목록과 숫자가 늘어난다.
- 데모 스냅샷·실제 검색 범위·동결 평가셋은 바꾸지 않는다.

## What

- 정상 수집된(`status=success`) 사업보고서(`report_type=annual`) 중 정정 전 문서(`superseded=true`)는 제외한다.
- 같은 회사가 여러 사업연도에 있으면 `corp_code` 하나를 한 곳으로 세고 가장 최근 사업연도·공시일을 슬라이드에 표시한다.
- manifest가 비어 있거나 코드·연도·공시일 형식이 잘못되면 잘못된 숫자를 배포하지 않고 생성 단계에서 실패한다.

## How

- `scripts/sync_landing_companies.py`가 `data/manifest.jsonl`에서 `backend/static/landing-data.js`를 생성하고 `backend/templates/login.html`의 보유 기업 수와 최신 사업연도를 맞춘다.
- Docker 이미지 빌드 때 이 스크립트를 실행한다. 저장소에서는 생성 결과를 커밋하고 `--check`로 manifest와 차이가 나면 CI에서 실패시킨다.
- 브라우저의 `hero-stats.js`는 지금처럼 `landing-data.js`의 목록 길이를 사용한다. 기업명은 `landing.js`의 `textContent`로만 출력한다.

## AC

- GIVEN 현재 서비스 manifest WHEN 동기화 THEN 목록·HTML에 100곳이 표시되고 모든 기업 정보가 manifest의 활성 사업보고서와 일치한다.
- GIVEN manifest에 새 회사 1곳이 추가됨 WHEN 다시 동기화 또는 이미지 빌드 THEN 목록·HTML이 함께 101곳으로 늘어난다.
- GIVEN 같은 회사의 새 사업연도 또는 정정 전 문서 WHEN 동기화 THEN 회사 수를 중복해서 늘리지 않고 최신 유효 문서를 표시한다.
- GIVEN 생성물이 manifest와 다름 WHEN `--check` THEN 실패한다.
