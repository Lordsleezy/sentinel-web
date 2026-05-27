import os
from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Request, status

import inventory_store


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_inventory_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    cf_access_user: Optional[str] = Header(default=None, alias="Cf-Access-Authenticated-User-Email"),
    x_sentinel_user: Optional[str] = Header(default=None, alias="X-Sentinel-User-Email"),
) -> Dict[str, Any]:
    if inventory_store.user_count() == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inventory beta has no invited users configured.",
        )

    token = _bearer_token(authorization)
    if token:
        service_token = os.getenv("SENTINEL_AUTH_TOKEN", "").strip()
        if service_token and token == service_token:
            email = (cf_access_user or x_sentinel_user or "").strip().lower()
            if email:
                user = inventory_store.get_user_by_email(email)
                if user:
                    return user
        user = inventory_store.get_user_by_token(token)
        if user:
            return user

    email = (cf_access_user or x_sentinel_user or "").strip().lower()
    if email:
        user = inventory_store.get_user_by_email(email)
        if user:
            return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Inventory beta access requires an invited authenticated user.",
    )
