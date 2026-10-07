"""
Choosing which microphones to try when the one in use delivers only zeros. Teammates do nothing, so this has to
pick sensibly and never record something it shouldn't (a webcam hears the whole room, which in the esports room
means the teammates sitting next to you).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion"))

import mic_pick as mp                                           # noqa: E402

PC = [("default", "Default"),
      ("{1}", "Microphone (USB Live Camera audio)"),
      ("{2}", "Stereo Mix (Realtek Audio)"),
      ("{3}", "Microphone (Realtek High Definition Audio)"),
      ("{4}", "Headset Microphone (AWPRO H Wireless Chat)"),
      ("{5}", "CABLE Output (VB-Audio Virtual Cable)"),
      ("{6}", "Microphone (Yeti Stereo Microphone)"),
      ("{7}", "Microphone Array (Intel Smart Sound)"),
      ("{8}", "Line Out (NVIDIA Broadcast)")]


def names(devs):
    return [n for _, n in devs]


def test_a_headset_comes_before_other_microphones_and_the_default_comes_first_of_all():
    got = mp.candidates(PC, tried=["{9}"])
    assert got[0][0] == "default"
    assert names(got[1:]) == ["Headset Microphone (AWPRO H Wireless Chat)",
                              "Microphone (Realtek High Definition Audio)", "Microphone (Yeti Stereo Microphone)"]


def test_things_that_hear_the_room_or_carry_the_computers_own_sound_are_never_tried():
    got = names(mp.candidates(PC))
    for bad in ("Camera", "Stereo Mix", "CABLE", "Array", "Line Out", "Broadcast"):
        assert not any(bad.lower() in n.lower() for n in got), bad


def test_a_device_already_tried_is_not_tried_again():
    got = [i for i, _ in mp.candidates(PC, tried=["default", "{4}"])]
    assert "default" not in got and "{4}" not in got and "{3}" in got


def test_nothing_usable_gives_an_empty_list():
    assert mp.candidates([("{1}", "Microphone (USB Live Camera audio)")]) == []
    assert mp.candidates([]) == []


def test_a_headset_is_found_again_by_name_on_another_pc():
    other_pc = [("default", "Default"), ("{zzz}", "Headset Microphone (AWPRO H Wireless Chat)"), ("{yyy}", "Microphone (Realtek Audio)")]
    assert mp.find_by_name(other_pc, "Headset Microphone (AWPRO H Wireless Chat)") == ("{zzz}", "Headset Microphone (AWPRO H Wireless Chat)")
    assert mp.find_by_name(other_pc, "headset microphone (awpro h wireless chat)")[0] == "{zzz}"        # case doesn't matter
    assert mp.find_by_name(other_pc, "Microphone (Something Else)") is None
    assert mp.find_by_name(other_pc, "") is None


def test_windows_renumbering_the_same_device_still_matches():
    pc = [("default", "Default"), ("{a}", "Microphone (2- AWPRO H Wireless Chat)")]
    assert mp.find_by_name(pc, "Microphone (AWPRO H Wireless Chat)")[0] == "{a}"
    pc = [("default", "Default"), ("{a}", "Headset Microphone (AWPRO H Wireless Chat 2)")]
    assert mp.find_by_name(pc, "Headset Microphone (AWPRO H Wireless Chat)")[0] == "{a}"


def test_a_partial_name_never_resolves_to_the_system_default_by_accident():
    assert mp.find_by_name([("default", "Default Microphone Thing")], "Default") is None
    assert mp.find_by_name([("default", "Default")], "Default") == ("default", "Default")      # exact is fine
