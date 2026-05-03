"""Authentication: session (browser) and HTTP Basic (API)."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from werkzeug.security import check_password_hash

from app import db

security = HTTPBasic(auto_error=False)


def _user_from_db(username: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
    return db.row_to_dict(row)


def verify_password(username: str, password: str) -> dict | None:
    user = _user_from_db(username)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def get_session_user(request: Request) -> dict | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, role FROM users WHERE id = ?",
            (uid,),
        )
        row = cur.fetchone()
    return db.row_to_dict(row)


def require_session_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def login_redirect() -> HTTPException:
    return HTTPException(status_code=302, headers={"Location": "/login"})


def require_role_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )


def require_admin_session(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    require_role_admin(user)
    return user


async def get_current_user_basic(
    creds: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> dict:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )
    user = verify_password(creds.username, creds.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


async def get_current_user_basic_admin(
    user: Annotated[dict, Depends(get_current_user_basic)],
) -> dict:
    require_role_admin(user)
    return user


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)
