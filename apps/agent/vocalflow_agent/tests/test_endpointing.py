from vocalflow_agent.pipeline.endpointing import EndpointState, is_endpoint


def test_hard_silence_ends_turn():
    s = EndpointState()
    s.update_text("thursday at three")
    s.update_silence(1200)
    assert is_endpoint(s)


def test_terminal_punctuation_with_short_silence_ends_turn():
    s = EndpointState()
    s.update_text("thursday at three.")
    s.update_silence(320)
    assert is_endpoint(s)


def test_hesitation_does_not_end_turn():
    s = EndpointState()
    s.update_text("thursday at three and")
    s.update_silence(500)
    assert not is_endpoint(s)


def test_empty_text_never_ends():
    s = EndpointState()
    s.update_silence(5000)
    assert not is_endpoint(s)


def test_lex_end_phrase():
    s = EndpointState()
    s.update_text("yes please")
    s.update_silence(400)
    assert is_endpoint(s)
