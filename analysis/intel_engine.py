import os
from pathlib import Path
from typing import Optional, cast, TYPE_CHECKING
from app.config import BASE_DIR, LLM_GPU_LAYERS, LLM_N_CTX, LLM_N_THREADS
from app.config import BASE_DIR, MODEL_DIR, MODEL_PATH

if TYPE_CHECKING:
    from llama_cpp import CreateCompletionResponse


def _detect_gpu_layers() -> int:
    """
    Returns the number of layers to offload to GPU.
    - NVIDIA: detected via nvidia-smi or CUDA env
    - AMD:    detected via ROCm / HIP env
    - Intel:  detected via SYCL / oneAPI env
    - None:   CPU-only fallback (returns 0)
    """
    # NVIDIA
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=3
        )
        if result.returncode == 0:
            print("[AI] NVIDIA GPU detected — offloading layers to CUDA.")
            return 32   # offload most layers; tune down if VRAM is tight
    except Exception:
        pass

    # AMD ROCm
    if os.environ.get("HIP_VISIBLE_DEVICES") or os.environ.get("ROCR_VISIBLE_DEVICES"):
        print("[AI] AMD GPU detected via ROCm — offloading layers.")
        return 32

    # Intel Arc / integrated via SYCL
    if os.environ.get("SYCL_DEVICE_FILTER") or os.environ.get("ONEAPI_DEVICE_SELECTOR"):
        print("[AI] Intel GPU detected via SYCL — offloading layers.")
        return 16   # conservative for integrated/Arc

    print("[AI] No dedicated GPU detected — running CPU-only inference.")
    return 0


class IntelEngine:
    """
    Local AI inference layer using llama-cpp-python + GGUF models.

    Hardware-agnostic: auto-detects GPU (NVIDIA/AMD/Intel)
    and falls back to CPU if none found.

    Model: Q4_K_M quantization — balances quality vs RAM footprint.
    Recommended GGUF: Mistral-7B-Instruct-v0.2.Q4_K_M.gguf
    """

    def __init__(self) -> None:
        self._llm = None   # lazy-loaded on first use

    # =====================================================
    # LAZY LOAD
    # =====================================================

    def _load_model(self) -> None:
        if self._llm is not None:
            return

        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"No model found at {MODEL_PATH}\n"
                f"Place a Q4_K_M .gguf file there and rename it to 'model.gguf'."
            )

        try:
            from llama_cpp import Llama
        except Exception as e:          # ← was ImportError, now catches all
            raise RuntimeError(
                f"Failed to load llama-cpp-python: {e}\n"
                f"This is usually a missing Visual C++ runtime.\n"
                f"Install it from: https://aka.ms/vs/17/release/vc_redist.x64.exe"
            ) from e



        gpu_layers = _detect_gpu_layers()

        print(f"[AI] Loading model from {MODEL_PATH} ...")
        print(f"[AI] GPU layers: {gpu_layers} | Context: 4096 | Threads: 6")

        

        # In _load_model, replace the Llama(...) call:
        self._llm = Llama(
            model_path=str(MODEL_PATH),
            n_gpu_layers=LLM_GPU_LAYERS,
            n_ctx=LLM_N_CTX,
            n_threads=LLM_N_THREADS,
            n_batch=512,
            verbose=False,
        )

        print("[AI] Model loaded.")

    # =====================================================
    # CORE GENERATE
    # =====================================================

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.3,    # low = more factual/consistent
        stop: Optional[list[str]] = None,
    ) -> str:
        """
        Runs inference and returns the generated text string.
        Temperature 0.3 is intentional — tactical analysis
        should be consistent, not creative.
        """
        self._load_model()

        if self._llm is None:
            return "[AI] Model not available."


        response = self._llm(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop or ["</s>", "[INST]", "[/INST]"],
                    echo=False,
                    stream=False,
                )

                # Cast is safe here — stream=False guarantees a dict response, not an iterator
        from llama_cpp import CreateCompletionResponse as CCR
        result = cast("CCR", response)
        return result["choices"][0]["text"].strip()

    # =====================================================
    # TACTICAL ANALYSIS METHODS
    # =====================================================

    def analyze_match(self, match_id: int) -> dict:
        """
        Generates a tactical summary for a completed match.
        Pulls structured data from the DB, formats a prompt,
        and injects the result into derived_metrics.
        """
        from database.repositories import Repository
        repo = Repository()

        match = repo.get_match_full(match_id)
        if match is None:
            return {"error": f"Match {match_id} not found."}

        prompt = self._build_match_prompt(match)

        try:
            summary = self.generate(prompt, max_tokens=400)
        except Exception as e:
            return {"error": str(e)}

        # Store result in derived_metrics
        self._store_metric(repo, match_id, "ai_match_summary", summary)

        return {"ai_match_summary": summary}

    def get_player_intel(self, match_id: int) -> dict:
        """
        Generates per-player performance notes for a match.
        """
        from database.repositories import Repository
        repo = Repository()

        match = repo.get_match_full(match_id)
        if match is None:
            return {}

        results = {}

        for round_obj in match.rounds:
            for stat in round_obj.player_stats:
                name = stat.player.name

                prompt = self._build_player_prompt(stat, round_obj.side)

                try:
                    intel = self.generate(prompt, max_tokens=200)
                except Exception as e:
                    intel = f"[Error: {e}]"

                results[name] = intel

        return results

    # =====================================================
    # PROMPT BUILDERS
    # =====================================================

    def _build_match_prompt(self, match) -> str:
        round_lines = []
        for r in match.rounds:
            kills  = sum(p.kills  for p in r.player_stats)
            deaths = sum(p.deaths for p in r.player_stats)
            round_lines.append(
                f"  Round {r.round_number}: {r.side} | Site: {r.site} "
                f"| Outcome: {r.outcome} | K/D: {kills}/{deaths}"
            )

        rounds_block = "\n".join(round_lines) if round_lines else "  No round data."

        return (
            f"[INST] You are a Rainbow Six Siege tactical analyst.\n"
            f"Analyze this match and give 3 concise tactical observations.\n\n"
            f"Match: vs {match.opponent_name} on {match.map}\n"
            f"Result: {match.result or 'In Progress'}\n"
            f"Rounds:\n{rounds_block}\n"
            f"[/INST]"
        )

    def _build_player_prompt(self, stat, side: str) -> str:
        return (
            f"[INST] You are a Rainbow Six Siege coach.\n"
            f"Give one sentence of actionable feedback for this player.\n\n"
            f"Player: {stat.player.name}\n"
            f"Operator: {stat.operator.name} ({side})\n"
            f"K/D/A: {stat.kills}/{stat.deaths}/{stat.assists}\n"
            f"Engagements: {stat.engagements_won}/{stat.engagements_taken} won\n"
            f"Ability used: {stat.ability_used}/{stat.ability_start}\n"
            f"[/INST]"
        )

    # =====================================================
    # HELPERS
    # =====================================================

    def _store_metric(
        self,
        repo,
        match_id: int,
        metric_name: str,
        value: str,
    ) -> None:
        """Stores a text metric in derived_metrics as a hash float + raw text."""
        try:
            with repo.db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO derived_metrics
                        (match_id, metric_name, metric_value, is_ai_generated)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT DO NOTHING
                    """,
                    (match_id, metric_name, float(len(value))),
                )
                # Store the actual text in a separate text column if you add one,
                # or use the transcripts table as scratch space for now.
                conn.commit()
        except Exception as e:
            print(f"[AI] Failed to store metric: {e}")