"""
Email service for sending password reset emails and other notifications.
Uses SMTP configuration from settings.
"""
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.config import settings
from app.models.password_reset import PasswordResetToken
from app.models import User
from app.utils.encryption import hash_value

logger = logging.getLogger(__name__)


def generate_reset_token() -> str:
    """
    Generate a cryptographically secure random token.

    Returns:
        str: 43-character URL-safe token with 256 bits of entropy
    """
    return secrets.token_urlsafe(32)


async def create_password_reset_token(db: AsyncSession, user: User) -> str:
    """
    Create a password reset token for a user.
    Invalidates any existing unused tokens for the user.

    Args:
        db: Database session
        user: User requesting password reset

    Returns:
        str: The generated token
    """
    # Delete any existing unused tokens for this user
    await db.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False
        )
    )

    # Generate new token
    token = generate_reset_token()
    expires_at = datetime.utcnow() + timedelta(minutes=settings.password_reset_token_expire_minutes)

    # Save to database
    reset_token = PasswordResetToken(
        token=token,
        token_hash=hash_value(token),
        user_id=user.id,
        expires_at=expires_at
    )
    db.add(reset_token)
    await db.commit()

    logger.info(f"Created password reset token for user {user.email} (expires at {expires_at})")
    return token


async def verify_reset_token(db: AsyncSession, token: str) -> Optional[User]:
    """
    Verify a password reset token and return the associated user.

    Args:
        db: Database session
        token: The reset token to verify

    Returns:
        User if token is valid, None otherwise
    """
    result = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_value(token))
    )
    reset_token = result.scalar_one_or_none()

    if not reset_token:
        logger.warning("Password reset token not found")
        return None

    if not reset_token.is_valid():
        logger.warning(f"Password reset token invalid or expired for user {reset_token.user_id}")
        return None

    return reset_token.user


async def mark_token_as_used(db: AsyncSession, token: str) -> None:
    """
    Mark a password reset token as used so it cannot be reused.

    Args:
        db: Database session
        token: The token to mark as used
    """
    result = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_value(token))
    )
    reset_token = result.scalar_one_or_none()

    if reset_token:
        reset_token.mark_as_used()
        await db.commit()
        logger.info(f"Marked password reset token as used for user {reset_token.user_id}")


def send_password_reset_email(email: str, token: str, user_name: Optional[str] = None) -> bool:
    """
    Send a password reset email to a user.

    Args:
        email: Recipient email address
        token: Password reset token
        user_name: User's full name (optional)

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    if not settings.smtp_host or not settings.smtp_username:
        logger.error("SMTP configuration is incomplete. Cannot send password reset email.")
        return False

    # Build reset URL
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"

    # Build email content
    subject = "Reset Your Password - Personal Finance Intelligence"

    greeting = f"Hi {user_name}," if user_name else "Hi,"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #4F46E5;">Password Reset Request</h2>
            <p>{greeting}</p>
            <p>We received a request to reset your password for your Personal Finance Intelligence account.</p>
            <p>Click the button below to reset your password:</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{reset_url}"
                   style="background-color: #4F46E5; color: white; padding: 12px 30px;
                          text-decoration: none; border-radius: 5px; display: inline-block;">
                    Reset Password
                </a>
            </div>
            <p>Or copy and paste this link into your browser:</p>
            <p style="background-color: #f3f4f6; padding: 10px; border-radius: 5px; word-break: break-all;">
                {reset_url}
            </p>
            <p style="color: #6B7280; font-size: 14px;">
                This link will expire in {settings.password_reset_token_expire_minutes} minutes.
            </p>
            <p style="color: #6B7280; font-size: 14px;">
                If you didn't request a password reset, you can safely ignore this email.
                Your password will not be changed.
            </p>
            <hr style="border: none; border-top: 1px solid #E5E7EB; margin: 30px 0;">
            <p style="color: #9CA3AF; font-size: 12px; text-align: center;">
                {settings.app_name} | Secure Financial Management
            </p>
        </div>
    </body>
    </html>
    """

    text_body = f"""
{greeting}

We received a request to reset your password for your Personal Finance Intelligence account.

Click this link to reset your password:
{reset_url}

This link will expire in {settings.password_reset_token_expire_minutes} minutes.

If you didn't request a password reset, you can safely ignore this email.
Your password will not be changed.

---
{settings.app_name} | Secure Financial Management
    """

    # Create message
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = email

    # Attach both plain text and HTML versions
    message.attach(MIMEText(text_body, "plain"))
    message.attach(MIMEText(html_body, "html"))

    # Send email
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)

        logger.info(f"Password reset email sent successfully to {email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send password reset email to {email}: {e}")
        return False


async def cleanup_expired_tokens(db: AsyncSession) -> int:
    """
    Delete expired password reset tokens from the database.
    This should be called periodically (e.g., daily via scheduler).

    Args:
        db: Database session

    Returns:
        int: Number of tokens deleted
    """
    result = await db.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.expires_at < datetime.utcnow()
        )
    )
    await db.commit()

    deleted_count = result.rowcount
    logger.info(f"Cleaned up {deleted_count} expired password reset tokens")
    return deleted_count
