"""Email template renderer unit tests — pure (no httpx)."""

from app.integrations.email import TEMPLATES, render


def test_appointment_confirmation_renders():
    subject, body = render(
        "appointment_confirmation",
        {
            "customer_name": "Sarah",
            "service": "Furnace tune-up",
            "start_at_pretty": "Thursday at 3 PM",
            "phone": "+15551112222",
            "business_phone": "+15553334444",
            "business_name": "Mike's HVAC",
        },
    )
    assert subject == "Your appointment is confirmed"
    assert "Sarah" in body
    assert "Furnace tune-up" in body
    assert "Thursday at 3 PM" in body


def test_unknown_template_uses_inline_subject_body():
    subject, body = render(
        "unknown_template",
        {"__subject__": "Hello", "__body__": "Goodbye"},
    )
    assert subject == "Hello"
    assert body == "Goodbye"


def test_all_templates_have_subject_and_body():
    for name, spec in TEMPLATES.items():
        assert "subject" in spec, f"{name} missing subject"
        assert "body" in spec, f"{name} missing body"
