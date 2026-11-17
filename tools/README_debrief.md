# Debrief video pipeline

Reusable tooling for the recorded post-scrim talks. These are a personal memory
aid: phone-in-pocket recordings made outside / in a car, mixing real game intel
with a lot of personal conversation. **The transcripts are private** — keep them
out of version control and out of any shared report; pull only the
game-relevant signal into something the team sees.

## 1. Transcribe (the reliable part, every video)

Run with the **system Python 3.12** (it has `faster-whisper` + `numpy`; the
project venv does not):

```
python tools/debrief.py "D:\Phones\...\DCIM\Camera\VID_20261112_200931821.mp4"
```

Writes to `debriefs/<video-stem>/`:
- `transcript.txt` — timestamped, readable
- `segments.json` — machine-readable segments
- `audio.wav` — 16 kHz mono extract

`debriefs/` is gitignored. ~53 min of audio transcribes in ~5 min on CPU.

## 2. Diarization (optional — only worth it for "held between us" clips)

Speaker separation needs both voices on roughly equal footing. For the usual
pocket recording it can't be done — even pyannote collapses to one speaker — so
`diarize_runner.py` **detects that and refuses to label** rather than guess.
Only bother when the phone was held out between the two of you.

pyannote lives in its own image (its deps conflict with the server's):

```
# build once (inside the R6Host WSL distro)
docker build -f deploy/Dockerfile.diarize -t r6diarize:local <context-with-that-Dockerfile>

# stage audio.wav + segments.json into the r6_data volume at /data/_diar/,
# plus a copy of tools/diarize_runner.py, then:
docker run --rm --env-file deploy/.env -e HF_HOME=/data/hf_cache -e DIAR_DIR=/data/_diar \
  -v r6_data:/data --network r6_egress r6diarize:local \
  python /data/_diar/diarize_runner.py
```

Needs `R6_HF_TOKEN` in `deploy/.env` (HuggingFace read token, after accepting the
`pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0` licenses). The
model caches once in the volume under `HF_HOME`.

Output `diar_labels.json` has `status: "labelled"` (with per-segment speakers) or
`status: "collapsed"` (audio couldn't be separated — transcript stays unlabelled).

## Why it's built this way

- Phone-in-pocket far-field audio of two similar voices doesn't carry enough
  speaker-distinguishing signal; confirmed on a real 53-min clip — both a
  voiceprint method and pyannote gave a 98% / 2% collapse. The one free fix for
  future clips is **not the pocket**: phone held out between you both.
- pyannote's dependency tree (torch/torchaudio 2.2.2, numpy<2, huggingface_hub
  0.25.2) is pinned to its era and kept out of the server image to avoid
  conflicts; see `deploy/Dockerfile.diarize`.
