from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from dhqa_api.config import settings

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> PyJWKClient:
    if not settings.supabase_jwks_url:
        raise RuntimeError("SUPABASE_JWKS_URL is not configured")
    return PyJWKClient(settings.supabase_jwks_url, cache_keys=True)


def current_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
) -> dict[str, Any]:
    """Resolve Supabase JWT identity. Header-based identity is development-only."""
    if settings.app_env == "development" and settings.dev_auth_enabled and x_dev_user:
        subject = x_dev_user.strip()
        if not subject or len(subject) > 255:
            raise HTTPException(status_code=400, detail="Invalid development identity")
        return {"sub": subject, "email": None, "role": "authenticated"}

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(credentials.credentials).key
        options = {"require": ["exp", "sub"]}
        decode_args: dict[str, Any] = {
            "algorithms": ["ES256", "RS256"],
            "audience": settings.supabase_audience,
            "options": options,
        }
        if settings.supabase_issuer:
            decode_args["issuer"] = settings.supabase_issuer
        claims = jwt.decode(credentials.credentials, signing_key, **decode_args)
        if not claims.get("sub"):
            raise ValueError("Missing subject")
        return claims
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token") from exc
