"""
Authentication router.

Endpoints
---------
POST /api/auth/register    — email/password sign-up
POST /api/auth/login       — email/password login → JWT in cookie + body
POST /api/auth/logout      — clear cookie
GET  /api/auth/me          — return current user profile
GET  /api/auth/google/login   — redirect to Google consent screen
GET  /api/auth/google/callback — exchange code for tokens, upsert user
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User
from app.utils.auth import create_access_token, get_current_user, get_password_hash, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Google OAuth via Authlib
# ---------------------------------------------------------------------------

oauth = OAuth()

if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    profile_picture: Optional[str] = None

    class Config:
        from_attributes = True


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=not settings.debug,   # HTTPS in production
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# Standard email/password endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Create a new user account with email and password."""
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        name=body.name,
        email=body.email,
        hashed_password=get_password_hash(body.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = create_access_token(user.id, user.email)
    _set_auth_cookie(response, token)

    return user


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Authenticate with email + password. Returns JWT in body and HttpOnly cookie."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalars().first()

    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(user.id, user.email)
    _set_auth_cookie(response, token)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user),
    }


@router.post("/logout")
async def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------

@router.get("/google/login")
async def google_login(request: Request):
    """Redirect the browser to Google's consent page."""
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured on this server",
        )
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """
    Handle Google's redirect, exchange code for tokens, upsert the User,
    issue a JWT cookie, and redirect to the dashboard.
    """
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        logger.error("Google OAuth error: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    google_user = token.get("userinfo")
    if not google_user:
        raise HTTPException(status_code=400, detail="Failed to retrieve user info from Google")

    google_id: str = google_user["sub"]
    email: str = google_user["email"]
    name: str = google_user.get("name", email.split("@")[0])
    picture: Optional[str] = google_user.get("picture")

    # Upsert: find by google_id, then by email, then create
    result = await db.execute(select(User).where(User.google_id == google_id))
    user: Optional[User] = result.scalars().first()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()

    if user:
        # Update their profile info from Google
        user.google_id = google_id
        user.name = name
        if picture:
            user.profile_picture = picture
    else:
        user = User(
            name=name,
            email=email,
            google_id=google_id,
            profile_picture=picture,
        )
        db.add(user)

    await db.flush()
    await db.refresh(user)

    jwt_token = create_access_token(user.id, user.email)
    redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    _set_auth_cookie(redirect, jwt_token)
    return redirect
