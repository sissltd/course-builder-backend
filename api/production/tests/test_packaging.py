"""Final production: publishing a course with the engine on builds its
canonical package and SCORM exports, and delivers it to each channel the
Approver chose through that channel's mapping - an API push for SoluDesk, an
upload kit for Udemy and Coursera. Channel mappings are versioned data,
managed over HTTP. Storage is a directory and no channel is ever called.
"""

import json
import zipfile
from unittest import mock

import httpx
import pytest
from django.test import override_settings

from api.courses.enums import CourseSourceType, CourseStatus, DistributionChannel, DistributionStatus, VideoProvider
from api.courses.models import Course, CourseDistribution, Lesson
from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course
from api.notification.models import Notification
from api.production.enums import AssetKind, ProductionRunStatus, RunKind
from api.production.models import ChannelMapping, ProductionAsset, ProductionRun
from api.production.services import channel_mapping_service, production_service
from api.production.tasks import run_production
from api.production.tests.conftest import BUCKET, set_settings
from api.reviews.enums import MediaAssetKind
from api.reviews.models import MediaAsset
from api.users.enums import UserRole

MAPPINGS = "/api/v1/admin/production/channel-mappings/"
PACKAGE = "/api/v1/admin/production/courses/{}/package/"
PUBLISHED = "/api/v1/admin/production/distributions/{}/published/"


@pytest.fixture
def engine(db):
    set_settings(production_enabled=True)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def admin(make_user):
    return make_user(role=UserRole.ADMIN)


def _published(creator, store, *, channels=(DistributionChannel.SOLUDESK, DistributionChannel.UDEMY), **fields) -> Course:
    """A published course whose lesson videos and captions sit in storage."""

    course = build_compliant_course(creator=creator)
    lessons = list(Lesson.objects.filter(module__course=course))
    assets = []
    for lesson in lessons:
        for name, body in ((f"{lesson.id}.mp4", b"video"), (f"{lesson.id}.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"), (f"{lesson.id}.vtt", b"WEBVTT\n\nHi\n")):
            (store.root / "media").mkdir(exist_ok=True)
            (store.root / "media" / name).write_bytes(body)
        lesson.video_url = f"{BUCKET}/media/{lesson.id}.mp4"
        lesson.video_script_file = f"media/{lesson.id}.srt"
        assets.append(MediaAsset(course=course, lesson=lesson, kind=MediaAssetKind.VIDEO, url=lesson.video_url, subtitle_url=f"{BUCKET}/media/{lesson.id}.vtt"))
    Lesson.objects.bulk_update(lessons, ["video_url", "video_script_file"])
    MediaAsset.objects.bulk_create(assets)
    values = {
        "status": CourseStatus.PUBLISHED,
        "slug": f"course-{course.id.hex[:8]}",
        "thumbnail_url": f"{BUCKET}/media/thumb.png",
        "preview_video_url": f"{BUCKET}/media/trailer.mp4",
        "duration_estimate_minutes": 240,
        **fields,
    }
    for name, value in values.items():
        setattr(course, name, value)
    course.save()
    for channel in channels:
        CourseDistribution.objects.create(course=course, channel=channel, learner_price="25000.00", status=DistributionStatus.QUEUED)
    return course


def _package(course, actor):
    run = production_service.request_production(course=course, actor=actor, kind=RunKind.PACKAGE)
    run_production(str(run.id))
    run.refresh_from_db()
    return run


def _channel(course, channel):
    return CourseDistribution.objects.get(course=course, channel=channel)


def _pushed(status=201, body=None):
    response = httpx.Response(status, json=body if body is not None else {"id": "sd-1"}, request=httpx.Request("POST", "https://soludesk.test/courses"))
    return mock.patch("api.production.services.packaging_service.httpx.post", return_value=response)


# --- publishing hands the course to final production --------------------------------


@pytest.mark.django_db
def test_switched_on_publishing_queues_a_package_run_and_leaves_soludesk_to_the_push(creator, store, engine, make_user, no_dispatch):
    superadmin = make_user(role=UserRole.SUPER_ADMIN)
    course = _published(creator, store, status=CourseStatus.APPROVED, slug=None)

    course_service.publish_course(course=course, actor=superadmin)

    assert _channel(course, DistributionChannel.SOLUDESK).status == DistributionStatus.QUEUED
    run = ProductionRun.objects.get(course=course)
    assert (run.kind, run.status, run.quote_amount) == (RunKind.PACKAGE, ProductionRunStatus.QUEUED, 0)
    course.refresh_from_db()
    assert course.slug == "test-course"


@pytest.mark.django_db
def test_switched_off_publishing_marks_soludesk_live_at_once_and_produces_nothing(creator, store, make_user):
    superadmin = make_user(role=UserRole.SUPER_ADMIN)
    course = _published(creator, store, status=CourseStatus.APPROVED, slug=None)

    course_service.publish_course(course=course, actor=superadmin)

    assert _channel(course, DistributionChannel.SOLUDESK).status == DistributionStatus.PUBLISHED
    assert not ProductionRun.objects.filter(course=course).exists()


@pytest.mark.django_db
def test_a_second_course_with_the_same_title_gets_the_next_free_slug(creator, store):
    first = _published(creator, store, slug="test-course")
    second = build_compliant_course(creator=creator)
    assert course_service.catalogue_slug(second) == "test-course-2"
    assert course_service.catalogue_slug(first) == "test-course"


# --- the package and SCORM ---------------------------------------------------------------


@pytest.mark.django_db
@override_settings(SOLUDESK_API_URL="https://soludesk.test/courses", SOLUDESK_API_KEY="sd-key")
def test_the_package_run_pushes_to_soludesk_and_builds_scorm_and_the_udemy_kit(creator, store, engine, no_dispatch):
    course = _published(creator, store)

    with _pushed() as post:
        run = _package(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    soludesk = _channel(course, DistributionChannel.SOLUDESK)
    assert (soludesk.status, soludesk.external_course_id) == (DistributionStatus.PUBLISHED, "sd-1")
    sent = post.call_args.kwargs
    assert sent["headers"]["Authorization"] == "Bearer sd-key"
    payload = sent["json"]
    assert payload["title"] == course.title and payload["price"]["amount"] == 25000.0
    lesson_links = [lesson["video_url"] for module in payload["modules"] for lesson in module["lessons"]]
    assert len(lesson_links) == 12 and all(link.startswith("https://signed.test/media/") for link in lesson_links)

    udemy = _channel(course, DistributionChannel.UDEMY)
    assert (udemy.status, udemy.failure_reason) == (DistributionStatus.QUEUED, "")  # waits for a person
    kit = ProductionAsset.objects.get(course=course, kind=AssetKind.UPLOAD_KIT)
    with zipfile.ZipFile(store.path(kit.file_key)) as bundle:
        names = set(bundle.namelist())
        assert {"payload.json", "README.md", "curriculum.csv"} <= names
        assert sum(name.startswith("captions/") for name in names) == 12
        assert json.loads(bundle.read("payload.json"))["title"] == course.title

    for kind, version in ((AssetKind.SCORM_12, "1.2"), (AssetKind.SCORM_2004, "2004 4th Edition")):
        scorm = ProductionAsset.objects.get(course=course, kind=kind)
        with zipfile.ZipFile(store.path(scorm.file_key)) as bundle:
            manifest = bundle.read("imsmanifest.xml").decode()
            assert f"<schemaversion>{version}</schemaversion>" in manifest
            assert manifest.count('identifierref="r_m') == 12
            assert bundle.read("media/m0_l0.mp4") == b"video"  # media travels inside
            page = bundle.read("lessons/m0_l0.html").decode()
            assert "../media/m0_l0.vtt" in page and "SoludeskScorm.complete()" in page
    assert ProductionAsset.objects.filter(course=course, kind=AssetKind.PACKAGE).exists()


@pytest.mark.django_db
def test_without_a_soludesk_endpoint_the_channel_fails_with_the_reason_and_managers_hear(creator, store, engine, admin, no_dispatch):
    course = _published(creator, store, channels=(DistributionChannel.SOLUDESK,))

    run = _package(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED
    soludesk = _channel(course, DistributionChannel.SOLUDESK)
    assert soludesk.status == DistributionStatus.FAILED and "SOLUDESK_API_URL" in soludesk.failure_reason
    assert Notification.objects.filter(receiver=admin, title="Distribution needs attention").exists()


@pytest.mark.django_db
@override_settings(SOLUDESK_API_URL="https://soludesk.test/courses")
def test_soludesk_refusing_the_course_records_its_answer(creator, store, engine, no_dispatch):
    course = _published(creator, store, channels=(DistributionChannel.SOLUDESK,))

    with _pushed(status=422, body={"error": "category unknown"}):
        _package(course, creator)

    soludesk = _channel(course, DistributionChannel.SOLUDESK)
    assert soludesk.status == DistributionStatus.FAILED
    assert "HTTP 422" in soludesk.failure_reason and "category unknown" in soludesk.failure_reason


@pytest.mark.django_db
@override_settings(SOLUDESK_API_URL="https://soludesk.test/courses")
def test_soludesk_being_down_retries_the_run_and_a_live_channel_is_never_pushed_twice(creator, store, engine, no_dispatch):
    course = _published(creator, store, channels=(DistributionChannel.SOLUDESK,))

    with _pushed(status=503):
        run = _package(course, creator)
    assert run.status == ProductionRunStatus.QUEUED  # retried later

    with _pushed() as post:
        run_production(str(run.id))
        run.refresh_from_db()
        assert run.status == ProductionRunStatus.COMPLETED
        again = production_service.request_production(course=course, actor=creator, kind=RunKind.PACKAGE)
        run_production(str(again.id))
    assert post.call_count == 1


@pytest.mark.django_db
def test_udemy_refuses_a_fully_ai_course_by_policy(creator, store, engine, no_dispatch):
    course = _published(
        creator, store, channels=(DistributionChannel.UDEMY,),
        source_type=CourseSourceType.DEVELOPER_API, video_provider=VideoProvider.PRODUCTION_ENGINE,
    )

    _package(course, creator)

    udemy = _channel(course, DistributionChannel.UDEMY)
    assert udemy.status == DistributionStatus.FAILED and "entirely AI-generated" in udemy.failure_reason
    assert not ProductionAsset.objects.filter(course=course, kind=AssetKind.UPLOAD_KIT).exists()


@pytest.mark.django_db
def test_a_course_short_of_udemys_minimums_fails_with_every_gap(creator, store, engine, no_dispatch):
    course = _published(creator, store, channels=(DistributionChannel.UDEMY,), duration_estimate_minutes=20, title="T" * 70)

    _package(course, creator)

    reason = _channel(course, DistributionChannel.UDEMY).failure_reason
    assert reason.startswith("Mapping v1 found gaps:")
    assert "total_video_minutes" in reason


@pytest.mark.django_db
def test_an_unchanged_course_reuses_its_scorm_exports(creator, store, engine, no_dispatch):
    course = _published(creator, store, channels=())
    _package(course, creator)
    first = set(ProductionAsset.objects.filter(course=course, kind=AssetKind.SCORM_12).values_list("key", flat=True))

    _package(course, creator)

    assert set(ProductionAsset.objects.filter(course=course, kind=AssetKind.SCORM_12).values_list("key", flat=True)) == first


# --- the mapper -----------------------------------------------------------------------------


def test_the_mapper_reads_paths_applies_transforms_and_maps_lists():
    mapping = ChannelMapping(
        channel="UDEMY",
        version=9,
        field_map={
            "title": {"from": "course.title", "transform": ["truncate:6"]},
            "lang": {"const": "English"},
            "price.amount": {"from": "channel.price", "transform": ["number"]},
            "missing": {"from": "course.nope", "default": "fallback"},
            "skipped": {"from": "course.nope"},
            "sections": {"each": "modules", "map": {"name": {"from": "title", "transform": ["upper"]}, "course": {"from": "/course.title"}}},
            "count": {"from": "modules", "transform": ["count"]},
        },
        target_schema={"type": "object", "required": ["skipped"], "properties": {"title": {"maxLength": 6}}},
    )
    package = {"course": {"title": "Kubernetes"}, "channel": {"price": "19.99"}, "modules": [{"title": "one"}, {"title": "two"}]}

    payload, gaps = channel_mapping_service.apply(mapping, package)

    assert payload == {
        "title": "Kuber…",
        "lang": "English",
        "price": {"amount": 19.99},
        "missing": "fallback",
        "sections": [{"name": "ONE", "course": "Kubernetes"}, {"name": "TWO", "course": "Kubernetes"}],
        "count": 2,
    }
    assert gaps == ["(payload): 'skipped' is a required property"]


# --- managing mappings over HTTP ------------------------------------------------------------


@pytest.mark.django_db
def test_mappings_list_needs_pipeline_access_and_shows_the_seeded_versions(api_client, creator, admin):
    assert api_client.get(MAPPINGS).status_code == 401
    api_client.force_authenticate(creator)
    assert api_client.get(MAPPINGS).status_code == 403

    api_client.force_authenticate(admin)
    response = api_client.get(MAPPINGS)

    assert response.status_code == 200
    active = {(row["channel"], row["version"]) for row in response.data if row["is_active"]}
    assert active == {("SOLUDESK", 1), ("UDEMY", 1), ("COURSERA", 1)}
    assert api_client.get(f"{MAPPINGS}?channel=NOPE").status_code == 400


@pytest.mark.django_db
def test_saving_a_mapping_validates_it_and_can_replace_the_active_version(api_client, admin):
    api_client.force_authenticate(admin)
    base = {"channel": "UDEMY", "delivery_method": "UPLOAD_KIT", "target_schema": {"type": "object"}}

    bad_schema = api_client.post(MAPPINGS, {**base, "target_schema": {"type": "nonsense"}, "field_map": {"t": {"const": 1}}}, format="json")
    bad_rule = api_client.post(MAPPINGS, {**base, "field_map": {"t": {"from": "a", "transform": ["explode"]}}}, format="json")
    saved = api_client.post(MAPPINGS, {**base, "field_map": {"title": {"from": "course.title"}}, "activate": True}, format="json")

    assert bad_schema.status_code == 400 and bad_schema.json()["errors"][0]["field_name"] == "target_schema"
    assert bad_rule.status_code == 400 and "unknown transform" in bad_rule.json()["errors"][0]["message"]
    assert saved.status_code == 201 and saved.data["version"] == 2 and saved.data["is_active"]
    assert list(ChannelMapping.objects.filter(channel="UDEMY", is_active=True).values_list("version", flat=True)) == [2]

    rollback = api_client.post(f"{MAPPINGS}{ChannelMapping.objects.get(channel='UDEMY', version=1).id}/activate/")
    assert rollback.status_code == 200 and rollback.data["version"] == 1
    assert list(ChannelMapping.objects.filter(channel="UDEMY", is_active=True).values_list("version", flat=True)) == [1]


@pytest.mark.django_db
def test_only_production_managers_change_mappings(api_client, make_user):
    viewer = make_user(role=UserRole.STAFF_WRITER)
    api_client.force_authenticate(viewer)
    mapping = ChannelMapping.objects.get(channel="SOLUDESK", version=1)

    assert api_client.post(MAPPINGS, {}, format="json").status_code == 403
    assert api_client.post(f"{MAPPINGS}{mapping.id}/activate/").status_code == 403


@pytest.mark.django_db
def test_previewing_a_mapping_shapes_a_course_and_lists_its_gaps(api_client, admin, creator, store):
    course = _published(creator, store, title="T" * 70)
    api_client.force_authenticate(admin)
    udemy = ChannelMapping.objects.get(channel="UDEMY", version=1)

    response = api_client.post(f"{MAPPINGS}{udemy.id}/preview/", {"course_id": str(course.id)}, format="json")

    assert response.status_code == 200
    assert response.data["payload"]["title"] == "T" * 59 + "…"
    assert response.data["gaps"] == []
    unknown = api_client.post(f"{MAPPINGS}{udemy.id}/preview/", {"course_id": "00000000-0000-4000-8000-000000000000"}, format="json")
    assert unknown.status_code == 404
    assert api_client.post(f"{MAPPINGS}00000000-0000-4000-8000-000000000000/preview/", {"course_id": str(course.id)}, format="json").status_code == 404


# --- package downloads, packaging again, manual uploads ------------------------------------


@pytest.mark.django_db
def test_package_downloads_list_the_newest_files_and_each_lessons_media(api_client, admin, creator, store, engine, no_dispatch):
    course = _published(creator, store, channels=(DistributionChannel.UDEMY,))
    _package(course, creator)
    api_client.force_authenticate(admin)

    response = api_client.get(PACKAGE.format(course.id))

    assert response.status_code == 200
    kinds = {row["kind"] for row in response.data["files"]}
    assert kinds == {"PACKAGE", "SCORM_12", "SCORM_2004", "UPLOAD_KIT"}
    assert all(row["url"].startswith("https://signed.test/production/") for row in response.data["files"])
    assert len(response.data["lessons"]) == 12
    assert response.data["lessons"][0]["captions_srt_url"].startswith("https://signed.test/media/")
    assert api_client.get(PACKAGE.format("00000000-0000-4000-8000-000000000000")).status_code == 404


@pytest.mark.django_db
def test_packaging_again_needs_a_published_course(api_client, admin, creator, store, no_dispatch):
    draft = build_compliant_course(creator=creator)
    published = _published(creator, store)
    api_client.force_authenticate(admin)

    refused = api_client.post(PACKAGE.format(draft.id))
    queued = api_client.post(PACKAGE.format(published.id))
    repeated = api_client.post(PACKAGE.format(published.id))

    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "production_run_conflict"
    assert queued.status_code == 201 and queued.data["kind"] == "PACKAGE"
    assert repeated.data["id"] == queued.data["id"]


@pytest.mark.django_db
def test_recording_a_manual_upload_marks_the_channel_live(api_client, admin, creator, store, make_user):
    course = _published(creator, store, channels=(DistributionChannel.UDEMY,))
    udemy = _channel(course, DistributionChannel.UDEMY)
    api_client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
    assert api_client.post(PUBLISHED.format(udemy.id), {"external_course_id": "51"}, format="json").status_code == 403

    api_client.force_authenticate(admin)
    response = api_client.post(PUBLISHED.format(udemy.id), {"external_course_id": "5123456"}, format="json")
    again = api_client.post(PUBLISHED.format(udemy.id), {"external_course_id": "5123456"}, format="json")

    assert response.status_code == 200 and response.data["status"] == "PUBLISHED"
    assert again.data["published_at"] == response.data["published_at"]  # idempotent
    assert api_client.post(PUBLISHED.format(udemy.id), {}, format="json").status_code == 400
    assert api_client.post(PUBLISHED.format("00000000-0000-4000-8000-000000000000"), {"external_course_id": "1"}, format="json").status_code == 404
    Course.objects.filter(pk=course.pk).update(status=CourseStatus.APPROVED)
    assert api_client.post(PUBLISHED.format(udemy.id), {"external_course_id": "9"}, format="json").status_code == 409
