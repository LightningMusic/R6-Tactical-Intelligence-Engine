"""
Milestone 5: turns on Tailscale Funnel for this server and records the
resulting permanent public HTTPS address in server_data/server_config.json,
so R6Analyzer.exe builds can embed it (see embed_client_credentials.py) and
reach this server from any network with an internet connection -- no VPN
client needed on the field laptop, no router port-forwarding, no manually
typed IP address.

Run this ONCE on the machine that will actually run R6Server.exe (i.e. the
home PC that stays on), after Tailscale is installed and logged in there.
Re-run it any time that machine's Tailscale identity changes (e.g. it was
re-registered under a different tailnet).

What it does:
  1. Confirms the `tailscale` CLI is on PATH and logged in.
  2. Runs `tailscale funnel --bg <port>` to expose the server's port
     persistently (survives reboots/logouts -- it's a tailscaled-level
     setting, not tied to this script's process).
  3. Reads this machine's own MagicDNS name from `tailscale status --json`
     and builds the public HTTPS URL Funnel serves it on.
  4. Saves that URL into server_data/server_config.json via
     ServerSettings.save_public_url(), alongside the API token
     the server self-provisions on its own first run.

Does NOT touch the API token, and does NOT require the server to be
running -- Funnel is a tailscaled feature, independent of R6Server.exe.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _find_tailscale() -> str:
    found = shutil.which("tailscale")
    if found:
        return found
    # Tailscale's default Windows install location isn't always on PATH.
    default = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    if default.exists():
        return str(default)
    raise SystemExit(
        "ERROR: Tailscale CLI not found on PATH or at the default Windows "
        "install location. Install it from https://tailscale.com/download "
        "and make sure you've run `tailscale up` to log in, then re-run "
        "this script."
    )


def _run(tailscale_exe: str, *args: str, capture: bool = True, timeout: int = 30) -> str:
    """Runs a tailscale CLI command.

    capture=True (the default) pipes stdout/stderr back so this script can
    parse it (used for `status --json`).

    capture=False instead lets the command's own stdout/stderr AND stdin
    inherit this console directly. This matters for `funnel`: the very
    first time Funnel is ever turned on for a tailnet, the CLI can print an
    interactive prompt (e.g. asking you to approve granting the tailnet's
    `funnel` node attribute, sometimes with a browser link to click) and
    then wait on stdin for a response. With output captured, that prompt
    text never reaches the console you're watching, so it silently hangs
    on a read from a terminal you can't see — indistinguishable from a
    plain hang until the timeout fires. Running it uncaptured means you'll
    actually see and can answer whatever it asks.
    """
    if capture:
        result = subprocess.run(
            [tailscale_exe, *args], capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            raise SystemExit(
                f"ERROR: `tailscale {' '.join(args)}` failed (exit {result.returncode}):\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    result = subprocess.run([tailscale_exe, *args], timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: `tailscale {' '.join(args)}` failed (exit {result.returncode}). "
            f"See its output above for details."
        )
    return ""


def main() -> int:
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            raise SystemExit(f"ERROR: '{sys.argv[1]}' is not a valid port number.")

    tailscale_exe = _find_tailscale()

    print("[setup_tailscale_funnel] Checking Tailscale login status...", flush=True)
    status_raw = _run(tailscale_exe, "status", "--json")
    status = json.loads(status_raw)
    backend_state = status.get("BackendState", "")
    if backend_state != "Running":
        raise SystemExit(
            f"ERROR: Tailscale reports BackendState='{backend_state}', not "
            "'Running'. Run `tailscale up` on this machine and log in first, "
            "then re-run this script."
        )

    dns_name = str(status.get("Self", {}).get("DNSName", "")).rstrip(".")
    if not dns_name:
        raise SystemExit(
            "ERROR: Could not read this machine's MagicDNS name from "
            "`tailscale status --json`. Make sure MagicDNS is enabled for "
            "your tailnet (https://login.tailscale.com/admin/dns)."
        )

    print(
        f"[setup_tailscale_funnel] Enabling Funnel for local port {port}...\n"
        f"[setup_tailscale_funnel] If this is the first time Funnel has been "
        f"turned on for your tailnet, Tailscale may print a prompt below "
        f"(e.g. a browser link to approve it, or a yes/no question) and "
        f"wait for you to respond right here in this window — that's "
        f"normal and only happens once.",
        flush=True,
    )
    _run(tailscale_exe, "funnel", "--bg", str(port), capture=False, timeout=120)

    public_url = f"https://{dns_name}"

    print(
        f"[setup_tailscale_funnel] Funnel is on. Public address: {public_url}",
        flush=True,
    )

    sys.path.insert(0, str(REPO_ROOT))
    from server.config import server_settings

    server_settings.save_public_url(public_url)
    print(
        f"[setup_tailscale_funnel] Saved to {server_settings.CONFIG_FILE}.\n"
        f"Next: run build_and_deploy.bat to build an R6Analyzer.exe with "
        f"this address (and the server's API token) baked in -- no manual "
        f"Settings entry needed on the client.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
