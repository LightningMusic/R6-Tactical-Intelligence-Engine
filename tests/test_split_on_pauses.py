from integration.whisper_transcriber import split_on_pauses


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "probability": 0.9}


def test_long_pause_starts_a_new_line():
    seg = {"start": 0.0, "end": 6.0, "text": " Someone's in there. Rotate B.", "avg_logprob": -0.3,
           "words": [_w(" Someone's", 0.0, 0.5), _w(" in", 0.5, 0.7), _w(" there.", 0.7, 1.0),
                     _w(" Rotate", 4.0, 4.4), _w(" B.", 4.4, 4.8)]}
    out = split_on_pauses([seg])
    assert [s["text"] for s in out] == ["Someone's in there.", "Rotate B."]
    assert (out[1]["start"], out[1]["end"]) == (4.0, 4.8)
    assert out[1]["avg_logprob"] == -0.3


def test_close_words_stay_together():
    seg = {"start": 0.0, "end": 1.0, "text": " one two", "words": [_w(" one", 0.0, 0.4), _w(" two", 0.5, 0.9)]}
    assert split_on_pauses([seg]) == [{"start": 0.0, "end": 0.9, "text": "one two", "words": seg["words"]}]


def test_long_run_without_pauses_breaks_at_a_full_stop():
    words = [_w(f" w{i}{'.' if i == 30 else ''}", i * 0.5, i * 0.5 + 0.4) for i in range(40)]
    out = split_on_pauses([{"start": 0.0, "end": 20.0, "text": "x", "words": words}])
    assert len(out) == 2
    assert out[0]["text"].endswith("w30.")


def test_segment_without_words_passes_through():
    seg = {"start": 1.0, "end": 2.0, "text": "hi"}
    assert split_on_pauses([seg]) == [seg]
