"""
Discord voice capture for per-speaker audio.
Runs a lightweight Discord bot that joins the voice channel during sessions
and records each user's audio to a separate WAV file.

Setup (one time):
  1. Go to https://discord.com/developers/applications
  2. Create an application → Bot → copy token
  3. Enable: Server Members Intent, Voice States Intent
  4. Bot permissions: Connect, Speak, Use Voice Activity
  5. Invite bot to your server with the generated OAuth2 URL
  6. Set discord_bot_token and discord_channel_id in settings

The bot records silently — it does not speak or react.
Per-user files are saved to data/transcripts/discord_audio/
"""

import sys
import io
import os
import time
import struct
import wave
import threading
from pathlib import Path
from typing import Optional, Callable

from app.config import TRANSCRIPTS_DIR

DISCORD_AUDIO_DIR = TRANSCRIPTS_DIR / "discord_audio"

# Discord voice uses Opus at 48000 Hz stereo
DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS    = 2
DISCORD_SAMPLE_WIDTH = 2   # 16-bit


def _ensure_console() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


class DiscordCapture:
    """
    Records per-user voice audio from a Discord voice channel.
    Each speaker gets their own WAV file for individual transcription.
    """

    def __init__(self) -> None:
        self._client: Optional[object]   = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._session_dir: Optional[Path] = None
        self._user_buffers: dict[str, list[bytes]] = {}
        self._user_names:   dict[int, str]         = {}   # user_id → display name
        self._lock = threading.Lock()

    # ── Dependency check ──────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        try:
            import discord  # type: ignore[import-untyped]
            # Check for voice support (requires PyNaCl)
            import nacl   # type: ignore[import-untyped]
            return True
        except ImportError:
            return False

    @staticmethod
    def install_instructions() -> str:
        return (
            "Discord capture requires:\n"
            "  pip install discord.py[voice] PyNaCl\n"
            "Then set discord_bot_token and discord_channel_id in Settings."
        )

    # ── Session management ────────────────────────────────────

    def start_capture(
        self,
        bot_token: str,
        channel_id: int,
        session_name: str,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        if not bot_token or not channel_id:
            if log_callback:
                log_callback(
                    "[Discord] No bot token or channel ID configured. "
                    "Set them in Settings → Discord."
                )
            return False

        if not self.is_available():
            if log_callback:
                log_callback(
                    f"[Discord] {self.install_instructions()}"
                )
            return False

        DISCORD_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self._session_dir = DISCORD_AUDIO_DIR / session_name
        self._session_dir.mkdir(exist_ok=True)
        self._user_buffers = {}
        self._user_names   = {}
        self._running      = True

        def _run_bot() -> None:
            try:
                import asyncio
                import discord  # type: ignore[import-untyped]
                from discord.ext import commands  # type: ignore[import-untyped]

                intents = discord.Intents.default()
                intents.voice_states = True
                intents.members      = True

                bot = commands.Bot(command_prefix="!", intents=intents)

                @bot.event  # type: ignore[misc]
                async def on_ready() -> None:  # type: ignore[misc]
                    if log_callback:
                        log_callback(f"[Discord] Bot ready: {bot.user}")
                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        if log_callback:
                            log_callback(
                                f"[Discord] Channel {channel_id} not found. "
                                "Check discord_channel_id in Settings."
                            )
                        await bot.close()
                        return

                    # Cache member display names
                    for member in channel.members:
                        self._user_names[member.id] = member.display_name

                    vc = await channel.connect()
                    if log_callback:
                        log_callback(
                            f"[Discord] Joined channel: {channel.name} "
                            f"({len(channel.members)} members)"
                        )

                    sink = _PerUserSink(
                        self._user_buffers,
                        self._user_names,
                        self._lock,
                        log_callback,
                    )
                    vc.start_recording(sink, _on_recording_finished, self._session_dir)
                    self._client = bot

                    # Keep running until stop is called
                    while self._running:
                        await asyncio.sleep(1)

                    vc.stop_recording()
                    await vc.disconnect()
                    await bot.close()

                asyncio.run(bot.start(bot_token))

            except Exception as e:
                if log_callback:
                    log_callback(f"[Discord] Bot error: {e}")
                self._running = False

        self._thread = threading.Thread(
            target=_run_bot, daemon=True, name="DiscordCapture"
        )
        self._thread.start()

        if log_callback:
            log_callback("[Discord] Starting bot...")
        return True

    def stop_capture(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Path]:
        """
        Stops capture and saves per-user WAV files.
        Returns dict of {display_name: wav_path}.
        """
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

        if log_callback:
            log_callback("[Discord] Capture stopped. Saving audio files...")

        saved: dict[str, Path] = {}

        with self._lock:
            for user_key, audio_chunks in self._user_buffers.items():
                if not audio_chunks or self._session_dir is None:
                    continue

                # user_key is either "user_id" or "display_name"
                safe_name = user_key.replace(" ", "_").replace("/", "_")
                wav_path  = self._session_dir / f"{safe_name}.wav"

                try:
                    combined = b"".join(audio_chunks)
                    with wave.open(str(wav_path), "wb") as wf:
                        wf.setnchannels(DISCORD_CHANNELS)
                        wf.setsampwidth(DISCORD_SAMPLE_WIDTH)
                        wf.setframerate(DISCORD_SAMPLE_RATE)
                        wf.writeframes(combined)
                    saved[user_key] = wav_path
                    if log_callback:
                        mb = wav_path.stat().st_size / (1024 * 1024)
                        log_callback(f"[Discord] Saved: {wav_path.name} ({mb:.1f} MB)")
                except Exception as e:
                    if log_callback:
                        log_callback(f"[Discord] Save failed for {user_key}: {e}")

        return saved

    def get_user_names(self) -> dict[int, str]:
        return dict(self._user_names)


def _on_recording_finished(sink: object, channel: object) -> None:
    pass   # handled in stop_capture


class _PerUserSink:
    """
    discord.py AudioSink that routes each user's audio to separate buffer.
    """

    def __init__(
        self,
        user_buffers: dict[str, list[bytes]],
        user_names:   dict[int, str],
        lock: threading.Lock,
        log_callback: Optional[Callable[[str], None]],
    ) -> None:
        self._buffers      = user_buffers
        self._user_names   = user_names
        self._lock         = lock
        self._log_callback = log_callback

    def write(self, data: object, user: object) -> None:
        try:
            import discord  # type: ignore[import-untyped]
            if not isinstance(data, discord.VoiceData):
                return

            user_id = int(user.id)  # type: ignore[union-attr]
            name    = self._user_names.get(user_id, f"User_{user_id}")

            with self._lock:
                if name not in self._buffers:
                    self._buffers[name] = []
                    if self._log_callback:
                        self._log_callback(
                            f"[Discord] Recording started: {name}"
                        )
                self._buffers[name].append(bytes(data.data))
        except Exception:
            pass

    def cleanup(self) -> None:
        pass