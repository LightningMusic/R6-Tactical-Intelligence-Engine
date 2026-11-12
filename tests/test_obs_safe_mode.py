"""Launching OBS must not stop on the "Safe Mode / Normal Mode" dialog.

The app ends OBS with TerminateProcess, which OBS reads as a crash and so
opens that dialog on the next launch -- and the dialog blocks the websocket,
so Connect hangs until someone clicks it. OBS 32 removed the
--disable-shutdown-check flag that used to suppress it, so the app clears
OBS's crash sentinel itself before launching (both the normal %APPDATA%
location and the stick's portable one).
"""
from pathlib import Path

from integration import obs_controller as oc


def _make_sentinel(base: Path) -> Path:
    """A .sentinel folder with a stale run marker in it, as OBS leaves behind."""
    sentinel = base / "obs-studio" / ".sentinel"
    sentinel.mkdir(parents=True, exist_ok=True)
    (sentinel / "run_abcdef0123").write_text("", encoding="utf-8")
    return sentinel


def test_clears_both_appdata_and_portable_sentinels(monkeypatch, tmp_path):
    appdata = tmp_path / "AppData"
    obs_root = tmp_path / "OBS-Studio"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(oc, "OBS_EXE_PATH", obs_root / "bin" / "64bit" / "obs64.exe")

    appdata_sentinel = _make_sentinel(appdata)
    portable_sentinel = _make_sentinel(obs_root / "config")

    oc._clear_obs_crash_sentinel()

    assert not appdata_sentinel.exists()
    assert not portable_sentinel.exists()


def test_is_a_no_op_and_never_raises_when_nothing_to_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(oc, "OBS_EXE_PATH", tmp_path / "OBS-Studio" / "bin" / "64bit" / "obs64.exe")
    # No .sentinel folders exist: must simply do nothing.
    oc._clear_obs_crash_sentinel()


def test_leaves_an_unrelated_obs_config_alone(monkeypatch, tmp_path):
    """Clearing the crash sentinel must not touch the rest of OBS's config."""
    appdata = tmp_path / "AppData"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(oc, "OBS_EXE_PATH", tmp_path / "OBS-Studio" / "bin" / "64bit" / "obs64.exe")

    _make_sentinel(appdata)
    keep = appdata / "obs-studio" / "global.ini"
    keep.write_text("real settings", encoding="utf-8")

    oc._clear_obs_crash_sentinel()

    assert keep.read_text(encoding="utf-8") == "real settings"
    assert not (appdata / "obs-studio" / ".sentinel").exists()
