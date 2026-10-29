from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.search.serializers import GlobalSearchResponseSerializer
from api.search.services import global_search_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


class GlobalSearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(
        min_length=2,
        trim_whitespace=True,
        help_text="Search term. Must be at least two non-space characters.",
    )
    limit = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=global_search_service.MAX_LIMIT,
        default=global_search_service.DEFAULT_LIMIT,
        help_text="Maximum results per bucket. Defaults to 5, capped at 10.",
    )


class GlobalSearchView(APIView):
    """Role-scoped global search for the top navigation search box."""

    permission_classes = [IsAuthenticated]
    serializer_class = GlobalSearchQuerySerializer

    @extend_schema(
        summary="Search across platform resources",
        description=(
            "Returns compact, bucketed search results for the global top-bar "
            "search. Buckets are scoped to the caller's role: Admins and "
            "Super Admins can search users and all courses; reviewers search "
            "reviewable courses; creators search only their own courses. "
            "Categories and topics are available to authenticated users.\n\n"
            "Called when the user types into the app-wide search input.\n\n"
            "**Auth:** Any authenticated user.\n\n"
            "**Prerequisites:** `q` must contain at least two non-space "
            "characters."
        ),
        tags=["Global Search"],
        parameters=[
            OpenApiParameter(
                name="q",
                type=str,
                required=True,
                description="Search term.",
            ),
            OpenApiParameter(
                name="limit",
                type=int,
                required=False,
                description=(
                    "Maximum results per bucket. Defaults to 5, capped at 10."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=GlobalSearchResponseSerializer,
                description="Bucketed global search results.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={
                            "query": "python",
                            "limit": 5,
                            "total_count": 2,
                            "results": {
                                "courses": {
                                    "count": 1,
                                    "results": [
                                        {
                                            "type": "course",
                                            "id": (
                                                "2e9c4a71-58b3-4d06-9f27-"
                                                "6a1e8c0b5d34"
                                            ),
                                            "title": "Python for Data Analysis",
                                            "subtitle": "Data Science - Jane Doe",
                                            "status": "SUBMITTED",
                                            "api_path": (
                                                "/api/v1/review-queue/"
                                                "2e9c4a71-58b3-4d06-9f27-"
                                                "6a1e8c0b5d34/"
                                            ),
                                        }
                                    ],
                                },
                                "categories": {
                                    "count": 1,
                                    "results": [
                                        {
                                            "type": "category",
                                            "id": (
                                                "5a1f83c6-92b4-4e70-8d3f-"
                                                "1c7e6b409af2"
                                            ),
                                            "title": "Python",
                                            "subtitle": "Programming courses.",
                                            "status": "ACTIVE",
                                            "api_path": (
                                                "/api/v1/categories/"
                                                "5a1f83c6-92b4-4e70-8d3f-"
                                                "1c7e6b409af2/"
                                            ),
                                        }
                                    ],
                                },
                            },
                        },
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request):
        serializer = GlobalSearchQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = global_search_service.search(
            actor=request.user,
            query=serializer.validated_data["q"],
            limit=serializer.validated_data["limit"],
        )
        return Response(data, status=status.HTTP_200_OK)
