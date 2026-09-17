from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.views import APIView

from api.authentication.serializers.staff_invitation_serializer import (
    TeamInvitationSerializer,
)
from api.authorization import codenames
from api.authorization.permissions import IsStrongMFASession, Perm
from api.users.docs.account_admin_docs import erase_docs, reset_docs
from api.users.serializers.account_admin_serializer import EraseAccountSerializer
from api.users.services import account_admin_service, account_erasure_service
from api.users.services.account_admin_service import STAFF, TEAMS
from includes.spectacular.responses import (
    STANDARD_ERROR_RESPONSES,
    inline_success_response,
)
from shared.response.success import custom_success_response


class _SendPasswordResetView(APIView):
    audience: str

    def post(self, request, pk):
        user = account_admin_service.resolve_target(audience=self.audience, user_id=pk)
        account_admin_service.send_password_reset(
            actor=request.user, user=user, audience=self.audience, request=request
        )
        return custom_success_response(
            status=status.HTTP_200_OK, message="Password reset link sent."
        )


class StaffSendPasswordResetView(_SendPasswordResetView):
    audience = STAFF
    permission_classes = [Perm(codenames.STAFF_RESET_PASSWORD)]

    @extend_schema(**reset_docs(STAFF))
    def post(self, request, pk):
        return super().post(request, pk)


class TeamsSendPasswordResetView(_SendPasswordResetView):
    audience = TEAMS
    permission_classes = [Perm(codenames.TEAMS_RESET_PASSWORD)]

    @extend_schema(**reset_docs(TEAMS))
    def post(self, request, pk):
        return super().post(request, pk)


class _EraseAccountView(APIView):
    audience: str

    def post(self, request, pk):
        serializer = EraseAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = account_admin_service.resolve_target(audience=self.audience, user_id=pk)
        account_erasure_service.erase_account(
            actor=request.user,
            user=user,
            audience=self.audience,
            request=request,
            **serializer.validated_data,
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Account deleted.",
            data={"id": str(user.id)},
        )


class StaffEraseView(_EraseAccountView):
    audience = STAFF
    permission_classes = [Perm(codenames.STAFF_DELETE), IsStrongMFASession]

    @extend_schema(**erase_docs(STAFF))
    def post(self, request, pk):
        return super().post(request, pk)


class TeamsEraseView(_EraseAccountView):
    audience = TEAMS
    permission_classes = [Perm(codenames.TEAMS_DELETE_ACCOUNT), IsStrongMFASession]

    @extend_schema(**erase_docs(TEAMS))
    def post(self, request, pk):
        return super().post(request, pk)


class TeamInvitationView(APIView):
    """Invite someone to join as a Creator Reviewer (Invite Teams)."""

    permission_classes = [Perm(codenames.TEAMS_INVITE)]

    @extend_schema(
        summary="Invite a Creator Reviewer",
        description=(
            "Emails an invitation to join as a Creator Reviewer. The invitee "
            "accepts through the same link and endpoint as a staff invitation "
            "(`POST /auth/staff/invitations/accept/`).\n\n"
            "**Auth:** The `teams.invite` permission (Invite Teams) — Admin and "
            "Super Admin by default.\n\n"
            "**Prerequisites:** The email must not belong to an existing account, "
            "unless that account is a pending invitation (which is re-sent).\n\n"
            "**Important:** A re-send within the cooldown is 400. The invitee "
            "cannot sign in until they accept and set a password."
        ),
        tags=["Admin — Users"],
        request=TeamInvitationSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "email": "reviewer@example.com",
                    "first_name": "Ada",
                    "last_name": "Obi",
                },
            )
        ],
        responses={
            201: inline_success_response(
                description="The invitation was sent.",
                examples=[
                    OpenApiExample(
                        name="Sent",
                        value={
                            "success": True,
                            "status": 201,
                            "message": "Invitation sent.",
                            "data": {
                                "id": "9f8e7d6c-5b4a-4321-8765-0fedcba98765",
                                "email": "reviewer@example.com",
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def post(self, request):
        from api.authentication.services.staff_service import StaffService
        from api.authorization.models import Role
        from api.authorization.services.role_registry import system_role_id
        from api.users.enums import UserRole

        serializer = TeamInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access_role = Role.objects.get(id=system_role_id(UserRole.CREATOR_REVIEWER))
        invitee = StaffService().invite_staff(
            invited_by=request.user,
            access_role=access_role,
            request=request,
            **serializer.validated_data,
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED,
            message="Invitation sent.",
            data={"id": str(invitee.id), "email": invitee.email},
        )
