"""
Phase 5 — phone-accessible web dashboard.

Serves a single self-contained static HTML page (server/static/dashboard.html)
that talks to the existing authenticated /api/v1/* JSON endpoints entirely
client-side via fetch(). No new auth mechanism, no server-side session state,
no template engine dependency — the page itself carries its own CSS/JS and
stores the user's API token in the browser's localStorage (per-device, never
sent anywhere but back to this same server).

Deliberately unauthenticated at the route level (like /api/v1/health): the
page is just static markup. Every actual data request it makes still goes
through verify_api_token like any other client.
"""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_DASHBOARD_HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"


def _load_dashboard_html() -> str:
    return _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_dashboard_root() -> HTMLResponse:
    return HTMLResponse(content=_load_dashboard_html())


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def serve_dashboard() -> HTMLResponse:
    return HTMLResponse(content=_load_dashboard_html())


_JOIN_HTML_PATH = Path(__file__).parent / "static" / "join.html"

# The recorder page handles a bearer-like invite token (kept in the URL
# fragment, which never reaches the server), so it must not leak it anywhere:
# no referrers, no caching, no third-party connections of any kind.
_JOIN_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline' blob:; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src data:; media-src blob:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}


@router.get("/join", response_class=HTMLResponse, include_in_schema=False)
def serve_join() -> HTMLResponse:
    """Browser recorder for teammates: open the invite link, click Start."""
    return HTMLResponse(content=_JOIN_HTML_PATH.read_text(encoding="utf-8"), headers=_JOIN_HEADERS)
