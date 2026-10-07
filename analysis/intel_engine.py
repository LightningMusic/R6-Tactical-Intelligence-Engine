import os
import sys
import io
import json
import time
import subprocess
from pathlib import Path
from typing import Any, Optional, Callable

_AI_FAILURE_MARKERS = ("[AI unavailable]", "[AI] Generation failed")


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

    # Without an explicit num_ctx, Ollama allocates the model's full trained
    # context -- 131,072 tokens for llama3.2 -- which put an 18 GB footprint
    # on an 8 GB card, forced a 60/40 CPU/GPU split and a ~56 s cold load,
    # all for prompts a few thousand tokens long. 8192 covers every prompt
    # this engine builds with room to spare; generate() warns if one ever
    # gets close.
    DEFAULT_NUM_CTX = 8192

    def __init__(
        self,
        ollama_exe: Optional[Path] = None,
        ollama_models: Optional[Path] = None,
        default_model: Optional[str] = None,
        options: Optional[dict] = None,
        ollama_url: Optional[str] = None,
    ) -> None:
        """
        ollama_url: talk to an Ollama that something else runs (a container)
        instead of launching a local ollama.exe; nothing is spawned then.

        options: Ollama runtime options applied to every request --
        num_ctx, num_gpu, num_thread. Anything not given explicitly is read
        from the client's settings (ollama_num_ctx / ollama_num_gpu /
        ollama_num_thread) and otherwise left to the defaults above. num_gpu
        is deliberately NOT defaulted: forcing CPU-only is right on a machine
        whose GPU is slower than its CPU (the server's case) and badly wrong
        on one with a strong GPU, and the client runs on whatever laptop the
        USB is plugged into.
        """
        self.api_base: str = (ollama_url or self.API_BASE).rstrip("/")
        self.external: bool = bool(ollama_url)

        if ollama_exe is None or ollama_models is None:
            from app.config import OLLAMA_EXE as _CLIENT_OLLAMA_EXE
            from app.config import OLLAMA_MODELS as _CLIENT_OLLAMA_MODELS
            ollama_exe = ollama_exe if ollama_exe is not None else _CLIENT_OLLAMA_EXE
            ollama_models = ollama_models if ollama_models is not None else _CLIENT_OLLAMA_MODELS

        self.ollama_exe: Path = ollama_exe
        self.ollama_models: Path = ollama_models

        if default_model is not None:
            self.model = default_model
        else:
            from app.config import settings
            self.model = str(settings.get("ollama_model") or self.DEFAULT_MODEL)

        self.options = self._resolve_options(options or {})

        self._process: Optional[subprocess.Popen[bytes]] = None  # type: ignore[type-arg]

    def _resolve_options(self, explicit: dict) -> dict:
        resolved: dict = {"num_ctx": self.DEFAULT_NUM_CTX}
        try:
            from app.config import settings
            for key in ("num_ctx", "num_gpu", "num_thread"):
                val = settings.get(f"ollama_{key}")
                if val is not None and str(val).strip() != "":
                    resolved[key] = int(val)
        except Exception:
            pass
        for key, val in explicit.items():
            if val is not None:
                resolved[key] = int(val)
        return resolved

    def _start_server(self) -> bool:
        if self.external:
            print(f"[AI] Ollama at {self.api_base} is not answering (it is managed outside this process).")
            return False

        if not self.ollama_exe.exists():
            print(
                f"[AI] Ollama exe not found at {self.ollama_exe}\n"
                "Download ollama-windows-amd64.zip from "
                "https://github.com/ollama/ollama/releases and extract to "
                f"{self.ollama_exe.parent}"
            )
            return False

        if self.is_running():
            return True

        env = os.environ.copy()
        env["OLLAMA_MODELS"] = str(self.ollama_models)
        env["OLLAMA_HOST"]   = "127.0.0.1:11434"
        self.ollama_models.mkdir(parents=True, exist_ok=True)

        print(f"[AI] Starting Ollama server from {self.ollama_exe} ...")
        try:
            self._process = subprocess.Popen(
                [str(self.ollama_exe), "serve"],
                env=env,
                # 2026-09-11: was DEVNULL/DEVNULL -- Ollama's own output
                # (including why it failed, if it does, after this call
                # returns) was being thrown away entirely. Now piped into
                # the app's own rotating log file instead (tagged
                # "ollama") via pipe_process_to_log() below, so a failure
                # here is no longer a silent dead end. stderr is merged
                # into stdout since Ollama (like most CLI tools) logs to
                # stderr by default.
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32"
                    else 0
                ),
            )
            from app.logging_setup import pipe_process_to_log
            pipe_process_to_log(self._process, "ollama")
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
                f"{self.api_base}/api/tags",
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
                f"{self.api_base}/api/tags",
                timeout=self.CONNECT_TIMEOUT,
            )
            data = json.loads(req.read().decode())
            names = {
                self._normalize_tag(str(m.get("name", "")))
                for m in (data.get("models") or [])
            }
            # Exact tag match. This used to substring-match on the part
            # before the colon, so asking for "qwen2.5:7b" counted as
            # already downloaded whenever "qwen2.5-coder:7b" existed -- the
            # pull was skipped and every generation then failed with
            # "model not found".
            return self._normalize_tag(self.model) in names
        except Exception:
            return False

    @staticmethod
    def _normalize_tag(name: str) -> str:
        name = name.strip().lower()
        return name if ":" in name else f"{name}:latest"

    def pull_model(self) -> bool:
        print(f"[AI] Pulling model: {self.model} → {self.api_base if self.external else self.ollama_models}")
        try:
            import urllib.request
            body = json.dumps({"name": self.model}).encode()
            req  = urllib.request.Request(
                f"{self.api_base}/api/pull",
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
                **self.options,
                "num_predict": max_tokens,
                "temperature": 0.2,
                "top_p":       0.9,
                "stop":        ["[/INST]", "</s>"],
            },
        }).encode()

        req = urllib.request.Request(
            f"{self.api_base}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                # Ollama silently drops the start of a prompt that overflows
                # num_ctx rather than erroring, which would quietly strip
                # match data out of the analysis. Make that visible.
                used = int(data.get("prompt_eval_count") or 0) + max_tokens
                ctx = int(self.options.get("num_ctx") or 0)
                if ctx and used > ctx * 0.9:
                    print(
                        f"[AI] WARNING: prompt ({data.get('prompt_eval_count')} tok) + "
                        f"output budget ({max_tokens}) is near num_ctx={ctx}; "
                        f"raise ollama_num_ctx if analyses look truncated."
                    )
                return str(data.get("response") or "").strip()
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e


# =====================================================
# LLAMA-CPP FALLBACK BACKEND
# =====================================================

class _LlamaCppBackend:

    def __init__(self, model_path: Optional[Path] = None) -> None:
        if model_path is None:
            from app.config import MODEL_PATH as _CLIENT_MODEL_PATH
            model_path = _CLIENT_MODEL_PATH
        self.model_path: Path = model_path
        self._llm: Any   = None
        self._error: Optional[str] = None

    def load(self) -> None:
        if self._llm is not None:
            return
        if self._error:
            raise RuntimeError(self._error)

        if not self.model_path.exists():
            self._error = (
                f"No GGUF model at {self.model_path}\n"
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
                model_path=str(self.model_path),
                n_gpu_layers=gpu_layers,
                n_ctx=settings.LLM_N_CTX,
                n_threads=n_threads,
                n_batch=512,
                verbose=False,
                use_mlock=False,
                use_mmap=True,
            )
            print(f"[AI] llama-cpp loaded: {self.model_path.name}")
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

    def __init__(
        self,
        model_path: Optional[Path] = None,
        ollama_exe: Optional[Path] = None,
        ollama_models: Optional[Path] = None,
        default_model: Optional[str] = None,
        db_path: Optional[Path] = None,
        schema_path: Optional[Path] = None,
        ollama_options: Optional[dict] = None,
        ollama_url: Optional[str] = None,
    ) -> None:
        """
        All paths default to the client's own app.config locations, same as
        before. Passing them explicitly — as server/services/session_processing.py
        does — points this exact class (Ollama-first, llama-cpp fallback,
        same prompts, same MetricsEngine) at the server's own portable-Ollama
        install and its own match database instead, with zero behavior change
        for the client.
        """
        _ensure_console()
        self._ollama    = _OllamaBackend(ollama_exe, ollama_models, default_model, ollama_options, ollama_url)
        self._llama_cpp = _LlamaCppBackend(model_path)
        self._backend: Optional[str] = None
        self._db_path = db_path
        self._schema_path = schema_path

    def _make_repo(self):
        from database.repositories import Repository

        return Repository(db_path=self._db_path, schema_path=self._schema_path)

    def store_ai_text(self, repo, match_id: int, name: str, value: str) -> None:
        """Public alias for _store_metric — lets callers (e.g. the server's
        per-player intel loop, which IntelEngine itself doesn't persist)
        save an AI-generated text result the same way analyze_match does."""
        self._store_metric(repo, match_id, name, value)

    def _select_backend(self) -> str:
        if self._backend is not None:
            return self._backend

        if self._ollama.ensure_running():
            if self._ollama.ensure_model():
                self._backend = "ollama"
                print(f"[AI] Backend: Ollama ({self._ollama.model}, options={self._ollama.options})")
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

        if backend == "none" and self._ollama.external:
            return (
                "[AI unavailable]\n"
                f"The Ollama service at {self._ollama.api_base} isn't answering, "
                f"or couldn't load the model {self._ollama.model}."
            )

        if backend == "none":
            return (
                "[AI unavailable]\n"
                "To enable AI analysis:\n"
                "  Option A (recommended): Download ollama-windows-amd64.zip from\n"
                "    https://github.com/ollama/ollama/releases\n"
                f"    and extract to {self._ollama.ollama_exe.parent}\n"
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

    def _resolve_ours(self, repo, match_id: int, match, explicit) -> Optional[set]:
        """Which players are on our team: given outright (the server reads it
        from the replay), remembered from an earlier pass, or flagged as team
        members. None means unknown, and the old everyone-in-the-match
        behaviour applies."""
        from analysis import team_facts

        names = team_facts.ours_set(explicit)
        try:
            if names:
                self._store_metric(repo, match_id, "our_players", json.dumps(sorted(names)))
                return names
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = 'our_players'",
                    (match_id,),
                ).fetchone()
            if row and row[0]:
                return team_facts.ours_set(json.loads(row[0]))
            flagged = {team_facts.norm(s.player.name) for r in match.rounds for s in r.player_stats
                       if getattr(s.player, "is_team_member", False)}
            if len(flagged) >= 3:
                return flagged
        except Exception as e:
            print(f"[AI] Could not work out which players are ours ({e}); using everyone.")
        return None

    def _player_baselines(self, repo, match_id: int, names: set) -> dict[str, dict]:
        """Each named player's results over every OTHER stored match, so a
        night can be judged against that player's own normal."""
        if not names:
            return {}
        marks = ",".join("?" for _ in names)
        try:
            with repo.db.get_connection() as conn:
                rows = conn.execute(
                    f"""SELECT lower(p.name) AS n, COUNT(DISTINCT r.match_id) AS matches, COUNT(*) AS rounds,
                               SUM(s.kills) AS k, SUM(s.deaths) AS d,
                               SUM(CASE WHEN s.deaths = 0 THEN 1 ELSE 0 END) AS surv,
                               SUM(s.engagements_won) AS ew, SUM(s.engagements_taken) AS et
                        FROM player_round_stats s
                        JOIN rounds r ON r.round_id = s.round_id
                        JOIN players p ON p.player_id = s.player_id
                        WHERE r.match_id != ? AND lower(p.name) IN ({marks})
                        GROUP BY p.player_id""",
                    (match_id, *sorted(names)),
                ).fetchall()
        except Exception as e:
            print(f"[AI] Could not load player history ({e}).")
            return {}
        out: dict[str, dict] = {}
        for r in rows:
            k, d, rounds = int(r["k"] or 0), int(r["d"] or 0), int(r["rounds"] or 0)
            et = int(r["et"] or 0)
            out[r["n"]] = {
                "matches": int(r["matches"]), "rounds": rounds,
                "kd": (k / d) if d else float(k),
                "survival": (int(r["surv"] or 0) / rounds) if rounds else 0.0,
                "ewr": (int(r["ew"] or 0) / et) if et else None,
            }
        return out

    def _team_usual(self, repo, match_id: int) -> Optional[dict]:
        """Totals over every OTHER stored match whose team is known, so a night can be judged against the
        team's own normal. None when there are too few to call anything usual (or on any trouble: this is a
        nicety, never a reason to fail a debrief)."""
        from analysis import team_facts
        try:
            with repo.db.get_connection() as conn:
                ids = [r[0] for r in conn.execute("SELECT match_id FROM matches WHERE match_id != ?", (match_id,))]
                teams = {r[0]: r[1] for r in conn.execute(
                    "SELECT match_id, metric_text FROM derived_metrics "
                    "WHERE metric_name = 'our_players' AND metric_text IS NOT NULL")}
        except Exception:
            return None
        records = []
        for mid in ids:
            try:
                names = team_facts.ours_set(json.loads(teams[mid])) if mid in teams else None
                other = repo.get_match_full(mid) if names else None
                if other is not None and other.rounds:
                    records.append(team_facts.match_record(other, names, self._get_round_events(mid)))
            except Exception:
                continue
        return team_facts.usual_baseline(records)

    def analyze_match(
        self,
        match_id: int,
        progress_callback: Optional[Callable[..., Any]] = None,
        our_players: Optional[Any] = None,
        display_names: Optional[dict] = None,
        report_players: Optional[Any] = None,
    ) -> dict[str, Any]:
        from analysis import team_facts
        from analysis.metrics_engine import MetricsEngine

        repo  = self._make_repo()
        match = repo.get_match_full(match_id)
        if match is None:
            return {"error": f"Match {match_id} not found."}

        ours    = self._resolve_ours(repo, match_id, match, our_players)
        display = {str(k).lower(): v for k, v in (display_names or {}).items()}
        facts   = (team_facts.build_match_facts(match, ours, self._get_round_events(match_id), display,
                                                report_players=team_facts.ours_set(report_players),
                                                usual=self._team_usual(repo, match_id))
                   if ours else None)
        if facts:
            facts["comms_counts"] = self._comms_counts(repo, match_id)

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
        prompt     = self._build_match_prompt(match, metrics, summary, tps, transcript, facts=facts, display=display)

        if progress_callback:
            progress_callback(0, 1, "Generating match summary...")

        text = self.generate(prompt, max_tokens=1100, progress_callback=progress_callback)
        if facts and not text.startswith(_AI_FAILURE_MARKERS):
            # The model writes the summary and the comms paragraph; everything counted or judged
            # (patterns, objective play, utility, operators, focus points) is assembled in code.
            text = team_facts.assemble_report(text, facts, team_facts.focus_points(match, facts))
        self._store_metric(repo, match_id, "ai_match_summary", text)
        return {"ai_match_summary": text}

    def get_player_intel(
        self,
        match_id: int,
        progress_callback: Optional[Callable[..., Any]] = None,
        our_players: Optional[Any] = None,
        display_names: Optional[dict] = None,
        report_players: Optional[Any] = None,
    ) -> dict[str, Any]:
        from analysis import team_facts
        from analysis.metrics_engine import MetricsEngine

        repo  = self._make_repo()
        match = repo.get_match_full(match_id)
        if match is None:
            return {}

        ours    = self._resolve_ours(repo, match_id, match, our_players)
        display = {str(k).lower(): v for k, v in (display_names or {}).items()}

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()

        named = team_facts.ours_set(report_players)      # the saved team list: only these get a write-up
        seen: dict[int, Any] = {}
        for r in match.rounds:
            for stat in r.player_stats:
                pn = team_facts.norm(stat.player.name)
                if ours is not None and pn not in ours:
                    continue            # opponents are not analysed
                if named is not None and pn not in named:
                    continue            # neither is a random who was only on our team this match
                seen.setdefault(stat.player_id, stat)

        players = list(seen.values())
        if not players:
            return {}

        context = None
        if ours is not None:
            events    = self._get_round_events(match_id)
            table     = team_facts.player_table(match, ours)
            context = {
                "table":     table,
                "team":      team_facts.team_totals(table),
                "baselines": self._player_baselines(repo, match_id, ours),
                "opening":   team_facts.opening_counts(events),
                "measured":  team_facts.utility_measured(match, ours, events),
                "objective": team_facts.player_objective(events, ours),
                "secondary": team_facts.player_secondary(events, ours),
            }

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
            if context is not None and name in context["table"]:
                # Strength, weakness and drill are all chosen in code from checked facts: the model
                # labelled "in line with usual" as a weakness and gave generic or backwards drills.
                row = context["table"][name]
                pu = team_facts.player_utility(row) if context["measured"] else None
                facts = team_facts.player_facts(
                    row, context["team"], context["baselines"].get(team_facts.norm(name)),
                    context["opening"].get(team_facts.norm(name)), utility=pu,
                    objective=context["objective"].get(team_facts.norm(name)),
                )
                strength, weakness = team_facts.pick_lines(facts)
                none = "Nothing clearly stands out tonight."
                drill = (team_facts.drill_for(weakness) if weakness
                         else "Keep the current routine; nothing needs fixing tonight.")
                lines = [f"STRENGTH: {strength or none}", f"FOCUS: {weakness or none}", f"DRILL: {drill}"]
                if context["measured"]:
                    lines.append("UTILITY: " + (
                        f"gadget used in {pu['used_rounds']} of {pu['rounds']} rounds that had one; "
                        f"charges used {pu['charges_used']} of {pu['charges_total']}."
                        if pu else "no countable operator gadget this match."))
                ops = team_facts.operator_text(row)
                if ops:
                    lines.append(f"OPERATORS: {ops}")
                sec = context["secondary"].get(team_facts.norm(name))
                if sec:
                    lines.append("SECONDARY: " + team_facts.secondary_text(sec))
                obj = context["objective"].get(team_facts.norm(name))
                if obj and (obj["plants"] or obj["defuses"] or obj["cut"]):
                    bits = []
                    if obj["plants"]:
                        bits.append("planted " + team_facts.objective_text(obj["plants"]))
                    if obj["defuses"]:
                        bits.append("disabled the enemy defuser " + team_facts.objective_text(obj["defuses"]))
                    if obj["cut"]:
                        bits.append(f"{obj['cut']} attempt(s) did not finish")
                    lines.append("OBJECTIVE: " + "; ".join(bits) + ".")
                results[name] = "\n".join(lines)
                continue
            comms_lines = self._get_player_transcript_lines(match_id, name)
            prompt = self._build_player_prompt(stat, pdata, float(tps.get(pid, 0.0)), comms_lines)
            results[name] = self.generate(prompt, max_tokens=400)
        return results

    def _comms_counts(self, repo, match_id: int) -> dict[str, int]:
        """How many flagged moments of each kind the comms timeline found."""
        counts: dict[str, int] = {}
        try:
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = 'comms_timeline'",
                    (match_id,),
                ).fetchone()
            for f in (json.loads(row[0]).get("flags") or []) if row and row[0] else []:
                counts[f["kind"]] = counts.get(f["kind"], 0) + 1
        except Exception:
            pass
        return counts

    def _get_round_events(self, match_id: int) -> dict[int, dict]:
        """
        Loads stored round kill feed events from derived_metrics.
        Returns {round_number: events_dict}.
        """
        result: dict[int, dict] = {}
        try:
            import json
            repo = self._make_repo()
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
    def _build_comms_section(self, transcript: dict) -> str:
        if not transcript or int(transcript.get("word_count", 0)) == 0:
            return "  No comms data recorded this session.\n"
        if transcript.get("timeline_summary"):
            return "\n".join(f"  {line}" for line in transcript["timeline_summary"].splitlines()) + "\n"

        top_locs    = list(transcript.get("top_locations", {}).keys())[:5]
        top_actions = list(transcript.get("top_actions",   {}).keys())[:5]
        gaps        = int(transcript.get("coord_gaps", 0))
        words       = int(transcript.get("word_count", 0))
        speakers    = dict(transcript.get("speakers", {}))
        named       = dict(transcript.get("named_speakers", {}))
        fight_silence_count = int(transcript.get("fight_silence_count", 0))
        worst_silences      = list(transcript.get("worst_fight_silences", []))

        speaker_lines = []
        for spk, sd in list(speakers.items())[:5]:
            wc  = int(sd.get("word_count", 0))
            top = list(sd.get("top_words", []))[:3]
            label = named.get(spk, f"{spk} (untagged)")
            speaker_lines.append(
                f"    {label}: {wc} words — "
                f"top words: {', '.join(top) or 'none'}"
            )

        section = (
            f"  Total words spoken : {words}\n"
            f"  Top location callouts : {', '.join(top_locs) or 'none'}\n"
            f"  Top action callouts   : {', '.join(top_actions) or 'none'}\n"
            f"  Communication gaps (>8s silence) : {gaps}\n"
        )

        if speaker_lines:
            tag_note = (
                "named where tagged, see Analysis > Tag Speakers for the rest"
                if len(named) < len(speakers) else "named"
            )
            section += f"  Speakers ({tag_note}):\n"
            section += "\n".join(speaker_lines) + "\n"
    
        if fight_silence_count > 0:
            section += (
                f"\n  ⚠ FIGHT INTEL GAPS: {fight_silence_count} instance(s) where "
                f"engagement language was detected but no callout followed for ≥15s.\n"
                f"  This suggests players were in fights without communicating.\n"
            )
            for i, fs in enumerate(worst_silences[:3], 1):
                section += (
                    f"  Gap {i}: {fs.get('gap_sec', 0):.0f}s silence "
                    f"at {fs.get('start_sec', 0):.0f}s into session\n"
                    f"    Before: \"{fs.get('prior_callout', '')[:60]}\"\n"
                    f"    After:  \"{fs.get('next_callout', '')[:60]}\"\n"
                )
    
        return section
    # ── Prompt builders ───────────────────────────────────────

    def _build_match_prompt(self, match, metrics, summary, tps, transcript, facts=None, display=None) -> str:
        display = display or {}
        opponent = match.opponent_name if match.opponent_name not in ("", None, "Imported") else "the opposing team"
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
            if facts and r.round_number in facts["round_kda"]:
                k, d, a = facts["round_kda"][r.round_number]       # our team only
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
        if facts and facts["ours"] is not None:
            players_with_stats = {
                pid: data for pid, data in players_with_stats.items()
                if str(data["player"].name).lower() in facts["ours"]
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
            name  = display.get(name.lower(), name)
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
        # (Also handles fight-intel-gap reporting — see _build_comms_section.
        # Was duplicated inline here before Milestone 6; consolidated to one
        # implementation so the named-speaker fix only had to be made once.)
        comms_section = self._build_comms_section(transcript)

        # ── Team metrics ──────────────────────────────────────────
        # With a known team, the numbers are ours only, and "man advantage" and
        # "clutch" come from the kill feed (the old versions counted every
        # player in the match, so they read 0% for everyone).
        if facts:
            tm = facts["team"]
            ewr_txt = f"{tm['ewr']:.0%}" if tm["ewr"] is not None else "n/a"
            team_metrics_text = (
                f"  Overall win rate          : {metrics['win_rate']:.0%}\n"
                f"  Attack rounds win rate    : {metrics['attack_win_rate']:.0%}\n"
                f"  Defense rounds win rate   : {metrics['defense_win_rate']:.0%}\n"
                f"  Our gunfights won         : {ewr_txt}  (our players only)\n"
                f"  Our team K/D              : {tm['kd']:.2f}  ({tm['k']} kills, {tm['d']} deaths)\n"
            )
            for line in facts["opening_lines"]:
                team_metrics_text += f"  {line}\n"
            for line in facts.get("usual_lines") or []:
                team_metrics_text += f"  {line.lstrip('- ')}\n"
            for line in facts.get("objective_lines", [])[:2]:
                team_metrics_text += f"  {line.lstrip('- ')}\n"
            u = facts.get("utility") or {}
            if u.get("measured") and u.get("total"):
                team_metrics_text += f"  Operator gadgets used in {u['used']} of {u['total']} operator-rounds.\n"
            if facts["clutches"]:
                team_metrics_text += "  Clutches: " + "; ".join(facts["clutches"]) + "\n"
        else:
            team_metrics_text = (
                f"  Overall win rate          : {metrics['win_rate']:.0%}\n"
                f"  Attack rounds win rate    : {metrics['attack_win_rate']:.0%}\n"
                f"  Defense rounds win rate   : {metrics['defense_win_rate']:.0%}\n"
                f"  Engagement win rate       : {metrics['engagement_win_rate']:.0%}  (gunfight win%)\n"
            )

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
- The opponent is "{opponent}" — use that wording. The player table lists OUR team only.
- The map is "{match.map}".

════════════════════════════════════════════════════════════════
MATCH DATA  (this is everything the system knows — nothing more)
════════════════════════════════════════════════════════════════

MATCH: vs {opponent} on {match.map}
RESULT: {wins}–{losses} {'WIN' if match.result == 'win' else 'LOSS' if match.result == 'loss' else '(result not set)'}
TOTAL ROUNDS: {total}
{no_stats_note}
TEAM METRICS (calculated from recorded stats):
{team_metrics_text}
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
Two sentences maximum. State the scoreline, result, and one headline observation supported by a metric above, preferring objective play (plants, defuses) and gadget use over kills. Do not reference operators or strategies not in the data.

## ROUND PATTERNS
List what the data actually shows across multiple rounds. Reference specific round numbers and sides. For each observation, cite the supporting data point (e.g. "R02, R04, R05 were all attack wins — attack win rate {metrics['attack_win_rate']:.0%}"). If a pattern is only visible in one round, say so and do not over-generalise.

## WHAT TO FOCUS ON NEXT
Based only on the weaknesses visible in the numbers, list 2–3 specific, actionable adjustments. Do not invent context. If engagement win rate is low, say "improve gunfight consistency" not "use more smokes". If no stats were recorded, focus on observable round patterns only.

## COMMUNICATION
Summarise comms data if present. When talk share and flagged moments are listed, name who talked most and least, and quote one or two of the flagged moments with their round and clock -- they are measured from the recording, not guesses. If speaker names are "Speaker_1" etc., note these are auto-detected and may not match actual players. If no comms data, write: No comms data recorded.

"""
        return prompt

    def _build_player_prompt(
        self,
        stat: Any,
        pdata: dict[str, Any],
        tps_score: float,
        comms_lines: Optional[list[str]] = None,
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

        # Comms section — empty until this player's speaker cluster has
        # been tagged for this match (Analysis > Tag Speakers). Optional
        # on purpose: quantitative feedback below doesn't depend on it.
        comms_section = ""
        if comms_lines:
            quoted = "\n".join(f'  - "{line}"' for line in comms_lines)
            comms_section = (
                f"\nCOMMS (things {stat.player.name} said this match, "
                f"per tagged transcript segments):\n{quoted}\n"
            )

        # Replays record when an ability was available (ability_start) but not
        # when it was used (ability_used is 0 in every stored match), so
        # "Utility Efficiency 0%" means "unknown", not "never used". Showing it
        # had the model telling nearly every player to practise their gadgets.
        utility_line = ""
        if int(pdata.get("ability_used", 0)) + int(pdata.get("gadget_used", 0)) > 0:
            utility_line = (
                f"Utility Efficiency  : {float(pdata.get('utility_efficiency', 0)):.0%}  "
                f"(ability + gadget usage rate)\n"
            )

        return (
            "You are a Rainbow Six Siege performance coach. Be direct and specific.\n"
            "RULES: Only reference stats and comms shown below. Do not invent operators, "
            "strategies, or things this player didn't say.\n\n"
            f"PLAYER: {stat.player.name}\n"
            f"ROUNDS PLAYED: {rp}\n"
            f"K/D/A: {k}/{d}/{a}  KD: {float(pdata.get('kd_ratio', 0)):.2f}\n"
            f"Engagement Win Rate : {float(pdata.get('engagement_win_rate', 0)):.0%}  "
            f"(gunfights won out of taken)\n"
            f"Survival Rate       : {float(pdata.get('survival_rate', 0)):.0%}  "
            f"(rounds survived)\n"
            f"{utility_line}"
            f"TPS Score          : {tps_score:.3f}  (composite performance)\n"
            f"{comms_section}\n"
            "Respond in EXACTLY this format, citing only the stats (and comms, if given) above:\n"
            "STRENGTH: [one specific strength — cite the stat or comms line that shows it]\n"
            "FOCUS: [one area to improve — cite the stat or comms line that shows it]\n"
            "DRILL: [one concrete practice activity that addresses the focus area]\n"
        )

    def _get_transcript_summary(self, match_id: int) -> dict:
        try:
            repo = self._make_repo()
            # A comms timeline (server: speaker-separated tracks lined up
            # with the kill feed) says who said what and when; prefer its
            # measured summary over the keyword counts below.
            with repo.db.get_connection() as conn:
                row = conn.execute(
                    "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = 'comms_summary'",
                    (match_id,),
                ).fetchone()
            if row and row[0]:
                return {"timeline_summary": row[0], "word_count": 1}
            data = repo.get_transcript_processed_data(match_id)
            if not data:
                return {}
            named = repo.get_named_speaker_map(match_id)
            return {
                "top_locations":        data.get("location_freq") or {},
                "top_actions":          data.get("action_freq")   or {},
                "coord_gaps":           len(data.get("coordination_gaps") or []),
                "word_count":           int(data.get("word_count") or 0),
                "speakers":             data.get("speakers") or {},
                "named_speakers":       named,  # {"Speaker_1": "PlayerName", ...} — only tagged ones
                "fight_silence_count":  int(data.get("fight_silence_count") or 0),
                "worst_fight_silences": data.get("fight_silences") or [],
            }
        except Exception:
            return {}

    def _get_player_transcript_lines(
        self, match_id: int, player_name: str, max_lines: int = 8
    ) -> list[str]:
        """
        Lines this specific player said in comms, resolved through the
        manual speaker-tagging map (transcript_speaker_labels — see
        gui/speaker_tagging_dialog.py). Empty until someone has actually
        tagged a speaker as this player for this match; that's expected
        and get_player_intel()/_build_player_prompt() handle it gracefully
        rather than treating it as an error.
        """
        try:
            repo = self._make_repo()
            data = repo.get_transcript_processed_data(match_id)
            if not data:
                return []
            named = repo.get_named_speaker_map(match_id)
            my_tags = [tag for tag, name in named.items() if name == player_name]
            if not my_tags:
                return []

            speakers = data.get("speakers") or {}
            lines: list[tuple[float, str]] = []
            for tag in my_tags:
                for seg in (speakers.get(tag, {}).get("segments") or []):
                    text = str(seg.get("text") or "").strip()
                    if text:
                        lines.append((float(seg.get("start") or 0.0), text))

            lines.sort(key=lambda x: x[0])
            return [text for _, text in lines[:max_lines]]
        except Exception:
            return []

    def _store_metric(self, repo, match_id: int, name: str, value: str) -> None:
        """Store AI-generated text metric. metric_value = char count, metric_text = full text.

        Update-then-insert rather than INSERT ... ON CONFLICT(match_id,
        metric_name): derived_metrics has no unique constraint on that pair,
        so the ON CONFLICT form raises "ON CONFLICT clause does not match any
        PRIMARY KEY or UNIQUE constraint" on every single call. That sat
        behind a bare `except` that assumed the only possible cause was an
        old DB missing metric_text, and quietly fell back to writing the
        char count with no text at all -- so every AI summary and every piece
        of player intel was generated, paid for in GPU time, reported as
        "completed", and then thrown away, on both the client and the server.
        This form needs no constraint, works on old and new databases alike,
        and does not create duplicate rows.

        A failure message ("[AI unavailable]...", "[AI] Generation failed...")
        never replaces text that was already generated successfully: a
        re-run that hits a transient Ollama error (out of memory, server
        restarting) used to wipe out a perfectly good summary with the error.
        """
        try:
            with repo.db.get_connection() as conn:
                if value.startswith(_AI_FAILURE_MARKERS):
                    existing = conn.execute(
                        "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = ?",
                        (match_id, name),
                    ).fetchone()
                    if existing and existing[0] and not existing[0].startswith(_AI_FAILURE_MARKERS):
                        return
                updated = conn.execute(
                    """UPDATE derived_metrics
                       SET metric_value = ?, metric_text = ?, is_ai_generated = 1
                       WHERE match_id = ? AND metric_name = ?""",
                    (float(len(value)), value, match_id, name),
                ).rowcount

                if not updated:
                    conn.execute(
                        """INSERT INTO derived_metrics
                        (match_id, metric_name, metric_value, metric_text, is_ai_generated)
                        VALUES (?, ?, ?, ?, 1)""",
                        (match_id, name, float(len(value)), value),
                    )
                conn.commit()
        except Exception as e:
            print(f"[AI] Store metric failed for '{name}': {e}")

    def shutdown(self) -> None:
        self._ollama.stop_server()