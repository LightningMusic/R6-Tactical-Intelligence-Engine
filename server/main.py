from contextlib import asynccontextmanager
from fastapi import FastAPI
from server.config import server_settings
from server.api.v1 import router as v1_router
from server.dashboard_routes import router as dashboard_router
from server.worker import server_worker


def _print_startup_banner() -> None:
    """Prints connection info every time the server starts — this is the
    whole point of self-provisioning a token in server/config.py: someone
    running the packaged exe with zero prior setup needs to be able to read
    the token straight off the console and paste it into the client.

    Built as one string and printed in a single flush=True call rather than
    several print()s: a frozen console app's stdout isn't always a live TTY
    (it can default to full block-buffering), and server_main.py's global
    line-buffering fix is the primary defense against this banner sitting
    invisibly in a buffer, but a single flushed print is cheap insurance on
    top of that for any other entry point that ever calls this."""
    lines = [
        "=" * 62,
        "  R6 Tactical Intelligence Engine - Remote Server",
        "=" * 62,
    ]
    if server_settings.API_TOKEN_HASH is None:
        lines.append("  WARNING: no API token is configured (see the warning above).")
        lines.append("  Every request will be rejected with HTTP 500 until this is fixed.")
    elif server_settings.API_TOKEN_PLAINTEXT and "server_config.json" in server_settings.TOKEN_SOURCE:
        lines.append(f"  API Token ({server_settings.TOKEN_SOURCE}):")
        lines.append(f"    {server_settings.API_TOKEN_PLAINTEXT}")
        lines.append("  Paste this into the R6Analyzer client's Settings -> Remote Sync")
        lines.append("  -> API Key field to connect it to this server.")
    else:
        lines.append(f"  API Token: configured via {server_settings.TOKEN_SOURCE} (not shown)")
    lines.append(f"  Data directory: {server_settings.DATA_DIR}")
    lines.append(f"  Listening on:   http://{server_settings.HOST}:{server_settings.PORT}")
    lines.append(f"  Dashboard:      http://<this-machine's-LAN-IP>:{server_settings.PORT}/dashboard")
    if server_settings.PUBLIC_URL:
        lines.append(f"  Public address: {server_settings.PUBLIC_URL}  (reachable from any network)")
    else:
        lines.append("  Public address: not set — run setup_tailscale_funnel.bat once to let")
        lines.append("                  R6Analyzer.exe builds reach this server from anywhere,")
        lines.append("                  not just this tailnet/LAN.")
    lines.append("=" * 62)
    print("\n".join(lines), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    server_settings.ensure_directories()
    _print_startup_banner()
    server_worker.start_in_background()
    # Keeps the server's operator/gadget catalog current with the game for
    # as long as it runs (checks hourly, syncs at most once a day).
    from server.match_db import ensure_match_database
    from integration.ubisoft_catalog import start_background_sync
    start_background_sync(ensure_match_database(), repeat=True)
    yield
    # Shutdown actions
    server_worker.stop()


def create_app() -> FastAPI:
    """
    Creates and configures the headless FastAPI remote server application.
    Independent of PySide6, Qt, GUI, OBS, Discord, and client settings.
    """
    app = FastAPI(
        title="R6Analyzer Remote Server",
        description="Headless archival database and async analysis server for R6Analyzer",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router)
    app.include_router(dashboard_router)
    return app


app = create_app()

if __name__ == "__main__":
    # Dev convenience only (hot-reload via a subprocess file-watcher). The
    # packaged R6Server.exe uses server_main.py instead — reload=True spawns
    # a subprocess that expects source files on disk, which don't exist in a
    # frozen build, and reload is meaningless there anyway.
    import uvicorn
    uvicorn.run("server.main:app", host=server_settings.HOST, port=server_settings.PORT, reload=True)
