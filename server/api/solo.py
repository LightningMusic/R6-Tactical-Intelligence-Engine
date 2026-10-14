"""
Routes for a teammate's own analyzer (/solo page) and their sensitivity tool
(/plugin), plus the host's routes to hand out player keys.

Every /solo and /plugin route works on exactly one player: a player key's own
username, or -- for the host's main key -- the one named with ?player=. A
player key naming someone else gets 403.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from server import players
from server.auth import PlayerPrincipal, verify_api_token, verify_player_token
from server.config import server_settings
from server.services import solo

router = APIRouter(prefix="/api/v1")


RATE_PER_MIN = 120           # per player key; the page polls every few seconds while matches are being read
_calls: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_calls_lock = threading.Lock()


def _rate_limit(username: str) -> None:
    now = time.monotonic()
    with _calls_lock:
        q = _calls[username.lower()]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            raise HTTPException(status_code=429, detail="Too many calls; wait a minute.", headers={"Retry-After": "30"})
        q.append(now)


def _owner(principal: PlayerPrincipal, player: Optional[str]) -> str:
    if principal.kind == "player":
        _rate_limit(principal.username or "")
        if player and player.lower() != (principal.username or "").lower():
            raise HTTPException(status_code=403, detail="This key can only see its own player.")
        return principal.username or ""
    if not player or not players.USERNAME_RE.match(player):
        raise HTTPException(status_code=400, detail="Say which player: ?player=<in-game name>.")
    return player


def _bad(exc: Exception):
    raise HTTPException(status_code=400, detail=str(exc))


# ── /solo: the player's own page ─────────────────────────────────────────────

@router.get("/solo/me")
def solo_me(player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    owner = _owner(p, player) if (p.kind == "player" or player) else None
    return {"kind": p.kind, "player": owner, "players": players.known_usernames() if p.kind == "main" else None}


@router.post("/solo/known")
def solo_known(body: dict, player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    """{"folders": [...]} -> which of these match folders were already uploaded, and their status."""
    return {"known": solo.known(_owner(p, player), list(body.get("folders") or []))}


@router.post("/solo/upload")
def solo_upload(folder: str = Form(...), setup: Optional[str] = Form(None), tz_offset_min: Optional[int] = Form(None),
                files: List[UploadFile] = File(...),
                player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    """One match folder's .rec files (multipart: folder, optional setup, files[])."""
    owner = _owner(p, player)
    try:
        return solo.store_upload(owner, folder, [(f.filename or "", f.file) for f in files], setup, tz_offset_min)
    except solo.UploadError as exc:
        _bad(exc)


@router.get("/solo/matches")
def solo_matches(player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    return {"matches": solo.list_matches(_owner(p, player))}


@router.get("/solo/matches/{folder}")
def solo_match(folder: str, player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    m = solo.get_match(_owner(p, player), folder)
    if m is None:
        raise HTTPException(status_code=404, detail="No such match.")
    return m


@router.put("/solo/matches/{folder}/setup")
def solo_match_setup(folder: str, body: dict, player: Optional[str] = None,
                     p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    try:
        if not solo.set_match_setup(_owner(p, player), folder, body.get("setup")):
            raise HTTPException(status_code=404, detail="No such match.")
    except solo.UploadError as exc:
        _bad(exc)
    return {"ok": True}


@router.delete("/solo/matches/{folder}")
def solo_delete(folder: str, player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    if not solo.delete_match(_owner(p, player), folder):
        raise HTTPException(status_code=404, detail="No such match (or it's being read right now).")
    return {"ok": True}


@router.get("/solo/trends")
def solo_trends(since: Optional[str] = None, player: Optional[str] = None,
                p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    return solo.trends(_owner(p, player), since)


@router.get("/solo/setups")
def solo_setups(player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    return {"setups": solo.list_setups(_owner(p, player))}


@router.put("/solo/setups")
def solo_save_setup(body: dict, player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    try:
        return solo.save_setup(_owner(p, player), str(body.get("name") or ""), body.get("mouse_dpi"),
                               str(body.get("note") or ""))
    except solo.UploadError as exc:
        _bad(exc)


@router.delete("/solo/setups/{name}")
def solo_delete_setup(name: str, player: Optional[str] = None, p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    if not solo.delete_setup(_owner(p, player), name):
        raise HTTPException(status_code=404, detail="No such setup.")
    return {"ok": True}


# ── /plugin: the player's own sensitivity tool ───────────────────────────────

@router.post("/plugin/sensitivity/snapshots", status_code=201)
def plugin_add_snapshot(body: dict, player: Optional[str] = None,
                        p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    try:
        return solo.add_snapshot(_owner(p, player), body)
    except solo.UploadError as exc:
        _bad(exc)


@router.get("/plugin/sensitivity/snapshots")
def plugin_snapshots(since: Optional[str] = None, limit: int = Query(200, ge=1, le=1000), player: Optional[str] = None,
                     p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    try:
        return {"snapshots": solo.list_snapshots(_owner(p, player), since, limit)}
    except solo.UploadError as exc:
        _bad(exc)


@router.get("/plugin/performance")
def plugin_performance(since: Optional[str] = None, player: Optional[str] = None,
                       p: PlayerPrincipal = Depends(verify_player_token)) -> dict:
    try:
        return {"matches": solo.performance(_owner(p, player), since)}
    except solo.UploadError as exc:
        _bad(exc)


# ── Host only: player keys ───────────────────────────────────────────────────

def _key_links(created: dict) -> dict:
    path = f"/solo#{created['token']}"
    base = server_settings.PUBLIC_URL.rstrip("/")
    return {**created, "solo_path": path, "solo_url": f"{base}{path}" if base else ""}


@router.get("/players")
def list_player_keys(_client: str = Depends(verify_api_token)) -> dict:
    return {"keys": players.list_all()}


@router.post("/players")
def create_player_key(body: dict, _client: str = Depends(verify_api_token)) -> dict:
    """{"username": "<in-game name>", "label": "Hector", "days": 365}. The key is shown once."""
    try:
        created = players.create(str(body.get("username", "")), int(body.get("days") or players.DEFAULT_DAYS),
                                 str(body.get("label", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _key_links(created)


@router.post("/players/{key_id}/regenerate")
def regenerate_player_key(key_id: str, _client: str = Depends(verify_api_token)) -> dict:
    created = players.regenerate(key_id)
    if created is None:
        raise HTTPException(status_code=404, detail="No such key.")
    return _key_links(created)


@router.delete("/players/{key_id}")
def revoke_player_key(key_id: str, _client: str = Depends(verify_api_token)) -> dict:
    if not players.revoke(key_id):
        raise HTTPException(status_code=404, detail="No such key, or it's already revoked.")
    return {"ok": True}
