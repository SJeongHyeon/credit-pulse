"""Gmail SMTP를 통한 가입 인증코드 발송 경계."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Callable, Protocol


class EmailDeliveryError(RuntimeError):
    """SMTP 설정 또는 전송에 실패했다."""


class SmtpConnection(Protocol):
    def __enter__(self) -> "SmtpConnection": ...

    def __exit__(self, *_args: object) -> None: ...

    def starttls(self, *, context: ssl.SSLContext) -> tuple[int, bytes]: ...

    def login(self, user: str, password: str) -> tuple[int, bytes]: ...

    def send_message(self, msg: EmailMessage) -> dict[str, tuple[int, bytes]]: ...


SmtpFactory = Callable[[str, int, float], SmtpConnection]


class GmailSmtpSender:
    def __init__(self, username: str, app_password: str, from_address: str, smtp_factory: SmtpFactory):
        self._username = username
        self._app_password = app_password
        self._from_address = from_address
        self._smtp_factory = smtp_factory

    @classmethod
    def from_environment(cls, *, smtp_factory: SmtpFactory = smtplib.SMTP) -> "GmailSmtpSender":
        username = os.getenv("SMTP_USERNAME", "")
        app_password = os.getenv("SMTP_APP_PASSWORD", "")
        from_address = os.getenv("SMTP_FROM", "")
        if not username or not app_password or not from_address:
            raise EmailDeliveryError("SMTP 설정이 없습니다")
        return cls(username, app_password, from_address, smtp_factory)

    def send_verification_code(self, to_email: str, code: str) -> None:
        message = EmailMessage()
        message["Subject"] = "CREDIT PULSE 회원가입 인증코드"
        message["From"] = self._from_address
        message["To"] = to_email
        message.set_content(f"회원가입 인증코드: {code}\n10분 안에 입력하세요.")
        try:
            with self._smtp_factory("smtp.gmail.com", 587, 10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._username, self._app_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("인증 메일을 보낼 수 없습니다") from exc
