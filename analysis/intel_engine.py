import os
import sys
import io
import json
import time
import subprocess
from pathlib import Path
from typing import Any, Optional, Callable

from app.config import MODEL_PATH, OLLAMA_EXE, OLLAMA_MODELS


def _ensure_console() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _detect_hardware() -> tuple[int, int]:
    try:
        import psutil
        n_threads = min(psutil.cpu_count(logical=False) or 8, 16)
    except Exception:
        n_threads = 8

    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if r.returncode == 0 and r.stdout.strip():
            free_mb    = int(r.stdout.strip().splitlines()[0].strip())
            gpu_layers = min(int(free_mb * 0.80 / 100), 40)
            gpu_layers = max(gpu_layers, 20)
            print(f"[AI] GPU: {free_mb}MB free → {gpu_layers} layers")
            return gpu_layers, n_threads
    except Exception:
        pass

    return 0, n_threads


# =====================================================
# OLLAMA PORTABLE BACKEND
# =====================================================

class _OllamaBackend:
    DEFAULT_MODEL   = "llama3.2:3b"
    API_BASE        = "http://localhost:11434"
    CONNECT_TIMEOUT = 5
    READ_TIMEOUT    = 300

    def __init__(self) -> None:
        from app.config import settings
        self.model    = str(settings.get("ollama_model") or self.DEFAULT_MODEL)
        self._process: Optional[subprocess.Popen[bytes]] = None  # type: ignore[type-arg]

    def _start_server(self) -> bool:
        if not OLLAMA_EXE.exists():
            print(
                f"[AI] Ollama exe not found at {OLLAMA_EXE}\n"
                "Download ollama-windows-amd64.zip from "
                "https://github.com/ollama/ollama/releases and extract to "
                f"{OLLAMA_EXE.parent}"
            )
            return False

        if self.is_running():
            return True

        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(OLLAMA_MODELS)
        env["OLLAMA_HOST"]   = "127.0.0.1:11434"
        OLLAMA_MODELS.mkdir(parents=True, exist_ok=True)

        print(f"[AI] Starting Ollama server from {OLLAMA_EXE} ...")
        try:
            self._process = subprocess.Popen(
                [str(OLLAMA_EXE), "serve"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32"
                    else 0
                ),
            )
        except Exception as e:
            print(f"[AI] Failed to start Ollama: {e}")
            return False

        for i in range(20):
            time.sleep(1)
            if self.is_running():
                print(f"[AI] Ollama server ready after {i+1}s.")
                return True

        print("[AI] Ollama server did not become ready in time.")
        return False

    def is_running(self) -> bool:
        try:
            import urllib.request
            req = urllib.request.urlopen(
                f"{self.API_BASE}/api/tags",
                timeout=self.CONNECT_TIMEOUT,
            )
            return req.status == 200
        except Exception:
            return False

    def ensure_running(self) -> bool:
        if self.is_running():
            return True
        return self._start_server()

    def stop_server(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None

    def model_is_available(self) -> bool:
        try:
            import urllib.request
            req  = urllib.request.urlopen(
                f"{self.API_BASE}/api/tags",
                timeout=self.CONNECT_TIMEOUT,
            )
            data = json.loads(req.read().decode())
            names: list[str] = [
                str(m.get("name", ""))
                for m in (data.get("models") or [])
            ]
            target = self.model.split(":")[0].lower()
            return any(target in n.lower() for n in names)
        except Exception:
            return False

    def pull_model(self) -> bool:
        print(f"[AI] Pulling model: {self.model} → {OLLAMA_MODELS}")
        try:
            import urllib.request
            body = json.dumps({"name": self.model}).encode()
            req  = urllib.request.Request(
                f"{self.API_BASE}/api/pull",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in resp:
                    try:
                        d = json.loads(line.decode())
                        status = str(d.get("status") or "")
                        if status and "pulling" in status.lower():
                            completed = int(d.get("completed") or 0)
                            total     = int(d.get("total") or 1)
                            pct = int(completed / total * 100) if total > 0 else 0
                            print(f"[AI] {status} {pct}%", end="\r")
                        elif status:
                            print(f"[AI] {status}")
                    except Exception:
                        pass
            print()
            return self.model_is_available()
        except Exception as e:
            print(f"[AI] Pull failed: {e}")
            return False

    def ensure_model(self) -> bool:
        if self.model_is_available():
            return True
        print(f"[AI] Model {self.model} not yet downloaded.")
        return self.pull_model()

    def generate(self, prompt: str, max_tokens: int = 900) -> str:
        import urllib.request
        import urllib.error

        body = json.dumps({
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.2,
                "top_p":       0.9,
                "stop":        ["[/INST]", "</s>"],
            },
        }).encode()

        req = urllib.request.Request(
            f"{self.API_BASE}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return str(data.get("response") or "").strip()
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e


# =====================================================
# LLAMA-CPP FALLBACK BACKEND
# =====================================================

class _LlamaCppBackend:

    def __init__(self) -> None:
        self._llm: Any   = None
        self._error: Optional[str] = None

    def load(self) -> None:
        if self._llm is not None:
            return
        if self._error:
            raise RuntimeError(self._error)

        if not MODEL_PATH.exists():
            self._error = (
                f"No GGUF model at {MODEL_PATH}\n"
                "Place model.gguf (Q4_K_M) in data/models/"
            )
            raise FileNotFoundError(self._error)

        try:
            from llama_cpp import Llama  # type: ignore[import-untyped]
        except Exception as e:
            self._error = (
                f"llama_cpp import failed: {e}\n"
                "Install the CUDA wheel:\n"
                "pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
            )
            raise RuntimeError(self._error) from e

        from app.config import settings
        gpu_layers, n_threads = _detect_hardware()
        if settings.LLM_GPU_LAYERS > 0:
            gpu_layers = settings.LLM_GPU_LAYERS

        try:
            self._llm = Llama(
                model_path=str(MODEL_PATH),
                n_gpu_layers=gpu_layers,
                n_ctx=settings.LLM_N_CTX,
                n_threads=n_threads,
                n_batch=512,
                verbose=False,
                use_mlock=False,
                use_mmap=True,
            )
            print(f"[AI] llama-cpp loaded: {MODEL_PATH.name}")
        except Exception as e:
            self._error = f"Model load failed: {e}"
            raise RuntimeError(self._error) from e

    def generate(self, prompt: str, max_tokens: int = 900) -> str:
        self.load()

        if self._llm is None:
            raise RuntimeError("LLM not loaded.")

        response: Any = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=0.2,
            stop=["</s>", "[INST]", "[/INST]"],
            echo=False,
            stream=False,
        )

        choices = response.get("choices") if isinstance(response, dict) else []
        if choices:
            return str(choices[0].get("text") or "").strip()
        return ""


# =====================================================
# INTEL ENGINE — MAIN CLASS
# =====================================================

class IntelEngine:
    MAX_RETRIES = 2
    RETRY_DELAY = 1.0

    def __init__(self) -> None:
        _ensure_console()
        self._ollama    = _OllamaBackend()
        self._llama_cpp = _LlamaCppBackend()
        self._backend: Optional[str] = None

    def _select_backend(self) -> str:
        if self._backend is not None:
            return self._backend

        if self._ollama.ensure_running():
            if self._ollama.ensure_model():
                self._backend = "ollama"
                print(f"[AI] Backend: Ollama ({self._ollama.model})")
                return self._backend
            print("[AI] Ollama running but model pull failed.")

        print("[AI] Falling back to llama-cpp-python...")
        try:
            self._llama_cpp.load()
            self._backend = "llama_cpp"
            print("[AI] Backend: llama-cpp-python")
            return self._backend
        except Exception as e:
            print(f"[AI] llama-cpp unavailable: {e}")
            self._backend = "none"
            return self._backend

    def generate(
        self,
        prompt: str,
        max_tokens: int = 1100,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> str:
        backend = self._select_backend()

        if backend == "none":
            return (
                "[AI unavailable]\n"
                "To enable AI analysis:\n"
                "  Option A (recommended): Download ollama-windows-amd64.zip from\n"
                "    https://github.com/ollama/ollama/releases\n"
                f"    and extract to {OLLAMA_EXE.parent}\n"
                "    The app will pull the model automatically on first run.\n\n"
                "  Option B: Place a model.gguf (Q4_K_M) in data/models/ and install\n"
                "    a compatible llama-cpp-python wheel."
            )

        for attempt in range(1, self.MAX_RETRIES + 1):
            if progress_callback:
                progress_callback(
                    attempt, self.MAX_RETRIES,
                    f"Generating via {backend}... ({attempt}/{self.MAX_RETRIES})"
                )
            try:
                text: str
                if backend == "ollama":
                    text = self._ollama.generate(prompt, max_tokens)
                else:
                    text = self._llama_cpp.generate(prompt, max_tokens)

                if text:
                    if progress_callback:
                        progress_callback(attempt, self.MAX_RETRIES, "Done.")
                    return text

            except Exception as e:
                print(f"[AI] Attempt {attempt} failed: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)

        return "[AI] Generation failed after retries."

    # ── Public analysis methods ───────────────────────────────

    def analyze_match(
        self,
        match_id: int,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> dict[str, Any]:
        from database.repositories import Repository
        from analysis.metrics_engine import MetricsEngine

        repo  = Repository()
        match = repo.get_match_full(match_id)
        if match is None:
            return {"error": f"Match {match_id} not found."}

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()
        metrics: dict[str, float] = {
            "win_rate":            engine.win_rate(),
            "attack_win_rate":     engine.attack_win_rate(),
            "defense_win_rate":    engine.defense_win_rate(),
            "engagement_win_rate": engine.average_team_engagement_win_rate(),
            "drone_efficiency":    engine.drone_efficiency(),
            "reinforcement_rate":  engine.reinforcement_usage_rate(),
            "man_advantage":       engine.man_advantage_conversion(),
            "clutch_rate":         engine.clutch_rate(),
        }

        transcript = self._get_transcript_summary(match_id)
        prompt     = self._build_match_prompt(match, metrics, summary, tps, transcript)

        if progress_callback:
            progress_callback(0, 1, "Generating match summary...")

        text = self.generate(prompt, max_tokens=1100, progress_callback=progress_callback)
        self._store_metric(repo, match_id, "ai_match_summary", text)
        return {"ai_match_summary": text}

    def get_player_intel(
        self,
        match_id: int,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> dict[str, Any]:
        from database.repositories import Repository
        from analysis.metrics_engine import MetricsEngine

        repo  = Repository()
        match = repo.get_match_full(match_id)
        if match is None:
            return {}

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()

        seen: dict[int, Any] = {}
        for r in match.rounds:
            for stat in r.player_stats:
                seen.setdefault(stat.player_id, stat)

        players = list(seen.values())
        if not players:
            return {}

        results: dict[str, Any] = {}
        for i, stat in enumerate(players):
            name  = str(stat.player.name)
            pid   = int(stat.player_id)
            pdata = summary.get(pid, {})
            if progress_callback:
                progress_callback(
                    i + 1, len(players),
                    f"Analyzing {name} ({i+1}/{len(players)})..."
                )
            prompt = self._build_player_prompt(stat, pdata, float(tps.get(pid, 0.0)))
            results[name] = self.generate(prompt, max_tokens=400)
        return results

    def _get_round_events(self, match_id: int) -> dict[int, dict]:
        """
        Loads stored round kill feed events from derived_metrics.
        Returns {round_number: events_dict}.
        """
        result: dict[int, dict] = {}
        try:
            from database.repositories import Repository
            import json
            repo = Repository()
            with repo.db.get_connection() as conn:
                rows = conn.execute(
                    """SELECT metric_name, metric_text
                       FROM derived_metrics
                       WHERE match_id = ? AND metric_name LIKE 'round_%_events'
                         AND metric_text IS NOT NULL""",
                    (match_id,)
                ).fetchall()
            for row in rows:
                # metric_name is "round_N_events"
                parts = row["metric_name"].split("_")
                if len(parts) >= 3:
                    try:
                        rnum = int(parts[1])
                        result[rnum] = json.loads(row["metric_text"])
                    except Exception:
                        pass
        except Exception:
            pass
        return result

    def _format_kill_feed_for_prompt(
        self,
        events_by_round: dict[int, dict],
        our_player_names: set[str],
    ) -> str:
        """
        Formats stored kill feed dicts into a concise text block.
        Only includes info we can verify — no invention.
        Only highlights OUR team's players by name; opponents shown as 'Enemy'.
        """
        if not events_by_round:
            return "  No kill feed data available from replay.\n"

        def label(username: str) -> str:
            return username if username in our_player_names else "Enemy"

        lines: list[str] = []

        for rnum in sorted(events_by_round.keys()):
            ev = events_by_round[rnum]
            lines.append(f"  R{rnum:02d}:")

            # First blood
            fb_killer = ev.get("first_blood_killer", "")
            fb_victim = ev.get("first_blood_victim", "")
            fb_time   = ev.get("first_blood_time")
            fb_won    = ev.get("opening_duel_won")
            if fb_killer:
                won_str = " (our advantage)" if fb_won else " (their advantage)"
                lines.append(
                    f"    Opening kill: {label(fb_killer)} → {label(fb_victim)}"
                    + (f" @ {fb_time:.0f}s" if fb_time is not None else "")
                    + won_str
                )

            # Kill sequence
            kills = ev.get("kills", [])
            if kills:
                kf_parts = []
                for k in kills:
                    kl = label(k.get("killer", ""))
                    vi = label(k.get("victim", ""))
                    tags = []
                    if k.get("headshot"):
                        tags.append("HS")
                    if k.get("trade"):
                        tags.append("trade")
                    tag_str = f"[{','.join(tags)}]" if tags else ""
                    t = k.get("time", "")
                    kf_parts.append(f"{kl}→{vi}@{t}{tag_str}")
                lines.append(f"    Kills: {', '.join(kf_parts)}")

            # Plant/defuse
            if ev.get("plant_completed"):
                who = ev.get("planter") or ""
                lines.append(f"    Bomb planted" + (f" by {label(who)}" if who else ""))
            elif ev.get("plant_attempted"):
                lines.append(f"    Plant started but not completed")
            if ev.get("defuse_completed"):
                who = ev.get("defuser") or ""
                lines.append(f"    Bomb defused" + (f" by {label(who)}" if who else ""))

            # Clutch
            clutch_player = ev.get("clutch_player", "")
            clutch_kills  = ev.get("clutch_kills", 0)
            if clutch_player:
                lines.append(
                    f"    Clutch: {label(clutch_player)} secured {clutch_kills} kill(s) to win"
                )

            # Per-player notable stats (headshot rate, trades) — only our players
            pd = ev.get("player_derived", {})
            for username, data in sorted(pd.items()):
                if username not in our_player_names:
                    continue
                notes = []
                hs_rate = data.get("headshot_rate", 0)
                kills_n = data.get("kills", 0)
                if kills_n >= 2 and hs_rate >= 0.5:
                    notes.append(f"{hs_rate:.0%} HS rate")
                if data.get("trades", 0) >= 1:
                    notes.append(f"{data['trades']} trade(s)")
                if notes:
                    lines.append(f"    {username}: {', '.join(notes)}")

        return "\n".join(lines)
    # ── Prompt builders ───────────────────────────────────────

    def _build_match_prompt(self, match, metrics, summary, tps, transcript) -> str:
        wins   = sum(1 for r in match.rounds if r.outcome == "win")
        losses = sum(1 for r in match.rounds if r.outcome == "loss")
        total  = len(match.rounds)

        # ── Determine side-switch point ───────────────────────────
        # In ranked: sides swap after round 6 (first to 4 wins, max 7 rounds)
        # We detect it from the data itself: find first round where side changes
        side_switch = None
        if match.rounds:
            prev_side = match.rounds[0].side
            for r in match.rounds[1:]:
                if r.side != prev_side:
                    side_switch = r.round_number
                    break

        # ── Round table ───────────────────────────────────────────
        round_lines = []
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            a = sum(p.assists for p in r.player_stats)
            # Only show K/D/A if stats were actually recorded
            if r.player_stats:
                kda = f"{k}K/{d}D/{a}A"
            else:
                kda = "no stats recorded"
            side_label = "ATK" if r.side == "attack" else "DEF"
            outcome_label = "WIN ✓" if r.outcome == "win" else "LOSS ✗"
            switch_note = " ← SIDE SWITCH" if side_switch and r.round_number == side_switch else ""
            round_lines.append(
                f"  R{r.round_number:02d}  {side_label}  "
                f"{outcome_label}  {kda:<16}  {r.site or 'Unknown site'}{switch_note}"
            )

        # ── Player table ──────────────────────────────────────────
        # Only include players that have actual stats recorded
        players_with_stats = {
            pid: data for pid, data in summary.items()
            if data.get("rounds_played", 0) > 0
        }

        player_lines = []
        sorted_players = sorted(
            players_with_stats.items(),
            key=lambda x: float(tps.get(x[0], 0.0)),
            reverse=True,
        )
        for pid, data in sorted_players:
            score = float(tps.get(pid, 0.0))
            kd    = float(data.get("kd_ratio", 0.0))
            ew    = float(data.get("engagement_win_rate", 0.0))
            sr    = float(data.get("survival_rate", 0.0))
            k     = int(data.get("kills", 0))
            d_    = int(data.get("deaths", 0))
            a     = int(data.get("assists", 0))
            rp    = int(data.get("rounds_played", 1))
            name  = str(data["player"].name)
            player_lines.append(
                f"  {name:<16} "
                f"K/D/A: {k}/{d_}/{a}  "
                f"(KD {kd:.2f}  EWR {ew:.0%}  Survival {sr:.0%}  "
                f"TPS {score:.2f})  "
                f"{rp} rounds"
            )

        no_stats_note = ""
        if not players_with_stats:
            no_stats_note = (
                "\n  NOTE: No player stats were recorded for this match.\n"
                "  Rounds were imported from replay but manual stat entry was not completed.\n"
                "  Analysis will be based on round outcomes only.\n"
            )

        # ── Comms section ─────────────────────────────────────────
        comms_section = "  No comms data recorded this session.\n"
        if transcript and int(transcript.get("word_count", 0)) > 0:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:5]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:5]
            gaps        = int(transcript.get("coord_gaps", 0))
            words       = int(transcript.get("word_count", 0))
            speakers    = dict(transcript.get("speakers", {}))

            speaker_lines = []
            for spk, sd in list(speakers.items())[:5]:
                wc  = int(sd.get("word_count", 0))
                top = list(sd.get("top_words", []))[:3]
                speaker_lines.append(
                    f"    {spk}: {wc} words — "
                    f"top words: {', '.join(top) or 'none'}"
                )

            comms_section = (
                f"  Total words spoken : {words}\n"
                f"  Top location callouts : {', '.join(top_locs) or 'none'}\n"
                f"  Top action callouts   : {', '.join(top_actions) or 'none'}\n"
                f"  Communication gaps (>8s silence) : {gaps}\n"
            )
            if speaker_lines:
                comms_section += "  Speakers (auto-detected, not named):\n"
                comms_section += "\n".join(speaker_lines) + "\n"

        # ── Build the full prompt ─────────────────────────────────
        prompt = f"""You are a Rainbow Six Siege post-match analyst. Your job is to write a clear, honest debrief based ONLY on the data provided below.

STRICT RULES — violating these makes the analysis worthless:
- DO NOT invent operator names, player names, or strategies not present in the data.
- DO NOT reference gadgets, abilities, or tactics unless they appear in the stats.
- DO NOT say a player "used smokes" or "played Thermite" unless that operator/gadget appears in the stats table below.
- DO NOT reference a round detail (e.g. "you could have used drones in R03") that contradicts the side shown.
- SIEGE RULES you must respect:
    * DEFENSE side: the team holds a site, uses reinforcements, barbed wire, gadgets. They do NOT have attack drones.
    * ATTACK side: the team pushes the site, uses drones to gather info, breaches. They do NOT place reinforcements.
    * Sides swap mid-match (see SIDE SWITCH marker in round table below).
    * Maximum 5 kills possible in a single round (5v5 format).
    * A round is won by eliminating all enemies, defusing/planting bomb, or time expiry on defense.
- If player stats show 0 kills and 0 deaths across the board, state "manual stats were not entered for this match" and skip player-specific analysis.
- If you are uncertain about what happened in a round, say so — do not guess.
- Base observations only on patterns visible in multiple rounds, not single-round anomalies.
- The opponent name is "{match.opponent_name}" — use it.
- The map is "{match.map}".

════════════════════════════════════════════════════════════════
MATCH DATA  (this is everything the system knows — nothing more)
════════════════════════════════════════════════════════════════

MATCH: vs {match.opponent_name} on {match.map}
RESULT: {wins}–{losses} {'WIN' if match.result == 'win' else 'LOSS' if match.result == 'loss' else '(result not set)'}
TOTAL ROUNDS: {total}
{no_stats_note}
TEAM METRICS (calculated from recorded stats):
  Overall win rate          : {metrics['win_rate']:.0%}
  Attack rounds win rate    : {metrics['attack_win_rate']:.0%}
  Defense rounds win rate   : {metrics['defense_win_rate']:.0%}
  Engagement win rate       : {metrics['engagement_win_rate']:.0%}  (gunfight win%)
  Man-advantage conversion  : {metrics['man_advantage']:.0%}  (when up in players, % converted to round win)
  Clutch rate               : {metrics['clutch_rate']:.0%}

ROUND BY ROUND:
{chr(10).join(round_lines)}

PLAYER STATS (only players with recorded data shown):
{chr(10).join(player_lines) if player_lines else "  No player stats recorded — manual entry incomplete."}

  Columns: K=Kills D=Deaths A=Assists  EWR=Engagement Win Rate  Survival=rounds alive at end  TPS=composite score

COMMS DATA:
{comms_section}
════════════════════════════════════════════════════════════════
DEBRIEF FORMAT — follow exactly, use only the data above
════════════════════════════════════════════════════════════════

## MATCH SUMMARY
Two sentences maximum. State the scoreline, result, and one headline observation supported by a metric above. Do not reference operators or strategies not in the data.

## ROUND PATTERNS
List what the data actually shows across multiple rounds. Reference specific round numbers and sides. For each observation, cite the supporting data point (e.g. "R02, R04, R05 were all attack wins — attack win rate {metrics['attack_win_rate']:.0%}"). If a pattern is only visible in one round, say so and do not over-generalise.

## WHAT TO FOCUS ON NEXT
Based only on the weaknesses visible in the numbers, list 2–3 specific, actionable adjustments. Do not invent context. If engagement win rate is low, say "improve gunfight consistency" not "use more smokes". If no stats were recorded, focus on observable round patterns only.

## COMMUNICATION
Summarise comms data if present. If speaker names are "Speaker_1" etc., note these are auto-detected and may not match actual players. If no comms data, write: No comms data recorded.

"""
        return prompt

    def _build_player_prompt(
        self,
        stat: Any,
        pdata: dict[str, Any],
        tps_score: float,
    ) -> str:
        k  = int(pdata.get("kills", 0))
        d  = int(pdata.get("deaths", 0))
        a  = int(pdata.get("assists", 0))
        rp = int(pdata.get("rounds_played", 1))

        # If no meaningful stats, say so
        if k == 0 and d == 0 and a == 0:
            return (
                f"No stats recorded for {stat.player.name} — "
                "manual round entry was not completed for this match."
            )

        return (
            "You are a Rainbow Six Siege performance coach. Be direct and specific.\n"
            "RULES: Only reference stats shown below. Do not invent operators or strategies.\n\n"
            f"PLAYER: {stat.player.name}\n"
            f"ROUNDS PLAYED: {rp}\n"
            f"K/D/A: {k}/{d}/{a}  KD: {float(pdata.get('kd_ratio', 0)):.2f}\n"
            f"Engagement Win Rate : {float(pdata.get('engagement_win_rate', 0)):.0%}  "
            f"(gunfights won out of taken)\n"
            f"Survival Rate       : {float(pdata.get('survival_rate', 0)):.0%}  "
            f"(rounds survived)\n"
            f"Utility Efficiency  : {float(pdata.get('utility_efficiency', 0)):.0%}  "
            f"(ability + gadget usage rate)\n"
            f"TPS Score           : {tps_score:.3f}  (composite performance)\n\n"
            "Respond in EXACTLY this format, citing only the stats above:\n"
            "STRENGTH: [one specific strength — cite the stat that shows it]\n"
            "FOCUS: [one area to improve — cite the stat that shows it]\n"
            "DRILL: [one concrete practice activity that addresses the focus area]\n"
        )

    def _get_transcript_summary(self, match_id: int) -> dict[str, Any]:
        try:
            from database.repositories import Repository
            repo = Repository()
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT processed_segments_json FROM transcripts WHERE match_id = ?",
                    (match_id,)
                ).fetchone()
            if not row or not row["processed_segments_json"]:
                return {}
            data: dict[str, Any] = json.loads(row["processed_segments_json"])
            return {
                "top_locations": data.get("location_freq") or {},
                "top_actions":   data.get("action_freq")   or {},
                "coord_gaps":    len(data.get("coordination_gaps") or []),
                "word_count":    int(data.get("word_count") or 0),
                "speakers":      data.get("speakers") or {},
            }
        except Exception:
            return {}

    def _store_metric(
        self,
        repo: Any,
        match_id: int,
        name: str,
        value: str,
    ) -> None:
        try:
            with repo.db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO derived_metrics
                       (match_id, metric_name, metric_value, is_ai_generated)
                       VALUES (?, ?, ?, 1) ON CONFLICT DO NOTHING""",
                    (match_id, name, float(len(value))),
                )
                conn.commit()
        except Exception as e:
            print(f"[AI] Store metric failed: {e}")

    def shutdown(self) -> None:
        self._ollama.stop_server()