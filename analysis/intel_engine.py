import os
import time
from typing import TYPE_CHECKING, Optional, Callable

from app.config import MODEL_PATH

if TYPE_CHECKING:
    from llama_cpp import CreateCompletionResponse


def _detect_gpu_layers() -> int:
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=3)
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


class IntelEngine:
    """
    Local AI inference layer using llama-cpp-python + GGUF models.
    Hardware-agnostic. Lazy-loaded on first use.
    Up to MAX_RETRIES attempts per generation call.
    """

    MAX_RETRIES = 5
    RETRY_DELAY = 2.0

    def __init__(self) -> None:
        self._llm = None

    # =====================================================
    # LAZY LOAD
    # =====================================================

    def _load_model(self) -> None:
        if self._llm is not None:
            return

        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"No model found at {MODEL_PATH}\n"
                "Place a Q4_K_M .gguf file there and rename it to 'model.gguf'."
            )

        try:
            from llama_cpp import Llama
        except Exception as e:
            raise RuntimeError(
                f"Failed to load llama-cpp-python: {e}\n"
                "Install VC++ runtime: https://aka.ms/vs/17/release/vc_redist.x64.exe"
            ) from e

        from app.config import settings
        gpu_layers = _detect_gpu_layers()

        print(f"[AI] Loading model: {MODEL_PATH.name}")
        print(f"[AI] GPU layers: {gpu_layers} | CTX: {settings.LLM_N_CTX} "
              f"| Threads: {settings.LLM_N_THREADS}")

        self._llm = Llama(
            model_path=str(MODEL_PATH),
            n_gpu_layers=gpu_layers,
            n_ctx=settings.LLM_N_CTX,
            n_threads=settings.LLM_N_THREADS,
            n_batch=512,
            verbose=False,
        )
        print("[AI] Model ready.")

    # =====================================================
    # GENERATE WITH RETRY
    # =====================================================

    def generate(
        self,
        prompt: str,
        max_tokens: int = 700,
        temperature: float = 0.3,
        stop: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> str:
        self._load_model()

        if self._llm is None:
            return "[AI] Model not available."

        last_error = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            if progress_callback:
                progress_callback(
                    attempt, self.MAX_RETRIES,
                    f"Attempt {attempt}/{self.MAX_RETRIES}..."
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

            except Exception as e:
                last_error = str(e)
                print(f"[AI] Attempt {attempt} failed: {e}")

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)

        return f"[AI] Failed after {self.MAX_RETRIES} attempts. Last error: {last_error}"

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
            progress_callback(0, self.MAX_RETRIES, "Generating match summary...")

        summary_text = self.generate(
            prompt, max_tokens=700, progress_callback=progress_callback
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

        # Deduplicate players across rounds
        seen: dict = {}
        for r in match.rounds:
            for stat in r.player_stats:
                seen.setdefault(stat.player_id, stat)
        players = list(seen.values())

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
            results[name] = self.generate(prompt, max_tokens=300)

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
                f"  R{r.round_number:02d} | {r.side:<8} | {(r.site or '?'):<28} "
                f"| {r.outcome.upper():<4} | K/D {k}/{d}"
            )
        rounds_block = "\n".join(round_lines) or "  No round data."

        player_lines = []
        for pid, data in summary.items():
            score = tps.get(pid, 0.0)
            player_lines.append(
                f"  {data['player'].name:<18} "
                f"K:{data['kills']} D:{data['deaths']} A:{data['assists']} | "
                f"EngW%:{data['engagement_win_rate']:.0%} "
                f"Surv%:{data['survival_rate']:.0%} "
                f"Util%:{data['utility_efficiency']:.0%} "
                f"TPS:{score:.3f}"
            )
        players_block = "\n".join(player_lines) or "  No player data."

        metrics_block = (
            f"  Win Rate:             {metrics['win_rate']:.0%}\n"
            f"  Attack Win Rate:      {metrics['attack_win_rate']:.0%}\n"
            f"  Defense Win Rate:     {metrics['defense_win_rate']:.0%}\n"
            f"  Engagement Win Rate:  {metrics['engagement_win_rate']:.0%}\n"
            f"  Drone Efficiency:     {metrics['drone_efficiency']:.0%}\n"
            f"  Reinforcement Usage:  {metrics['reinforcement_rate']:.0%}\n"
            f"  Man Advantage Conv:   {metrics['man_advantage']:.0%}\n"
            f"  Clutch Rate:          {metrics['clutch_rate']:.0%}"
        )

        comms_block = ""
        if transcript:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:4]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:4]
            comms_block = (
                f"\nCommunications Analysis:\n"
                f"  Words spoken:       {transcript.get('word_count', 0)}\n"
                f"  Top locations:      {', '.join(top_locs) or 'none'}\n"
                f"  Top actions:        {', '.join(top_actions) or 'none'}\n"
                f"  Coordination gaps:  {transcript.get('coord_gaps', 0)} "
                f"silence periods >8s\n"
            )

        return (
            f"[INST] You are an elite Rainbow Six Siege tactical analyst with deep "
            f"knowledge of the game's meta, operator synergies, and team coordination.\n\n"
            f"MATCH CONTEXT\n"
            f"=============\n"
            f"Opponent : {match.opponent_name}\n"
            f"Map      : {match.map}\n"
            f"Result   : {(match.result or 'In Progress').upper()}\n\n"
            f"PERFORMANCE METRICS\n"
            f"===================\n"
            f"{metrics_block}\n\n"
            f"ROUND BREAKDOWN\n"
            f"===============\n"
            f"{rounds_block}\n\n"
            f"PLAYER STATISTICS\n"
            f"=================\n"
            f"{players_block}\n"
            f"{comms_block}\n"
            f"ANALYSIS TASK\n"
            f"=============\n"
            f"Provide a structured tactical debrief:\n\n"
            f"1. MATCH SUMMARY (2-3 sentences on overall performance)\n\n"
            f"2. KEY STRENGTHS (2 specific things done well, cite data)\n\n"
            f"3. CRITICAL WEAKNESSES (2 areas needing improvement, cite data)\n\n"
            f"4. TACTICAL ADJUSTMENTS (3 concrete recommendations for next match)\n\n"
            f"5. STANDOUT PLAYERS (highest and lowest TPS scorer — explain why)\n\n"
            f"Be specific. Reference round numbers, operators, and metrics. "
            f"No generic advice.\n"
            f"[/INST]"
        )

    def _build_player_prompt(self, stat, pdata: dict, tps_score: float) -> str:
        return (
            f"[INST] You are a Rainbow Six Siege performance coach.\n\n"
            f"PLAYER PROFILE\n"
            f"==============\n"
            f"Player   : {stat.player.name}\n"
            f"Operator : {stat.operator.name} ({stat.operator.side})\n\n"
            f"AGGREGATE STATS\n"
            f"===============\n"
            f"K/D/A            : {pdata.get('kills', 0)} / "
            f"{pdata.get('deaths', 0)} / {pdata.get('assists', 0)}\n"
            f"K/D Ratio        : {pdata.get('kd_ratio', 0.0):.2f}\n"
            f"Engagement Win%  : {pdata.get('engagement_win_rate', 0.0):.0%}\n"
            f"Survival Rate    : {pdata.get('survival_rate', 0.0):.0%}\n"
            f"Utility Eff%     : {pdata.get('utility_efficiency', 0.0):.0%}\n"
            f"Plant Success%   : {pdata.get('plant_success_rate', 0.0):.0%}\n"
            f"Rounds Played    : {pdata.get('rounds_played', 1)}\n"
            f"TPS Score        : {tps_score:.3f} / ~1.0\n\n"
            f"COACHING TASK\n"
            f"=============\n"
            f"Format your response exactly as:\n\n"
            f"STRENGTH: (one thing done well — cite a stat)\n"
            f"WEAKNESS: (one area to improve — cite a stat)\n"
            f"DRILL: (one concrete practice recommendation)\n\n"
            f"Under 80 words. Direct and specific.\n"
            f"[/INST]"
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