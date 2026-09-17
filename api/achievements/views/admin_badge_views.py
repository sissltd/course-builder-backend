from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.views import APIView

from api.achievements.docs.achievement_docs import (
    BADGE_AWARD_DOCS,
    BADGE_CREATE_DOCS,
    BADGE_DELETE_DOCS,
    BADGE_DELETION_IMPACT_DOCS,
    BADGE_HOLDER_LIST_DOCS,
    BADGE_LIST_DOCS,
    BADGE_RETRIEVE_DOCS,
    BADGE_REVOKE_DOCS,
    BADGE_UPDATE_DOCS,
)
from api.achievements.serializers.admin_badge_serializer import (
    BadgeAwardCreateSerializer,
    BadgeCreateSerializer,
    BadgeDeleteQuerySerializer,
    BadgeDeletionImpactSerializer,
    BadgeDeletionResultSerializer,
    BadgeHolderSerializer,
    BadgeSerializer,
    BadgeUpdateSerializer,
)
from api.achievements.services import award_service, badge_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from includes.helpers.pagination import PageNumberAPIPagination
from shared.response.success import custom_success_response


class BadgeListCreateView(APIView):
    """The Achievement award list, and the Add new badge dialog."""

    permission_classes = [Perm(codenames.ACHIEVEMENTS_MANAGE)]
    pagination_class = PageNumberAPIPagination
    serializer_class = BadgeSerializer  # schema generation only

    @extend_schema(**BADGE_LIST_DOCS)
    def get(self, request):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(badge_service.list_badges(), request, self)
        return paginator.get_paginated_response(BadgeSerializer(page, many=True).data)

    @extend_schema(**BADGE_CREATE_DOCS)
    def post(self, request):
        serializer = BadgeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        badge = badge_service.create_badge(
            actor=request.user, **serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED,
            message="Badge created.",
            data=BadgeSerializer(badge).data,
        )


class BadgeDetailView(APIView):
    """One badge: read, Edit/Configure, Delete."""

    permission_classes = [Perm(codenames.ACHIEVEMENTS_MANAGE)]
    serializer_class = BadgeSerializer  # schema generation only

    @extend_schema(**BADGE_RETRIEVE_DOCS)
    def get(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=BadgeSerializer(badge).data,
        )

    @extend_schema(**BADGE_UPDATE_DOCS)
    def patch(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        serializer = BadgeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        badge = badge_service.update_badge(
            badge=badge, actor=request.user, data=serializer.validated_data
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Badge updated.",
            data=BadgeSerializer(badge).data,
        )

    @extend_schema(**BADGE_DELETE_DOCS)
    def delete(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        query = BadgeDeleteQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        result = badge_service.delete_badge(
            badge=badge,
            actor=request.user,
            move_to_previous=query.validated_data["move_to_previous"],
        )
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Badge deleted.",
            data=BadgeDeletionResultSerializer(result).data,
        )


class BadgeDeletionImpactView(APIView):
    """What the Delete this badge dialog needs to know before confirming."""

    permission_classes = [Perm(codenames.ACHIEVEMENTS_MANAGE)]
    serializer_class = BadgeDeletionImpactSerializer  # schema generation only

    @extend_schema(**BADGE_DELETION_IMPACT_DOCS)
    def get(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=BadgeDeletionImpactSerializer(
                badge_service.get_deletion_impact(badge=badge)
            ).data,
        )


class BadgeHolderListCreateView(APIView):
    """Creators holding a badge, and awarding it by hand."""

    permission_classes = [Perm(codenames.ACHIEVEMENTS_MANAGE)]
    pagination_class = PageNumberAPIPagination
    serializer_class = BadgeHolderSerializer  # schema generation only

    @extend_schema(**BADGE_HOLDER_LIST_DOCS)
    def get(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(
            award_service.list_holders(badge=badge), request, self
        )
        return paginator.get_paginated_response(
            BadgeHolderSerializer(page, many=True).data
        )

    @extend_schema(**BADGE_AWARD_DOCS)
    def post(self, request, badge_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        serializer = BadgeAwardCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        award = award_service.award_manually(
            badge=badge,
            creator_id=serializer.validated_data["creator_id"],
            actor=request.user,
        )
        return custom_success_response(
            status=status.HTTP_201_CREATED,
            message="Badge awarded.",
            data=BadgeHolderSerializer(award).data,
        )


class BadgeHolderDetailView(APIView):
    """Revoking one creator's badge."""

    permission_classes = [Perm(codenames.ACHIEVEMENTS_MANAGE)]

    @extend_schema(**BADGE_REVOKE_DOCS)
    def delete(self, request, badge_id, creator_id):
        badge = badge_service.get_live_badge(badge_id=badge_id)
        award_service.revoke_award(
            badge=badge, creator_id=creator_id, actor=request.user
        )
        return custom_success_response(
            status=status.HTTP_200_OK, message="Badge revoked."
        )
