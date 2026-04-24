import os
import sys
import time
import io
import json
import subprocess
from typing import Optional, Callable

from app.config import MODEL_PATH


def _ensure_console() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _detect_hardware() -> tuple[int, int]:
    """Returns (n_gpu_layers, n_threads) for llama-cpp fallback."""
    try:
        import psutil
        n_threads = min(psutil.cpu_count(logical=False) or 8, 16)
    except Exception:
        n_threads = 8

    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
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


class _OllamaBackend:
    """
    Calls a locally running Ollama server.
    Ollama auto-detects GPU, needs no admin, no AVX worries.
    """

    DEFAULT_MODEL  = "llama3.2:3b"
    OLLAMA_URL     = "http://localhost:11434/api/generate"
    CONNECT_TIMEOUT = 5
    READ_TIMEOUT    = 300

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    def is_available(self) -> bool:
        try:
            import urllib.request
            req = urllib.request.urlopen(
                "http://localhost:11434/api/tags",
                timeout=self.CONNECT_TIMEOUT,
            )
            return req.status == 200
        except Exception:
            return False

    def model_is_pulled(self) -> bool:
        try:
            import urllib.request
            req  = urllib.request.urlopen(
                "http://localhost:11434/api/tags",
                timeout=self.CONNECT_TIMEOUT,
            )
            data = json.loads(req.read().decode())
            models = [m.get("name","") for m in data.get("models", [])]
            return any(self.model.split(":")[0] in m for m in models)
        except Exception:
            return False

    def ensure_model(self) -> bool:
        """Pulls the model if not already present. Returns True on success."""
        if self.model_is_pulled():
            return True
        print(f"[AI] Pulling Ollama model: {self.model} ...")
        try:
            import urllib.request
            import urllib.error
            body = json.dumps({"name": self.model}).encode()
            req  = urllib.request.Request(
                "http://localhost:11434/api/pull",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in resp:
                    try:
                        d = json.loads(line.decode())
                        status = d.get("status", "")
                        if status:
                            print(f"[AI] {status}")
                    except Exception:
                        pass
            return self.model_is_pulled()
        except Exception as e:
            print(f"[AI] Model pull failed: {e}")
            return False

    def generate(self, prompt: str, max_tokens: int = 900) -> str:
        import urllib.request
        import urllib.error

        body = json.dumps({
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.3,
                "top_p":       0.9,
                "stop":        ["[/INST]", "</s>"],
            },
        }).encode()

        req = urllib.request.Request(
            self.OLLAMA_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return data.get("response", "").strip()
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request failed: {e}")


class _LlamaCppBackend:
    """
    Fallback: direct llama-cpp-python if Ollama isn't running.
    """

    def __init__(self) -> None:
        self._llm        = None
        self._load_error: Optional[str] = None

    def load(self) -> None:
        if self._llm is not None:
            return
        if self._load_error:
            raise RuntimeError(self._load_error)

        if not MODEL_PATH.exists():
            self._load_error = (
                f"No model at {MODEL_PATH}\n"
                "Place a Q4_K_M GGUF file there named model.gguf"
            )
            raise FileNotFoundError(self._load_error)

        try:
            from llama_cpp import Llama
        except Exception as e:
            self._load_error = (
                f"llama_cpp import failed: {e}\n"
                "Try: pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
            )
            raise RuntimeError(self._load_error) from e

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
            self._load_error = f"Model load failed: {e}"
            raise RuntimeError(self._load_error) from e

    def generate(self, prompt: str, max_tokens: int = 900) -> str:
        self.load()
        from llama_cpp import CreateCompletionResponse
        from typing import cast
        response = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=0.3,
            stop=["</s>", "[INST]", "[/INST]"],
            echo=False,
            stream=False,
        )
        result = cast("CreateCompletionResponse", response)
        return result["choices"][0]["text"].strip()


class IntelEngine:
    """
    AI analysis engine.
    Priority: Ollama (best) → llama-cpp-python (fallback) → error message.
    """

    MAX_RETRIES = 2
    RETRY_DELAY = 1.0

    def __init__(self) -> None:
        _ensure_console()
        self._ollama     = _OllamaBackend()
        self._llama_cpp  = _LlamaCppBackend()
        self._backend: Optional[str] = None   # "ollama" | "llama_cpp" | None

    def _select_backend(self) -> str:
        if self._backend is not None:
            return self._backend

        # Try Ollama first
        if self._ollama.is_available():
            if not self._ollama.model_is_pulled():
                print(f"[AI] Ollama running — pulling {self._ollama.model}...")
                if self._ollama.ensure_model():
                    self._backend = "ollama"
                    print(f"[AI] Backend: Ollama ({self._ollama.model})")
                    return self._backend
            else:
                self._backend = "ollama"
                print(f"[AI] Backend: Ollama ({self._ollama.model})")
                return self._backend

        print("[AI] Ollama not available — trying llama-cpp-python...")

        # Try llama-cpp
        try:
            self._llama_cpp.load()
            self._backend = "llama_cpp"
            print("[AI] Backend: llama-cpp-python")
            return self._backend
        except Exception as e:
            print(f"[AI] llama-cpp also unavailable: {e}")
            self._backend = "none"
            return self._backend

    def generate(
        self,
        prompt: str,
        max_tokens: int = 900,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        backend = self._select_backend()

        if backend == "none":
            return (
                "[AI unavailable]\n"
                "To enable AI analysis, either:\n"
                "  1. Install Ollama: https://ollama.com/download\n"
                "     Then run: ollama pull llama3.2:3b\n"
                "  2. Place a model.gguf in data/models/ and install\n"
                "     a compatible llama-cpp-python wheel."
            )

        for attempt in range(1, self.MAX_RETRIES + 1):
            if progress_callback:
                progress_callback(
                    attempt, self.MAX_RETRIES,
                    f"Generating via {backend}... ({attempt}/{self.MAX_RETRIES})"
                )
            try:
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
            progress_callback(0, 1, "Generating match summary...")

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

    # ── Prompt builders ───────────────────────────────────────

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

        comms = ""
        if transcript:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:5]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:5]
            speakers    = transcript.get("speakers", {})
            spk_lines   = ""
            for spk, sd in list(speakers.items())[:5]:
                spk_lines += (
                    f"    {spk}: {sd.get('word_count',0)} words, "
                    f"top: {', '.join(sd.get('top_words',[])[:4])}\n"
                )
            comms = (
                "\nCOMMUNICATIONS\n==============\n"
                f"  Words       : {transcript.get('word_count',0)}\n"
                f"  Locations   : {', '.join(top_locs) or 'none'}\n"
                f"  Actions     : {', '.join(top_actions) or 'none'}\n"
                f"  Coord gaps  : {transcript.get('coord_gaps',0)} (>8s silences)\n"
            )
            if spk_lines:
                comms += f"  Speakers:\n{spk_lines}"

        return (
            "[INST] You are an elite Rainbow Six Siege tactical analyst. "
            "Be specific, cite data, no filler.\n\n"
            f"MATCH: vs {match.opponent_name} on {match.map} "
            f"| {(match.result or 'In Progress').upper()}\n\n"
            "METRICS\n=======\n"
            f"  Win Rate {metrics['win_rate']:.0%} | "
            f"Atk {metrics['attack_win_rate']:.0%} | "
            f"Def {metrics['defense_win_rate']:.0%} | "
            f"Eng {metrics['engagement_win_rate']:.0%}\n"
            f"  Drone Eff {metrics['drone_efficiency']:.0%} | "
            f"Reinf {metrics['reinforcement_rate']:.0%} | "
            f"ManAdv {metrics['man_advantage']:.0%} | "
            f"Clutch {metrics['clutch_rate']:.0%}\n\n"
            "ROUNDS\n======\n" + "\n".join(round_lines) + "\n\n"
            "PLAYERS (EW=EngWin% SR=Survival% UE=Utility% TPS=score)\n"
            "=========================================================\n"
            + "\n".join(player_lines) + "\n"
            + comms + "\n"
            "TASK: Write a debrief in EXACTLY this structure:\n\n"
            "## MATCH SUMMARY\n(2 sentences: result and overall tone)\n\n"
            "## STRENGTHS\n1. (with stat)\n2. (with stat)\n\n"
            "## WEAKNESSES\n1. (with stat)\n2. (with stat)\n\n"
            "## ADJUSTMENTS\n1. (actionable)\n2. (actionable)\n3. (actionable)\n\n"
            "## STANDOUT PLAYERS\n"
            "Best TPS: (name + why)\n"
            "Needs work: (name + why)\n\n"
            "## COMMS ANALYSIS\n"
            "(Who called most, coordination gaps, key callout patterns)\n\n"
            "Cite round numbers and metrics. No generic advice.\n[/INST]"
        )

    def _build_player_prompt(self, stat, pdata: dict, tps_score: float) -> str:
        return (
            "[INST] You are a Rainbow Six Siege performance coach. Direct and specific.\n\n"
            f"PLAYER: {stat.player.name} | {stat.operator.name} ({stat.operator.side})\n"
            f"K/D/A: {pdata.get('kills',0)}/{pdata.get('deaths',0)}/{pdata.get('assists',0)} | "
            f"KD:{pdata.get('kd_ratio',0.0):.2f} | "
            f"EW:{pdata.get('engagement_win_rate',0.0):.0%} | "
            f"SR:{pdata.get('survival_rate',0.0):.0%} | "
            f"UE:{pdata.get('utility_efficiency',0.0):.0%} | "
            f"TPS:{tps_score:.3f}\n\n"
            "Respond in EXACTLY this format:\n"
            "STRENGTH: [one thing done well — cite one stat]\n"
            "WEAKNESS: [one area to improve — cite one stat]\n"
            "DRILL: [one concrete 10-minute practice drill]\n[/INST]"
        )

    def _get_transcript_summary(self, match_id: int) -> dict:
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