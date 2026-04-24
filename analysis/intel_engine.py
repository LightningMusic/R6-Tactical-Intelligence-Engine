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
    """
    Runs Ollama from a portable exe on the USB.
    No installation needed — just extract ollama-windows-amd64.zip to E:/ollama/
    Models are stored in data/ollama_models/ on the USB.
    """

    DEFAULT_MODEL   = "llama3.2:3b"
    API_BASE        = "http://localhost:11434"
    CONNECT_TIMEOUT = 5
    READ_TIMEOUT    = 300

    def __init__(self) -> None:
        from app.config import settings
        self.model    = str(settings.get("ollama_model") or self.DEFAULT_MODEL)
        self._process: Optional[subprocess.Popen[bytes]] = None  # type: ignore[type-arg]

    # ── Server lifecycle ──────────────────────────────────────

    def _start_server(self) -> bool:
        """Start Ollama server from the USB portable exe."""
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

        # Tell Ollama where to store models (USB, not user profile)
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(OLLAMA_MODELS)
        env["OLLAMA_HOST"]   = "127.0.0.1:11434"

        OLLAMA_MODELS.mkdir(parents=True, exist_ok=True)

        print(f"[AI] Starting Ollama server from {OLLAMA_EXE} ...")
        print(f"[AI] Model storage: {OLLAMA_MODELS}")

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

        # Wait for server to become ready
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

    # ── Model management ──────────────────────────────────────

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
        """Pull model into USB storage. Shows progress."""
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

    # ── Generation ────────────────────────────────────────────

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
            temperature=0.3,
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
    """
    AI analysis engine.
    Tries Ollama (portable, GPU-accelerated) first,
    falls back to llama-cpp-python if Ollama isn't available.
    """

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

        # Try to start/connect Ollama
        if self._ollama.ensure_running():
            if self._ollama.ensure_model():
                self._backend = "ollama"
                print(f"[AI] Backend: Ollama ({self._ollama.model})")
                return self._backend
            print("[AI] Ollama running but model pull failed.")

        # Fallback to llama-cpp
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
        max_tokens: int = 900,
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

        text = self.generate(prompt, max_tokens=900, progress_callback=progress_callback)
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

    # ── Prompt builders ───────────────────────────────────────

    def _build_match_prompt(
        self,
        match: Any,
        metrics: dict[str, float],
        summary: dict[int, Any],
        tps: dict[int, float],
        transcript: dict[str, Any],
    ) -> str:
        round_lines: list[str] = []
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            round_lines.append(
                f"  R{r.round_number:02d} | {r.side:<8} | "
                f"{'WIN' if r.outcome=='win' else 'LOSS'} | "
                f"{str(r.site or '?'):<30} | K/D {k}/{d}"
            )

        player_lines: list[str] = []
        for pid, data in summary.items():
            score = float(tps.get(pid, 0.0))
            player_lines.append(
                f"  {str(data['player'].name):<18} "
                f"K:{int(data['kills']):>2} D:{int(data['deaths']):>2} "
                f"A:{int(data['assists']):>2} | "
                f"EW:{float(data['engagement_win_rate']):.0%} "
                f"SR:{float(data['survival_rate']):.0%} "
                f"UE:{float(data['utility_efficiency']):.0%} "
                f"TPS:{score:.3f}"
            )

        comms = ""
        if transcript:
            top_locs    = list(transcript.get("top_locations", {}).keys())[:5]
            top_actions = list(transcript.get("top_actions",   {}).keys())[:5]
            speakers    = dict(transcript.get("speakers", {}))
            spk_lines   = ""
            for spk, sd in list(speakers.items())[:5]:
                wc   = int(sd.get("word_count", 0))
                top  = list(sd.get("top_words", []))[:4]
                spk_lines += f"    {spk}: {wc} words, top: {', '.join(top)}\n"
            comms = (
                "\nCOMMUNICATIONS\n==============\n"
                f"  Words       : {int(transcript.get('word_count', 0))}\n"
                f"  Locations   : {', '.join(top_locs) or 'none'}\n"
                f"  Actions     : {', '.join(top_actions) or 'none'}\n"
                f"  Coord gaps  : {int(transcript.get('coord_gaps', 0))} (>8s silences)\n"
            )
            if spk_lines:
                comms += f"  Speakers:\n{spk_lines}"

        return (
            "[INST] You are an elite Rainbow Six Siege tactical analyst. "
            "Be specific, cite data, no filler.\n\n"
            f"MATCH: vs {match.opponent_name} on {match.map} "
            f"| {str(match.result or 'In Progress').upper()}\n\n"
            "METRICS\n=======\n"
            f"  Win {metrics['win_rate']:.0%} | "
            f"Atk {metrics['attack_win_rate']:.0%} | "
            f"Def {metrics['defense_win_rate']:.0%} | "
            f"Eng {metrics['engagement_win_rate']:.0%} | "
            f"Drone {metrics['drone_efficiency']:.0%} | "
            f"Reinf {metrics['reinforcement_rate']:.0%} | "
            f"ManAdv {metrics['man_advantage']:.0%} | "
            f"Clutch {metrics['clutch_rate']:.0%}\n\n"
            "ROUNDS\n======\n" + "\n".join(round_lines) + "\n\n"
            "PLAYERS (EW=EngWin% SR=Survival% UE=Utility% TPS=score)\n"
            "=========================================================\n"
            + "\n".join(player_lines) + "\n"
            + comms + "\n"
            "TASK: Write a debrief in EXACTLY this structure:\n\n"
            "## MATCH SUMMARY\n(2 sentences)\n\n"
            "## STRENGTHS\n1. (with stat)\n2. (with stat)\n\n"
            "## WEAKNESSES\n1. (with stat)\n2. (with stat)\n\n"
            "## ADJUSTMENTS\n1. (actionable)\n2. (actionable)\n3. (actionable)\n\n"
            "## STANDOUT PLAYERS\n"
            "Best TPS: (name + why)\nNeeds work: (name + why)\n\n"
            "## COMMS ANALYSIS\n"
            "(Who called most, gaps, patterns — if no comms data, skip.)\n\n"
            "Cite round numbers and metrics. No generic advice.\n[/INST]"
        )

    def _build_player_prompt(
        self,
        stat: Any,
        pdata: dict[str, Any],
        tps_score: float,
    ) -> str:
        return (
            "[INST] You are a Rainbow Six Siege performance coach. Direct and specific.\n\n"
            f"PLAYER: {stat.player.name} | {stat.operator.name} ({stat.operator.side})\n"
            f"K/D/A:{pdata.get('kills',0)}/{pdata.get('deaths',0)}/{pdata.get('assists',0)} | "
            f"KD:{float(pdata.get('kd_ratio',0)):.2f} | "
            f"EW:{float(pdata.get('engagement_win_rate',0)):.0%} | "
            f"SR:{float(pdata.get('survival_rate',0)):.0%} | "
            f"UE:{float(pdata.get('utility_efficiency',0)):.0%} | "
            f"TPS:{tps_score:.3f}\n\n"
            "Respond in EXACTLY this format:\n"
            "STRENGTH: [one thing done well — cite one stat]\n"
            "WEAKNESS: [one area to improve — cite one stat]\n"
            "DRILL: [one concrete 10-minute practice drill]\n[/INST]"
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
        """Call on app exit to stop the Ollama server process."""
        self._ollama.stop_server()