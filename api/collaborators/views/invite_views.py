from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.collaborators.enums import CollaboratorInviteStatus
from api.collaborators.models import CollaboratorInvite
from api.collaborators.serializers import (
    CollaboratorInviteCreateSerializer,
    CollaboratorInviteSerializer,
)
from api.collaborators.services import collaborator_service, invite_service
from api.courses.models import Course
from api.authorization import codenames
from api.authorization.permissions import Perm
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_AUTH_LINE = (
    "**Auth:** Sending, listing and revoking invites needs the "
    "`courses.create` permission (Course Creator and Writer by default) plus "
    "manage access to the course (its creator or an Admin-role collaborator). "
    "The invitee-side actions - reading your own invite, `/incoming/`, accept "
    "and decline - need only a signed-in session whose email matches the "
    "invite, since an invitee holds no permission on the course yet."
)

#: Actions an invitee performs on their own invite. These deliberately drop the
#: `courses.create` gate: an invite is addressed to an email, not to a role, so
#: requiring a course permission would lock the recipient out of the very offer
#: that would grant them access. Scoping to the caller's own email in
#: `get_queryset` (and the email match in `invite_service`) is the real guard.
_SELF_SERVICE_ACTIONS = frozenset({"retrieve", "incoming", "accept", "decline"})

_NOT_OPEN_400 = OpenApiResponse(
    description="The invite is not in an actionable state (already responded, revoked, or expired).",
    examples=[
        OpenApiExample(
            name="Not open",
            value={
                "errors": [
                    {
                        "type": "validation_error",
                        "code": "invalid",
                        "message": "This invite has expired. Ask the course owner to send a new one.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)


class CollaboratorInviteViewSet(ModelViewSet):
    """Lifecycle endpoints for course collaboration invites.

    An invite starts PENDING; acceptance creates the CourseCollaborator
    grant, decline/revoke close it out, and 14 days of silence expires it.
    Until acceptance the invite confers no access.
    """

    permission_classes = [Perm(codenames.COURSES_CREATE)]
    serializer_class = CollaboratorInviteSerializer

    def get_permissions(self):
        """Gate the manage surface on `courses.create`, the invitee surface on
        a session alone - see `_SELF_SERVICE_ACTIONS`."""

        if self.action in _SELF_SERVICE_ACTIONS:
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CollaboratorInvite.objects.none()
        queryset = CollaboratorInvite.objects.select_related(
            "course", "invited_by"
        ).prefetch_related("assigned_modules")
        if self.action == "list":
            course_id = self.request.query_params.get("course_id")
            if not course_id:
                raise exceptions.ValidationError(
                    {"course_id": "This query parameter is required."}
                )
            queryset = queryset.filter(course=self._get_accessible_course(course_id))
        elif self.action == "incoming":
            # Expired invites are excluded rather than shown as actionable:
            # accept would reject them anyway, so listing one would offer the
            # invitee a button that can only fail.
            queryset = queryset.filter(
                email__iexact=self.request.user.email,
                status=CollaboratorInviteStatus.PENDING,
                expires_at__gt=timezone.now(),
            )
        elif self.action == "retrieve":
            # The target of the emailed deep-link, which carries an invite id
            # and nothing else. Resolvable from either side: the invitee by
            # email match, the course's manager by access. Unlike /incoming/
            # this does NOT hide closed or expired invites - the page needs to
            # read `status`/`is_expired` to explain why there is no action,
            # instead of rendering an empty screen.
            queryset = queryset.filter(
                Q(email__iexact=self.request.user.email)
                | Q(
                    course__in=collaborator_service.get_courses_accessible_to(
                        self.request.user
                    )
                )
            )
        elif self.action in {"accept", "decline"}:
            # The invitee usually has NO access to the course yet - that's
            # what acceptance grants - so these actions resolve by email
            # match instead of course access; anything else 404s.
            queryset = queryset.filter(email__iexact=self.request.user.email)
        else:
            # Remaining detail actions (revoke) resolve through courses the
            # caller can access, so invites on other courses 404 instead of
            # leaking existence.
            queryset = queryset.filter(
                course__in=collaborator_service.get_courses_accessible_to(
                    self.request.user
                )
            )
        return queryset

    def _get_accessible_course(self, course_id) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=course_id)
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can manage invites."
            )

    @extend_schema(
        summary="Send a collaboration invite",
        description=(
            "Creates a PENDING invite for an email address. The email does "
            "not need an account yet - the recipient signs up with that "
            "address and accepts. An invitation email is sent immediately. "
            "Re-inviting supersedes any prior pending "
            "invite for the same course+email. Invites expire after 14 "
            "days.\n\n"
            f"{_AUTH_LINE}"
        ),
        tags=["Creator — Collaborators"],
        request=CollaboratorInviteCreateSerializer,
        responses={
            201: OpenApiResponse(response=CollaboratorInviteSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = CollaboratorInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        course = self._get_accessible_course(data.pop("course_id"))
        self._require_manage_access(course)
        invite = invite_service.create_invite(
            course=course, inviter=request.user, **data
        )
        return Response(
            CollaboratorInviteSerializer(invite).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="List a course's invites",
        description=(
            "Returns every invite for a course (any status), newest first - "
            "the pending queue plus the accepted/declined/revoked history.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Important:** `course_id` is required; omitting it returns 400."
        ),
        tags=["Creator — Collaborators"],
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="UUID of the course to list invites for.",
            ),
        ],
        responses={
            200: OpenApiResponse(response=CollaboratorInviteSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Read a single invite",
        description=(
            "Resolves one invite by id - the endpoint behind the emailed "
            "deep-link `/creator/invitations?invite_id={id}`. Readable by the "
            "invitee (matched on the session email) or by someone with access "
            "to the course; to anyone else the invite 404s rather than "
            "confirming it exists.\n\n"
            "Invites in any state are returned, so read `status` and "
            "`is_expired` to decide what to render: PENDING and not expired "
            "means show Accept/Decline, anything else means explain why the "
            "invite is closed.\n\n"
            "**Auth:** a signed-in session; no course permission is required."
        ),
        tags=["Creator — Collaborators"],
        responses={
            200: OpenApiResponse(response=CollaboratorInviteSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="List my incoming invites",
        description=(
            "Returns the caller's own open invites - PENDING and not yet "
            "expired - the inbox behind 'You've been invited to collaborate' "
            "banners. Expired invites are omitted because they can no longer "
            "be accepted.\n\n"
            "**Auth:** a signed-in session; no course permission is required."
        ),
        tags=["Creator — Collaborators"],
        responses={
            200: OpenApiResponse(response=CollaboratorInviteSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=False, methods=["get"])
    def incoming(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        summary="Accept an invite",
        description=(
            "Accepts a PENDING invite as the signed-in user, creating the "
            "collaborator grant with the invited role and module scope. "
            "Requires the session account's email to match the invite."
        ),
        tags=["Creator — Collaborators"],
        request=None,
        responses={
            200: OpenApiResponse(description="Invite accepted."),
            400: _NOT_OPEN_400,
            **STANDARD_ERROR_RESPONSES["auth"],
            # Invites not addressed to the session email (and invites on
            # courses you can't access) resolve as 404 - existence hidden.
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        invite = self.get_object()
        collaborator = invite_service.accept_invite(
            invite=invite, user=request.user
        )
        return Response(
            {
                "detail": "Invite accepted.",
                "collaborator_id": str(collaborator.id),
                "course_id": str(invite.course_id),
            }
        )

    @extend_schema(
        summary="Decline an invite",
        description=(
            "Declines a PENDING invite as the signed-in user. Requires the "
            "session account's email to match the invite. The inviter is "
            "notified."
        ),
        tags=["Creator — Collaborators"],
        request=None,
        responses={
            200: OpenApiResponse(description="Invite declined."),
            400: _NOT_OPEN_400,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        invite = self.get_object()
        invite_service.decline_invite(invite=invite, user=request.user)
        return Response({"detail": "Invite declined.", "invite_id": str(invite.id)})

    def perform_destroy(self, instance):
        self._require_manage_access(instance.course)
        invite_service.revoke_invite(invite=instance)

    @extend_schema(
        summary="Revoke an invite",
        description=(
            "Cancels a PENDING invite so it can no longer be accepted. "
            "Accepted and declined invites are final and can't be revoked."
        ),
        tags=["Creator — Collaborators"],
        request=None,
        responses={
            204: OpenApiResponse(description="Invite revoked."),
            400: OpenApiResponse(
                description="The invite was already accepted or declined."
            ),
            403: OpenApiResponse(
                description="Caller lacks manage access to the invite's course."
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
