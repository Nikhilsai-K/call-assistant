"""KB ingestion: text extraction + crawl helpers (network-free)."""

from unittest.mock import MagicMock

from vocalflow_worker.tasks.kb_ingest import _extract


def test_extract_plain_text_fallback():
    title, body = _extract("text/plain", b"hello world\nmore\n")
    assert title == "Text document"
    assert "hello world" in body


def test_extract_pdf_uses_pypdf(monkeypatch):
    """Make a minimal valid PDF byte stream; pypdf either parses or errors;
    either way our function must not crash and must return strings."""
    fake_reader = MagicMock()
    fake_reader.metadata = {"/Title": "Spec PDF"}
    page = MagicMock()
    page.extract_text.return_value = "Some text"
    fake_reader.pages = [page]

    import vocalflow_worker.tasks.kb_ingest as mod

    monkeypatch.setattr(mod, "PdfReader", lambda _: fake_reader)
    title, body = _extract("application/pdf", b"%PDF-1.4 fake")
    assert title == "Spec PDF"
    assert "Some text" in body


def test_extract_handles_unicode_decoding():
    _title, body = _extract("text/plain", "café 🥐".encode())
    assert "café" in body
