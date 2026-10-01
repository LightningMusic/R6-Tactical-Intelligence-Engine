"""
Milestone 5 follow-up (2026-09-10): keeps the client build's embedded API
token/address in sync with whatever server is ACTUALLY deployed and running,
instead of always reading the project root's own separate, independently
self-provisioned server_data.

The bug this fixes: server/config.py resolves its data folder as
"./server_data", relative to whatever directory the process was started
from. build_and_deploy.bat always runs from the project root, so
scripts/embed_client_credentials.py always reads
server_data/server_config.json there -- but R6Server.exe, once actually
deployed and run from dist\\R6Server\\ (or copied off to whichever machine
ends up hosting it), resolves its OWN server_data\\server_config.json
relative to ITS OWN folder instead, and self-provisions its own independent
token there the first time it runs, since it has never seen the project
root's file. These end up as two completely separate identities that
happen to live in the same repo -- confirmed for real on 2026-09-10, when a
client built against the project root's token could not authenticate
against the actually-running, actually-reachable dist\\R6Server instance at
all. build_and_deploy.bat's own printed instructions ("rebuild so the next
R6Analyzer.exe has that address baked in automatically") can't work without
this, since the embed step has no way to know the deployed copy generated
a different token on its own.

Called by build_and_deploy.bat right before embed_client_credentials.py
--write, every build: if dist\\R6Server\\server_data\\server_config.json
exists (i.e. R6Server.exe has actually been run for real at least once from
its deployed location), its api_token and generated_at are copied into the
project root's server_data\\server_config.json, so the very next thing
embed_client_credentials.py reads is guaranteed to match whatever's
actually live. public_url is merged rather than overwritten outright: the
deployed copy's value wins if it has one set (that machine is the one
Funnel is actually configured on), otherwise the project root's existing
value survives unchanged -- running this should never quietly erase a
public_url someone already configured. If dist\\R6Server\\server_data
doesn't exist yet (nothing has ever been deployed and run), this does
nothing and exits 0 -- a fully local/first-ever build is still fine without
it, same as always.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_CONFIG = REPO_ROOT / "server_data" / "server_config.json"
DEPLOYED_CONFIG = REPO_ROOT / "dist" / "R6Server" / "server_data" / "server_config.json"

# Docker deployment (deploy/r6ctl): once `r6ctl login` has written a public
# address into deploy/.env, the container is the live server and its volume's
# server_config.json -- not dist\R6Server -- is the source of truth.
DOCKER_ENV = REPO_ROOT / "deploy" / ".env"
DOCKER_VOLUME_CONFIG = "/var/lib/docker/volumes/r6_data/_data/server_config.json"


def _docker_public_url() -> str:
    try:
        for line in DOCKER_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("R6_SERVER_PUBLIC_URL="):
                return line.split("=", 1)[1].strip().strip("\"'").rstrip("/")
    except OSError:
        pass
    return ""


def _read_docker_config() -> dict:
    result = subprocess.run(
        ["wsl.exe", "-d", "R6Host", "-u", "root", "--exec", "cat", DOCKER_VOLUME_CONFIG],
        capture_output=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError("could not read the Docker volume's server_config.json (is R6Host up? try: deploy\\r6ctl.bat status)")
    return json.loads(result.stdout.decode("utf-8-sig"))


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[sync_deployed_server_config] WARNING: could not read {path}: {e}", flush=True)
        return {}


def _sync_from_docker(docker_url: str) -> int:
    try:
        deployed = _read_docker_config()
    except Exception as e:
        print(f"[sync_deployed_server_config] ERROR: the Docker server is configured ({docker_url}) but {e}. "
              "Not falling back to the old dist\\R6Server config, since that would embed the retired address.",
              flush=True)
        return 1
    token = str(deployed.get("api_token", "")).strip()
    voice_token = str(deployed.get("voice_token", "")).strip()
    if not token or not voice_token:
        print("[sync_deployed_server_config] ERROR: the Docker volume's server_config.json is missing the "
              "api_token or voice_token.", flush=True)
        return 1
    merged = _load(PROJECT_CONFIG)
    merged.update({"api_token": token, "voice_token": voice_token, "public_url": docker_url})
    for key in ("generated_at", "note"):
        if key in deployed:
            merged[key] = deployed[key]
    PROJECT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_CONFIG.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"[sync_deployed_server_config] Synced api_token (***{token[-4:]}) and {docker_url} from the "
          "Docker server's volume.", flush=True)
    return 0


def sync() -> int:
    docker_url = _docker_public_url()
    if docker_url:
        return _sync_from_docker(docker_url)
    if not DEPLOYED_CONFIG.exists():
        print(
            "[sync_deployed_server_config] No deployed server_data found at "
            f"{DEPLOYED_CONFIG} yet -- nothing to sync (normal before "
            "R6Server.exe has ever actually been run once from there). "
            "Continuing with whatever is already in the project root's own "
            "server_data.",
            flush=True,
        )
        return 0

    deployed = _load(DEPLOYED_CONFIG)
    token = str(deployed.get("api_token", "")).strip()
    if not token:
        print(
            f"[sync_deployed_server_config] WARNING: {DEPLOYED_CONFIG} exists "
            "but has no usable api_token -- leaving the project root's "
            "server_data untouched.",
            flush=True,
        )
        return 0

    # The voice key teammates' R6Voice builds carry (2026-09-30). The deployed
    # server is the one that has to accept it, so it's created THERE if
    # missing -- R6Server.exe reads it on its next start -- and copied into
    # the project root like the API token.
    voice_token = str(deployed.get("voice_token", "")).strip()
    if not voice_token:
        import secrets
        voice_token = secrets.token_urlsafe(24)
        deployed["voice_token"] = voice_token
        DEPLOYED_CONFIG.write_text(json.dumps(deployed, indent=2), encoding="utf-8")
        print("[sync_deployed_server_config] Created a voice key in the deployed server config "
              "(takes effect when R6Server.exe next starts).", flush=True)

    project = _load(PROJECT_CONFIG)
    deployed_url = str(deployed.get("public_url", "")).strip()

    merged = dict(project)
    merged["api_token"] = token
    merged["voice_token"] = voice_token
    if "generated_at" in deployed:
        merged["generated_at"] = deployed["generated_at"]
    if "note" in deployed:
        merged["note"] = deployed["note"]
    if deployed_url:
        merged["public_url"] = deployed_url
    # else: keep whatever public_url (if any) was already in the project
    # root's own config -- don't erase a previously-configured address just
    # because the deployed copy hasn't had setup_tailscale_funnel.bat run
    # against it yet.

    PROJECT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_CONFIG.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    masked = f"***{token[-4:]}" if len(token) >= 4 else "***"
    print(
        f"[sync_deployed_server_config] Synced api_token ({masked}) from the "
        f"deployed server at {DEPLOYED_CONFIG} into the project root's "
        "server_data -- this build will embed the same token the "
        "actually-running server is using.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(sync())
