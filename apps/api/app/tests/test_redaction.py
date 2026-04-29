from app.services.redaction import redact_inline


def test_redact_credit_card():
    r = redact_inline("My card is 4532 1488 0343 6467.")
    assert "[REDACTED_CC]" in r.text
    assert r.had_pii
    assert "CC" in r.categories


def test_redact_ssn():
    r = redact_inline("SSN 123-45-6789 please")
    assert "[REDACTED_SSN]" in r.text


def test_redact_email():
    r = redact_inline("Email me at john@example.com okay?")
    assert "[REDACTED_EMAIL]" in r.text


def test_payment_window_redacts_cvv_and_phone():
    r = redact_inline("555 and 415-555-0123", payment_window=True)
    assert "[REDACTED_CVV]" in r.text
    assert "[REDACTED_PHONE]" in r.text


def test_safe_text_unchanged():
    text = "Appointment at 3pm on Thursday sounds great."
    r = redact_inline(text)
    assert r.text == text
    assert not r.had_pii
