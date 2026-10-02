"""The host app must not mistake the R6Companion stick's OBS for its own."""
from types import SimpleNamespace

import pytest

from integration import obs_controller as oc


def _procs(*infos):
    return lambda attrs=None: [SimpleNamespace(info=i) for i in infos]


COMPANION = {"name": "obs64.exe", "exe": r"H:\R6Companion\OBS-Studio\bin\64bit\obs64.exe"}
OWN = {"name": "obs64.exe", "exe": r"F:\OBS-Studio\bin\64bit\obs64.exe"}


@pytest.mark.parametrize("running, expected", [
    ([], False),
    ([COMPANION], False),                       # only the stick's OBS is up: the host's own still has to be launched
    ([OWN], True),
    ([COMPANION, OWN], True),
    ([{"name": "obs64.exe", "exe": None}], True),   # path unreadable: assume it is ours, as before
    ([{"name": "notepad.exe", "exe": r"C:\Windows\notepad.exe"}], False),
])
def test_only_this_apps_obs_counts_as_running(monkeypatch, running, expected):
    monkeypatch.setattr(oc.psutil, "process_iter", _procs(*running))
    assert oc._obs_is_running() is expected
