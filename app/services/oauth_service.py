"""
Google OAuth 2.0 authentication service.
Verifies Google ID tokens and extracts user information.
"""
import logging
import uuid
from typing import Optional, Dict
from datetime import datetime

from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import User
from app.utils.encryption import hash_value

logger = logging.getLogger(__name__)


async def verify_google_token(credential: str) -> Optional[Dict[str, str]]:
    """
    Verify a Google ID token and extract user information.

    Args:
        credential: The Google ID token (JWT) to verify

    Returns:
        dict with user info if valid:
            - email: User's email address
            - name: User's full name
            - picture: URL to user's profile picture
            - sub: Google user ID (subject)
        None if token is invalid
    """
    if not settings.google_oauth_client_id:
        logger.error("Google OAuth Client ID is not configured")
        return None

    try:
        # Verify the token with Google
        idinfo = id_token.verify_oauth2_token(
            credential,
            requests.Request(),
            settings.google_oauth_client_id
        )

        # Verify the issuer
        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            logger.warning(f"Invalid token issuer: {idinfo['iss']}")
            return None

        # Extract user information
        user_info = {
            'email': idinfo.get('email'),
            'name': idinfo.get('name'),
            'picture': idinfo.get('picture'),
            'sub': idinfo.get('sub'),  # Google user ID
            'email_verified': idinfo.get('email_verified', False)
        }

        # Ensure email is verified
        if not user_info['email_verified']:
            logger.warning(f"Email not verified for Google account: {user_info['email']}")
            return None

        logger.info(f"Successfully verified Google token for: {user_info['email']}")
        return user_info

    except ValueError as e:
        # Invalid token
        logger.error(f"Failed to verify Google token: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error verifying Google token: {e}")
        return None


async def get_or_create_google_user(
    db: AsyncSession,
    google_user_info: Dict[str, str]
) -> Optional[User]:
    """
    Get an existing user by email or create a new one from Google account.

    Args:
        db: Database session
        google_user_info: User information from Google (from verify_google_token)

    Returns:
        User object if successful, None otherwise
    """
    email = google_user_info.get('email')
    if not email:
        logger.error("Google user info missing email")
        return None

    # Check if user already exists
    result = await db.execute(
        select(User).where(User.email_hash == hash_value(email.lower()))
    )
    user = result.scalar_one_or_none()

    if user:
        # User exists - update last login
        user.last_login = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        logger.info(f"Existing user logged in via Google: {email}")
        return user

    # Create new user from Google account
    try:
        new_user = User(
            uuid=str(uuid.uuid4()),
            email=email,
            email_hash=hash_value(email.lower()),
            full_name=google_user_info.get('name'),
            # Google users don't have a password in our system
            # Set a random unguessable hash that can never be used to login via password
            hashed_password=f"google_oauth_{uuid.uuid4().hex}",
            is_active=True,
            is_admin=False,
            last_login=datetime.utcnow()
        )

        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        logger.info(f"Created new user from Google account: {email}")
        return new_user

    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create user from Google account {email}: {e}")
        return None


async def link_google_account(
    db: AsyncSession,
    user: User,
    google_user_info: Dict[str, str]
) -> bool:
    """
    Link a Google account to an existing user account.
    This allows a user who signed up with email/password to also login via Google.

    Args:
        db: Database session
        user: Existing user to link Google account to
        google_user_info: User information from Google

    Returns:
        bool: True if successfully linked, False otherwise
    """
    google_email = google_user_info.get('email')

    if not google_email:
        logger.error("Google user info missing email")
        return False

    # Verify the Google email matches the user's email
    if user.email.lower() != google_email.lower():
        logger.warning(
            f"Cannot link Google account: email mismatch "
            f"(user: {user.email}, google: {google_email})"
        )
        return False

    try:
        # Update user's full name if not set and Google provides it
        if not user.full_name and google_user_info.get('name'):
            user.full_name = google_user_info['name']

        # Mark that this user can use Google OAuth
        # (in the future, could add a google_sub field to User model)

        user.last_login = datetime.utcnow()
        await db.commit()

        logger.info(f"Linked Google account to user: {user.email}")
        return True

    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to link Google account for user {user.email}: {e}")
        return False
