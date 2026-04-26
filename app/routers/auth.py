"""
Authentication router for login/logout endpoints.
Supports both JWT authentication (for API/mobile) and session-based (for web).
"""
from datetime import datetime, timedelta
from typing import Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr, constr
from app.database import get_db
from app.models import User
from app.utils.auth import verify_password, create_access_token, decode_access_token, get_password_hash
from app.config import settings
from app.services.email_service import create_password_reset_token, verify_reset_token, mark_token_as_used, send_password_reset_email
from app.services.oauth_service import verify_google_token, get_or_create_google_user
from app.utils.encryption import hash_value

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# OAuth2 scheme for JWT token authentication (Authorization: Bearer <token>)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ---- Pydantic Models ----

class Token(BaseModel):
    """Token response model"""
    access_token: str
    token_type: str
    user_email: str
    user_id: int
    needs_consent: bool = False


class LoginRequest(BaseModel):
    """Login request model (alternative to OAuth2PasswordRequestForm)"""
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User information response"""
    id: int
    email: str
    full_name: Optional[str]
    is_active: bool
    is_admin: bool
    created_at: datetime


class SignupRequest(BaseModel):
    """Signup request model"""
    email: EmailStr
    password: constr(min_length=8)
    full_name: Optional[str] = None
    terms_accepted: bool = False
    privacy_accepted: bool = False


class GoogleLoginRequest(BaseModel):
    """Google OAuth login request"""
    credential: str  # Google ID token


class PasswordResetRequest(BaseModel):
    """Password reset request model"""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password with token"""
    token: str
    new_password: constr(min_length=8)


class SessionResponse(BaseModel):
    """Session check response"""
    authenticated: bool
    user_email: Optional[str] = None
    user_id: Optional[int] = None


class ConsentRequest(BaseModel):
    """Consent acceptance request"""
    accept_terms: bool
    accept_privacy: bool


# ---- Helper Functions ----

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Get user by email (looks up via email_hash for encrypted email column)"""
    result = await db.execute(select(User).where(User.email_hash == hash_value(email.lower())))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """
    Authenticate user with email and password.

    Returns:
        User object if credentials are valid, None otherwise
    """
    user = await get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user_from_token(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Get current user from JWT token.
    Used for API authentication (mobile app).

    Returns:
        User object if token is valid, None otherwise
    """
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    email: str = payload.get("sub")
    if not email:
        return None

    user = await get_user_by_email(db, email)
    if not user or not user.is_active:
        return None

    return user


async def get_current_user_from_session(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Get current user from session (for web interface).

    Returns:
        User object if session is valid, None otherwise
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        request.session.clear()
        return None

    return user


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get current user from either JWT token (API) or session (web).
    Raises 401 if neither authentication method provides a valid user.

    This is the main dependency to use in protected endpoints.
    """
    # Try token first (API/mobile)
    if token:
        user = await get_current_user_from_token(token, db)
        if user:
            return user

    # Try session (web)
    user = await get_current_user_from_session(request, db)
    if user:
        return user

    # No valid authentication
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---- Endpoints ----

@router.post("/login", response_model=Token)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Login endpoint supporting both JWT (API) and session (web) authentication.

    For API (mobile): Returns JWT token in response body.
    For web: Sets session cookie and returns token.

    Body (form-data):
        username: User's email address (OAuth2 uses 'username' field)
        password: User's password
    """
    # Authenticate user (OAuth2PasswordRequestForm uses 'username' field for email)
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last login time
    user.last_login = datetime.utcnow()
    await db.commit()

    # Create JWT token (for API/mobile)
    access_token = create_access_token(data={"sub": user.email})

    # Set session (for web)
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_email=user.email,
        user_id=user.id
    )


@router.post("/login/json", response_model=Token)
async def login_json(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Login endpoint with JSON body (alternative to form-data).

    Body (JSON):
        email: User's email address
        password: User's password
    """
    # Authenticate user
    user = await authenticate_user(db, login_data.email, login_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Update last login time
    user.last_login = datetime.utcnow()
    await db.commit()

    # Create JWT token
    access_token = create_access_token(data={"sub": user.email})

    # Set session (for web)
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_email=user.email,
        user_id=user.id
    )


@router.post("/logout")
async def logout(request: Request):
    """
    Logout endpoint - clears session.

    For API users: Client should discard the JWT token.
    For web users: Clears the session cookie.
    """
    request.session.clear()
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    Get current authenticated user information.

    Requires authentication (JWT token or session).
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,
        created_at=current_user.created_at
    )


@router.post("/signup", response_model=Token)
async def signup(
    request: Request,
    signup_data: SignupRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new user account with email and password.

    Body (JSON):
        email: User's email address
        password: Password (minimum 8 characters)
        full_name: User's full name (optional)
    """
    if not settings.allow_signup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled."
        )

    # Validate consent checkboxes
    if not signup_data.terms_accepted or not signup_data.privacy_accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must accept the Terms of Service and Privacy Policy to create an account."
        )

    # Check if user already exists
    existing_user = await get_user_by_email(db, signup_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    now = datetime.utcnow()

    # Create new user
    new_user = User(
        uuid=str(uuid.uuid4()),
        email=signup_data.email,
        email_hash=hash_value(signup_data.email.lower()),
        hashed_password=get_password_hash(signup_data.password),
        full_name=signup_data.full_name,
        is_active=True,
        is_admin=False,
        last_login=now,
        terms_accepted_at=now,
        privacy_accepted_at=now,
        ai_consent_at=now
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Create JWT token
    access_token = create_access_token(data={"sub": new_user.email})

    # Set session (for web)
    request.session["user_id"] = new_user.id
    request.session["user_email"] = new_user.email

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_email=new_user.email,
        user_id=new_user.id
    )


@router.post("/google/login", response_model=Token)
async def google_login(
    request: Request,
    google_data: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Login or signup using Google OAuth.

    Body (JSON):
        credential: Google ID token (JWT) from Google Sign-In
    """
    # Verify Google token
    google_user_info = await verify_google_token(google_data.credential)
    if not google_user_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token"
        )

    # Get or create user
    user = await get_or_create_google_user(db, google_user_info)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user account"
        )

    # Check if user has accepted terms and privacy
    needs_consent = not (user.terms_accepted_at and user.privacy_accepted_at)

    # Create JWT token
    access_token = create_access_token(data={"sub": user.email})

    # Set session (for web)
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_email=user.email,
        user_id=user.id,
        needs_consent=needs_consent
    )


@router.post("/request-password-reset")
async def request_password_reset(
    reset_request: PasswordResetRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Request a password reset email.

    Body (JSON):
        email: User's email address

    Sends an email with a password reset link if the email exists.
    Always returns success to prevent email enumeration.
    """
    # Look up user by email
    user = await get_user_by_email(db, reset_request.email)

    if user:
        # Create password reset token
        token = await create_password_reset_token(db, user)

        # Send password reset email
        send_password_reset_email(
            email=user.email,
            token=token,
            user_name=user.full_name
        )

    # Always return success to prevent email enumeration
    return {
        "message": "If the email exists, a password reset link has been sent"
    }


@router.post("/reset-password")
async def reset_password(
    request: Request,
    reset_data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Reset password using a valid reset token.

    Body (JSON):
        token: Password reset token (from email)
        new_password: New password (minimum 8 characters)
    """
    # Verify token and get user
    user = await verify_reset_token(db, reset_data.token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )

    # Update user's password
    user.hashed_password = get_password_hash(reset_data.new_password)
    user.updated_at = datetime.utcnow()

    # Mark token as used
    await mark_token_as_used(db, reset_data.token)

    await db.commit()

    # Clear any existing sessions (force re-login)
    request.session.clear()

    return {
        "message": "Password reset successfully"
    }


@router.get("/session", response_model=SessionResponse)
async def check_session(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Check if the current session is valid.

    Returns authenticated status and user information if logged in.
    """
    user = await get_current_user_from_session(request, db)

    if user:
        return SessionResponse(
            authenticated=True,
            user_email=user.email,
            user_id=user.id
        )

    return SessionResponse(
        authenticated=False
    )


@router.post("/consent")
async def record_consent(
    request: Request,
    consent_data: ConsentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Record user consent for Terms of Service and Privacy Policy.
    Requires an active session (for web interface, e.g. Google OAuth users).
    """
    user = await get_current_user_from_session(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    if not consent_data.accept_terms or not consent_data.accept_privacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must accept both the Terms of Service and Privacy Policy."
        )

    now = datetime.utcnow()
    if consent_data.accept_terms:
        user.terms_accepted_at = now
    if consent_data.accept_privacy:
        user.privacy_accepted_at = now
        user.ai_consent_at = now

    await db.commit()

    return {"message": "Consent recorded successfully"}
