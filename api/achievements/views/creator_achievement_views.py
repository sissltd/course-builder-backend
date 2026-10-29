from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.views import APIView

from api.achievements.docs.achievement_docs import CREATOR_ACHIEVEMENTS_DOCS
from api.achievements.serializers.creator_achievement_serializer import (
    CreatorAchievementSerializer,
)
from api.achievements.services import award_service
from api.authorization import codenames
from api.authorization.permissions import Perm
from shared.response.success import custom_success_response


class CreatorAchievementListView(APIView):
    """The signed-in creator's badges and progress."""

    permission_classes = [Perm(codenames.COURSES_CREATE)]
    serializer_class = CreatorAchievementSerializer  # schema generation only

    @extend_schema(**CREATOR_ACHIEVEMENTS_DOCS)
    def get(self, request):
        rows = award_service.creator_achievements(creator=request.user)
        return custom_success_response(
            status=status.HTTP_200_OK,
            message="Retrieved successfully",
            data=CreatorAchievementSerializer(rows, many=True).data,
        )
