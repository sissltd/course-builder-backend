from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.authentication.serializers import (
    AcceptStaffInvitationSerializer,
    StaffInvitationSerializer,
    StaffMemberSerializer,
    SuperAdminBootstrapSerializer,
)
from api.authentication.serializers.response_serializers import (
    AuthTokenPairResponseSerializer,
    StaffActionResponseSerializer,
    StaffInvitationCreatedResponseSerializer,
)
from api.authentication.services.authentication_service import AuthenticationService
from api.authentication.services.staff_service import StaffService
from api.users.models import User
from api.users.permissions import IsSuperAdminRole
from api.users.serializers import MeSerializer
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

staff_service = StaffService()
auth_service = AuthenticationService()

# Reused across the examples below so the docs read as one coherent scenario.
_SUPER_ADMIN_EXAMPLE = {
    "id": "0f8b9a1c-4d2e-4c7a-9f31-6b5d2a7e8c14",
    "email": "ops@soludesks.com",
    "first_name": "Amara",
    "last_name": "Eze",
    "role": "SUPER_ADMIN",
    "is_active": True,
    "created_datetime": "2026-07-18T09:14:22.481Z",
    "has_completed_onboarding": False,
}

_PENDING_STAFF_EXAMPLE = {
    "id": "3c7e5f20-91ab-4d63-8e5c-2f4a1b9d7e08",
    "email": "tunde.bakare@soludesks.com",
    "first_name": "Tunde",
    "last_name": "Bakare",
    "role": "STAFF_WRITER",
    "role_label": "Writer",
    "invitation_status": "PENDING",
    "invited_by": "ops@soludesks.com",
    "created_datetime": "2026-07-18T10:02:55.117Z",
}

_ACTIVE_STAFF_EXAMPLE = {
    "id": "b41d9e63-2c78-4a15-95ef-8d3c0a2b6f71",
    "email": "ngozi.okonkwo@soludesks.com",
    "first_name": "Ngozi",
    "last_name": "Okonkwo",
    "role": "STAFF_APPROVER",
    "role_label": "Approver",
    "invitation_status": "ACTIVE",
    "invited_by": "ops@soludesks.com",
    "created_datetime": "2026-07-16T08:41:03.902Z",
}


# Public endpoint: no security requirement, so Swagger's padlock does
# not attach a bearer token to it.
@extend_schema(auth=[{}])
class SuperAdminBootstrapView(APIView):
    """Claim the platform's single Super Admin seat in enabled environments."""

    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "superadmin_bootstrap"
    serializer_class = SuperAdminBootstrapSerializer  # schema generation only

    @extend_schema(
        summary="Bootstrap the platform super admin",
        description=(
            "Creates the platform's one and only Super Admin account. Every "
            "other staff account descends from this one: the Super Admin is the "
            "only role that can invite staff, and staff roles cannot be "
            "self-registered through any public route. Because no privileged "
            "account exists yet when this is called, the request is authorized "
            "by the deployment environment rather than by a logged-in user.\n\n"
            "This is the very first call made against a fresh deployment, "
            "before any other staff flow is usable: **bootstrap** → invite "
            "staff → staff accepts. The account returned is active immediately "
            "and can log in at `/api/v1/auth/login/` with the email and "
            "password supplied here.\n\n"
            "**Auth:** Public — no bearer token. This route is enabled only "
            "for local, development, and staging-like environments. Production "
            "must set `DJANGO_ENV=production`, which disables it. No Super Admin "
            "may exist yet.\n\n"
            "**Important:** This works exactly once per deployment and is "
            "irreversible through the API. A second call returns 400, and the "
            "one-super-admin rule is enforced by a database constraint, so "
            "concurrent calls cannot both succeed. The account is created with "
            "Django's `is_staff` and `is_superuser` flags set, granting access "
            "to `/admin/` as well as the API. Rate limited to **5 requests "
            "per hour per IP** because this is a public account-creation route. "
            "Success returns the profile but **no tokens** — log in at "
            "`/api/v1/auth/login/` afterwards to get a JWT pair."
        ),
        tags=["Auth — Super Admin Bootstrap"],
        request=SuperAdminBootstrapSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "ops@soludesks.com",
                    "password": "Kt7#pQz2Lm9v",
                    "first_name": "Amara",
                    "last_name": "Eze",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=MeSerializer,
                description="Super admin created and ready to log in.",
                examples=[
                    OpenApiExample(name="Success", value=_SUPER_ADMIN_EXAMPLE),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "A super admin already exists, the email is taken, or the "
                    "payload failed validation."
                ),
                examples=[
                    OpenApiExample(
                        name="Super admin already exists",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "A super admin already exists for this platform."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Email already taken",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "A user with this email already exists.",
                                    "field_name": "email",
                                }
                            ]
                        },
                    ),
                ],
            ),
            403: OpenApiResponse(
                description="Bootstrapping is disabled in this environment.",
                examples=[
                    OpenApiExample(
                        name="Bootstrap disabled",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "permission_denied",
                                    "message": (
                                        "Super admin bootstrap is disabled on "
                                        "this deployment."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["rate_limited"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = SuperAdminBootstrapSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = staff_service.bootstrap_superadmin(
            **serializer.validated_data, request=request
        )
        return Response(MeSerializer(user).data, status=201)


class StaffListView(APIView):
    """List every staff member and pending invitation for the Teams page."""

    permission_classes = [IsSuperAdminRole]
    serializer_class = StaffMemberSerializer  # schema generation only

    @extend_schema(
        summary="List staff and pending invitations",
        description=(
            "Returns the platform's full staff roster — active members, "
            "invitations that have not been accepted yet, and members whose "
            "access has been revoked — newest first. This is the data behind "
            "the Teams page, and each row carries everything needed to render "
            "it: display name, role label, who issued the invitation, and the "
            "status pill.\n\n"
            "Called when the Teams page loads, and again after any invite, "
            "revoke, or reactivate action to refresh the list.\n\n"
            "**Auth:** Super Admin.\n\n"
            "**Prerequisites:** None beyond being signed in as the Super "
            "Admin.\n\n"
            "**Important:** Read `invitation_status`, not `is_active`, to "
            "decide what a row can do — `PENDING` and `REVOKED` are both "
            "inactive but offer different actions (resend vs reactivate). "
            "Public Course Creators are not staff and never appear here. "
            "The response is an unpaginated array; the roster is expected to "
            "stay small."
        ),
        tags=["Admin — Staff"],
        responses={
            200: OpenApiResponse(
                response=StaffMemberSerializer(many=True),
                description="The staff roster, newest first.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value=[_PENDING_STAFF_EXAMPLE, _ACTIVE_STAFF_EXAMPLE],
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        staff = staff_service.list_staff()
        return Response(StaffMemberSerializer(staff, many=True).data, status=200)


class InviteStaffView(APIView):
    """Invite a new staff member in a chosen role. Super Admin only."""

    permission_classes = [IsSuperAdminRole]
    serializer_class = StaffInvitationSerializer  # schema generation only

    @extend_schema(
        summary="Invite a staff member",
        description=(
            "Invites someone to join the team in one of three positions and "
            "emails them a single-use invitation link. This is the only way a "
            "staff account can come into existence — no public signup yields a "
            "staff role. The invitee's account is created immediately in a "
            "pending state (inactive, no usable password) so the email address "
            "is reserved, but it cannot authenticate until they accept.\n\n"
            "Step 2 of the staff flow: bootstrap super admin → **invite "
            "staff** → staff accepts. Backs the 'Invite a staff' dialog; the "
            "invitee continues at "
            "`/api/v1/auth/staff/invitations/accept/`.\n\n"
            "**Auth:** Super Admin. Approvers and Admins cannot invite — this "
            "is deliberately the capability that sets the Super Admin apart.\n\n"
            "**Prerequisites:** A Super Admin account must exist (see "
            "`/api/v1/auth/superadmin/bootstrap/`) and the caller must be "
            "signed in as it. The email must not already belong to an existing "
            "user of any role.\n\n"
            "**Important:** `role` accepts only `STAFF_WRITER`, "
            "`STAFF_VERIFIER`, or `STAFF_APPROVER`; anything else — including "
            "`SUPER_ADMIN` — is rejected with 400, so this endpoint cannot "
            "mint a second Super Admin. Re-inviting someone whose invitation "
            "is still pending reissues the link, invalidates the previous one, "
            "and updates their role, which is how you correct a "
            "mis-selected role before acceptance. Resends are rate-limited by "
            "the verification-email cooldown, so an immediate second call "
            "returns 400. The invitation link expires; after that the invitee "
            "needs a fresh one."
        ),
        tags=["Admin — Staff"],
        request=StaffInvitationSerializer,
        examples=[
            OpenApiExample(
                name="Invite a Writer",
                request_only=True,
                value={
                    "email": "tunde.bakare@soludesks.com",
                    "first_name": "Tunde",
                    "last_name": "Bakare",
                    "role": "STAFF_WRITER",
                },
            ),
            OpenApiExample(
                name="Invite an Approver",
                request_only=True,
                value={
                    "email": "ngozi.okonkwo@soludesks.com",
                    "first_name": "Ngozi",
                    "last_name": "Okonkwo",
                    "role": "STAFF_APPROVER",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=StaffInvitationCreatedResponseSerializer,
                description="Invitation created and emailed to the invitee.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": (
                                "An invitation has been sent to "
                                "tunde.bakare@soludesks.com."
                            ),
                            "staff": _PENDING_STAFF_EXAMPLE,
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "The email belongs to an existing user, the role is not an "
                    "invitable staff role, or the resend cooldown is still "
                    "running."
                ),
                examples=[
                    OpenApiExample(
                        name="Email already taken",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "A user with this email already exists.",
                                    "field_name": "email",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Role not invitable",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid_choice",
                                    "message": '"SUPER_ADMIN" is not a valid choice.',
                                    "field_name": "role",
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Resend cooldown active",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "An invitation was just sent to this "
                                        "email. Please wait before resending."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = StaffInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = staff_service.invite_staff(
            invited_by=request.user, **serializer.validated_data, request=request
        )
        return Response(
            {
                "detail": f"An invitation has been sent to {staff.email}.",
                "staff": StaffMemberSerializer(staff).data,
            },
            status=201,
        )


class RevokeStaffView(APIView):
    """Withdraw a pending invitation or deactivate an active staff member."""

    permission_classes = [IsSuperAdminRole]

    @extend_schema(
        summary="Revoke a staff member's access",
        description=(
            "Removes someone from the team. If their invitation is still "
            "pending, it is withdrawn and the emailed link stops working; if "
            "they are already active, their account is deactivated and they can "
            "no longer sign in. One action covers both because the Teams page "
            "presents them as one thing — 'remove this person'.\n\n"
            "Called from the row actions on the Teams page.\n\n"
            "**Auth:** Super Admin.\n\n"
            "**Prerequisites:** The target must be an existing staff member "
            "who is currently active or pending.\n\n"
            "**Important:** This is reversible, not destructive — the account "
            "row is kept so activity logs, `created_by` references, and any "
            "courses they authored keep pointing at a real user. Undo it with "
            "the reactivate endpoint, except for invitations revoked before "
            "acceptance, which must be re-invited instead. You cannot revoke "
            "your own account, and the Super Admin seat cannot be revoked at "
            "all — it is bootstrap-only, so a revoked Super Admin could not be "
            "replaced through the API. Revoking does not free the email "
            "address for reuse."
        ),
        tags=["Admin — Staff"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=StaffActionResponseSerializer,
                description="Access revoked.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": (
                                "Staff access revoked for ngozi.okonkwo@soludesks.com."
                            ),
                            "staff": {
                                **_ACTIVE_STAFF_EXAMPLE,
                                "invitation_status": "REVOKED",
                            },
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "The target is already inactive, is not staff, is the "
                    "Super Admin, or is the caller themselves."
                ),
                examples=[
                    OpenApiExample(
                        name="Cannot revoke yourself",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "You cannot revoke your own account.",
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already inactive",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "This staff member is already inactive.",
                                    "field_name": None,
                                }
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
    def post(self, request, pk):
        staff = get_object_or_404(User, pk=pk)
        staff = staff_service.revoke_staff(
            actor=request.user, staff=staff, request=request
        )
        return Response(
            {
                "detail": f"Staff access revoked for {staff.email}.",
                "staff": StaffMemberSerializer(staff).data,
            },
            status=200,
        )


class ReactivateStaffView(APIView):
    """Restore a previously revoked staff member's access."""

    permission_classes = [IsSuperAdminRole]

    @extend_schema(
        summary="Reactivate a revoked staff member",
        description=(
            "Restores access for a staff member whose account was previously "
            "revoked. They keep their original role and password, so they can "
            "sign in again immediately without a new invitation.\n\n"
            "Called from the row actions on the Teams page for rows showing "
            "the `REVOKED` status.\n\n"
            "**Auth:** Super Admin.\n\n"
            "**Prerequisites:** The target must be an existing staff member "
            "who is currently inactive and who had accepted their invitation "
            "before being revoked.\n\n"
            "**Important:** An invitation revoked *before* it was ever "
            "accepted cannot be reactivated — that account has no usable "
            "password, so switching it on would produce an active user who "
            "cannot log in. Those return 400 and must be re-invited instead, "
            "which is why the Teams page should offer 'resend invitation' on "
            "`PENDING` rows and 'reactivate' only on `REVOKED` ones. To change "
            "someone's role, revoke and re-invite; this endpoint does not "
            "alter roles."
        ),
        tags=["Admin — Staff"],
        request=None,
        responses={
            200: OpenApiResponse(
                response=StaffActionResponseSerializer,
                description="Access restored.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "detail": (
                                "Staff access restored for ngozi.okonkwo@soludesks.com."
                            ),
                            "staff": _ACTIVE_STAFF_EXAMPLE,
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "The target is already active, was never accepted, is not "
                    "staff, or is the caller themselves."
                ),
                examples=[
                    OpenApiExample(
                        name="Never accepted",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "This invitation was revoked before it "
                                        "was accepted. Send a fresh invitation "
                                        "instead."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already active",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": "This staff member is already active.",
                                    "field_name": None,
                                }
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
    def post(self, request, pk):
        staff = get_object_or_404(User, pk=pk)
        staff = staff_service.reactivate_staff(
            actor=request.user, staff=staff, request=request
        )
        return Response(
            {
                "detail": f"Staff access restored for {staff.email}.",
                "staff": StaffMemberSerializer(staff).data,
            },
            status=200,
        )


# Public endpoint: no security requirement, so Swagger's padlock does
# not attach a bearer token to it.
@extend_schema(auth=[{}])
class AcceptStaffInvitationView(APIView):
    """Consume a staff invitation token, set a password, and activate."""

    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes = [AllowAny]
    serializer_class = AcceptStaffInvitationSerializer  # schema generation only

    @extend_schema(
        summary="Accept a staff invitation",
        description=(
            "Completes a staff invitation: verifies the emailed token, sets the "
            "invitee's chosen password, and activates the account in the role "
            "the Super Admin selected. This proves the invitee controls the "
            "mailbox that was invited, which is why the account stays dormant "
            "until this call succeeds. A JWT pair is returned so the new staff "
            "member lands straight in the dashboard without a separate login "
            "round-trip.\n\n"
            "Final step of the staff flow: bootstrap super admin → invite "
            "staff → **staff accepts**. The frontend reads `email` and `token` "
            "from the invitation link's query string and collects only the "
            "password from the user.\n\n"
            "**Auth:** Public — no bearer token. The invitation token is the "
            "credential.\n\n"
            "**Prerequisites:** A Super Admin must have invited this email via "
            "`/api/v1/auth/staff/invitations/`, and the invitation must be "
            "unused, unexpired, and not revoked. `email` must match the "
            "invited address exactly.\n\n"
            "**Important:** The token is single-use — replaying this call "
            "returns 404, so treat the returned tokens as the only chance to "
            "capture the session. Invalid tokens, unknown emails, revoked "
            "invitations, and mismatched email/token pairs all return an "
            "identical 404 so the endpoint cannot be used to discover who has "
            "been invited. The role comes from the invitation and cannot be "
            "influenced here. The password set here becomes the permanent "
            "login credential; later sessions use `/api/v1/auth/login/`."
        ),
        tags=["Auth — Staff Invitation"],
        request=AcceptStaffInvitationSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "tunde.bakare@soludesks.com",
                    "token": "8Kj2mNqR7vXyB4dW1sHfL6pT0aZcE3gU9nY5bV8rQmI",
                    "password": "Rw4$eTn8Kp2q",
                },
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=AuthTokenPairResponseSerializer,
                description=(
                    "Invitation accepted; the staff member is now active and signed in."
                ),
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIn0.7Qm2",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCJ9.9Lp4",
                            "user": {
                                "id": "3c7e5f20-91ab-4d63-8e5c-2f4a1b9d7e08",
                                "email": "tunde.bakare@soludesks.com",
                                "first_name": "Tunde",
                                "last_name": "Bakare",
                                "role": "STAFF_WRITER",
                                "is_active": True,
                                "created_datetime": "2026-07-18T10:02:55.117Z",
                                "has_completed_onboarding": False,
                            },
                        },
                    ),
                ],
            ),
            400: OpenApiResponse(
                description=(
                    "The invitation expired, was attempted too many times, or "
                    "the chosen password failed validation."
                ),
                examples=[
                    OpenApiExample(
                        name="Expired invitation",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "This link has expired. Please request a new one."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Weak password",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "password_too_short",
                                    "message": (
                                        "This password is too short. It must "
                                        "contain at least 8 characters."
                                    ),
                                    "field_name": "password",
                                }
                            ]
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                description=(
                    "No matching unused invitation — wrong token, unknown "
                    "email, already accepted, or revoked."
                ),
                examples=[
                    OpenApiExample(
                        name="Invalid or used invitation",
                        value={
                            "errors": [
                                {
                                    "type": "client_error",
                                    "code": "not_found",
                                    "message": (
                                        "Invalid or expired invitation link. "
                                        "Please ask your super admin to resend it."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        serializer = AcceptStaffInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = staff_service.accept_staff_invitation(
            **serializer.validated_data, request=request
        )
        tokens = auth_service.generate_access_token(user=user)
        return Response({**tokens, "user": MeSerializer(user).data}, status=200)
