"""
integration/discord_capture.py

Per-user voice capture via a Discord bot.
Uses discord.py[voice] with discord-ext-sinks for per-user WAV recording.

Install requirements:
    pip install "discord.py[voice]" discord-ext-sinks PyNaCl

Each user in the voice channel gets their own WAV file, which is then
transcribed individually by WhisperTranscriber for named speaker attribution.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app.config import TRANSCRIPTS_DIR


# ─────────────────────────────────────────────────────────────────────────────
# AVAILABILITY CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _check_deps() -> tuple[bool, str]:
    """Returns (available, reason_if_not)."""
    try:
        import discord  # noqa: F401
    except ImportError:
        return False, "discord.py not installed"

    try:
        import discord.ext.sinks  # noqa: F401  # type: ignore[import-untyped]
    except (ImportError, AttributeError):
        return False, "discord-ext-sinks not installed"

    try:
        import nacl  # noqa: F401
    except ImportError:
        return False, "PyNaCl not installed (required for voice)"

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD CAPTURE
# ─────────────────────────────────────────────────────────────────────────────

class DiscordCapture:
    """
    Connects a Discord bot to a voice channel and records each user
    to a separate WAV file using discord-ext-sinks.

    Usage:
        capture = DiscordCapture()
        capture.start_capture(token, channel_id, session_name, log_callback)
        # ... session runs ...
        user_files = capture.stop_capture(log_callback)
        # user_files: {display_name: Path(...wav)}
    """

    def __init__(self) -> None:
        self._bot_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._voice_client: Optional[object] = None   # discord.VoiceClient
        self._bot: Optional[object] = None            # discord.Client
        self._output_dir: Optional[Path] = None
        self._session_name: str = ""
        self._user_names: dict[int, str] = {}         # user_id → display_name
        self._running: bool = False

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        ok, _ = _check_deps()
        return ok

    @staticmethod
    def install_instructions() -> str:
        _, reason = _check_deps()
        return (
            f"Discord capture unavailable: {reason}\n\n"
            "To enable per-user voice capture, run:\n"
            "  pip install \"discord.py[voice]\" discord-ext-sinks PyNaCl\n\n"
            "Without this, speaker detection uses heuristic silence-gap analysis."
        )

    def get_user_names(self) -> dict[int, str]:
        """Returns {user_id: display_name} for all recorded users."""
        return dict(self._user_names)

    def start_capture(
        self,
        token: str,
        channel_id: int,
        session_name: str,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Start recording in a background thread.
        Returns True if the bot connected successfully within 10 seconds.
        """
        if not self.is_available():
            if log_callback:
                log_callback(f"[Discord] {self.install_instructions()}")
            return False

        if not token or not channel_id:
            if log_callback:
                log_callback("[Discord] No token/channel configured — skipping capture.")
            return False

        self._session_name = session_name
        self._output_dir = TRANSCRIPTS_DIR / session_name
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._user_names = {}
        self._running = False

        # Use an event to signal successful connection
        connected_event = threading.Event()

        def _run_bot() -> None:
            try:
                import discord
                from discord.ext import sinks as discord_sinks  # type: ignore[import-untyped]

                intents = discord.Intents.default()
                intents.voice_states = True
                intents.members = True

                bot = discord.Client(intents=intents)
                self._bot = bot

                @bot.event
                async def on_ready() -> None:  # type: ignore[misc]
                    if log_callback:
                        log_callback(f"[Discord] Bot logged in as {bot.user}")

                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        if log_callback:
                            log_callback(f"[Discord] Channel {channel_id} not found.")
                        connected_event.set()
                        return

                    # Must be a VoiceChannel — only VoiceChannel supports connect()
                    if not isinstance(channel, discord.VoiceChannel):
                        if log_callback:
                            log_callback(
                                f"[Discord] Channel {channel_id} is not a VoiceChannel "
                                f"(got {type(channel).__name__}). Cannot connect."
                            )
                        connected_event.set()
                        return

                    # Log who is in the channel
                    members_in_channel: list[discord.Member] = [
                        m for m in channel.members
                        if not m.bot
                    ]
                    if log_callback:
                        names = [m.display_name for m in members_in_channel]
                        log_callback(
                            f"[Discord] Channel '{channel.name}' has "
                            f"{len(members_in_channel)} user(s): {', '.join(names) or 'none'}"
                        )

                    # Store user id → display_name mapping
                    for member in members_in_channel:
                        self._user_names[member.id] = member.display_name

                    try:
                        vc: discord.VoiceClient = await channel.connect()
                        self._voice_client = vc
                        self._running = True
                        connected_event.set()

                        # Start recording — each user gets their own sink file
                        sink = discord_sinks.WaveSink()

                        def _after_recording(
                            snk: discord_sinks.WaveSink,
                            channel_ref: discord.VoiceChannel,
                            *args: object,
                        ) -> None:
                            """Called by discord.py after stop_recording() completes."""
                            if log_callback:
                                log_callback(
                                    f"[Discord] Recording finished, "
                                    f"{len(snk.audio_data)} user track(s) captured."
                                )
                            # Save each user's audio to a WAV file
                            if self._output_dir:
                                for user_id, audio in snk.audio_data.items():
                                    display = self._user_names.get(user_id, str(user_id))
                                    safe_name = "".join(
                                        c if c.isalnum() or c in "-_" else "_"
                                        for c in display
                                    )
                                    out_path = self._output_dir / f"{safe_name}_{user_id}.wav"
                                    try:
                                        with open(out_path, "wb") as f:
                                            f.write(audio.file.read())
                                        if log_callback:
                                            log_callback(
                                                f"[Discord] Saved: {out_path.name}"
                                            )
                                    except Exception as save_err:
                                        if log_callback:
                                            log_callback(
                                                f"[Discord] Could not save {display}: {save_err}"
                                            )

                        vc.start_recording(sink, _after_recording, channel)

                        if log_callback:
                            log_callback(
                                f"[Discord] Recording started in '{channel.name}'."
                            )

                    except Exception as conn_err:
                        if log_callback:
                            log_callback(f"[Discord] Voice connect error: {conn_err}")
                        connected_event.set()

                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(bot.start(token))

            except Exception as e:
                if log_callback:
                    log_callback(f"[Discord] Bot thread error: {e}")
                connected_event.set()

        self._bot_thread = threading.Thread(
            target=_run_bot,
            daemon=True,
            name="DiscordCaptureBot",
        )
        self._bot_thread.start()

        # Wait up to 10 seconds for the bot to connect to voice
        connected_event.wait(timeout=10)
        return self._running

    def stop_capture(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Path]:
        """
        Stop recording and disconnect. Returns {display_name: wav_path}.
        Blocks briefly to allow the sink's after-callback to write files.
        """
        if not self._running or self._voice_client is None:
            return {}

        try:
            import discord

            vc = self._voice_client
            if isinstance(vc, discord.VoiceClient) and vc.is_connected():
                if log_callback:
                    log_callback("[Discord] Stopping voice recording...")

                # stop_recording triggers the _after_recording callback
                async def _stop() -> None:
                    vc.stop_recording()
                    await asyncio.sleep(1.5)   # let after-callback write files
                    await vc.disconnect()

                if self._loop and self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(_stop(), self._loop)
                    try:
                        future.result(timeout=10)
                    except Exception as e:
                        if log_callback:
                            log_callback(f"[Discord] Stop error: {e}")

        except Exception as e:
            if log_callback:
                log_callback(f"[Discord] Disconnect error: {e}")

        # Stop the bot event loop
        try:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

        # Brief wait for file writes to complete
        time.sleep(2.0)

        self._running = False

        # Collect output files
        result: dict[str, Path] = {}
        if self._output_dir and self._output_dir.exists():
            for wav_file in self._output_dir.glob("*.wav"):
                # Filename is {display_name}_{user_id}.wav
                # Extract display name (everything before the last underscore segment)
                stem_parts = wav_file.stem.rsplit("_", 1)
                display = stem_parts[0] if len(stem_parts) == 2 else wav_file.stem
                # Prefer the stored display name for the user_id
                try:
                    user_id = int(stem_parts[-1])
                    display = self._user_names.get(user_id, display)
                except ValueError:
                    pass
                result[display] = wav_file
                if log_callback:
                    size_kb = wav_file.stat().st_size // 1024
                    log_callback(f"[Discord] Track: {display} — {size_kb} KB")

        if log_callback:
            log_callback(f"[Discord] Capture complete. {len(result)} track(s) saved.")

        return result

    def is_connected(self) -> bool:
        """Returns True if currently connected to a voice channel."""
        try:
            import discord
            vc = self._voice_client
            return isinstance(vc, discord.VoiceClient) and vc.is_connected()
        except Exception:
            return False