from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from api.courses.models import CourseVersion
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


class CourseVersionSerializer(serializers.ModelSerializer):
    """A selectable course version label."""

    class Meta:
        model = CourseVersion
        fields = ["id", "label", "is_active"]
        read_only_fields = fields


@extend_schema(tags=["Creator — Courses"])
class CourseVersionListView(ListAPIView):
    """The canonical version labels a course can publish under."""

    permission_classes = [IsAuthenticated]
    serializer_class = CourseVersionSerializer
    pagination_class = None

    def get_queryset(self):
        return CourseVersion.objects.filter(is_active=True).order_by("label")

    @extend_schema(
        summary="List selectable course versions",
        description=(
            "Returns the active, canonical version labels a course can be "
            "published under, oldest label first.\\n\\n"
            "Call this to populate the builder's Version step, then send the "
            "chosen `id` as `version` on "
            "`PATCH /api/v1/courses/{id}/`. Labels are canonical and shared "
            "across every course \\u2014 do not hardcode them in the client.\\n\\n"
            "**Auth:** Any authenticated user.\\n\\n"
            "**Prerequisites:** None.\\n\\n"
            "**Important:** Frozen versions (`is_active=false`) are excluded, "
            "so anything returned here is safe to select. The response is not "
            "paginated. A course's version is honoured at publish time; if "
            "none is set, the lowest active label is used."
        ),
        responses={
            200: OpenApiResponse(
                response=CourseVersionSerializer(many=True),
                description="Active version labels.",
                examples=[
                    OpenApiExample(
                        "Success",
                        value=[
                            {
                                "id": "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b",
                                "label": "1.0",
                                "is_active": True,
                            }
                        ],
                    )
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
