"""
Password reset token model for secure password reset functionality.
Tokens expire after 1 hour and can only be used once.
"""
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base
from app.utils.encryption import EncryptedString


class PasswordResetToken(Base):
    """
    Secure password reset tokens with expiration and one-time use.
    """
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The secure token (256-bit entropy via secrets.token_urlsafe(32))
    token: Mapped[str] = mapped_column(EncryptedString(64), nullable=False)
    # Hashed token for lookup (peppered SHA-256)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # User this token belongs to
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Token expiration (default 1 hour from creation)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Whether this token has been used (one-time use only)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

    def is_valid(self) -> bool:
        """
        Check if token is still valid (not expired and not used).

        Returns:
            bool: True if token can be used, False otherwise
        """
        return (
            not self.used
            and self.expires_at > datetime.utcnow()
        )

    def mark_as_used(self) -> None:
        """Mark this token as used so it cannot be reused."""
        self.used = True
        self.used_at = datetime.utcnow()

    def __repr__(self):
        return f"<PasswordResetToken(user_id={self.user_id}, expires_at={self.expires_at}, used={self.used})>"
