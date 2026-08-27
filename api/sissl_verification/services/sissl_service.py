"""
SISSLServices — the domain-level wrapper around SisslProvider.

This is the ONLY module the rest of the codebase should import from for
SISSL operations. Views import these methods, never the provider directly.

Responsibilities:
  [1] Call the provider (the raw HTTP client in providers/sissl.py)
  [2] Write a SISSLLog row for every attempt (success AND failure)
  [3] Apply the liveness threshold (and any other policy)
  [4] Translate provider errors into typed domain exceptions
  [5] Enforce per-user rate limits on liveness (cost discipline)
  [6] Emit AuditService events for compliance trails

NO bypass / kill-switch / non-blocking submission. Verification is
mandatory — if SISSL fails, we raise and the user does NOT proceed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from api.authentication.services.activity_service import log_activity
from api.sissl_verification.exceptions import (
    SISSLBVNNotFound,
    SISSLError,
    SISSLLivenessFailed,
    SISSLNINNotFound,
)
from api.sissl_verification.models import SISSLConfiguration, SISSLLog
from api.sissl_verification.providers.sissl import SisslProvider
from api.users.services.kyc_identity_service import update_sissl_response
from shared.constants.kyc import (
    SISSL_LIVENESS_HOURLY_CAP,
    SISSL_LIVENESS_THRESHOLD,
)
from shared.redis import RedisService

logger = logging.getLogger(__name__)


# >>>>>>>>>>>>>>>>>>>>> Typed Return Shapes <<<<<<<<<<<<<<<<<<<<<<<<<<
"""
We use lightweight dataclasses so callers get autocomplete + type safety
on the most-used response fields, while still preserving the raw vendor
response (in `raw`) for anything we haven't surfaced as a first-class field.

The BVN / NIN paths just return the raw dict directly because their shape
is dictated by NIBSS and changes outside our control — pinning a dataclass
to it would just create another thing to keep in sync.
"""

@dataclass
class LivenessResult:
    is_real: bool
    score: Decimal
    raw: dict[str, Any]


# >>>>>>>>>>>>>>>>>>>>>>>>> SISSL Services <<<<<<<<<<<<<<<<<<<<<<<<<<<
class SISSLServices:
    """
    The public API for SISSL verification.

    All methods are @staticmethod — there is no instance state. This matches
    the existing NotificationService / MessagingService pattern in the codebase.

    Methods accept primitives (the user instance, a photo string, a BVN string,
    optional ip / user_agent for audit). Never pass a request or serializer in.
    """

    # >>>>>>>>>>>>>>>>>>>> Internal Helpers <<<<<<<<<<<<<<<<<<<<<<<<<<<<
    @staticmethod
    def _provider() -> SisslProvider:
        """Hook for tests to override — returns a fresh provider per call."""
        return SisslProvider()

    @staticmethod
    def _liveness_threshold() -> int:
        """
        Returns the active liveness threshold.

        Reads from the SISSLConfiguration singleton if seeded, otherwise
        falls back to the env-var baseline. Treats a missing singleton row
        as "use the env default" rather than crashing.
        """
        config = SISSLConfiguration.current()
        if config and config.liveness_threshold is not None:
            return int(config.liveness_threshold)
        return int(SISSL_LIVENESS_THRESHOLD)

    @staticmethod
    def _enforce_liveness_rate_limit(user) -> None:
        """
        Cost-discipline guard on the liveness endpoint.

        Each SISSL call is billed, so a buggy / abusive client hitting
        /liveness/ in a tight loop is a direct money leak.

        Uses an atomic INCR + EXPIRE backed by a Lua script (see
        RedisService) so the TTL is set only on the first call of the
        window — giving us a fixed 1-hour window, not a sliding one.

        Raises SISSLError if the cap is exceeded (so the caller short-circuits
        before any vendor call is made).
        """
        if user is None or not getattr(user, "id", None):
            # Anonymous / system calls don't get rate-limited at this layer
            return

        key = f"sissl:liveness:{user.id}"
        count, _ttl = RedisService._atomic_increment(key, 3600)  # 1-hour fixed window

        if int(count) > SISSL_LIVENESS_HOURLY_CAP:
            logger.warning(
                f"[<!>SISSLService<!>] liveness rate-limit hit for {user.email} "
                f"(count={count}, cap={SISSL_LIVENESS_HOURLY_CAP})"
            )
            raise SISSLError(
                "Too many liveness attempts. Please try again later."
            )

    @staticmethod
    def _log_call(
        *,
        user,
        kind: str,
        status: str,
        latency_ms: int,
        request_summary: dict[str, Any],
        response_summary: dict[str, Any],
        error_message: str = "",
    ) -> None:
        """
        Writes a single SISSLLog row.

        PII redaction is enforced HERE — `request_summary` and `response_summary`
        must only carry presence flags, scores, and statuses. Never the BVN, NIN,
        photo URL, base64 image, or any name field. Callers are responsible for
        passing redacted dicts; this helper does not police shape.
        """
        try:
            SISSLLog.objects.create(
                user=user,
                kind=kind,
                status=status,
                latency_ms=latency_ms,
                request_summary=request_summary,
                response_summary=response_summary,
                error_message=error_message,
            )
        except Exception as exc:
            # Logging must NEVER block the user flow. If the DB write fails,
            # we log the error but the verification result is unchanged.
            logger.error(f"[<!>SISSLService<!>] failed to write SISSLLog row: {exc}")


    # >>>>>>>>>>>>>>>>>>>> Liveness <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    @staticmethod
    def liveness(user, photo, ip=None, user_agent=""):
        """
        Runs a SISSL liveness check on the given photo.

        Pass = `result == "real"` AND `score >= liveness_threshold`.

        Args:
            user        : The authenticated user this call is for (or None)
            photo       : URL or base64 string of the selfie
            ip          : Optional IP — used for audit only
            user_agent  : Optional UA — used for audit only

        Returns:
            LivenessResult(is_real=True, score=Decimal, raw=dict)

        Raises:
            SISSLError            -> vendor unreachable / rate-limit hit
            SISSLLivenessFailed   -> selfie not classified as a live human
        """
        # [1] Cost discipline — block here BEFORE any vendor call is made.
        SISSLServices._enforce_liveness_rate_limit(user)

        # [2] Make the call
        provider = SISSLServices._provider()
        start = time.monotonic()

        try:
            raw = provider.liveness({"photo": photo})

        except SISSLError as exc:
            # Vendor failed — log the attempt as ERROR, surface to caller
            SISSLServices._log_call(
                user=user,
                kind=SISSLLog.Kind.LIVENESS,
                status=SISSLLog.Status.ERROR,
                latency_ms=int((time.monotonic() - start) * 1000),
                request_summary={"photo_present": bool(photo)},
                response_summary={},
                error_message=str(exc),
            )
            
            log_activity(
                user=user,
                category="sissl",
                action="liveness_vendor_error",
                summary="SISSL vendor error during liveness check",
                details={"kind": "Liveness", "error": str(exc)},
                ip_address=ip,
                user_agent=user_agent,
            )
            raise

        # [3] Apply threshold
        latency_ms = int((time.monotonic() - start) * 1000)
        score = Decimal(str(raw.get("score") or 0))
        threshold = SISSLServices._liveness_threshold()
        is_real = raw.get("result") == "real" and score >= threshold

        # [4] Log the SUCCESSFUL HTTP call (verification outcome is in response_summary)
        SISSLServices._log_call(
            user=user,
            kind=SISSLLog.Kind.LIVENESS,
            status=SISSLLog.Status.SUCCESS,
            latency_ms=latency_ms,
            request_summary={"photo_present": True},
            response_summary={
                "result": raw.get("result"),
                "score": float(score),
                "threshold": threshold,
                "is_real": is_real,
            },
        )

        # [5] Hard-fail if the user isn't real / score too low
        if not is_real:
            logger.info(
                f"[<>SISSLService<>] liveness FAILED for {getattr(user, 'email', 'anon')} "
                f"(result={raw.get('result')}, score={score}, threshold={threshold})"
            )
            log_activity(
                user=user,
                category="sissl",
                action="liveness_failed",
                summary="Liveness check failed — user not classified as a live human",
                details={
                    "result": raw.get("result"),
                    "score": float(score),
                    "threshold": threshold,
                },
            )
            raise SISSLLivenessFailed(
                "Liveness check failed — please retake your selfie in good lighting."
            )

        # [6] Happy path
        logger.info(
            f"[<>SISSLService<>] liveness PASSED for {getattr(user, 'email', 'anon')} "
            f"(score={score}, threshold={threshold})"
        )
        # AuditService.log_event(
        #     event="SISSL_LIVENESS_PASSED",
        #     email=getattr(user, "email", "") or "",
        #     ip=ip,
        #     user_agent=user_agent,
        #     metadata={"score": float(score), "threshold": threshold},
        # )
        log_activity(
            user=user,
            category="sissl",
            action="liveness_passed",
            summary="Liveness check passed — user classified as a live human",
            details={"score": float(score), "threshold": threshold},
            ip_address=ip,
            user_agent=user_agent,
        )
        return LivenessResult(is_real=True, score=score, raw=raw)


    # >>>>>>>>>>>>>>>>>>>> BVN Lookup <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    @staticmethod
    def bvn_lookup(user, bvn, kyc_request=None, ip=None, user_agent=""):
        """
        Looks up a BVN against NIBSS via SISSL.

        Args:
            user        : The authenticated user this call is for
            bvn         : 11-digit BVN string
            ip          : Optional IP — audit only
            user_agent  : Optional UA — audit only

        Returns:
            The unwrapped data dict from SISSL — typically including
            `status`, `firstName`, `lastName`, `gender`, `image` (base64), etc.

        Raises:
            SISSLError         -> vendor unreachable
            SISSLBVNNotFound   -> BVN doesn't resolve at NIBSS
        """
        provider = SISSLServices._provider()
        start = time.monotonic()

        try:
            raw = provider.bvn_verification({"id": bvn})

        except SISSLError as exc:
            SISSLServices._log_call(
                user=user,
                kind=SISSLLog.Kind.BVN,
                status=SISSLLog.Status.ERROR,
                latency_ms=int((time.monotonic() - start) * 1000),
                request_summary={"bvn_present": bool(bvn)},
                response_summary={},
                error_message=str(exc),
            )
            log_activity(
                user=user,
                category="sissl",
                action="bvn_vendor_error",
                summary="SISSL vendor error during BVN lookup",
                details={"kind": "bvn", "error": str(exc)},
                ip_address=ip,
                user_agent=user_agent,
            )
            raise
        
        status_value = raw.get("status") if isinstance(raw, dict) else None
        latency_ms = int((time.monotonic() - start) * 1000)

        # Redacted summary — presence/status only. NEVER log the BVN, image, or names.
        SISSLServices._log_call(
            user=user,
            kind=SISSLLog.Kind.BVN,
            status=SISSLLog.Status.SUCCESS,
            latency_ms=latency_ms,
            request_summary={"bvn_present": True},
            response_summary={
                "status": status_value,
                "image_present": bool(isinstance(raw, dict) and raw.get("image")),
                "all_validation_passed": (
                    raw.get("allValidationPassed") if isinstance(raw, dict) else None
                ),
            },
        )

        if kyc_request:
            update_sissl_response(kyc_request, status_value, {"bvn_present": bool(bvn)}, raw if isinstance(raw, dict) else {})

        # SISSL returns status="found" on a hit. Anything else is a hard fail.
        if status_value != "found":
            logger.info(
                f"[<>SISSLService<>] BVN NOT FOUND for {getattr(user, 'email', 'anon')} "
                f"(status={status_value})"
            )
            log_activity(
                user=user,
                category="sissl",
                action="bvn_not_found",
                summary="BVN lookup failed — BVN not found at NIBSS",
                details={"status": status_value},
                ip_address=ip,
                user_agent=user_agent,
            )
            raise SISSLBVNNotFound("BVN could not be verified.")

        logger.info(
            f"[<>SISSLService<>] BVN VERIFIED for {getattr(user, 'email', 'anon')}"
        )
        log_activity(
            user=user,
            category="sissl",
            action="bvn_verified",
            summary="BVN lookup succeeded — BVN found at NIBSS",
            details={"status": status_value},
            ip_address=ip,
            user_agent=user_agent,
        )
        return raw


    # >>>>>>>>>>>>>>>>>>>> NIN Lookup <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    @staticmethod
    def nin_lookup(user, nin, kyc_request=None, ip=None, user_agent=""):
        """
        Looks up a NIN against NIMC via SISSL.

        Args:
            user        : The authenticated user this call is for
            nin         : 11-digit NIN string
            ip          : Optional IP — audit only
            user_agent  : Optional UA — audit only

        Returns:
            The data dict from SISSL — shape mirrors BVN response.

        Raises:
            SISSLError         -> vendor unreachable
            SISSLNINNotFound   -> NIN doesn't resolve at NIMC
        """
        provider = SISSLServices._provider()
        start = time.monotonic()

        try:
            raw = provider.nin_verification({"id": nin})

        except SISSLError as exc:
            SISSLServices._log_call(
                user=user,
                kind=SISSLLog.Kind.NIN,
                status=SISSLLog.Status.ERROR,
                latency_ms=int((time.monotonic() - start) * 1000),
                request_summary={"nin_present": bool(nin)},
                response_summary={},
                error_message=str(exc),
            )
            log_activity(
                user=user,
                category="sissl",
                action="nin_vendor_error",
                summary="SISSL vendor error during NIN lookup",
                details={"kind": "nin", "error": str(exc)},
                ip_address=ip,
                user_agent=user_agent,
            )
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        status_value = raw.get("status") if isinstance(raw, dict) else None

        # Redacted summary — presence/status only. NEVER log the NIN or names.
        SISSLServices._log_call(
            user=user,
            kind=SISSLLog.Kind.NIN,
            status=SISSLLog.Status.SUCCESS,
            latency_ms=latency_ms,
            request_summary={"nin_present": True},
            response_summary={
                "status": status_value,
                "image_present": bool(isinstance(raw, dict) and raw.get("image")),
            },
        )

        if kyc_request:
            update_sissl_response(kyc_request, status_value, {"nin_present": bool(nin)}, raw if isinstance(raw, dict) else {})
        
        # SISSL returns status="found" on a hit (mirroring BVN). Anything else is a hard fail.
        if status_value != "found":
            logger.info(
                f"[<>SISSLService<>] NIN NOT FOUND for {getattr(user, 'email', 'anon')} "
                f"(status={status_value})"
            )
            log_activity(
                user=user,
                category="sissl",
                action="nin_not_found",
                summary="NIN lookup failed — NIN not found at NIMC",
                details={"status": status_value},
                ip_address=ip,
                user_agent=user_agent,
            )
            raise SISSLNINNotFound("NIN could not be verified.")

        logger.info(
            f"[<>SISSLService<>] NIN VERIFIED for {getattr(user, 'email', 'anon')}"
        )
        log_activity(
            user=user,
            category="sissl",
            action="nin_verified",
            summary="NIN lookup succeeded — NIN found at NIMC",
            details={"status": status_value},
            ip_address=ip,
            user_agent=user_agent,
        )
        return raw
