"""Deterministic fake embeddings must be stable and correct dim."""

from vocalflow_worker.tasks.kb_index import EMBED_DIM, _embed


def test_fake_embeddings_are_deterministic(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "")
    a = _embed(["hello world"])
    b = _embed(["hello world"])
    assert a == b
    assert len(a[0]) == EMBED_DIM


def test_fake_embeddings_differ_per_input(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "")
    a, b = _embed(["hello world", "goodbye world"])
    assert a != b
