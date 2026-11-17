#!/usr/bin/env python3
"""Optional speaker diarization for a debrief, run INSIDE the r6diarize image.

Built as its own clean, minimal image (deploy/Dockerfile.diarize) because
pyannote's dependency tree conflicts with the server's pinned transcription
stack. Phone-in-pocket audio usually can't be separated into two speakers, so
this DETECTS that collapse and refuses to label rather than guess: if the
quieter speaker is under MIN_MINORITY of the talk time, it reports
"single-dominant speaker -- attribution unavailable" and leaves the transcript
unlabelled. Labels are applied only when both voices are genuinely present
(e.g. the phone held between the two of you).

Stage audio.wav + segments.json into a dir visible to the container, then:

  docker run --rm --env-file deploy/.env -e HF_HOME=/data/hf_cache \\
    -e DIAR_DIR=/data/_diar -v r6_data:/data --network r6_egress \\
    r6diarize:local python /data/_diar/diarize_runner.py

Output: <DIAR_DIR>/diar_labels.json (+ printed summary). Names the two clusters
by talk-time (the heavier talker = the label passed in NAMES[0]); swap as needed.
"""
import collections
import json
import os
import wave

MIN_MINORITY = 0.15          # quieter speaker must hold at least this share, else "collapsed"
NAMES = ("James", "Elijah")  # (heavier talker, lighter talker) -- adjust per recording


def hhmmss(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def main():
    import torch
    from pyannote.audio import Pipeline

    diar_dir = os.environ.get("DIAR_DIR", "/data/_diar")
    wav = os.path.join(diar_dir, "audio.wav")
    seg = os.path.join(diar_dir, "segments.json")
    out = os.path.join(diar_dir, "diar_labels.json")
    token = os.environ.get("R6_HF_TOKEN")

    print("loading pyannote speaker-diarization-3.1 ...", flush=True)
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
    pipe.to(torch.device("cpu"))

    print("diarizing (num_speakers=2) ...", flush=True)
    dia = pipe(wav, num_speakers=2)
    turns = [(s.start, s.end, spk) for s, _, spk in dia.itertracks(yield_label=True)]
    talk = collections.Counter()
    for s, e, spk in turns:
        talk[spk] += e - s
    total = sum(talk.values()) or 1.0
    ordered = talk.most_common()
    minority = (ordered[1][1] / total) if len(ordered) > 1 else 0.0
    print("pyannote speakers (talk sec):", {k: round(v) for k, v in ordered},
          "| minority share %.1f%%" % (100 * minority), flush=True)

    collapsed = minority < MIN_MINORITY
    segs = json.load(open(seg, encoding="utf-8"))

    if collapsed:
        print("COLLAPSED: the two voices did not separate (pocket/far-field audio). "
              "No speaker attribution -- leaving the transcript unlabelled.", flush=True)
        labelled = [{"start": float(s["start"]), "end": float(s["end"]),
                     "speaker": "unknown", "text": s["text"]} for s in segs]
        json.dump({"status": "collapsed", "minority_share": round(minority, 3),
                   "segments": labelled}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print("wrote", out, flush=True)
        return

    name = {ordered[0][0]: NAMES[0]}
    if len(ordered) > 1:
        name[ordered[1][0]] = NAMES[1]
    print("naming by talk-time:", name, flush=True)

    labelled = []
    for s in segs:
        st, en = float(s["start"]), float(s["end"])
        best, ov = None, 0.0
        for ts, te, spk in turns:
            o = max(0.0, min(en, te) - max(st, ts))
            if o > ov:
                ov, best = o, spk
        labelled.append({"start": st, "end": en,
                         "speaker": name.get(best, "unknown") if best is not None else "unknown",
                         "text": s["text"]})
    json.dump({"status": "labelled", "names": name, "minority_share": round(minority, 3),
               "segments": labelled}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print("wrote", out, flush=True)
    for r in labelled[:16]:
        print(f"  [{hhmmss(r['start'])}] {r['speaker']:<6}: {r['text'][:72]}", flush=True)


if __name__ == "__main__":
    main()
