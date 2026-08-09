"""Shared audit logging helpers.

This module provides a consistent way to emit audit log entries from any
part of the codebase. It uses Celery for async logging by default.
"""

from __future__ import annotations

from shared.celery import CeleryService


class AuditService:
    """Helper for creating audit log entries."""

    @staticmethod
    def log_event(event: str, email: str, ip: str | None = None, user_agent: str = "", metadata: dict | None = None):
        """Create an audit log entry.

        Args:
            event: One of the AuditLog.Event values.
            email: User email.
            ip: Optional IP address.
            user_agent: Optional user agent string.
            metadata: Optional dictionary with extra metadata.
        """
        CeleryService.write_audit_log(event=event, email=email, ip=ip, meta=metadata or {})

    @staticmethod
    def log_otp_requested(email: str, ip: str | None = None, user_agent: str = "", metadata: dict | None = None):
        return AuditService.log_event(
            event="OTP_REQUESTED",
            email=email,
            ip=ip,
            user_agent=user_agent,
            metadata=metadata,
        )

    @staticmethod
    def log_otp_verified(email: str, ip: str | None = None, user_agent: str = "", metadata: dict | None = None):
        return AuditService.log_event(
            event="OTP_VERIFIED",
            email=email,
            ip=ip,
            user_agent=user_agent,
            metadata=metadata,
        )

    @staticmethod
    def log_otp_failed(email: str, ip: str | None = None, user_agent: str = "", metadata: dict | None = None):
        return AuditService.log_event(
            event="OTP_FAILED",
            email=email,
            ip=ip,
            user_agent=user_agent,
            metadata=metadata,
        )

    @staticmethod
    def log_otp_locked(email: str, ip: str | None = None, user_agent: str = "", metadata: dict | None = None):
        return AuditService.log_event(
            event="OTP_LOCKED",
            email=email,
            ip=ip,
            user_agent=user_agent,
            metadata=metadata,
        )


    @staticmethod
    def log_job_offer_accepted(email: str, ip: str | None = None, metadata: dict | None = None):
        return AuditService.log_event(
            event="JOB_OFFER_ACCEPTED",
            email=email,
            ip=ip,
            metadata=metadata,
        )

    @staticmethod
    def log_job_offer_rejected(email: str, ip: str | None = None, metadata: dict | None = None):
        return AuditService.log_event(
            event="JOB_OFFER_REJECTED",
            email=email,
            ip=ip,
            metadata=metadata,
        )