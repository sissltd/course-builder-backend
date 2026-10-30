"""The Studio review contract we publish matches what is enforced.

The developer docs and Swagger carry full text and video review examples
(documentation_service.studio_delivery_example) and a typed schema per
`kind`. These tests hold both to the real validation of
studio_review_service.accept_review, so the published contract cannot drift
from it.
"""

from decimal import Decimal

import pytest
from drf_spectacular.generators import SchemaGenerator

from api.catalog.enums import CategoryStatus
from api.courses.enums import QuestionType
from api.courses.models import CourseVersion, LessonContentBlock
from api.courses.tests.factories import make_category
from api.mie.serializers.course_push_serializer import CoursePushSerializer
from api.mie.serializers.studio_review_serializer import (
    StudioReviewSerializer,
    StudioVideoSerializer,
)
from api.mie.services import studio_review_service
from api.mie.services.documentation_service import studio_delivery_example

REVIEWS = "/api/v1/mie/v1/studio/reviews/"


@pytest.fixture
def text_content(db):
    category = make_category(
        creator_price_with_video=Decimal("150.00"), status=CategoryStatus.ACTIVE
    )
    version = CourseVersion.objects.get_or_create(label="1.0")[0]
    return {
        **studio_delivery_example("text")["content"],
        "category": str(category.id),
        "version": str(version.id),
    }


@pytest.mark.parametrize("kind", ["text", "video"])
def test_the_example_envelopes_pass_the_envelope_validation(kind):
    example = studio_delivery_example(kind)
    serializer = StudioReviewSerializer(data=example)

    assert serializer.is_valid(), serializer.errors
    assert (
        studio_review_service.content_hash(example["content"])
        == example["content_hash"]
    )


def test_the_text_example_passes_the_text_review_rules(text_content):
    serializer = CoursePushSerializer(
        data=text_content, context={"staged_review_flow": True}
    )
    assert serializer.is_valid(), serializer.errors

    studio_review_service._source_map(
        studio_delivery_example("text")["source_map"], text_content
    )
    studio_review_service._list_items(text_content)
    lessons = [
        lesson for module in text_content["modules"] for lesson in module["lessons"]
    ]
    assert "preview_video_url" not in text_content
    assert {lesson["lesson_type"] for lesson in lessons} == {"TEXT"}
    assert not any(
        lesson.get(field)
        for lesson in lessons
        for field in ("video_url", "embedded_link")
    )
    assert not {
        block["block_type"]
        for lesson in lessons
        for block in lesson.get("content_blocks", [])
    } & {"VIDEO", "EMBED"}


def test_the_video_example_covers_every_text_lesson():
    example = studio_delivery_example("video")
    serializer = StudioVideoSerializer(data=example["content"])
    assert serializer.is_valid(), serializer.errors

    text_lessons = {
        (module["order"], lesson["order"])
        for module in studio_delivery_example("text")["source_map"]
        for lesson in module["lessons"]
    }
    assert example["source_map"] == studio_delivery_example("text")["source_map"]
    assert {
        (lesson["module_order"], lesson["lesson_order"])
        for lesson in serializer.validated_data["lessons"]
    } == text_lessons


def test_the_text_example_uses_every_field_block_and_question_type_text_allows():
    content = studio_delivery_example("text")["content"]
    module = content["modules"][0]
    lessons = module["lessons"]
    blocks = {
        block["block_type"]
        for lesson in lessons
        for block in lesson.get("content_blocks", [])
    }
    quizzes = [
        module["assessment"],
        content["final_assessment"],
        *(lesson["assessment"] for lesson in lessons if "assessment" in lesson),
    ]

    writable = {
        name
        for name, field in CoursePushSerializer().fields.items()
        if not field.read_only
    }
    assert writable - set(content) == {"preview_video_url"}  # sent with the video
    assert blocks == set(LessonContentBlock.BlockType.values) - {
        "QUIZ",
        "VIDEO",
        "EMBED",
    }
    assert {
        question["type"] for quiz in quizzes for question in quiz["questions"]
    } == set(QuestionType.values)


def test_swagger_shows_the_full_content_structure_for_each_kind():
    schema = SchemaGenerator().get_schema(request=None, public=True)
    operation = schema["paths"][REVIEWS]["post"]
    body = operation["requestBody"]["content"]["application/json"]
    review = _component(schema, body["schema"]["$ref"])

    assert review["discriminator"]["propertyName"] == "kind"
    text = _component(schema, review["discriminator"]["mapping"]["text"])
    video = _component(schema, review["discriminator"]["mapping"]["video"])
    course = _component(schema, text["properties"]["content"]["allOf"][0]["$ref"])
    module = _component(schema, course["properties"]["modules"]["items"]["$ref"])
    lesson = _component(schema, module["properties"]["lessons"]["items"]["$ref"])
    assert {"title", "modules", "final_assessment"} <= set(course["properties"])
    assert {"lessons", "assessment"} <= set(module["properties"])
    assert {"content_blocks", "assessment", "script"} <= set(lesson["properties"])
    media = _component(schema, video["properties"]["content"]["allOf"][0]["$ref"])
    media_lesson = _component(schema, media["properties"]["lessons"]["items"]["$ref"])
    assert {"video_url", "captions_url", "transcript"} <= set(
        media_lesson["properties"]
    )
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["required"]
        for parameter in operation["parameters"]
    )


def _component(schema, ref):
    return schema["components"]["schemas"][ref.rsplit("/", maxsplit=1)[-1]]
