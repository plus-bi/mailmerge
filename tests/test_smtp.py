import smtplib

from mailmerge.smtp import classify_smtp_error


def test_smtp_error_classification():
    assert classify_smtp_error(smtplib.SMTPDataError(451, b"later"))[0] == "transient"
    assert classify_smtp_error(smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")}))[0] == "permanent"
    assert classify_smtp_error(TimeoutError())[0] == "transient"

