import hmac
import hashlib
from typing import Optional
from fastapi import Request, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

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
