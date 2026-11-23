"""
Only spellings that can mean nothing but an operator are corrected; ordinary
English words that happen to resemble a name are left alone.
"""
import pytest

from analysis.operator_vocab import INITIAL_PROMPT, correct, initial_prompt


@pytest.mark.parametrize("heard, fixed", [
    ("yeager has his ads on the wall", "Jäger has his ads on the wall"),
    ("Dokebi is on the roof", "Dokkaebi is on the roof"),
    ("thorne placed one", "Thorn placed one"),
    ("capitan put a smoke", "Capitão put a smoke"),
    ("Montaigne shield up", "Montagne shield up"),
    ("watch wamay's gadget", "watch Wamai's gadget"),
    ("denaria is up there", "Denari is up there"),
])
def test_unmistakable_misspellings_become_the_operator(heard, fixed):
    assert correct(heard) == fixed


@pytest.mark.parametrize("text", [
    "I play with it and look back, good room",       # with / look / back / good / room
    "we were playing in there, said first",           # Ying / there / Kaid / Frost lookalikes
    "Warden is on the roof and Lion is on the ladder",  # already right
    "",
])
def test_ordinary_words_and_correct_names_are_untouched(text):
    assert correct(text) == text


def test_whole_words_only():
    assert correct("the yeagerish thing") == "the yeagerish thing"


def test_priming_prompt_names_our_vocabulary():
    p = initial_prompt()
    assert p is INITIAL_PROMPT and p
    # the operators most often misheard as common words, plus core jargon
    for term in ("Fuze", "Twitch", "Buck", "Thatcher", "defuser", "drone"):
        assert term in p


def test_priming_prompt_fits_whispers_prompt_window():
    # Whisper only keeps the last ~224 tokens of the prompt; a rough word count
    # keeps us comfortably inside that so the whole vocabulary actually biases.
    assert len(initial_prompt().split()) < 120
