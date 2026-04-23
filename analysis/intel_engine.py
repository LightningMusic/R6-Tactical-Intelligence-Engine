import os
import sys
import time
import io
import subprocess
from typing import Optional, Callable

from app.config import MODEL_PATH


def _ensure_console() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _fix_frozen_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    _ensure_console()
    internal_dir = os.path.join(os.path.dirname(sys.executable), "_internal")
    llama_lib    = os.path.join(internal_dir, "llama_cpp", "lib")
    if os.path.isdir(llama_lib):
        os.environ["PATH"] = llama_lib + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(llama_lib)
        except (AttributeError, OSError):
            pass
        print(f"[AI] llama_cpp lib registered: {llama_lib}")
    else:
        print(f"[AI] WARNING: llama_cpp lib not at {llama_lib}")


def _detect_hardware() -> tuple[int, int]:
    """
    Returns (n_gpu_layers, n_threads).
    Detects NVIDIA GPU via nvidia-smi without admin rights.
    Falls back gracefully to CPU.
    """
    # ── CPU threads ──────────────────────────────────────────
    try:
        import psutil
        physical_cores = psutil.cpu_count(logical=False) or 8
        # Use physical cores only — logical (HT) cores hurt LLM perf
        n_threads = min(physical_cores, 16)
    except Exception:
        n_threads = 8

    # ── NVIDIA GPU via nvidia-smi (no admin needed) ───────────
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_name  = parts[0]
                    total_mb  = int(parts[1])
                    free_mb   = int(parts[2])
                    # RTX 4060 has 8GB — use ~80% for model layers
                    # Rule of thumb: ~100MB per layer for Q4_K_M models
                    usable_mb    = int(free_mb * 0.80)
                    gpu_layers   = min(usable_mb // 100, 40)
                    gpu_layers   = max(gpu_layers, 20)
                    print(
                        f"[AI] GPU detected: {gpu_name} "
                        f"({total_mb}MB total, {free_mb}MB free) "
                        f"→ {gpu_layers} layers offloaded"
                    )
                    return gpu_layers, n_threads
    except FileNotFoundError:
        print("[AI] nvidia-smi not found — checking environment variables...")
    except Exception as e:
        print(f"[AI] GPU detection error: {e}")

    # ── Check environment GPU hints ───────────────────────────
    if os.environ.get("CUDA_VISIBLE_DEVICES") not in (None, "", "-1"):
        print("[AI] CUDA_VISIBLE_DEVICES set — assuming GPU available (20 layers)")
        return 20, n_threads

    if os.environ.get("HIP_VISIBLE_DEVICES") or os.environ.get("ROCR_VISIBLE_DEVICES"):
        print("[AI] AMD GPU detected via env — 20 layers")
        return 20, n_threads

    print(f"[AI] CPU-only mode — {n_threads} threads")
    return 0, n_threads


class IntelEngine:
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0

    def __init__(self) -> None:
        self._llm  = None
        self._load_error: Optional[str] = None

    def _load_model(self) -> None:
        if self._llm is not None:
            return
        if self._load_error is not None:
            raise RuntimeError(self._load_error)

        _fix_frozen_paths()
        _ensure_console()

        if not MODEL_PATH.exists():
            self._load_error = (
                f"No model found at {MODEL_PATH}\n"
                "Place a Q4_K_M .gguf file there named 'model.gguf'."
            )
            raise FileNotFoundError(self._load_error)

        try:
            from llama_cpp import Llama
        except Exception as e:
            msg = str(e)
            if "0xc000001d" in msg or "illegal instruction" in msg.lower():
                self._load_error = (
                    f"Model load failed: {e}\n"
                    "This usually means the bundled llama runtime is using CPU "
                    "instructions the machine does not support.\n"
                    "Fix: pip uninstall llama-cpp-python -y && "
                    "pip install llama-cpp-python "
                    "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
                )
            else:
                self._load_error = f"Failed to import llama_cpp: {e}"
            raise RuntimeError(self._load_error) from e

        from app.config import settings
        gpu_layers, n_threads = _detect_hardware()

        # Allow settings override
        if settings.LLM_GPU_LAYERS > 0:
            gpu_layers = settings.LLM_GPU_LAYERS
        if settings.LLM_N_THREADS > 0:
            n_threads = settings.LLM_N_THREADS

        n_ctx = settings.LLM_N_CTX

        print(f"[AI] Loading: {MODEL_PATH.name}")
        print(f"[AI] GPU layers={gpu_layers} | CTX={n_ctx} | Threads={n_threads}")

        try:
            self._llm = Llama(
                model_path=str(MODEL_PATH),
                n_gpu_layers=gpu_layers,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_batch=512,
                verbose=False,
                use_mlock=False,   # never requires admin/root
                use_mmap=True,
            )
            print("[AI] Model loaded successfully.")
        except Exception as e:
            self._load_error = f"Model load failed: {e}"
            raise RuntimeError(self._load_error) from e

    def generate(
        self,
        prompt: str,
        max_tokens: int = 800,
        temperature: float = 0.3,
        stop: Optional[list] = None,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        try:
            self._load_model()
        except Exception as e:
            return f"[AI unavailable: {e}]"

        if self._llm is None:
            return "[AI] Model not available."

        last_error = ""
        for attempt in range(1, self.MAX_RETRIES + 1):
            if progress_callback:
                progress_callback(attempt, self.MAX_RETRIES,
                                  f"Generating... {attempt}/{self.MAX_RETRIES}")
            try:
                response = self._llm(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop or ["</s>", "[INST]", "[/INST]"],
                    echo=False,
                    stream=False,
                )
                from typing import cast
                from llama_cpp import CreateCompletionResponse
                result = cast("CreateCompletionResponse", response)
                text   = result["choices"][0]["text"].strip()
                if text:
                    if progress_callback:
                        progress_callback(attempt, self.MAX_RETRIES, "Done.")
                    return text
                last_error = "Empty response"
            except Exception as e:
                last_error = str(e)
                print(f"[AI] Attempt {attempt} failed: {e}")
            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)

        return f"[AI] Failed after {self.MAX_RETRIES} attempts. Last: {last_error}"

    def analyze_match(
        self,
        match_id: int,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        from database.repositories import Repository
        from analysis.metrics_engine import MetricsEngine

        repo  = Repository()
        match = repo.get_match_full(match_id)
        if match is None:
            return {"error": f"Match {match_id} not found."}

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()
        metrics = {
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
            progress_callback(0, self.MAX_RETRIES, "Generating match summary...")

        text = self.generate(prompt, max_tokens=900, progress_callback=progress_callback)
        self._store_metric(repo, match_id, "ai_match_summary", text)
        return {"ai_match_summary": text}

    def get_player_intel(
        self,
        match_id: int,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        from database.repositories import Repository
        from analysis.metrics_engine import MetricsEngine

        repo  = Repository()
        match = repo.get_match_full(match_id)
        if match is None:
            return {}

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()

        seen: dict = {}
        for r in match.rounds:
            for stat in r.player_stats:
                seen.setdefault(stat.player_id, stat)
        players = list(seen.values())
        if not players:
            return {}

        results: dict = {}
        for i, stat in enumerate(players):
            name  = stat.player.name
            pid   = stat.player_id
            pdata = summary.get(pid, {})
            if progress_callback:
                progress_callback(i+1, len(players),
                                  f"Analyzing {name} ({i+1}/{len(players)})...")
            prompt = self._build_player_prompt(stat, pdata, tps.get(pid, 0.0))
            results[name] = self.generate(prompt, max_tokens=400)
        return results

    def _build_match_prompt(self, match, metrics, summary, tps, transcript) -> str:
        round_lines = []
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            round_lines.append(
                f"  R{r.round_number:02d} | {r.side:<8} | "
                f"{'WIN' if r.outcome=='win' else 'LOSS'} | "
                f"{(r.site or '?'):<30} | K/D {k}/{d}"
            )
        rounds_block = "\n".join(round_lines) or "  No data."

        player_lines = []
        for pid, data in summary.items():
            score = tps.get(pid, 0.0)
            player_lines.append(
                f"  {data['player'].name:<18} "
                f"K:{data['kills']:>2} D:{data['deaths']:>2} A:{data['assists']:>2} | "
                f"EW:{data['engagement_win_rate']:.0%} "
                f"SR:{data['survival_rate']:.0%} "
                f"UE:{data['utility_efficiency']:.0%} "
                f"TPS:{score:.3f}"
            )
        players_block = "\n".join(player_lines) or "  No data."

        comms = ""
        if transcript:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:5]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:5]
            speakers    = transcript.get("speakers", {})
            speaker_block = ""
            if speakers:
                for spk, stats in list(speakers.items())[:5]:
                    speaker_block += (
                        f"    {spk}: {stats.get('word_count',0)} words, "
                        f"top: {', '.join(stats.get('top_words',[])[:3])}\n"
                    )
            comms = (
                f"\nCOMMUNICATIONS\n==============\n"
                f"  Words spoken : {transcript.get('word_count', 0)}\n"
                f"  Locations    : {', '.join(top_locs) or 'none'}\n"
                f"  Actions      : {', '.join(top_actions) or 'none'}\n"
                f"  Silence gaps : {transcript.get('coord_gaps', 0)} (>8s)\n"
            )
            if speaker_block:
                comms += f"  Speakers:\n{speaker_block}"

        return (
            "[INST] You are an elite Rainbow Six Siege tactical analyst. "
            "Be specific, cite data, no filler.\n\n"
            f"MATCH: vs {match.opponent_name} on {match.map} "
            f"| Result: {(match.result or 'In Progress').upper()}\n\n"
            "METRICS\n=======\n"
            f"  Win Rate             {metrics['win_rate']:.0%}\n"
            f"  Attack Win Rate      {metrics['attack_win_rate']:.0%}\n"
            f"  Defense Win Rate     {metrics['defense_win_rate']:.0%}\n"
            f"  Engagement Win Rate  {metrics['engagement_win_rate']:.0%}\n"
            f"  Drone Efficiency     {metrics['drone_efficiency']:.0%}\n"
            f"  Reinforcement Usage  {metrics['reinforcement_rate']:.0%}\n"
            f"  Man Advantage Conv   {metrics['man_advantage']:.0%}\n"
            f"  Clutch Rate          {metrics['clutch_rate']:.0%}\n\n"
            "ROUNDS\n======\n" + rounds_block + "\n\n"
            "PLAYERS (EW=EngWin% SR=Survival% UE=Utility% TPS=score)\n"
            "=========================================================\n"
            + players_block + "\n"
            + comms + "\n"
            "TASK\n====\n"
            "Write a tactical debrief in EXACTLY this structure:\n\n"
            "## MATCH SUMMARY\n(2 sentences: result + overall tone)\n\n"
            "## STRENGTHS\n1. (stat evidence)\n2. (stat evidence)\n\n"
            "## WEAKNESSES\n1. (stat evidence)\n2. (stat evidence)\n\n"
            "## ADJUSTMENTS\n1. (actionable)\n2. (actionable)\n3. (actionable)\n\n"
            "## STANDOUT PLAYERS\n"
            "Best TPS: (name + why)\nNeeds work: (name + why)\n\n"
            "## COMMS ANALYSIS\n(If comms data available: who called most, "
            "any coordination gaps, key callout patterns)\n\n"
            "Cite round numbers and metrics. No generic advice.\n[/INST]"
        )

    def _build_player_prompt(self, stat, pdata: dict, tps_score: float) -> str:
        return (
            "[INST] You are a Rainbow Six Siege performance coach. "
            "Be direct and specific.\n\n"
            f"PLAYER: {stat.player.name} | Op: {stat.operator.name} ({stat.operator.side})\n\n"
            "STATS\n=====\n"
            f"K/D/A           : {pdata.get('kills',0)}/{pdata.get('deaths',0)}/{pdata.get('assists',0)}\n"
            f"K/D Ratio       : {pdata.get('kd_ratio',0.0):.2f}\n"
            f"Engagement Win% : {pdata.get('engagement_win_rate',0.0):.0%}\n"
            f"Survival Rate   : {pdata.get('survival_rate',0.0):.0%}\n"
            f"Utility Eff%    : {pdata.get('utility_efficiency',0.0):.0%}\n"
            f"Plant Success%  : {pdata.get('plant_success_rate',0.0):.0%}\n"
            f"Rounds Played   : {pdata.get('rounds_played',1)}\n"
            f"TPS Score       : {tps_score:.3f}/1.0\n\n"
            "Respond in EXACTLY this format (under 80 words):\n"
            "STRENGTH: [one thing done well — cite one stat]\n"
            "WEAKNESS: [one area to improve — cite one stat]\n"
            "DRILL: [one concrete 10-minute practice drill]\n[/INST]"
        )

    def _get_transcript_summary(self, match_id: int) -> dict:
        try:
            from database.repositories import Repository
            import json
            repo = Repository()
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT processed_segments_json FROM transcripts WHERE match_id = ?",
                    (match_id,)
                ).fetchone()
            if not row or not row["processed_segments_json"]:
                return {}
            data = json.loads(row["processed_segments_json"])
            return {
                "top_locations": data.get("location_freq", {}),
                "top_actions":   data.get("action_freq",   {}),
                "coord_gaps":    len(data.get("coordination_gaps", [])),
                "word_count":    data.get("word_count", 0),
                "speakers":      data.get("speakers", {}),
            }
        except Exception:
            return {}

    def _store_metric(self, repo, match_id: int, name: str, value: str) -> None:
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