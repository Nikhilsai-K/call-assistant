"""TTS sentence boundary must NOT flush on commas (causes mid-clause cuts)."""

from vocalflow_agent.pipeline.session import _has_sentence_boundary, _split_on_last_boundary


def test_period_is_a_boundary():
    assert _has_sentence_boundary("Got it.")


def test_question_mark_is_a_boundary():
    assert _has_sentence_boundary("Is that right?")


def test_comma_is_not_a_boundary():
    assert not _has_sentence_boundary("Yes, and then")


def test_unfinished_text_is_not_a_boundary():
    assert not _has_sentence_boundary("Let me check the calendar")


def test_split_keeps_remainder():
    head, tail = _split_on_last_boundary("Sure thing! And then we book?")
    assert head.endswith("?")
    assert tail == ""
