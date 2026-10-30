"""Soludesk Studio deliveries (`studio.review.v1`) and upload grants.

Studio sends course text and then video to one endpoint, told apart by
`kind`, and uploads video into platform storage through submission-scoped
grants first. Storage itself is faked: request_upload hands out keys and
object_size answers from what the test has "uploaded".

Every request goes to the literal URL Studio calls.
"""

import uuid
from decimal import Decimal

import pytest
from drf_spectacular.generators import SchemaGenerator

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseStatus, QuestionType
from api.courses.models import Course, CourseVersion, LessonContentBlock
from api.courses.tests.factories import make_category, make_questions
from api.mie.enums import StudioReviewKind, WebhookEventType
from api.mie.models import StudioMediaUpload, StudioReviewReceipt, WebhookEvent
from api.mie.serializers.course_push_serializer import CoursePushSerializer
from api.mie.serializers.studio_serializer import StudioReviewEnvelopeSerializer
from api.mie.services import studio_review_service
from api.mie.services.documentation_service import studio_delivery_example
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_submission,
)
from api.platform.services import platform_settings_service
from api.users.enums import UserRole
from shared.services.storage_service import InvalidUploadMetadata, StorageService

REVIEWS = "/api/v1/mie/v1/studio/reviews/"
UPLOADS = "/api/v1/mie/v1/studio/uploads/"
QUEUE = "/api/v1/review-queue/"
EXTERNAL_PREVIEW = "https://cdn.studio.example/rust/preview.mp4"


class FakeStorage:
    """Grants keys like request_upload and remembers what was "PUT"."""

    def __init__(self):
        self.sizes = {}
        self.granted = []

    def request_upload(self, *, filename, content_type, folder, purpose, **metadata):
        if metadata.get("codec") not in (None, "h264"):
            raise InvalidUploadMetadata(f"{purpose} requires the H264 codec.")
        file_key = f"uploads/{folder}/{uuid.uuid4().hex}.mp4"
        self.granted.append(file_key)
        return {
            "upload_url": f"https://storage.example.com/{file_key}?X-Amz-Signature=abc",
            "upload_headers": {"Content-Type": content_type},
            "file_url": f"https://storage.example.com/{file_key}?read",
            "file_key": file_key,
            "expires_in": 3600,
        }

    def put(self, media_url, size):
        self.sizes[media_url.removeprefix(StorageService.public_url(""))] = size

    def object_size(self, file_key):
        return self.sizes.get(file_key)


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(StorageService, "request_upload", fake.request_upload)
    monkeypatch.setattr(StorageService, "object_size", fake.object_size)
    return fake


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def category(db):
    return make_category(
        creator_price_with_video=Decimal("150.00"), status=CategoryStatus.ACTIVE
    )


@pytest.fixture
def version(db):
    return CourseVersion.objects.get_or_create(label="1.0")[0]


@pytest.fixture
def developer(db):
    return make_approved_developer()


@pytest.fixture
def idea(developer, category):
    account, _ = developer
    return make_decided_submission(
        developer=account,
        title="Build a Rust Course",
        category=category,
        reserved_course_id=uuid.uuid4(),
    )


def _text_content(*, title, category, version, description="word " * 150):
    modules = []
    for module_number in range(1, 5):
        modules.append(
            {
                "title": f"Module {module_number}",
                "order": module_number,
                "learning_objectives": ["Module objective"],
                "lessons": [
                    {
                        "title": f"Lesson {module_number}-{lesson_number}",
                        "order": lesson_number,
                        "lesson_type": "VIDEO"
                        if module_number == 1 and lesson_number == 1
                        else "TEXT",
                        "script": "word " * 600,
                        "learning_objectives": ["Objective 1", "Objective 2"],
                        "duration_minutes": 20,
                    }
                    for lesson_number in range(1, 4)
                ],
                "assessment": {
                    "title": f"Module {module_number} quiz",
                    "questions": make_questions(3),
                },
            }
        )
    return {
        "title": title,
        "description": description,
        "category": str(category.id),
        "version": str(version.id),
        "difficulty_level": "INTERMEDIATE",
        "learning_objectives": [f"Course objective {i}" for i in range(1, 6)],
        "terms_accepted": True,
        "modules": modules,
        "final_assessment": {"title": "Final exam", "questions": make_questions(15)},
    }


SOURCE_MAP = [
    {"id": "mod_a", "order": 1, "lessons": [{"id": "les_a1", "order": 1}]},
    {"id": "mod_b", "order": 2, "lessons": []},
]


def _envelope(idea, kind, content, *, revision=1, source_map=None, **overrides):
    body = {
        "schema_version": "studio.review.v1",
        "submission_id": str(idea.id),
        "course_id": str(idea.stable_course_id),
        "revision": revision,
        "kind": kind,
        "content_hash": studio_review_service.content_hash(content),
        "content": content,
    }
    if source_map is not None:
        body["source_map"] = source_map
    body.update(overrides)
    return body


def _deliver(client, key, body):
    return client.post(REVIEWS, body, format="json", HTTP_X_MIE_API_KEY=key)


def _grant(client, key, idea, files):
    return client.post(
        UPLOADS,
        {"submission_id": str(idea.id), "files": files},
        format="json",
        HTTP_X_MIE_API_KEY=key,
    )


def _video_file(asset_id, *, size=48_000_000, purpose="LESSON_VIDEO"):
    return {
        "asset_id": asset_id,
        "filename": f"{asset_id}.mp4",
        "content_type": "video/mp4",
        "purpose": purpose,
        "size": size,
        "width": 1920,
        "height": 1080,
        "codec": "h264",
        **({"duration_seconds": 90} if purpose == "COURSE_PREVIEW_VIDEO" else {}),
    }


def _text_delivered(client, key, idea, category, version):
    content = _text_content(title=idea.title, category=category, version=version)
    response = _deliver(client, key, _envelope(idea, "text", content))
    assert response.status_code == 201
    return content


def _approve_text(client, idea, make_user):
    course = Course.objects.get(pk=idea.stable_course_id)
    client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
    assert client.post(f"{QUEUE}{course.id}/claim/", format="json").status_code == 200
    assert client.post(f"{QUEUE}{course.id}/approve/", format="json").status_code == 200
    client.force_authenticate(None)
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO
    return course


# --- text delivery ------------------------------------------------------------


@pytest.mark.django_db
def test_a_text_delivery_creates_the_course_and_answers_with_a_receipt(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)

    response = _deliver(
        api_client, key, _envelope(idea, "text", content, source_map=SOURCE_MAP)
    )

    assert response.status_code == 201
    course = Course.objects.get(pk=idea.reserved_course_id)
    receipt = StudioReviewReceipt.objects.get()
    assert response.data["receipt_id"] == str(receipt.id)
    assert response.data["course_id"] == str(course.id)
    assert response.data["submission_id"] == str(idea.id)
    assert response.data["kind"] == "text"
    assert response.data["revision"] == 1
    assert response.data["content_hash"] == studio_review_service.content_hash(content)
    assert response.data["status"] == "accepted"
    assert response.data["course_status"] == course.status
    assert response.data["source_map"] == SOURCE_MAP
    assert response.data["media"] == []
    assert response.data["duplicate"] is False
    assert (
        WebhookEvent.objects.filter(
            submission=idea, event_type=WebhookEventType.COURSE_SUBMITTED
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_an_identical_retry_returns_the_first_receipt_and_runs_nothing(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)
    first = _deliver(api_client, key, _envelope(idea, "text", content))

    retry = _deliver(api_client, key, _envelope(idea, "text", content))

    assert retry.status_code == 200
    assert retry.data["receipt_id"] == first.data["receipt_id"]
    assert retry.data["duplicate"] is True
    assert retry["Idempotent-Replayed"] == "true"
    assert StudioReviewReceipt.objects.count() == 1
    assert (
        WebhookEvent.objects.filter(
            submission=idea, event_type=WebhookEventType.COURSE_SUBMITTED
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_the_same_revision_with_other_content_is_a_409(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    _text_delivered(api_client, key, idea, category, version)
    changed = _text_content(
        title=idea.title, category=category, version=version, description="new " * 150
    )

    response = _deliver(api_client, key, _envelope(idea, "text", changed))

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "revision_conflict"
    assert StudioReviewReceipt.objects.count() == 1


@pytest.mark.django_db
def test_an_older_revision_than_one_accepted_is_a_409(
    api_client, developer, idea, category, version, staged_flow
):
    account, key = developer
    StudioReviewReceipt.objects.create(
        developer=account,
        submission=idea,
        schema_version="studio.review.v1",
        kind=StudioReviewKind.TEXT,
        revision=3,
        content_hash="0" * 64,
        course_status=CourseStatus.SUBMITTED,
    )
    content = _text_content(title=idea.title, category=category, version=version)

    response = _deliver(api_client, key, _envelope(idea, "text", content, revision=2))

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "stale_revision"
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_a_hash_that_does_not_match_the_content_is_a_400(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)

    response = _deliver(
        api_client, key, _envelope(idea, "text", content, content_hash="a" * 64)
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "content_hash"
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_the_hash_ignores_key_order_but_not_values():
    assert studio_review_service.content_hash(
        {"a": 1, "b": [{"y": "é", "x": 2}]}
    ) == studio_review_service.content_hash({"b": [{"x": 2, "y": "é"}], "a": 1})
    assert studio_review_service.content_hash({"a": 1}) != (
        studio_review_service.content_hash({"a": 2})
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"schema_version": "studio.review.v2"}, "schema_version"),
        ({"kind": "audio"}, "kind"),
        ({"revision": 0}, "revision"),
        ({"content_hash": "NOT-A-HASH"}, "content_hash"),
    ],
)
def test_an_invalid_envelope_is_a_400(
    api_client, developer, idea, category, version, staged_flow, overrides, field
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)

    response = _deliver(
        api_client, key, {**_envelope(idea, "text", content), **overrides}
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == field
    assert not StudioReviewReceipt.objects.exists()


@pytest.mark.django_db
def test_invalid_content_is_a_400_and_stores_nothing(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)
    del content["modules"]

    response = _deliver(api_client, key, _envelope(idea, "text", content))

    assert response.status_code == 400
    assert not Course.objects.exists()
    assert not StudioReviewReceipt.objects.exists()


@pytest.mark.django_db
def test_a_source_map_naming_a_lesson_content_lacks_is_a_400(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)
    source_map = [{"id": "mod_a", "order": 1, "lessons": [{"id": "x", "order": 9}]}]

    response = _deliver(
        api_client, key, _envelope(idea, "text", content, source_map=source_map)
    )

    assert response.status_code == 400
    assert response.data["errors"][0]["field_name"] == "source_map"


@pytest.mark.django_db
def test_a_course_id_other_than_the_reserved_one_is_a_409(
    api_client, developer, idea, category, version, staged_flow
):
    _, key = developer
    content = _text_content(title=idea.title, category=category, version=version)

    response = _deliver(
        api_client, key, _envelope(idea, "text", content, course_id=str(uuid.uuid4()))
    )

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_id_mismatch"
    assert not Course.objects.exists()


@pytest.mark.django_db
def test_push_refusals_pass_through_unchanged(
    api_client, developer, category, version, staged_flow
):
    account, key = developer
    rejected = make_decided_submission(
        developer=account,
        approved=False,
        title="Build a Rust Course",
        reserved_course_id=uuid.uuid4(),
    )
    content = _text_content(title=rejected.title, category=category, version=version)

    response = _deliver(api_client, key, _envelope(rejected, "text", content))

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "idea_not_approved"
    assert not StudioReviewReceipt.objects.exists()


@pytest.mark.django_db
def test_another_developers_idea_is_a_404_on_both_endpoints(
    api_client, idea, category, version, staged_flow, storage
):
    _, other_key = make_approved_developer()
    content = _text_content(title=idea.title, category=category, version=version)

    delivered = _deliver(api_client, other_key, _envelope(idea, "text", content))
    granted = _grant(api_client, other_key, idea, [_video_file("m1l1")])

    assert delivered.status_code == 404
    assert granted.status_code == 404
    assert not Course.objects.exists()
    assert not StudioMediaUpload.objects.exists()


@pytest.mark.django_db
def test_both_endpoints_need_an_api_key(api_client, idea):
    assert api_client.post(REVIEWS, {}, format="json").status_code == 401
    assert api_client.post(UPLOADS, {}, format="json").status_code == 401


# --- uploads ------------------------------------------------------------------


@pytest.mark.django_db
def test_uploads_are_granted_per_asset_without_the_api_key(
    api_client, developer, idea, storage
):
    _, key = developer

    response = _grant(
        api_client,
        key,
        idea,
        [_video_file("preview", purpose="COURSE_PREVIEW_VIDEO"), _video_file("m1l1")],
    )

    assert response.status_code == 200
    assert [grant["asset_id"] for grant in response.data] == ["preview", "m1l1"]
    for grant in response.data:
        assert grant["status"] == "upload_required"
        assert grant["method"] == "PUT"
        assert grant["upload_url"].startswith("https://storage.example.com/")
        assert grant["media_url"].startswith(StorageService.public_url(""))
        assert not any("mie" in header.lower() for header in grant["upload_headers"])
        assert key not in str(grant)
    assert StudioMediaUpload.objects.filter(submission=idea).count() == 2


@pytest.mark.django_db
def test_asking_again_resumes_a_missing_upload_and_reports_a_stored_one(
    api_client, developer, idea, storage
):
    _, key = developer
    first = _grant(api_client, key, idea, [_video_file("m1l1")]).data[0]

    resumed = _grant(api_client, key, idea, [_video_file("m1l1")]).data[0]
    storage.put(resumed["media_url"], 48_000_000)
    stored = _grant(api_client, key, idea, [_video_file("m1l1")]).data[0]

    assert resumed["status"] == "upload_required"
    assert resumed["media_url"] != first["media_url"]
    assert stored["status"] == "stored"
    assert stored["upload_url"] is None
    assert stored["media_url"] == resumed["media_url"]
    assert StudioMediaUpload.objects.filter(submission=idea).count() == 1


@pytest.mark.django_db
def test_a_stored_asset_asked_for_with_another_size_is_a_409(
    api_client, developer, idea, storage
):
    _, key = developer
    grant = _grant(api_client, key, idea, [_video_file("m1l1")]).data[0]
    storage.put(grant["media_url"], 48_000_000)

    response = _grant(api_client, key, idea, [_video_file("m1l1", size=10)])

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "asset_already_stored"


@pytest.mark.django_db
def test_uploads_need_an_approved_idea(api_client, developer, storage):
    account, key = developer
    pending = make_submission(developer=account)

    response = _grant(api_client, key, pending, [_video_file("m1l1")])

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "idea_not_approved"
    assert not StudioMediaUpload.objects.exists()


@pytest.mark.django_db
def test_one_refused_file_grants_nothing(api_client, developer, idea, storage):
    _, key = developer
    files = [_video_file("m1l1"), {**_video_file("m1l2"), "codec": "vp9"}]

    response = _grant(api_client, key, idea, files)

    assert response.status_code == 400
    assert not StudioMediaUpload.objects.exists()


@pytest.mark.django_db
def test_an_asset_id_may_appear_once_per_request(api_client, developer, idea, storage):
    _, key = developer

    response = _grant(api_client, key, idea, [_video_file("m1l1"), _video_file("m1l1")])

    assert response.status_code == 400
    assert not StudioMediaUpload.objects.exists()


# --- video delivery -----------------------------------------------------------


@pytest.mark.django_db
def test_a_video_delivery_is_accepted_once_its_files_are_stored(
    api_client, developer, idea, category, version, staged_flow, storage, make_user
):
    _, key = developer
    _text_delivered(api_client, key, idea, category, version)
    _approve_text(api_client, idea, make_user)
    grant = _grant(api_client, key, idea, [_video_file("m1l1")]).data[0]
    content = {
        "preview_video_url": EXTERNAL_PREVIEW,
        "lessons": [
            {"module_order": 1, "lesson_order": 1, "video_url": grant["media_url"]}
        ],
    }

    early = _deliver(api_client, key, _envelope(idea, "video", content))
    storage.put(grant["media_url"], 48_000_000)
    accepted = _deliver(api_client, key, _envelope(idea, "video", content))

    assert early.status_code == 400
    assert early.data["errors"][0]["code"] == "media_not_stored"
    assert accepted.status_code == 201
    assert accepted.data["kind"] == "video"
    assert accepted.data["media"] == [
        {
            "url": EXTERNAL_PREVIEW,
            "asset_id": None,
            "stored": False,
            "source": "external",
            "size": None,
        },
        {
            "url": grant["media_url"],
            "asset_id": "m1l1",
            "stored": True,
            "source": "platform",
            "size": 48_000_000,
        },
    ]
    course = Course.objects.get(pk=idea.stable_course_id)
    assert course.status != CourseStatus.AWAITING_VIDEO
    assert course.preview_video_url == EXTERNAL_PREVIEW
    assert StudioMediaUpload.objects.get(asset_id="m1l1").verified_at is not None


@pytest.mark.django_db
def test_storage_urls_not_granted_to_the_idea_are_refused(
    api_client, developer, idea, category, version, staged_flow, storage, make_user
):
    _, key = developer
    _text_delivered(api_client, key, idea, category, version)
    _approve_text(api_client, idea, make_user)
    stray = StorageService.public_url("uploads/courses/someone-else.mp4")
    content = {
        "preview_video_url": EXTERNAL_PREVIEW,
        "lessons": [{"module_order": 1, "lesson_order": 1, "video_url": stray}],
    }

    response = _deliver(api_client, key, _envelope(idea, "video", content))

    assert response.status_code == 400
    assert response.data["errors"][0]["code"] == "media_not_stored"
    assert not StudioReviewReceipt.objects.filter(kind="video").exists()


@pytest.mark.django_db
def test_video_before_the_text_passes_passes_the_push_refusal_through(
    api_client, developer, idea, category, version, staged_flow, storage
):
    _, key = developer
    _text_delivered(api_client, key, idea, category, version)
    content = {"preview_video_url": EXTERNAL_PREVIEW, "lessons": []}

    response = _deliver(api_client, key, _envelope(idea, "video", content))

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "course_not_awaiting_video"
    assert not StudioReviewReceipt.objects.filter(kind="video").exists()


# ── The published contract matches what is enforced ─────────────────────


@pytest.mark.parametrize("kind", ["text", "video"])
def test_the_documented_example_deliveries_pass_validation(
    staged_flow, category, version, kind
):
    example = studio_delivery_example(kind)
    content = example["content"]
    if kind == "text":
        content = {**content, "category": str(category.id), "version": str(version.id)}
    envelope = {
        **example,
        "content": content,
        "content_hash": studio_review_service.content_hash(content),
    }

    serializer = StudioReviewEnvelopeSerializer(
        data=envelope, context={"staged_review_flow": True}
    )

    assert serializer.is_valid(), serializer.errors


def test_the_text_example_uses_every_field_and_block_type():
    content = studio_delivery_example("text")["content"]
    module = content["modules"][0]
    lessons = module["lessons"]
    blocks = [block for lesson in lessons for block in lesson.get("content_blocks", [])]
    questions = [
        question
        for quiz in (
            module["assessment"],
            content["final_assessment"],
            *(lesson["assessment"] for lesson in lessons if "assessment" in lesson),
        )
        for question in quiz["questions"]
    ]

    writable = {
        name for name, field in CoursePushSerializer().fields.items() if not field.read_only
    }
    assert writable - set(content) == {"preview_video_url"}  # sent with the video
    assert {block["block_type"] for block in blocks} == (
        set(LessonContentBlock.BlockType.values) - {"QUIZ"}
    )
    assert {question["type"] for question in questions} == set(QuestionType.values)


def test_swagger_shows_the_full_content_structure_for_each_kind(db):
    schema = SchemaGenerator().get_schema(request=None, public=True)
    body = schema["paths"][REVIEWS]["post"]["requestBody"]["content"]["application/json"]
    delivery = _component(schema, body["schema"]["$ref"])

    assert delivery["discriminator"]["propertyName"] == "kind"
    mapping = delivery["discriminator"]["mapping"]
    text = _component(schema, mapping["text"])
    video = _component(schema, mapping["video"])
    course = _component(schema, text["properties"]["content"]["allOf"][0]["$ref"])
    module = _component(schema, course["properties"]["modules"]["items"]["$ref"])
    lesson = _component(schema, module["properties"]["lessons"]["items"]["$ref"])
    assert {"title", "modules", "final_assessment"} <= set(course["properties"])
    assert {"lessons", "assessment"} <= set(module["properties"])
    assert {"content_blocks", "assessment", "script"} <= set(lesson["properties"])
    video_content = _component(schema, video["properties"]["content"]["allOf"][0]["$ref"])
    assert {"preview_video_url", "lessons"} <= set(video_content["properties"])


def _component(schema, ref):
    return schema["components"]["schemas"][ref.rsplit("/", maxsplit=1)[-1]]
