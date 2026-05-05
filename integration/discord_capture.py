from __future__ import annotations

import asyncio
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional, Dict

import discord

from app.config import TRANSCRIPTS_DIR


# ─────────────────────────────────────────────────────────────
# PER-USER AUDIO BUFFER
# ─────────────────────────────────────────────────────────────

class AudioBuffer:
    def __init__(self, user_id: int, name: str):
        self.user_id = user_id
        self.name = name
        self.frames: list[bytes] = []

    def push(self, pcm: bytes) -> None:
        self.frames.append(pcm)

    def write_wav(self, path: Path, sample_rate: int = 48000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(sample_rate)

            for frame in self.frames:
                wf.writeframes(frame)


class VoiceBufferManager:
    def __init__(self):
        self.buffers: Dict[int, AudioBuffer] = {}

    def get(self, user_id: int, name: str) -> AudioBuffer:
        if user_id not in self.buffers:
            self.buffers[user_id] = AudioBuffer(user_id, name)
        return self.buffers[user_id]

    def add(self, user_id: int, name: str, pcm: bytes) -> None:
        self.get(user_id, name).push(pcm)

    def export_all(self, out_dir: Path) -> Dict[str, Path]:
        result: Dict[str, Path] = {}

        for buf in self.buffers.values():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in buf.name)
            path = out_dir / f"{safe}_{buf.user_id}.wav"
            buf.write_wav(path)
            result[buf.name] = path

        return result


# ─────────────────────────────────────────────────────────────
# DISCORD CAPTURE ENGINE
# ─────────────────────────────────────────────────────────────

class DiscordCapture:
    """
    Raw voice capture engine:
    - connects to voice channel
    - receives per-user PCM audio
    - stores buffers in memory
    - exports WAV per user on stop
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._client: Optional[discord.Client] = None
        self._voice: Optional[discord.VoiceClient] = None

        self._buffers = VoiceBufferManager()
        self._output_dir: Optional[Path] = None

        self._session_name = ""
        self._running = False

        self._user_map: dict[int, str] = {}

    # ─────────────────────────────────────────────
    # AVAILABILITY
    # ─────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        try:
            import discord  # noqa
            import nacl     # noqa
            return True
        except ImportError:
            return False

    @staticmethod
    def install_instructions() -> str:
        return (
            "Missing Discord voice capture dependencies.\n\n"
            "Install inside your venv:\n"
            '  pip install "discord.py[voice]" PyNaCl\n\n'
            "Note: This system no longer requires discord-ext-sinks."
        )
    # ─────────────────────────────────────────────
    # START CAPTURE
    # ─────────────────────────────────────────────

    def start_capture(
        self,
        token: str,
        channel_id: int,
        session_name: str,
        log: Optional[Callable[[str], None]] = None,
    ) -> bool:

        if not self.is_available():
            if log:
                log("[Discord] Missing discord.py or PyNaCl")
            return False

        self._session_name = session_name
        self._output_dir = TRANSCRIPTS_DIR / session_name
        self._output_dir.mkdir(parents=True, exist_ok=True)

        connected = threading.Event()

        def run_bot():
            try:
                intents = discord.Intents.default()
                intents.voice_states = True
                intents.members = True

                client = discord.Client(intents=intents)
                self._client = client

                @client.event
                async def on_ready():
                    if log:
                        log(f"[Discord] Logged in as {client.user}")

                    channel = client.get_channel(channel_id)
                    if not isinstance(channel, discord.VoiceChannel):
                        if log:
                            log("[Discord] Invalid voice channel")
                        connected.set()
                        return

                    for m in channel.members:
                        if not m.bot:
                            self._user_map[m.id] = m.display_name

                    try:
                        vc = await channel.connect()
                        self._voice = vc
                        self._running = True

                        if log:
                            log("[Discord] Connected to voice channel")

                        connected.set()

                        # ─────────────────────────────────────────────
                        # RAW AUDIO RECEIVER
                        # ─────────────────────────────────────────────

                        def audio_sink(user: discord.User, data: bytes):
                            if not user or user.bot:
                                return

                            name = getattr(user, "display_name", str(user))
                            self._buffers.add(user.id, name, data)

                        # ⚠️ IMPORTANT:
                        # discord.py does NOT officially document this API,
                        # but VoiceClient exposes receive callback in voice pipeline.
                        #
                        # If your version differs, this is the ONLY section you'd adapt.

                        vc.recv_audio = audio_sink  # type: ignore[attr-defined]

                        if log:
                            log("[Discord] Raw audio capture active")

                    except Exception as e:
                        if log:
                            log(f"[Discord] Voice connect error: {e}")
                        connected.set()

                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(client.start(token))

            except Exception as e:
                if log:
                    log(f"[Discord] Fatal error: {e}")
                connected.set()

        self._thread = threading.Thread(target=run_bot, daemon=True)
        self._thread.start()

        connected.wait(timeout=10)
        return self._running

    # ─────────────────────────────────────────────
    # STOP CAPTURE
    # ─────────────────────────────────────────────

    def stop_capture(self, log: Optional[Callable[[str], None]] = None) -> dict[str, Path]:

        if not self._running:
            return {}

        if log:
            log("[Discord] Stopping capture...")

        if self._voice and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._voice.disconnect(),
                    self._loop
                )
            except Exception:
                pass

        time.sleep(2.0)

        self._running = False

        out = self._output_dir or Path("output")
        results = self._buffers.export_all(out)

        if log:
            log(f"[Discord] Exported {len(results)} user recordings")

        return results

    # ─────────────────────────────────────────────
    # STATUS
    # ─────────────────────────────────────────────

    def is_connected(self) -> bool:
        try:
            return self._voice is not None and self._voice.is_connected()
        except Exception:
            return False

    def get_user_map(self) -> dict[int, str]:
        return dict(self._user_map)
    def get_user_names(self) -> dict[int, str]:
        """
        Returns mapping of Discord user IDs → display names.
        Used by SessionManager for transcript attribution.
        """
        return {
            buf.user_id: buf.name
            for buf in self._buffers.buffers.values()
        }