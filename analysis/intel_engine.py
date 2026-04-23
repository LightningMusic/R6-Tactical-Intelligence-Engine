import os
import sys
import time
import io
from typing import TYPE_CHECKING, Optional, Callable

from app.config import get_llm_model_path

if TYPE_CHECKING:
    from llama_cpp import CreateCompletionResponse


def _fix_frozen_paths() -> None:
    """
    In a frozen PyInstaller exe, llama_cpp loads its native DLLs via
    ctypes using a relative path. We need to tell it where _internal is.
    """
    if not getattr(sys, "frozen", False):
        return

    # Ensure stdout/stderr are never None (windowed exe has no console)
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()

    # Point llama_cpp at its lib directory inside _internal
    internal_dir = os.path.join(os.path.dirname(sys.executable), "_internal")
    llama_lib    = os.path.join(internal_dir, "llama_cpp", "lib")

    if os.path.isdir(llama_lib):
        # Prepend to PATH so Windows finds the DLLs
        os.environ["PATH"] = llama_lib + os.pathsep + os.environ.get("PATH", "")
        # Also add to DLL search path (Python 3.8+)
        try:
            os.add_dll_directory(llama_lib)
        except (AttributeError, OSError):
            pass
        print(f"[AI] llama_cpp lib path registered: {llama_lib}")
    else:
        print(f"[AI] WARNING: llama_cpp lib not found at {llama_lib}")


def _detect_gpu_layers() -> int:
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=3,
            creationflags=0x08000000 if sys.platform == "win32" else 0
        )
        if r.returncode == 0:
            print("[AI] NVIDIA GPU detected.")
            return 32
    except Exception:
        pass
    if os.environ.get("HIP_VISIBLE_DEVICES") or os.environ.get("ROCR_VISIBLE_DEVICES"):
        print("[AI] AMD GPU detected.")
        return 32
    if os.environ.get("SYCL_DEVICE_FILTER") or os.environ.get("ONEAPI_DEVICE_SELECTOR"):
        print("[AI] Intel GPU detected.")
        return 16
    print("[AI] CPU-only inference.")
    return 0


def _resolve_gpu_layers() -> int:
    from app.config import settings

    configured = settings.LLM_GPU_LAYERS
    if configured > 0:
        print(f"[AI] Using configured GPU layers: {configured}")
        return configured
    if configured == 0:
        print("[AI] GPU layers set to 0 in settings. Using CPU-only inference.")
        return 0
    return _detect_gpu_layers()


class IntelEngine:
    """
    Local AI inference using llama-cpp-python + GGUF models.
    Lazy-loaded on first use. Up to MAX_RETRIES per generation.
    """

    MAX_RETRIES = 5
    RETRY_DELAY = 2.0

    def __init__(self) -> None:
        self._llm = None
        self._load_error: Optional[str] = None  # cache load failures

    # =====================================================
    # LAZY LOAD
    # =====================================================

    def _load_model(self) -> None:
        if self._llm is not None:
            return

        # Don't retry a known-broken load
        if self._load_error is not None:
            raise RuntimeError(self._load_error)

        # Fix DLL paths before importing llama_cpp
        _fix_frozen_paths()
        model_path = get_llm_model_path()

        if not model_path.exists():
            self._load_error = (
                f"No model found at {model_path}\n"
                "Place a .gguf file in data/models/ or set llm_model_filename in settings.json."
            )
            raise FileNotFoundError(self._load_error)

        try:
            from llama_cpp import Llama
        except Exception as e:
            self._load_error = (
                f"Failed to load llama-cpp-python: {e}\n"
                "Ensure VC++ runtime is installed:\n"
                "https://aka.ms/vs/17/release/vc_redist.x64.exe"
            )
            raise RuntimeError(self._load_error) from e

        from app.config import settings
        preferred_gpu_layers = _resolve_gpu_layers()
        attempts = [preferred_gpu_layers]
        if preferred_gpu_layers > 0:
            attempts.append(0)

        last_error = None
        for gpu_layers in attempts:
            print(f"[AI] Loading: {model_path.name}")
            print(f"[AI] GPU layers={gpu_layers} | CTX={settings.LLM_N_CTX} "
                  f"| Threads={settings.LLM_N_THREADS}")
            try:
                self._llm = Llama(
                    model_path=str(model_path),
                    n_gpu_layers=gpu_layers,
                    n_ctx=settings.LLM_N_CTX,
                    n_threads=settings.LLM_N_THREADS,
                    n_batch=min(512, settings.LLM_N_CTX),
                    verbose=False,
                )
                print("[AI] Model ready.")
                return
            except Exception as e:
                last_error = e
                if gpu_layers > 0:
                    print(f"[AI] GPU-backed load failed: {e}")
                    print("[AI] Retrying with CPU-only inference...")
                else:
                    break

        detail = str(last_error) if last_error is not None else "Unknown error"
        if "0xc000001d" in detail or "-1073741795" in detail:
            detail += (
                "\nThis usually means the bundled llama runtime is using CPU instructions "
                "the machine does not support."
            )
        self._load_error = f"Model load failed: {detail}"
        raise RuntimeError(self._load_error) from last_error

    # =====================================================
    # GENERATE WITH RETRY
    # =====================================================

    def generate(
        self,
        prompt: str,
        max_tokens: int = 700,
        temperature: float = 0.35,
        stop: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
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
                progress_callback(
                    attempt, self.MAX_RETRIES,
                    f"Generating... attempt {attempt}/{self.MAX_RETRIES}"
                )

            try:
                response = self._llm(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop or ["</s>", "[INST]", "[/INST]"],
                    echo=False,
                    stream=False,
                )

                from llama_cpp import CreateCompletionResponse
                from typing import cast
                result = cast("CreateCompletionResponse", response)
                text   = result["choices"][0]["text"].strip()

                if text:
                    if progress_callback:
                        progress_callback(attempt, self.MAX_RETRIES, "Done.")
                    return text

                last_error = "Empty response"
                print(f"[AI] Attempt {attempt}: empty response, retrying...")

            except Exception as e:
                last_error = str(e)
                print(f"[AI] Attempt {attempt} failed: {e}")

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)

        return f"[AI] Failed after {self.MAX_RETRIES} attempts. Last: {last_error}"

    # =====================================================
    # MATCH ANALYSIS
    # =====================================================

    def analyze_match(
        self,
        match_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
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
            progress_callback(0, self.MAX_RETRIES, "Building match summary...")

        summary_text = self.generate(
            prompt, max_tokens=800, progress_callback=progress_callback
        )
        self._store_metric(repo, match_id, "ai_match_summary", summary_text)
        return {"ai_match_summary": summary_text}

    def get_player_intel(
        self,
        match_id: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
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
                progress_callback(
                    i + 1, len(players),
                    f"Analyzing {name} ({i+1}/{len(players)})..."
                )

            prompt = self._build_player_prompt(stat, pdata, tps.get(pid, 0.0))
            results[name] = self.generate(prompt, max_tokens=350)

        return results

    # =====================================================
    # PROMPT BUILDERS
    # =====================================================

    def _build_match_prompt(
        self,
        match,
        metrics: dict,
        summary: dict,
        tps: dict,
        transcript: dict,
    ) -> str:
        round_lines = []
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            round_lines.append(
                f"  R{r.round_number:02d} | {r.side:<8} | "
                f"{(r.site or '?'):<30} | "
                f"{'WIN' if r.outcome == 'win' else 'LOSS'} | "
                f"K/D {k}/{d}"
            )
        rounds_block = "\n".join(round_lines) or "  No round data."

        player_lines = []
        for pid, data in summary.items():
            score = tps.get(pid, 0.0)
            player_lines.append(
                f"  {data['player'].name:<18} "
                f"K:{data['kills']:>2} D:{data['deaths']:>2} "
                f"A:{data['assists']:>2} | "
                f"EW:{data['engagement_win_rate']:.0%} "
                f"SR:{data['survival_rate']:.0%} "
                f"UE:{data['utility_efficiency']:.0%} "
                f"TPS:{score:.3f}"
            )
        players_block = "\n".join(player_lines) or "  No player data."

        metrics_block = (
            f"  Win Rate             {metrics['win_rate']:.0%}\n"
            f"  Attack Win Rate      {metrics['attack_win_rate']:.0%}\n"
            f"  Defense Win Rate     {metrics['defense_win_rate']:.0%}\n"
            f"  Engagement Win Rate  {metrics['engagement_win_rate']:.0%}\n"
            f"  Drone Efficiency     {metrics['drone_efficiency']:.0%}\n"
            f"  Reinforcement Usage  {metrics['reinforcement_rate']:.0%}\n"
            f"  Man Advantage Conv   {metrics['man_advantage']:.0%}\n"
            f"  Clutch Rate          {metrics['clutch_rate']:.0%}"
        )

        comms_block = ""
        if transcript:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:4]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:4]
            comms_block = (
                f"\nCOMMUNICATIONS\n"
                f"==============\n"
                f"  Words spoken      {transcript.get('word_count', 0)}\n"
                f"  Top locations     {', '.join(top_locs) or 'none'}\n"
                f"  Top actions       {', '.join(top_actions) or 'none'}\n"
                f"  Coord gaps        {transcript.get('coord_gaps', 0)} "
                f"silence periods >8s\n"
            )

        return (
            "[INST] You are an elite Rainbow Six Siege tactical analyst. "
            "Be specific, cite data, no filler.\n\n"
            "MATCH\n"
            "=====\n"
            f"Opponent : {match.opponent_name}\n"
            f"Map      : {match.map}\n"
            f"Result   : {(match.result or 'In Progress').upper()}\n\n"
            "METRICS\n"
            "=======\n"
            f"{metrics_block}\n\n"
            "ROUNDS\n"
            "======\n"
            f"{rounds_block}\n\n"
            "PLAYERS  (EW=EngWin% SR=Survival% UE=Utility% TPS=score)\n"
            "=========================================================\n"
            f"{players_block}\n"
            f"{comms_block}\n"
            "TASK\n"
            "====\n"
            "Write a tactical debrief using EXACTLY this structure:\n\n"
            "## MATCH SUMMARY\n"
            "(2 sentences. State result and overall performance tone.)\n\n"
            "## STRENGTHS\n"
            "1. (Specific strength with stat evidence)\n"
            "2. (Specific strength with stat evidence)\n\n"
            "## WEAKNESSES\n"
            "1. (Specific weakness with stat evidence)\n"
            "2. (Specific weakness with stat evidence)\n\n"
            "## ADJUSTMENTS\n"
            "1. (Concrete actionable change for next match)\n"
            "2. (Concrete actionable change for next match)\n"
            "3. (Concrete actionable change for next match)\n\n"
            "## STANDOUT PLAYERS\n"
            "Best TPS: (name and why)\n"
            "Needs work: (name and why)\n\n"
            "Reference round numbers and specific metrics. No generic advice.\n"
            "[/INST]"
        )

    def _build_player_prompt(self, stat, pdata: dict, tps_score: float) -> str:
        return (
            "[INST] You are a Rainbow Six Siege performance coach. "
            "Be direct and specific.\n\n"
            "PLAYER\n"
            "======\n"
            f"Name     : {stat.player.name}\n"
            f"Operator : {stat.operator.name} ({stat.operator.side})\n\n"
            "STATS\n"
            "=====\n"
            f"K/D/A           : {pdata.get('kills', 0)}"
            f"/{pdata.get('deaths', 0)}"
            f"/{pdata.get('assists', 0)}\n"
            f"K/D Ratio       : {pdata.get('kd_ratio', 0.0):.2f}\n"
            f"Engagement Win% : {pdata.get('engagement_win_rate', 0.0):.0%}\n"
            f"Survival Rate   : {pdata.get('survival_rate', 0.0):.0%}\n"
            f"Utility Eff%    : {pdata.get('utility_efficiency', 0.0):.0%}\n"
            f"Plant Success%  : {pdata.get('plant_success_rate', 0.0):.0%}\n"
            f"Rounds Played   : {pdata.get('rounds_played', 1)}\n"
            f"TPS Score       : {tps_score:.3f}/1.0\n\n"
            "TASK\n"
            "====\n"
            "Respond in EXACTLY this format (under 80 words total):\n\n"
            "STRENGTH: [one thing done well — cite one stat]\n"
            "WEAKNESS: [one area to improve — cite one stat]\n"
            "DRILL: [one concrete 10-minute practice drill]\n"
            "[/INST]"
        )

    # =====================================================
    # HELPERS
    # =====================================================

    def _get_transcript_summary(self, match_id: int) -> dict:
        try:
            from database.repositories import Repository
            import json
            repo = Repository()
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT processed_segments_json FROM transcripts "
                    "WHERE match_id = ?",
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
            }
        except Exception:
            return {}

    def _store_metric(self, repo, match_id: int, name: str, value: str) -> None:
        try:
            with repo.db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO derived_metrics
                        (match_id, metric_name, metric_value, is_ai_generated)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT DO NOTHING
                    """,
                    (match_id, name, float(len(value))),
                )
                conn.commit()
        except Exception as e:
            print(f"[AI] Failed to store metric: {e}")
