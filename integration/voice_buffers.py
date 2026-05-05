from __future__ import annotations

import wave
import audioop
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class UserAudioBuffer:
    user_id: int
    display_name: str
    pcm_frames: List[bytes] = field(default_factory=list)

    def add_frame(self, frame: bytes) -> None:
        self.pcm_frames.append(frame)

    def export_wav(self, path: Path, sample_rate: int = 48000) -> None:
        """Convert PCM → WAV file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)  # 16-bit audio
            wf.setframerate(sample_rate)

            for frame in self.pcm_frames:
                wf.writeframes(frame)


class VoiceBufferManager:
    """Manages per-user audio streams."""

    def __init__(self) -> None:
        self.users: Dict[int, UserAudioBuffer] = {}

    def get(self, user_id: int, name: str) -> UserAudioBuffer:
        if user_id not in self.users:
            self.users[user_id] = UserAudioBuffer(user_id, name)
        return self.users[user_id]

    def add_audio(self, user_id: int, name: str, pcm: bytes) -> None:
        self.get(user_id, name).add_frame(pcm)

    def export_all(self, out_dir: Path) -> Dict[str, Path]:
        results: Dict[str, Path] = {}

        for buf in self.users.values():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in buf.display_name)
            path = out_dir / f"{safe}_{buf.user_id}.wav"
            buf.export_wav(path)
            results[buf.display_name] = path

        return results