from vocalflow_agent.pipeline.sentiment import SentimentTracker


def test_neutral_text_score_zero():
    t = SentimentTracker()
    assert t.score("thursday at three works") == 0.0


def test_negative_text_triggers_negative_score():
    t = SentimentTracker()
    assert t.score("this is ridiculous, I want a manager") < -0.2


def test_consecutive_negative_suggests_handoff():
    t = SentimentTracker()
    t.observe("this is unacceptable", 1000)
    assert not t.should_suggest_handoff
    t.observe("worst service ever", 2000)
    assert t.should_suggest_handoff


def test_positive_resets_counter():
    t = SentimentTracker()
    t.observe("this is unacceptable", 1000)
    t.observe("oh thanks that works", 2000)
    assert not t.should_suggest_handoff
