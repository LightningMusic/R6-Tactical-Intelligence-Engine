import hmac
import hashlib
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from server import invites
from server.config import server_settings

security_scheme = HTTPBearer(auto_error=False)


def verify_api_token(credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)) -> str:
    """
    Validates Bearer token using constant-time hash comparison.
    Fails closed (HTTP 500) if server has no API token configured.
    Rejects missing, empty, or invalid tokens with HTTP 401 Unauthorized.
    Tokens are NEVER printed or logged.
    """
    if server_settings.API_TOKEN_HASH is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication error: No server API token is configured.",
        )

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_bytes = credentials.credentials.strip().encode("utf-8")
    token_hash = hashlib.sha256(token_bytes).hexdigest().lower()

    if not hmac.compare_digest(token_hash, server_settings.API_TOKEN_HASH):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "authenticated_client"


@dataclass(frozen=True)
class VoicePrincipal:
    """Who is calling a voice endpoint. `username` is set only for a browser
    invite, which is tied to exactly one in-game name."""
    kind: str                      # "voice" | "main" | "invite"
    username: Optional[str] = None
    invite_id: Optional[str] = None


def verify_voice_token(credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)) -> VoicePrincipal:
    """For the voice-upload endpoints only: accepts the narrow voice token
    that teammates' R6Voice/R6Companion carry, a browser invite token, or the
    main API token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest().lower()
    if server_settings.VOICE_TOKEN_HASH and hmac.compare_digest(token_hash, server_settings.VOICE_TOKEN_HASH):
        return VoicePrincipal("voice")
    if server_settings.API_TOKEN_HASH and hmac.compare_digest(token_hash, server_settings.API_TOKEN_HASH):
        return VoicePrincipal("main")
    if token.startswith(invites.TOKEN_PREFIX):
        invite = invites.authenticate(token)
        if invite is not None:
            return VoicePrincipal("invite", invite["username"], invite["invite_id"])
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
