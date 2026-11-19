# Spec — Gmail 인증코드 회원가입

## Why

- **페르소나**: Gmail 주소로 본인 인증을 받고 CREDIT PULSE 계정을 만들려는 기업여신 심사역.
- **상황**: 기존 가입 화면은 이메일·비밀번호만 받으며, 수신 주소의 소유를 증명하지 않는다.
- **문제**:
  1. 타인의 이메일로 계정을 만들 수 있다.
  2. 별도 메일 발송 서비스가 없으므로, 인증 메일을 실제로 보낼 경로가 없다.
  3. 인증코드가 만료·재전송·시도 횟수 제한 없이 쓰이면 무차별 대입과 반복 발송에 취약하다.
  4. 가입 화면에 비밀번호 확인 칸이 없어 오타를 가입 전에 확인할 수 없다.
  5. 비밀번호가 bcrypt 허용량인 UTF-8 72바이트를 넘으면 서버의 공통 400 문구만 보여 원인을 알기 어렵다.
- **측정 지표**: 올바른 코드가 있어야만 사용자 1개 생성, 만료·틀린 코드로 사용자 생성 0개, 같은 코드는 한 번만 사용, SMTP 오류에서 코드·비밀번호 노출 0건.

## Goal

- **해결 목표**: `@gmail.com` 또는 `@googlemail.com` 주소로 보낸 일회용 인증코드를 검증한 뒤에만 이메일·비밀번호 계정을 만든다.
- **성공 기준**:
  - `SIGNUP_ENABLED=1`과 SMTP 설정이 있을 때, 인증코드 요청은 202이고 올바른 6자리 코드는 10분 안에 한 번만 201 가입을 완료한다.
  - 코드가 틀리거나 만료됐거나 5회 실패하면 400 `VALIDATION_EMAIL_VERIFICATION`이며 사용자 행은 생성되지 않는다. 5회 실패한 코드는 이후 맞는 값이어도 쓸 수 없다.
  - 같은 이메일은 60초 안에 재발송할 수 없고 429 `RATE_LIMITED`를 반환한다.
  - 기존 계정 이메일에는 코드 발송·가입 여부를 구분하지 않는 202을 반환하며 사용자 정보는 노출하지 않는다.
  - 화면에서 두 비밀번호가 일치하지 않거나 확인 칸이 비면 가입 요청을 보내지 않고 오류를 표시한다.
  - 비밀번호 또는 확인 칸이 UTF-8 72바이트를 넘으면 가입 요청 0건과 길이 제한 전용 안내를 보장한다.
  - 가입 화면에서 입력한 닉네임이 로그인 후 랜딩·심사 건 목록·상세 헤더에 표시된다.
- **Out of Scope**: Google OAuth 로그인·자동가입, Google API/Client ID, 비 Gmail 주소, 비밀번호 재설정, 이메일 변경, 계정 연결, SMS, 대량 메일 발송, 운영 SMTP·DB 설정 실행.

## What

**Happy Path**

1. 사용자가 가입 화면에서 Gmail 주소를 입력하고 인증코드 발송을 요청한다.
2. 서버가 가입 기능과 SMTP 설정을 확인하고, 난수 기반 6자리 코드를 만든다.
3. 서버는 코드 원문 대신 `AUTH_SECRET` 기반 HMAC만 `signup_verifications`에 저장하고, Gmail SMTP STARTTLS로 코드를 보낸다.
4. 사용자가 닉네임을 입력하고 비밀번호를 확인 칸에 다시 입력한다. 두 비밀번호가 같으면 email·password·code·display_name을 가입 endpoint로 보낸다.
5. 서버는 email과 코드 HMAC을 상수 시간 비교하고, 성공 시 검증 레코드를 소비한 뒤 bcrypt 해시·`is_demo=false` 사용자 1개를 만든다.
6. 서버는 201을 반환한다. 로그인은 기존 `/api/v1/auth/login`으로 한다.

**Edge Cases**

| # | 상황 | 처리 방식 |
|---|---|---|
| EC-01 | `SIGNUP_ENABLED`가 정확히 `1`이 아님 | 코드 요청·가입 모두 403 `SIGNUP_DISABLED`; DB·SMTP 부작용 없음 |
| EC-02 | Gmail/Googlemail이 아닌 주소, 빈 값, 254자 초과 | 400 `VALIDATION_ERROR`; 발송·저장 없음 |
| EC-03 | SMTP 설정 누락 또는 SMTP/TLS/인증 오류 | 503 `SERVICE_UNAVAILABLE_EMAIL`; 코드 원문·앱 비밀번호·SMTP 상세 미노출 |
| EC-04 | 60초 안에 같은 주소 재발송 | 429 `RATE_LIMITED`; 기존 코드·만료 시각 유지 |
| EC-05 | 코드 만료(10분), 틀린 코드, 형식 오류 | 400 `VALIDATION_EMAIL_VERIFICATION`; 사용자 생성 없음 |
| EC-06 | 틀린 코드 5회 | 다섯 번째와 이후 요청은 400, 해당 코드는 무효화(맞는 코드도 거절); 검증 레코드는 지우지 않아 마지막 발송 60초 뒤에만 재발송 가능, 재발송 뒤 새 코드만 사용 가능 |
| EC-07 | 같은 코드를 두 번 제출 또는 동시 제출 | 첫 성공만 201; 나머지는 400; 사용자 1개만 존재 |
| EC-08 | 이미 가입한 이메일 | 코드 요청은 202(발송하지 않음); 가입 제출은 409 `CONFLICT_EMAIL_EXISTS`; 계정 존재 정보가 코드 요청에 노출되지 않음 |
| EC-09 | 비밀번호 확인이 비었거나 원래 비밀번호와 다름 | 화면에 오류를 표시하고 가입 요청을 보내지 않음; 확인 값은 서버로 보내지 않음 |
| EC-10 | 비밀번호 또는 확인 칸이 UTF-8 72바이트를 넘음 | 가입 요청 전에 길이 제한 전용 문구 표시. 글자당 바이트 수가 달라 공통 최대 글자 수나 HTML `maxlength`는 정하지 않음 |
| EC-11 | 닉네임이 공백뿐이거나 50자를 넘거나 제어 문자·줄 구분자를 포함함 | 화면에서 가입 요청을 막고, 직접 API 호출 시 400 `VALIDATION_ERROR`를 반환한다. 인증코드는 소비하지 않는다. |

## How

```
POST /api/v1/auth/signup/verification-codes
요청: { "email": string }
성공: 202
실패: 403 SIGNUP_DISABLED | 400 VALIDATION_ERROR | 429 RATE_LIMITED | 503 SERVICE_UNAVAILABLE_EMAIL

POST /api/v1/auth/signup
요청: { "email": string, "password": string, "verification_code": string, "display_name"?: string }
성공: 201 { "id": UUID, "email": string, "display_name": string, "is_demo": false }
실패: 400 VALIDATION_ERROR | 400 VALIDATION_EMAIL_VERIFICATION |
      403 SIGNUP_DISABLED | 409 CONFLICT_EMAIL_EXISTS | 503 SERVICE_UNAVAILABLE_EMAIL
```

- `db/04_signup_verifications.sql`에 `signup_verifications(email PK, code_hmac CHAR(64), expires_at, sent_at, failed_attempts)`를 만든다. 가입 성공 시 delete하고, 5회 실패하면 행은 두되 `failed_attempts`가 5에 닿아 코드를 소비할 수 없게 한다(재발송 60초 제한은 `sent_at`이 그대로 유지). SQL 조건으로 코드 소비와 실패 횟수 증가를 원자적으로 처리한다.
- 코드는 `secrets`로 생성한 6자리 숫자다. `hmac.new(AUTH_SECRET, f"{email}:{code}", sha256)`만 DB에 저장하며 원문은 SMTP 발송 후 메모리·로그에 남기지 않는다. 비교는 `hmac.compare_digest`를 쓴다.
- SMTP는 표준 라이브러리 `smtplib.SMTP("smtp.gmail.com", 587)`와 `starttls()`만 쓴다. 환경 변수는 `SMTP_USERNAME`, `SMTP_APP_PASSWORD`, `SMTP_FROM`이며 모두 필수다. 코드·환경 변수 값을 예시 파일·테스트 fixture·로그에 쓰지 않는다.
- `backend/services/email_sender.py`에 SMTP 경계를 두고, `backend/auth.py`는 발송 인터페이스를 의존성으로 주입한다. 테스트는 SMTP가 아닌 이 발송 인터페이스의 가짜 구현을 사용한다.
- 코드 발송 요청에는 이메일만 보낸다. 가입 화면에서는 닉네임과 비밀번호 확인이 필수이며, 비밀번호 확인 값은 서버에 보내지 않는다.
- 화면과 서버 모두 닉네임의 앞뒤 공백을 제거하고 1~50자를 확인한다. 기존 `users.display_name`을 사용하며 `display_name` 없이 가입 API를 호출하면 이메일의 `@` 앞부분을 최대 50자까지 사용한다.
- 두 비밀번호 입력란에는 영문·숫자 72자, 한글 24자 안내를 연결하고, 제출 시 `TextEncoder`로 두 입력의 바이트 길이를 검사한다. 72바이트 초과는 인증코드·입력값 공통 400 안내보다 먼저 길이 제한 문구를 표시한다. 서버의 72바이트 검증은 그대로 유지한다. 서버에 별도 최소 글자 수 정책은 없다(화면의 `required`만 빈 입력을 막는다).
- `SPEC.md`에는 endpoint, Gmail-only 정책, 상한, 오류 코드를 먼저 추가한다. 기존 `signup-api.md`와 실행 계획은 이 Spec 승인 후 대체한다.

## AC (Given-When-Then)

**AC-EMAIL-01 · Gmail 인증코드 발송**
- GIVEN: `SIGNUP_ENABLED=1`, 유효 SMTP 설정과 새 Gmail 주소
- WHEN: 코드 발송 endpoint를 호출
- THEN: 202, 발송기는 수신 주소·6자리 코드로 한 번 호출되고 DB에는 code HMAC·10분 만료만 남는다

**AC-EMAIL-02 · 인증 후 가입**
- GIVEN: 유효한 미사용 인증코드
- WHEN: 맞는 email·72바이트 이하 password·code로 가입
- THEN: 201, `is_demo=false` 사용자 1개가 생성되고 인증 레코드는 삭제된다

**AC-EMAIL-03 · 잘못된·만료된 코드 차단**
- GIVEN: 유효하지 않거나 10분을 지난 코드
- WHEN: 가입 요청
- THEN: 400 `VALIDATION_EMAIL_VERIFICATION`, 사용자·세션 쿠키가 생성되지 않는다

**AC-EMAIL-04 · 재발송·시도 상한**
- GIVEN: 같은 이메일의 최근 발송 또는 실패 5회 인증 레코드
- WHEN: 60초 안에 재발송하거나 다시 가입을 시도 (실패 5회 직후의 재발송 포함)
- THEN: 재발송은 429, 다섯 번째 이후 검증은 맞는 코드여도 400이며 각각 추가 이메일·사용자가 없다

**AC-EMAIL-05 · 비밀 비노출**
- GIVEN: 식별 가능한 비밀번호·인증코드·SMTP 앱 비밀번호
- WHEN: 성공·실패·SMTP 오류를 처리
- THEN: 응답과 로그에 세 값이 없다

**AC-EMAIL-06 · 비밀번호 확인**
- GIVEN: 가입 화면에서 인증코드와 비밀번호를 입력한 상태
- WHEN: 비밀번호 확인 칸이 비었거나 원래 비밀번호와 다르게 가입을 제출
- THEN: 일치 여부를 화면에 안내하고 가입 API는 호출하지 않는다. 두 값이 같으면 확인 값을 제외하고 가입 필드를 보낸다

**AC-EMAIL-07 · 비밀번호 바이트 제한 안내**
- GIVEN: 비밀번호·확인 칸과 유효한 인증코드를 입력한 상태
- WHEN: 영문 기본 문자 73자 또는 한글 25자처럼 어느 한 칸이 UTF-8 72바이트를 넘은 채 제출
- THEN: 가입 API 호출 없이 72바이트 제한과 영문·한글 입력 예시를 안내한다. 영문 기본 문자 72자·한글 24자는 길이만으로 차단하지 않는다

**AC-EMAIL-08 · 닉네임 표시**
- GIVEN: 앞뒤 공백이 있는 닉네임과 유효한 인증코드
- WHEN: 회원가입하고 로그인한다
- THEN: `/api/v1/auth/me`가 공백을 제거한 닉네임을 반환하고 랜딩·심사 건 목록·상세 헤더에 `닉네임님`이 표시된다

## 사용자 수행: Gmail 발송 설정

1. 전용 Gmail 발신 계정을 만들고 2단계 인증을 켠다.
2. 해당 계정에서 SMTP용 앱 비밀번호를 만든다.
3. 로컬 `.env`와 배포 환경에만 `SMTP_USERNAME`, `SMTP_APP_PASSWORD`, `SMTP_FROM`을 설정한다. 세 값은 저장소·문서·로그에 넣지 않는다.
4. 운영 DB에 `db/04_signup_verifications.sql`을 직접 적용한다.

성공 기준: 인증코드가 Gmail 수신함에 도착하고, 코드 검증 전에는 `users`에 새 행이 없으며, 맞는 코드를 한 번 제출했을 때만 201이 반환된다. Gmail SMTP는 `smtp.gmail.com`, TLS/STARTTLS 포트 587, 인증을 요구한다. [Gmail 공식 SMTP 설정](https://support.google.com/mail/answer/7104828) 앱 비밀번호는 2단계 인증이 필요하며 계정 비밀번호 변경 시 취소될 수 있다. [Google 공식 앱 비밀번호 안내](https://support.google.com/mail/answer/185833)
