from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.users.filters import UserAdminFilter
from api.users.models import User
from api.users.permissions import IsAdminOrSuperAdminRole
from api.users.serializers import (
    UserAdminSerializer,
    UserReinstateSerializer,
    UserSuspendSerializer,
)
from api.users.services import user_admin_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


def _validation_error(*, message: str, code: str = "invalid", field_name=None) -> dict:
    """One entry of the project's error envelope, matching what
    includes.spectacular.responses builds for the shared buckets - repeated
    here because that module's builder is private to it."""

    return {
        "type": "validation_error",
        "code": code,
        "message": message,
        "field_name": field_name,
    }


_USER_EXAMPLE = {
    "id": "5a1f83c6-92b4-4e70-8d3f-1c7e6b409af2",
    "email": "chidera.nwosu@example.com",
    "first_name": "Chidera",
    "last_name": "Nwosu",
    "role": "COURSE_CREATOR",
    "role_label": "Course Creator",
    "status": "ACTIVE",
    "status_label": "Active",
    "is_active": True,
    "is_locked": False,
    "country": "NG",
    "last_login": "2026-08-04T14:22:09.117Z",
    "created_datetime": "2026-06-11T08:35:41.902Z",
}

_SUSPENDED_USER_EXAMPLE = {
    **_USER_EXAMPLE,
    "status": "SUSPENDED",
    "status_label": "Suspended",
    "is_active": False,
}

_AUTH_LINE = (
    "**Auth:** Admin or Super Admin. Approvers are excluded — they handle "
    "course approvals, not account moderation."
)

_SCOPE_NOTE = (
    "Admin and Super Admin accounts cannot be actioned here (400). Disabling "
    "a peer admin or the platform owner is an employment decision, not a "
    "moderation one, and belongs to the Super Admin-only staff endpoints at "
    "`/api/v1/auth/staff/{id}/revoke/`."
)


@extend_schema_view(
    list=extend_schema(
        summary="List all user accounts",
        description=(
            "Returns every account on the platform — public Course Creators "
            "included — with role, lifecycle status, lockout state, and last "
            "login. This is the roster an Admin moderates from, and it is "
            "deliberately wider than the Super Admin Teams page, which lists "
            "only staff and therefore never shows the self-registered users "
            "who generate most moderation work.\n\n"
            "Called when the admin Users screen loads, and on every filter or "
            "search keystroke.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None beyond holding the Admin or Super Admin "
            "role.\n\n"
            "**Important:** Results are paginated and ordered newest-first. "
            "Filter with `?role=`, `?status=`, `?is_active=`, or `?search=` "
            "(which matches email, first name, or last name). Note `status` "
            "and `is_active` are related but distinct: `is_active` is the "
            "authentication gate, `status` is the human-facing reason."
        ),
        tags=["Admin — Users"],
        responses={
            200: OpenApiResponse(
                response=UserAdminSerializer(many=True),
                description="User accounts, newest first.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value=[_USER_EXAMPLE, _SUSPENDED_USER_EXAMPLE],
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a user account",
        description=(
            "Returns one account by id, with the same fields as the roster "
            "list.\n\n"
            "Called when opening a user's detail panel from the admin Users "
            "screen, before deciding whether to suspend or reinstate them.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None beyond holding the Admin or Super Admin "
            "role.\n\n"
            "**Important:** None."
        ),
        tags=["Admin — Users"],
        responses={
            200: OpenApiResponse(
                response=UserAdminSerializer,
                description="The requested user account.",
                examples=[OpenApiExample(name="Success", value=_USER_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class UserAdminViewSet(ReadOnlyModelViewSet):
    """Admin roster over every user account, with moderation actions.

    Read operations mirror KYCReviewViewSet's shape. The write actions
    (suspend / deactivate / reinstate) delegate to user_admin_service, which
    re-checks the role itself, so the rules hold even if this view is ever
    reused behind a different permission class.
    """

    permission_classes = [IsAdminOrSuperAdminRole]
    filterset_class = UserAdminFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "last_login", "email"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        return user_admin_service.list_users(actor=self.request.user)

    def get_serializer_class(self):
        if self.action == "suspend":
            return UserSuspendSerializer
        if self.action == "deactivate":
            return UserSuspendSerializer
        if self.action == "reinstate":
            return UserReinstateSerializer
        return UserAdminSerializer

    @extend_schema(
        summary="Suspend a user account",
        description=(
            "Suspends an account for policy reasons: the user is signed out "
            "everywhere, blocked from signing back in, and told why via an "
            "in-app notification quoting the reason given here.\n\n"
            "Called from the 'Suspend' action on a user's detail panel, "
            "typically after a report or a KYC/content problem.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The account must not already be suspended.\n\n"
            "**Important:** Reversible via `/reinstate/`. Sets `status` to "
            "`SUSPENDED` and `is_active` to `false` together, and blacklists "
            "every outstanding refresh token — but an access token already "
            "issued keeps working until it expires, so the cut-off lands at "
            "the user's next token refresh rather than instantly. You cannot "
            f"suspend your own account (400). {_SCOPE_NOTE}"
        ),
        tags=["Admin — Users"],
        request=UserSuspendSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"reason": "Repeated plagiarism in submitted course content."},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=UserAdminSerializer,
                description="Account suspended.",
                examples=[
                    OpenApiExample(name="Success", value=_SUSPENDED_USER_EXAMPLE)
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "Missing reason, already suspended, self-targeted, or a "
                    "privileged account."
                ),
                examples=[
                    OpenApiExample(
                        name="Missing reason",
                        value={
                            "errors": [
                                _validation_error(
                                    code="required",
                                    message="This field is required.",
                                    field_name="reason",
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already suspended",
                        value={
                            "errors": [
                                _validation_error(
                                    message="This account is already suspended."
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Own account",
                        value={
                            "errors": [
                                _validation_error(
                                    message="You cannot suspend your own account."
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Privileged account",
                        value={
                            "errors": [
                                _validation_error(
                                    message=(
                                        "You cannot suspend an admin or super "
                                        "admin account here. Use the staff "
                                        "endpoints instead."
                                    )
                                )
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        serializer = UserSuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = user_admin_service.suspend_user(
            actor=request.user,
            user=self.get_object(),
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(UserAdminSerializer(user).data)

    @extend_schema(
        summary="Deactivate a user account",
        description=(
            "Deactivates an account permanently. Same immediate effect as "
            "suspension — signed out, blocked from signing in — but it carries "
            "the terminal meaning: suspension is corrective and expected to be "
            "lifted, deactivation ends the account's life on the platform.\n\n"
            "Called from the 'Deactivate' action on a user's detail panel, for "
            "accounts that will not be coming back.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The account must not already be "
            "deactivated.\n\n"
            "**Important:** The row is never deleted — activity logs, "
            "`created_by` references, and any authored courses keep pointing "
            "at a real user. Still reversible via `/reinstate/` if it was a "
            "mistake, so this is not a destructive operation despite the name. "
            "No in-app notification is raised (unlike suspension), since a "
            "deactivated user has no session in which to read it. You cannot "
            f"deactivate your own account (400). {_SCOPE_NOTE}"
        ),
        tags=["Admin — Users"],
        request=UserSuspendSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"reason": "Account closed at the user's own request."},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=UserAdminSerializer,
                description="Account deactivated.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            **_USER_EXAMPLE,
                            "status": "DEACTIVATED",
                            "status_label": "Deactivated",
                            "is_active": False,
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "Missing reason, already deactivated, self-targeted, or a "
                    "privileged account."
                ),
                examples=[
                    OpenApiExample(
                        name="Missing reason",
                        value={
                            "errors": [
                                _validation_error(
                                    code="required",
                                    message="This field is required.",
                                    field_name="reason",
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already deactivated",
                        value={
                            "errors": [
                                _validation_error(
                                    message="This account is already deactivated."
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Privileged account",
                        value={
                            "errors": [
                                _validation_error(
                                    message=(
                                        "You cannot deactivate an admin or super "
                                        "admin account here. Use the staff "
                                        "endpoints instead."
                                    )
                                )
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        serializer = UserSuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = user_admin_service.deactivate_user(
            actor=request.user,
            user=self.get_object(),
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(UserAdminSerializer(user).data)

    @extend_schema(
        summary="Reinstate a suspended or deactivated account",
        description=(
            "Restores a suspended or deactivated account to `ACTIVE`, letting "
            "the user sign in again, and tells them so via an in-app "
            "notification.\n\n"
            "Called from the 'Reinstate' action shown on suspended and "
            "deactivated rows of the admin Users screen.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The account's status must be `SUSPENDED` or "
            "`DEACTIVATED`, and it must have a usable password.\n\n"
            "**Important:** Accounts that were invited but never accepted have "
            "no usable password; reinstating one would leave it active with no "
            "way to sign in, so those are rejected with 400 and must be "
            "re-invited instead. A `PENDING_VERIFICATION` account is also "
            "rejected — it was never active, and reinstating it would skip "
            f"email verification. {_SCOPE_NOTE}"
        ),
        tags=["Admin — Users"],
        request=UserReinstateSerializer,
        examples=[
            OpenApiExample(name="Sample Request", request_only=True, value={}),
        ],
        responses={
            200: OpenApiResponse(
                response=UserAdminSerializer,
                description="Account reinstated and able to sign in again.",
                examples=[OpenApiExample(name="Success", value=_USER_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description=(
                    "Not in a reinstatable status, no usable password, "
                    "self-targeted, or a privileged account."
                ),
                examples=[
                    OpenApiExample(
                        name="Not reinstatable",
                        value={
                            "errors": [
                                _validation_error(
                                    message=(
                                        "An account with status 'ACTIVE' cannot "
                                        "be reinstated."
                                    )
                                )
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Never accepted an invitation",
                        value={
                            "errors": [
                                _validation_error(
                                    message=(
                                        "This account has no usable password. "
                                        "Send a fresh invitation instead."
                                    )
                                )
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        user = user_admin_service.reinstate_user(
            actor=request.user, user=self.get_object(), request=request
        )
        return Response(UserAdminSerializer(user).data)
