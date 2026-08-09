from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from api.authentication.models import UserSession
from api.users.models import User


def _client_meta(request):
    """Extract (ip_address, user_agent) from `request`, or (None, "") if
    there is none (e.g. a test calling the service directly)."""

    if request is None:
        return None, ""
    return request.META.get("REMOTE_ADDR"), request.META.get("HTTP_USER_AGENT", "")


def create_session(*, user: User, refresh_jti: str, request=None) -> UserSession:
    """Create a UserSession at login. `refresh_jti` is the jti of the
    refresh token just issued for this session - the caller (LoginSerializer)
    embeds this session's id back into that same token as a "sid" claim."""

    ip_address, user_agent = _client_meta(request)
    return UserSession.objects.create(
        user=user,
        current_jti=refresh_jti,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def bump_last_seen(*, session_id, new_jti: str) -> None:
    """Update a session's current_jti and last_seen_at after a token
    refresh. Silently no-ops if session_id is missing/unknown - covers
    tokens issued before this feature existed, or an already-revoked
    session whose refresh token somehow still got used."""

    if not session_id:
        return
    UserSession.objects.filter(id=session_id).update(
        current_jti=new_jti, last_seen_at=timezone.now()
    )


def list_active_sessions(*, user: User):
    """Return `user`'s non-revoked sessions, newest-active-first."""

    return UserSession.objects.filter(user=user, revoked_at__isnull=True)


def revoke_session(*, user: User, session_id) -> UserSession:
    """Revoke one of `user`'s sessions: blacklist its current refresh token
    (if still outstanding) and mark it revoked. Scoped to `user` so a
    session id belonging to someone else raises DoesNotExist rather than
    leaking whether it exists (callers should translate that to a 404)."""

    session = UserSession.objects.get(user=user, id=session_id, revoked_at__isnull=True)

    outstanding = OutstandingToken.objects.filter(
        user=user, jti=session.current_jti
    ).first()
    if outstanding is not None:
        BlacklistedToken.objects.get_or_create(token=outstanding)

    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    return session


def revoke_all_sessions(*, user: User) -> None:
    """Mark every one of `user`'s sessions revoked. Called alongside
    logout_all_sessions' own blanket OutstandingToken blacklist, so the
    sessions list reflects logout-all immediately."""

    UserSession.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
