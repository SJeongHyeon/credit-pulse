import ssl

import pytest

from backend.services.email_sender import EmailDeliveryError, GmailSmtpSender


class FakeSmtp:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.calls.append(("close",))

    def starttls(self, context):
        assert isinstance(context, ssl.SSLContext)
        self.calls.append(("starttls",))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send_message", message["To"], message.get_content()))


def test_sender_rejects_missing_gmail_smtp_configuration(monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)

    with pytest.raises(EmailDeliveryError, match="SMTP 설정"):
        GmailSmtpSender.from_environment()


def test_sender_uses_starttls_then_authentication_before_sending_code(monkeypatch):
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "test-only-password")
    monkeypatch.setenv("SMTP_FROM", "sender@example.com")
    created = []

    def smtp_factory(host, port, timeout):
        smtp = FakeSmtp(host, port, timeout)
        created.append(smtp)
        return smtp

    sender = GmailSmtpSender.from_environment(smtp_factory=smtp_factory)
    sender.send_verification_code("recipient@gmail.com", "123456")

    smtp = created[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert [call[0] for call in smtp.calls] == ["starttls", "login", "send_message", "close"]
    assert smtp.calls[1][1] == "sender@example.com"
    assert smtp.calls[2][1] == "recipient@gmail.com"
    assert "123456" in smtp.calls[2][2]
