import subprocess
import time
from typing import Optional

import psutil
import obswebsocket
from obswebsocket import requests as obs_requests

from app.config import RECORDINGS_DIR, OBS_EXE_PATH, settings

# Add these constants near the top of obs_controller.py
SCENE_COMMS = "R6_Comms"    # Discord audio capture scene
SCENE_GAME  = "R6_Game"     # Game capture scene for streaming/recording

# 2026-09-11: per-person audio capture, option 3 from
# claude/per-person-audio-capture-options.md -- separates "you" from
# "everyone else" as two genuinely distinct audio tracks in the recording
# itself, instead of relying entirely on voice-similarity clustering to
# untangle one already-mixed Discord signal. Discord's mixed output stays
# on track 1 (unchanged); your own mic gets its own source and its own
# track. This does NOT get all 5 teammates separated on its own -- it
# just means your own voice no longer needs to be guessed at by
# clustering at all, and clustering only has to work out the remaining
# (up to 4) voices in the Discord track instead of all 5.
MIC_INPUT_NAME  = "My_Mic"
DISCORD_INPUT_NAME = "Discord_Audio"
DISCORD_TRACK   = 1
MIC_TRACK       = 2
OTHER_TRACK     = 3     # game / desktop audio / anything else: kept, but off the comms tracks

# In the recorded file the audio streams come out in track order, so with
# tracks 1-3 recorded: stream 0 = Discord (everyone else), 1 = your mic,
# 2 = everything else. Packaging reads this file to know the split is real
# (a recording made before routing ran has the same number of streams, but
# every one of them is the full mix).
TRACK_LAYOUT_FILE = RECORDINGS_DIR / "track_layouts.json"
TRACK_LAYOUT = {"team": 0, "self": 1, "other": 2}

# Application Audio Capture picks its target by "title:class:exe"; with
# priority 2 only the executable has to match, so Discord's changing window
# title (the channel you're in) doesn't matter.
_DISCORD_CAPTURE_SETTINGS = {"window": "Discord:Chrome_WidgetWin_1:Discord.exe", "priority": 2}


def record_track_layout(since_epoch: float, layout: dict, path=None) -> None:
    import json
    path = path or TRACK_LAYOUT_FILE
    try:
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        entries = []
    entries.append({"since": since_epoch, "layout": layout})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[-200:], indent=1), encoding="utf-8")


def track_layout_for(recording_start_epoch: float, path=None) -> Optional[dict]:
    """The track layout in force when a recording started, if routing had
    been confirmed for it (see OBSController.ensure_comms_tracks)."""
    import json
    path = path or TRACK_LAYOUT_FILE
    try:
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        return None
    # Routing is written just before StartRecord; OBS names the file a
    # moment later, so allow a little slack either side.
    best = None
    for e in entries:
        if e["since"] <= recording_start_epoch + 5 and (best is None or e["since"] > best["since"]):
            best = e
    if best is None or recording_start_epoch - best["since"] > 12 * 3600:
        return None
    return best.get("layout")

def _obs_is_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and "obs64" in proc.info["name"].lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


class OBSController:
    """
    Manages OBS Studio connection and recording lifecycle.
    Auto-launches OBS Portable from the USB if not already running.

    connect() rotates through every saved OBS profile (Settings -> OBS
    profiles -- one per PC this USB stick has been used on) trying each
    profile's host/port/password in turn, instead of requiring the
    correct profile to be hand-picked as "active" beforehand. The
    profile that was active last time is tried first (fast path when
    nothing changed); if that fails, every other saved profile is tried
    before giving up. Whichever profile actually connects is remembered
    as the new active profile, so the next launch on the same PC tries
    the right one first. "Failed to connect to OBS" is only reported
    once every saved profile has been tried and none worked.
    """
    # Add these constants near the top of obs_controller.py
    SCENE_COMMS = "R6_Comms"    # Discord audio capture scene
    SCENE_GAME  = "R6_Game"     # Game capture scene for streaming/recording
    LAUNCH_WAIT_SEC    = 12   # max seconds to wait for OBS to open
    CONNECT_ROUNDS      = 4   # full passes over every saved profile
    CONNECT_RETRY_WAIT  = 2   # seconds between rounds (lets OBS's own
                              # websocket server finish starting up --
                              # a wrong password fails instantly, so
                              # this only costs time when OBS itself
                              # genuinely isn't ready yet)

    def __init__(self) -> None:
        self._connected       = False
        self._client: Optional[obswebsocket.obsws] = None
        self._launched_by_us  = False


    def _require_client(self) -> obswebsocket.obsws:
        if not self._connected or self._client is None:
            raise RuntimeError("OBS client is not connected.")
        return self._client


    # =====================================================
    # LAUNCH
    # =====================================================

    def _launch_obs(self) -> bool:
        if not OBS_EXE_PATH.exists():
            print(f"[OBS] Not found at: {OBS_EXE_PATH}")
            return False

        print(f"[OBS] Launching: {OBS_EXE_PATH}")
        try:
            subprocess.Popen(
                [str(OBS_EXE_PATH)],   # ← remove --minimize-to-tray
                cwd=str(OBS_EXE_PATH.parent),
                # no CREATE_NO_WINDOW — let it show
            )
            self._launched_by_us = True
        except Exception as e:
            print(f"[OBS] Launch failed: {e}")
            return False

        print(f"[OBS] Waiting up to {self.LAUNCH_WAIT_SEC}s...")
        for i in range(self.LAUNCH_WAIT_SEC):
            time.sleep(1)
            if _obs_is_running():
                print(f"[OBS] Detected after {i+1}s. Waiting 4s for websocket...")
                time.sleep(4)
                return True

        print("[OBS] Never appeared.")
        return False

    # =====================================================
    # CONNECTION
    # =====================================================

    def connect(self) -> bool:
        if self._connected:
            return True

        # ── Ensure OBS is running ─────────────────────────────
        if not _obs_is_running():
            print("[OBS] OBS not running — attempting launch...")
            if not self._launch_obs():
                print("[OBS] Could not launch OBS.")
                return False
        else:
            print("[OBS] OBS already running.")

        # ── Build the list of credentials to try ──────────────
        # settings.get_obs_profiles() is the real source of truth (one
        # profile per PC this stick has been used on); the legacy single
        # obs_host/obs_port/obs_password keys are read here only as a
        # last-resort fallback if no profiles exist at all.
        profiles = settings.get_obs_profiles()
        if not profiles:
            profiles = [{
                "name":     "Default",
                "host":     settings.OBS_HOST,
                "port":     settings.OBS_PORT,
                "password": settings.OBS_PASSWORD,
            }]

        active_idx = int(settings.get("obs_active_profile") or 0)
        if not (0 <= active_idx < len(profiles)):
            active_idx = 0

        # Active profile first (fast path), then every other profile,
        # skipping exact host/port/password duplicates.
        order = [active_idx] + [i for i in range(len(profiles)) if i != active_idx]
        seen_creds = set()
        candidates = []  # list of (original_index, profile_dict)
        for i in order:
            p = profiles[i]
            creds = (
                str(p.get("host", "localhost")),
                int(p.get("port", 4455)),
                p.get("password", ""),
            )
            if creds in seen_creds:
                continue
            seen_creds.add(creds)
            candidates.append((i, p))

        print(f"[OBS] Will try {len(candidates)} saved profile(s): "
              f"{', '.join(p.get('name', f'Profile {i+1}') for i, p in candidates)}")

        # ── Connect: rotate through every profile, retrying the whole
        # rotation a few times in case OBS's websocket server just hasn't
        # finished starting up yet ────────────────────────────────────
        last_error = None
        for round_num in range(1, self.CONNECT_ROUNDS + 1):
            for idx, profile in candidates:
                name     = profile.get("name", f"Profile {idx + 1}")
                host     = str(profile.get("host", "localhost"))
                port     = int(profile.get("port", 4455))
                password = profile.get("password", "")
                try:
                    # Always create a fresh client — avoids stale connection state
                    self._client = obswebsocket.obsws(host, port, password)
                    self._client.connect()
                    self._connected = True

                    # Point OBS at the USB recordings folder
                    self._client.call(
                        obs_requests.SetRecordDirectory(
                            recordDirectory=str(RECORDINGS_DIR)
                        )
                    )
                    print(f"[OBS] Connected using profile '{name}'. "
                          f"Recording dir → {RECORDINGS_DIR}")

                    if idx != active_idx:
                        print(f"[OBS] '{name}' worked — making it the active "
                              f"profile so this PC connects on the first try "
                              f"next time.")
                        settings.set_obs_profiles(profiles, idx)
                        settings.save()

                    return True

                except Exception as e:
                    last_error = e
                    self._client = None
                    print(f"[OBS] Profile '{name}' failed: {e}")

            if round_num < self.CONNECT_ROUNDS:
                time.sleep(self.CONNECT_RETRY_WAIT)

        print(f"[OBS] Failed to connect to OBS. Tried {len(candidates)} saved "
              f"profile(s), none worked. Last error: {last_error}")
        return False

    def disconnect(self) -> None:
        if not self._connected or self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception as e:
            print(f"[OBS] Disconnect error: {e}")
        finally:
            self._connected = False
            self._client    = None

    @property
    def is_connected(self) -> bool:
        return self._connected


    def setup_scenes(self) -> bool:
        """
        Creates/verifies both OBS scenes and their sources.
        Safe to call multiple times — checks existence before creating.

        R6_Comms scene:
        - Application Audio Capture → Discord (track 1)
        - Your own microphone, as a separate source on track 2 (2026-09-11 --
          see MIC_INPUT_NAME comment near the top of this file)
        - Used during sessions for comms recording

        R6_Game scene:
        - Game Capture → Rainbow Six Siege
        - Desktop Audio output capture
        - Used for personal video recording or streaming
        """
        client = self._client
        if client is None:
            return False
        if not self._connected or self._client is None:
            print("[OBS] Not connected — cannot set up scenes.")
            return False

        try:
            # ── Get existing scenes ───────────────────────────────────
            scene_list = self._client.call(obs_requests.GetSceneList())
            existing_scenes = {
                s.get("sceneName", "")
                for s in (scene_list.getScenes() or [])
            }

            # ── Helper: get existing input names in a scene ───────────
            def get_scene_items(scene_name: str) -> set:
                try:
                    items_resp = client.call(
                        obs_requests.GetSceneItemList(sceneName=scene_name)
                    )
                    return {
                        item.get("sourceName", "")
                        for item in (items_resp.getSceneItems() or [])
                    }
                except Exception:
                    return set()
                

            # ── Create R6_Comms scene ─────────────────────────────────
            if SCENE_COMMS not in existing_scenes:
                self._client.call(
                    obs_requests.CreateScene(sceneName=SCENE_COMMS)
                )
                print(f"[OBS] Created scene: {SCENE_COMMS}")
            else:
                print(f"[OBS] Scene exists: {SCENE_COMMS}")

            # Add Discord audio source if not already present
            comms_items = get_scene_items(SCENE_COMMS)
            if "Discord_Audio" not in comms_items:
                try:
                    self._client.call(obs_requests.CreateInput(
                        sceneName=SCENE_COMMS,
                        inputName="Discord_Audio",
                        inputKind="wasapi_process_output_capture",
                        inputSettings={
                            "window": "Discord.exe",
                            "use_device_timing": True,
                        },
                        sceneItemEnabled=True,
                    ))
                    print(f"[OBS] Added Discord_Audio source to {SCENE_COMMS}")
                except Exception as e:
                    print(f"[OBS] Could not add Discord_Audio (may already exist globally): {e}")
                    # Try adding existing input to scene instead
                    try:
                        self._client.call(obs_requests.CreateSceneItem(
                            sceneName=SCENE_COMMS,
                            sourceName="Discord_Audio",
                            sceneItemEnabled=True,
                        ))
                        print(f"[OBS] Added existing Discord_Audio to {SCENE_COMMS}")
                    except Exception:
                        pass
            else:
                print(f"[OBS] Discord_Audio already in {SCENE_COMMS}")

            # Add your own mic as a SEPARATE source from Discord_Audio --
            # see the MIC_INPUT_NAME comment near the top of this file.
            # inputSettings={} defaults to OBS's currently-selected system
            # default microphone, same pattern already used for
            # Desktop_Audio below.
            if MIC_INPUT_NAME not in comms_items:
                try:
                    self._client.call(obs_requests.CreateInput(
                        sceneName=SCENE_COMMS,
                        inputName=MIC_INPUT_NAME,
                        inputKind="wasapi_input_capture",
                        inputSettings={},
                        sceneItemEnabled=True,
                    ))
                    print(f"[OBS] Added {MIC_INPUT_NAME} source to {SCENE_COMMS}")
                except Exception as e:
                    print(f"[OBS] Could not add {MIC_INPUT_NAME} (may already exist globally): {e}")
                    try:
                        self._client.call(obs_requests.CreateSceneItem(
                            sceneName=SCENE_COMMS,
                            sourceName=MIC_INPUT_NAME,
                            sceneItemEnabled=True,
                        ))
                        print(f"[OBS] Added existing {MIC_INPUT_NAME} to {SCENE_COMMS}")
                    except Exception:
                        pass
            else:
                print(f"[OBS] {MIC_INPUT_NAME} already in {SCENE_COMMS}")

            # Route Discord_Audio and My_Mic onto separate mixer tracks so
            # a multi-track recording keeps them as two distinct audio
            # streams in the file instead of blending them back together.
            self._configure_comms_audio_tracks()

            # ── Create R6_Game scene ──────────────────────────────────
            if SCENE_GAME not in existing_scenes:
                self._client.call(
                    obs_requests.CreateScene(sceneName=SCENE_GAME)
                )
                print(f"[OBS] Created scene: {SCENE_GAME}")
            else:
                print(f"[OBS] Scene exists: {SCENE_GAME}")

            game_items = get_scene_items(SCENE_GAME)

            # Add Game Capture source
            if "R6_Game_Capture" not in game_items:
                try:
                    self._client.call(obs_requests.CreateInput(
                        sceneName=SCENE_GAME,
                        inputName="R6_Game_Capture",
                        inputKind="game_capture",
                        inputSettings={
                            "capture_mode": "window",
                            "window":       "Rainbow Six Siege [RainbowSix.exe]",
                            "allow_transparency": False,
                        },
                        sceneItemEnabled=True,
                    ))
                    print(f"[OBS] Added R6_Game_Capture source to {SCENE_GAME}")
                except Exception as e:
                    print(f"[OBS] Could not add R6_Game_Capture: {e}")
            else:
                print(f"[OBS] R6_Game_Capture already in {SCENE_GAME}")

            # Add Desktop Audio source
            if "Desktop_Audio" not in game_items:
                try:
                    self._client.call(obs_requests.CreateInput(
                        sceneName=SCENE_GAME,
                        inputName="Desktop_Audio",
                        inputKind="wasapi_output_capture",
                        inputSettings={},
                        sceneItemEnabled=True,
                    ))
                    print(f"[OBS] Added Desktop_Audio source to {SCENE_GAME}")
                except Exception as e:
                    print(f"[OBS] Could not add Desktop_Audio (may exist globally): {e}")
                    try:
                        self._client.call(obs_requests.CreateSceneItem(
                            sceneName=SCENE_GAME,
                            sourceName="Desktop_Audio",
                            sceneItemEnabled=True,
                        ))
                        print(f"[OBS] Added existing Desktop_Audio to {SCENE_GAME}")
                    except Exception:
                        pass
            else:
                print(f"[OBS] Desktop_Audio already in {SCENE_GAME}")

            return True

        except Exception as e:
            print(f"[OBS] Scene setup error: {e}")
            print("[OBS] Create scenes manually in OBS if auto-setup fails.")
            return False

    

    def _configure_comms_audio_tracks(self) -> None:
        """
        2026-09-11: wires Discord_Audio -> track 1, My_Mic -> track 2, and
        switches the active OBS profile to Advanced output mode with both
        tracks 1+2 enabled for recording. That -- PLUS the recording
        container actually being Matroska (.mkv), which this deliberately
        does NOT set automatically (see below) -- is the full combination
        needed for OBS to mux both tracks into the recorded file as two
        separate audio streams, instead of flattening them back into one
        track on disk.

        IMPORTANT / honest limitation: there is no real OBS install
        reachable from this sandbox, so nothing here has been exercised
        against a live running OBS. Two things were at least checked
        against OBS/obs-websocket's own real source rather than memory:
        - SetInputAudioTracks's `inputAudioTracks` shape (string keys
          "1".."6" -> bool) was confirmed directly against
          obs-websocket's own RequestHandler_Inputs.cpp.
        - Output/Mode ("Simple"/"Advanced") and AdvOut/RecTracks (a plain
          bitmask int, bit0 = track 1) were confirmed directly against
          OBS Studio's own frontend source and have been stable config
          keys for years.

        Deliberately NOT auto-written: AdvOut/RecFormat2 (the recording
        container). Recent OBS versions default to newer "hybrid_mp4" /
        "hybrid_mov" containers, not the classic "mkv" string multi-track
        recording actually needs -- and the exact valid literal values
        genuinely differ across OBS versions. SetProfileParameter has no
        validation at all (it's a raw ini writer), so a wrong guess here
        wouldn't error, it would just silently write a container value
        OBS's recording logic might not handle the way intended -- and
        this setting controls whether your actual match recordings work
        at all. Given that stakes, this reads the current value and logs
        clear manual instructions instead of gambling on a version-specific
        string it can't verify. Everything else here is either read back
        via GetProfileParameter and logged, or (RecFormat2) read-only.

        Never raises -- every step is independently wrapped so a failure
        in one doesn't stop the rest of setup_scenes() from finishing.
        """
        client = self._client
        if client is None:
            return

        # ── Per-input track routing ───────────────────────────────
        def _route(input_name: str, track: int) -> None:
            try:
                tracks = {str(t): (t == track) for t in range(1, 7)}
                client.call(obs_requests.SetInputAudioTracks(
                    inputName=input_name,
                    inputAudioTracks=tracks,
                ))
                print(f"[OBS] Routed '{input_name}' to track {track} only.")
            except Exception as e:
                print(f"[OBS] Could not set audio track routing for "
                      f"'{input_name}': {e}")

        _route("Discord_Audio", DISCORD_TRACK)
        _route(MIC_INPUT_NAME, MIC_TRACK)

        # ── Profile output settings (unverified against real OBS --
        # see docstring above) ────────────────────────────────────
        def _set_and_verify(category: str, name: str, value: str) -> None:
            try:
                client.call(obs_requests.SetProfileParameter(
                    parameterCategory=category,
                    parameterName=name,
                    parameterValue=value,
                ))
                readback = client.call(obs_requests.GetProfileParameter(
                    parameterCategory=category,
                    parameterName=name,
                ))
                actual = None
                try:
                    actual = readback.datain.get("parameterValue")
                except Exception:
                    pass
                if actual == value:
                    print(f"[OBS] Confirmed profile setting {category}/{name} = {value}")
                else:
                    print(f"[OBS] WARNING: set {category}/{name} to {value} but "
                          f"OBS reports it as {actual!r} -- verify manually in "
                          f"Settings -> Output.")
            except Exception as e:
                print(f"[OBS] Could not set profile parameter "
                      f"{category}/{name}: {e} -- verify manually in "
                      f"Settings -> Output that Advanced/mkv with tracks "
                      f"1+2 is selected for recording.")

        # Advanced output mode is required to record more than one audio
        # track into a file at all -- Simple output mode has no per-track
        # selection.
        _set_and_verify("Output", "Mode", "Advanced")
        _set_and_verify("AdvOut", "RecType", "Standard")
        # RecTracks is a bitmask, bit0=track1 .. bit5=track6. Tracks 1+2 ->
        # binary 000011 -> 3. Safe to set even before the container below
        # is switched -- it just won't have a visible effect until then.
        _set_and_verify("AdvOut", "RecTracks", "3")

        # RecFormat2 (the recording container) is intentionally NOT
        # auto-written -- see this method's docstring for why. Read the
        # current value and tell you plainly what to check by hand.
        try:
            current = client.call(obs_requests.GetProfileParameter(
                parameterCategory="AdvOut",
                parameterName="RecFormat2",
            ))
            current_format = current.datain.get("parameterValue")
        except Exception as e:
            current_format = None
            print(f"[OBS] Could not read current recording format: {e}")

        if current_format and "mkv" in current_format.lower():
            print(f"[OBS] Recording format is '{current_format}' -- OK, "
                  f"supports multi-track recording.")
        else:
            print(f"[OBS] ACTION NEEDED: current recording format is "
                  f"'{current_format}'. For My_Mic (track {MIC_TRACK}) and "
                  f"Discord_Audio (track {DISCORD_TRACK}) to actually end up "
                  f"as two separate audio streams in the recorded file, "
                  f"open OBS -> Settings -> Output -> Recording and set "
                  f"'Recording Format' to Matroska (.mkv), then make sure "
                  f"both Track 1 and Track 2 are checked. This wasn't "
                  f"changed automatically because the correct value varies "
                  f"by OBS version and a wrong guess here risks breaking "
                  f"recording entirely -- this is a one-time manual check.")

    def start_comms_recording(self) -> bool:
        """Switch to comms scene and start recording."""
        if not self._connected or self._client is None:
            return False
        try:
            self._client.call(
                obs_requests.SetCurrentProgramScene(sceneName=SCENE_COMMS)
            )
            status = self._client.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                self._client.call(obs_requests.StartRecord())
                print(f"[OBS] Comms recording started (scene: {SCENE_COMMS})")
            return True
        except Exception as e:
            print(f"[OBS] Comms recording error: {e}")
            return False


    def start_game_recording(self) -> bool:
        """Switch to game scene and start recording (for personal/streaming use)."""
        if not self._connected or self._client is None:
            return False
        try:
            self._client.call(
                obs_requests.SetCurrentProgramScene(sceneName=SCENE_GAME)
            )
            status = self._client.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                self._client.call(obs_requests.StartRecord())
                print(f"[OBS] Game recording started (scene: {SCENE_GAME})")
            return True
        except Exception as e:
            print(f"[OBS] Game recording error: {e}")
            return False


    def start_streaming(self) -> bool:
        """Start Twitch stream using R6_Game scene."""
        if not self._connected or self._client is None:
            return False
        try:
            self._client.call(
                obs_requests.SetCurrentProgramScene(sceneName=SCENE_GAME)
            )
            self._client.call(obs_requests.StartStream())
            print("[OBS] Twitch stream started.")
            return True
        except Exception as e:
            print(f"[OBS] Stream start error: {e}")
            return False


    def stop_streaming(self) -> bool:
        if not self._connected or self._client is None:
            return False
        try:
            self._client.call(obs_requests.StopStream())
            print("[OBS] Stream stopped.")
            return True
        except Exception as e:
            print(f"[OBS] Stream stop error: {e}")
            return False


    def get_stream_status(self) -> dict:
        if not self._connected or self._client is None:
            return {"streaming": False, "recording": False}
        try:
            rec    = self._client.call(obs_requests.GetRecordStatus())
            stream = self._client.call(obs_requests.GetStreamStatus())
            return {
                "recording":  bool(rec.getOutputActive()),
                "streaming":  bool(stream.getOutputActive()),
            }
        except Exception:
            return {"streaming": False, "recording": False}
    # =====================================================
    # RECORDING
    # =====================================================

    def ensure_comms_tracks(self, scene_name: Optional[str] = None) -> bool:
        """
        Runs before every recording starts, so it never depends on anyone
        pressing "Set up scenes". Makes sure the recording scene has
        Discord's app audio and your own mic as separate sources, then routes
        EVERY audio source in OBS: Discord -> track 1 only, your mic -> track
        2 only, everything else (desktop audio, game, OBS's own global
        mic/desktop devices) -> track 3 only, and records tracks 1-3.

        Before this, OBS's default Desktop Audio and Mic/Aux sat on every
        track -- and Desktop Audio includes Discord's playback -- so the
        2026-09-29 recording's two tracks were both the whole conversation.

        Returns True only when both comms sources ended up alone on their
        tracks; the result is written to TRACK_LAYOUT_FILE either way so
        packaging knows whether to trust the split.
        """
        client = self._client
        if client is None or not self._connected:
            return False
        scene = scene_name or settings.OBS_SCENE_NAME
        started = time.time()
        ok = True

        def call(req):
            return client.call(req)

        # ── Sources in the recording scene ────────────────────────
        try:
            scene_items = {i.get("sourceName") for i in
                           (call(obs_requests.GetSceneItemList(sceneName=scene)).getSceneItems() or [])}
        except Exception:
            try:
                call(obs_requests.CreateScene(sceneName=scene))
                print(f"[OBS] Created scene: {scene}")
            except Exception as e:
                print(f"[OBS] Could not read or create scene '{scene}': {e}")
            scene_items = set()
        try:
            existing = {i.get("inputName"): i.get("inputKind") for i in
                        (call(obs_requests.GetInputList()).getInputs() or [])}
        except Exception as e:
            print(f"[OBS] Could not list inputs: {e}")
            existing = {}

        for name, kind, input_settings in (
            (DISCORD_INPUT_NAME, "wasapi_process_output_capture", _DISCORD_CAPTURE_SETTINGS),
            (MIC_INPUT_NAME, "wasapi_input_capture", {}),
        ):
            try:
                if name not in existing:
                    call(obs_requests.CreateInput(sceneName=scene, inputName=name, inputKind=kind,
                                                  inputSettings=input_settings, sceneItemEnabled=True))
                    existing[name] = kind
                    print(f"[OBS] Added {name} to {scene}.")
                elif name not in scene_items:
                    call(obs_requests.CreateSceneItem(sceneName=scene, sourceName=name, sceneItemEnabled=True))
                    print(f"[OBS] Added existing {name} to {scene}.")
                if name == DISCORD_INPUT_NAME:
                    # Older setups pointed this at the bare "Discord.exe",
                    # which Application Audio Capture doesn't match.
                    call(obs_requests.SetInputSettings(inputName=name, inputSettings=input_settings,
                                                       overlay=True))
            except Exception as e:
                ok = False
                print(f"[OBS] Could not set up {name}: {e}")

        # ── Track routing for every audio source ──────────────────
        try:
            special = call(obs_requests.GetSpecialInputs()).datain or {}
            for key in ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4"):
                if special.get(key):
                    existing.setdefault(special[key], "global")
        except Exception:
            pass
        for name in existing:
            track = {DISCORD_INPUT_NAME: DISCORD_TRACK, MIC_INPUT_NAME: MIC_TRACK}.get(name, OTHER_TRACK)
            try:
                call(obs_requests.SetInputAudioTracks(
                    inputName=name, inputAudioTracks={str(t): t == track for t in range(1, 7)}))
            except Exception as e:
                if name in (DISCORD_INPUT_NAME, MIC_INPUT_NAME):
                    ok = False
                    print(f"[OBS] Could not route {name} to track {track}: {e}")
                # anything else is most likely a video-only source: no audio to route

        # ── Record tracks 1-3 ─────────────────────────────────────
        try:
            mode = call(obs_requests.GetProfileParameter(parameterCategory="Output",
                                                         parameterName="Mode")).datain.get("parameterValue")
            if mode != "Advanced":
                call(obs_requests.SetProfileParameter(parameterCategory="Output", parameterName="Mode",
                                                      parameterValue="Advanced"))
            call(obs_requests.SetProfileParameter(parameterCategory="AdvOut", parameterName="RecTracks",
                                                  parameterValue="7"))
            got = call(obs_requests.GetProfileParameter(parameterCategory="AdvOut",
                                                        parameterName="RecTracks")).datain.get("parameterValue")
            if str(got) != "7":
                ok = False
                print(f"[OBS] Recording tracks setting reads back as {got!r}, not 7.")
        except Exception as e:
            ok = False
            print(f"[OBS] Could not set recording tracks: {e}")

        record_track_layout(started, TRACK_LAYOUT if ok else None)
        print("[OBS] Comms tracks ready: Discord -> track 1, your mic -> track 2, everything else -> track 3."
              if ok else "[OBS] Comms track setup incomplete -- this recording will be treated as one mixed track.")
        return ok

    def start_recording(self) -> bool:
        if not self._connected or self._client is None:
            print("[OBS] Not connected.")
            return False
        try:
            self._client.call(
                obs_requests.SetCurrentProgramScene(
                    sceneName=settings.OBS_SCENE_NAME
                )
            )
            status = self._client.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                self.ensure_comms_tracks()
                self._client.call(obs_requests.StartRecord())
                print(f"[OBS] Recording started.")
            else:
                print("[OBS] Already recording.")
            return True
        except Exception as e:
            print(f"[OBS] Error starting recording: {e}")
            return False

    def stop_recording(self) -> Optional[str]:
        if not self._connected or self._client is None:
            return None
        try:
            response = self._client.call(obs_requests.StopRecord())

            # Try all known path field names across obs-websocket versions
            save_path = None
            for getter in ["getOutputPath", "getOutputFilePath"]:
                try:
                    fn = getattr(response, getter, None)
                    if fn:
                        result = fn()
                        if result:
                            save_path = result
                            break
                except Exception:
                    pass

            if not save_path:
                try:
                    d = response.datain
                    save_path = (
                        d.get("outputPath")
                        or d.get("outputFilePath")
                        or d.get("output-path")
                    )
                except Exception:
                    pass

            if save_path:
                print(f"[OBS] Recording saved → {save_path}")
            else:
                # Fall back to latest file in recordings dir
                try:
                    files = sorted(
                        RECORDINGS_DIR.glob("*.mp4"),
                        key=lambda f: f.stat().st_mtime,
                        reverse=True,
                    )
                    if not files:
                        files = sorted(
                            RECORDINGS_DIR.glob("*.mkv"),
                            key=lambda f: f.stat().st_mtime,
                            reverse=True,
                        )
                    if files:
                        save_path = str(files[0])
                        print(f"[OBS] Path not returned — using latest file: {save_path}")
                    else:
                        print("[OBS] Warning: no recording file found in recordings folder.")
                except Exception as e:
                    print(f"[OBS] Fallback path detection failed: {e}")

            return save_path
        except Exception as e:
            print(f"[OBS] Error stopping recording: {e}")
            return None

    def get_active_recording_path(self) -> Optional[str]:
        """
        Best-effort path of the recording currently being written.

        obs-websocket only reports an output path when recording *stops*, so
        while a session is live the newest file in the recordings folder is
        the only way to identify it. That is enough for slicing each finished
        match's audio out mid-session, which is what lets a match be packaged
        and uploaded before you ever press stop.

        Note this needs OBS set to Matroska (.mkv): an MP4's index is only
        written when recording ends, so an in-progress MP4 generally cannot
        be read back. setup_scenes() already asks for .mkv for the separate
        mic/Discord tracks -- same setting, second reason.
        """
        try:
            files = sorted(
                list(RECORDINGS_DIR.glob("*.mkv")) + list(RECORDINGS_DIR.glob("*.mp4")),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            return str(files[0]) if files else None
        except Exception as e:
            print(f"[OBS] Could not resolve the active recording path: {e}")
            return None

    def get_recording_status(self) -> bool:
        if not self._connected or self._client is None:
            return False
        try:
            status = self._client.call(obs_requests.GetRecordStatus())
            return bool(status.getOutputActive())
        except Exception:
            return False
        
    def ensure_recording(self) -> bool:
        """
        Checks if OBS is still recording. If it stopped unexpectedly,
        attempts to restart it. Returns True if recording is active after check.
        """
        if not self._connected or self._client is None:
            return False
        try:
            status = self._client.call(obs_requests.GetRecordStatus())
            if status.getOutputActive():
                return True
            # Recording stopped — try to restart
            print("[OBS] Recording stopped unexpectedly — restarting...")
            self._client.call(obs_requests.StartRecord())
            time.sleep(1)
            status2 = self._client.call(obs_requests.GetRecordStatus())
            if status2.getOutputActive():
                print("[OBS] Recording restarted successfully.")
                return True
            print("[OBS] Could not restart recording.")
            return False
        except Exception as e:
            print(f"[OBS] ensure_recording error: {e}")
            # Try full reconnect
            try:
                self.disconnect()
                time.sleep(2)
                if self.connect():
                    self._client.call(obs_requests.StartRecord())
                    print("[OBS] Reconnected and restarted recording.")
                    return True
            except Exception as e2:
                print(f"[OBS] Reconnect failed: {e2}")
            return False