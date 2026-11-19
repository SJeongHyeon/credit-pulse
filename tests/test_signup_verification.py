from datetime import datetime, timedelta, timezone

from backend import auth


def test_verification_code_is_bound_to_the_normalized_email(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "s" * 32)

    digest = auth.signup_code_hmac("reviewer@gmail.com", "123456")

    assert auth.verify_signup_code("reviewer@gmail.com", "123456", digest) is True
    assert auth.verify_signup_code("other@gmail.com", "123456", digest) is False


def test_verification_code_requires_exactly_six_ascii_digits():
    assert auth.is_verification_code_format_valid("123456") is True
    assert auth.is_verification_code_format_valid("12345") is False
    assert auth.is_verification_code_format_valid("1234567") is False
    assert auth.is_verification_code_format_valid("１２３４５６") is False
    assert auth.is_verification_code_format_valid("12345a") is False


def test_only_gmail_and_googlemail_addresses_are_eligible_for_signup():
    assert auth.is_signup_gmail_address(" Reviewer@GMAIL.com ") is True
    assert auth.is_signup_gmail_address("reviewer@googlemail.com") is True
    assert auth.is_signup_gmail_address("reviewer@example.com") is False
    assert auth.is_signup_gmail_address("reviewer@gmail.com.example") is False


def test_verification_record_accepts_only_an_unexpired_unconsumed_matching_code(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "s" * 32)
    now = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    record = auth.SignupVerification(
        email="reviewer@gmail.com",
        code_hmac=auth.signup_code_hmac("reviewer@gmail.com", "123456"),
        expires_at=now + timedelta(minutes=10),
        failed_attempts=0,
        consumed=False,
    )

    assert auth.can_consume_signup_verification(record, "reviewer@gmail.com", "123456", now) is True
    assert auth.can_consume_signup_verification(
        record, "reviewer@gmail.com", "123456", now + timedelta(minutes=10)
    ) is False
    assert auth.can_consume_signup_verification(
        auth.SignupVerification(**{**record.__dict__, "consumed": True}),
        "reviewer@gmail.com",
        "123456",
        now,
    ) is False


def test_postgres_verification_repository_reports_resend_cooldown_from_update_result():
    class Cursor:
        def __init__(self, row):
            self.row = row

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return self

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, row):
            self.cursor_instance = Cursor(row)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return self.cursor_instance

    allowed = auth.PostgresVerificationRepository(lambda: Connection(("reviewer@gmail.com",)))
    blocked = auth.PostgresVerificationRepository(lambda: Connection(None))

    assert allowed.issue("reviewer@gmail.com", "a" * 64) is True
    assert blocked.issue("reviewer@gmail.com", "a" * 64) is False


def test_postgres_verification_repository_consumes_only_a_matching_active_code():
    class Cursor:
        def __init__(self, row):
            self.row = row

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return self

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self, row):
            self.cursor_instance = Cursor(row)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return self.cursor_instance

    consumed = auth.PostgresVerificationRepository(lambda: Connection(("reviewer@gmail.com",)))
    rejected = auth.PostgresVerificationRepository(lambda: Connection(None))

    assert consumed.consume("reviewer@gmail.com", "a" * 64) is True
    assert rejected.consume("reviewer@gmail.com", "a" * 64) is False


def test_postgres_verification_repository_counts_a_failed_active_attempt():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, *_args): return self
        def fetchone(self): return ("reviewer@gmail.com",)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    repository = auth.PostgresVerificationRepository(Connection)
    assert repository.record_failed_attempt("reviewer@gmail.com") is True


def test_failed_attempts_invalidate_the_code_without_deleting_the_record():
    statements = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, sql, *_args):
            statements.append(" ".join(sql.split()))
            return self
        def fetchone(self): return ("reviewer@gmail.com",)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    auth.PostgresVerificationRepository(Connection).record_failed_attempt("reviewer@gmail.com")

    joined = " ".join(statements)
    assert "UPDATE signup_verifications" in joined
    assert "failed_attempts < 5" in joined
    # 행을 지우면 sent_at도 사라져 60초 재발송 제한이 우회된다(EC-06).
    assert "DELETE" not in joined
