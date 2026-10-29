"""Final production: a published course's package and channel deliveries.

One canonical package describes the course for every destination: its
metadata, curriculum, media links, assessments, prices and AI disclosure.
SCORM 1.2 and 2004 exports carry the media inside them, so any corporate
LMS can import the course. Each distribution channel then gets the package
shaped by its active mapping (channel_mapping_service): pushed to the
channel's API, or built into an upload kit a person uploads.
"""

import csv
import html
import io
import json
import logging
import tempfile
import zipfile
from pathlib import Path

import httpx
from django.conf import settings
from django.utils import timezone

from api.courses.enums import (
    CourseSourceType,
    DistributionChannel,
    DistributionStatus,
    VideoProvider,
)
from api.courses.models import Course, CourseDistribution, Lesson
from api.courses.ai.providers import AIProviderError
from api.operations.enums import PipelineStage
from api.production.enums import AssetKind, DeliveryMethod, PACKAGE_ASSET_KINDS
from api.production.services import asset_store, channel_mapping_service
from api.production.services.run_ledger import RunLedger, alert
from shared.services.storage_service import StorageService

logger = logging.getLogger(__name__)

PACKAGE_SCHEMA_VERSION = "1"
SCORM_TEMPLATE_VERSION = "scorm-1"

MEDIA_LINK_SECONDS = 7 * 24 * 60 * 60
"""Media links in the package and kits last a week, the longest a storage
link may; the package downloads endpoint always signs fresh ones."""

PUSH_TIMEOUT_SECONDS = 60
RETRYABLE_STATUSES = {408, 409, 429, 500, 502, 503, 504}

#: The environment settings a channel's API push reads its URL and key from.
#: Never from the mapping itself: a mapping is admin-editable data and must
#: not be able to point a server secret at an address of its choosing.
PUSH_ENDPOINTS = {DistributionChannel.SOLUDESK: ("SOLUDESK_API_URL", "SOLUDESK_API_KEY")}

AI_NARRATION_STATEMENT = (
    "This course is narrated by an AI-generated (synthetic) voice, and its "
    "visuals were produced automatically from the approved course script."
)
AI_CONTENT_STATEMENT = (
    "The course content was generated with AI and checked by human reviewers before publication."
)
UDEMY_POLICY_REASON = (
    "Udemy does not accept courses that are entirely AI-generated, so this course "
    "is not offered there."
)


# --- the canonical package ----------------------------------------------------------


def disclosure_for(course: Course) -> dict:
    """How the course was made, for every place it is shown or sold.

    Read from fields already loaded, so listing courses costs no queries.
    A developer course whose video the engine makes came from the platform's
    own crawler (see course_push_service.video_provider_for).
    """

    ai_narration = course.video_provider == VideoProvider.PRODUCTION_ENGINE
    ai_content = course.source_type == CourseSourceType.AI_GENERATED or (
        course.source_type == CourseSourceType.DEVELOPER_API and ai_narration
    )
    statements = []
    if ai_content:
        statements.append(AI_CONTENT_STATEMENT)
    if ai_narration:
        statements.append(AI_NARRATION_STATEMENT)
    return {
        "ai_narration": ai_narration,
        "ai_generated_content": ai_content,
        "statement": " ".join(statements),
    }


def _ours(url: str) -> bool:
    return bool(url) and url.startswith(StorageService.public_url(""))


def _link(value: str) -> str:
    """A link an outside system can fetch: our private files are signed,
    anything else passes through."""

    if not value:
        return ""
    if value.startswith(("http://", "https://")) and not _ours(value):
        return value
    return StorageService.generate_presigned_get(value, expires_in=MEDIA_LINK_SECONDS) or ""


def _captions(course: Course) -> dict:
    """VTT caption key per lesson id, from the media evidence."""

    from api.reviews.models import MediaAsset

    return {
        asset.lesson_id: asset.subtitle_url
        for asset in MediaAsset.objects.filter(course=course, kind="VIDEO").only("lesson_id", "subtitle_url")
    }


def build_package(course: Course) -> dict:
    """The canonical package: everything a destination could need, in one
    shape that each channel mapping reads from."""

    course = (
        Course.objects.select_related("category", "topic", "creator", "version", "final_assessment")
        .prefetch_related("modules__lessons__assessment", "modules__assessment", "distribution_channels")
        .get(pk=course.pk)
    )
    captions = _captions(course)
    modules = []
    lesson_count = 0
    for module in sorted(course.modules.all(), key=lambda item: item.order):
        lessons = []
        for lesson in sorted(module.lessons.all(), key=lambda item: item.order):
            lesson_count += 1
            assessment = getattr(lesson, "assessment", None)
            lessons.append(
                {
                    "id": str(lesson.id),
                    "order": lesson.order,
                    "title": lesson.title,
                    "content_type": lesson.content_type,
                    "duration_minutes": lesson.duration_minutes or 0,
                    "learning_objectives": lesson.learning_objectives or [],
                    "video_url": _link(lesson.video_url or lesson.embedded_link),
                    "captions_vtt_url": _link(captions.get(lesson.id, "")),
                    "captions_srt_url": _link(lesson.video_script_file),
                    "transcript": lesson.script or "",
                    "quiz": assessment.questions if assessment else [],
                }
            )
        module_assessment = getattr(module, "assessment", None)
        modules.append(
            {
                "order": module.order,
                "title": module.title,
                "description": module.description or "",
                "lessons": lessons,
                "quiz": module_assessment.questions if module_assessment else [],
            }
        )
    final = getattr(course, "final_assessment", None)
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "course": {
            "id": str(course.id),
            "slug": course.slug or "",
            "title": course.title,
            "description": course.description,
            "category": course.category.name if course.category_id else "",
            "topic": course.topic.name if course.topic_id else "",
            "level": course.difficulty_level,
            "language": "en",
            "duration_minutes": course.duration_estimate_minutes or 0,
            "learning_objectives": course.learning_objectives or [],
            "thumbnail_url": _link(course.thumbnail_url),
            "trailer_url": _link(course.preview_video_url),
            "creator_name": course.creator.get_full_name() if course.creator_id else "",
            "version": course.version.label if course.version_id else "",
            "published_at": course.published_at.isoformat() if course.published_at else "",
            "module_count": len(modules),
            "lesson_count": lesson_count,
        },
        "modules": modules,
        "final_quiz": final.questions if final else [],
        "pricing": [
            {
                "channel": row.channel,
                "price": str(row.learner_price),
                "promotional_price": str(row.promotional_price) if row.promotional_price is not None else None,
                "pricing_model": row.pricing_model,
            }
            for row in course.distribution_channels.all()
        ],
        "disclosure": disclosure_for(course),
    }


def package_for_channel(package: dict, channel: str) -> dict:
    """The package with the channel's own price row as `channel`."""

    row = next((item for item in package["pricing"] if item["channel"] == channel), {})
    return {**package, "channel": row}


# --- SCORM ----------------------------------------------------------------------------

SCORM_JS = """(function () {
  function find(win, name) {
    var hops = 0;
    while (win && !win[name] && win.parent && win.parent !== win && hops < 10) { win = win.parent; hops++; }
    return win && win[name] ? win[name] : null;
  }
  function locate() {
    var api = find(window, "API_1484_11") || (window.opener && find(window.opener, "API_1484_11"));
    if (api) { return { version: 2004, api: api }; }
    api = find(window, "API") || (window.opener && find(window.opener, "API"));
    return api ? { version: 12, api: api } : null;
  }
  var lms = locate();
  var done = false;
  window.SoludeskScorm = {
    start: function () {
      if (!lms) { return; }
      if (lms.version === 2004) { lms.api.Initialize(""); lms.api.SetValue("cmi.completion_status", "incomplete"); }
      else { lms.api.LMSInitialize(""); lms.api.LMSSetValue("cmi.core.lesson_status", "incomplete"); }
    },
    complete: function () {
      if (!lms || done) { return; }
      done = true;
      if (lms.version === 2004) { lms.api.SetValue("cmi.completion_status", "completed"); lms.api.Commit(""); }
      else { lms.api.LMSSetValue("cmi.core.lesson_status", "completed"); lms.api.LMSCommit(""); }
    },
    finish: function () {
      if (!lms) { return; }
      if (lms.version === 2004) { lms.api.Terminate(""); } else { lms.api.LMSFinish(""); }
    }
  };
})();
"""

COMPLETE_AT_FRACTION = 0.9
"""A lesson counts as completed once 90% of its video has played."""


def _lesson_page(*, title: str, video: str, captions: str, transcript: str, disclosure: str) -> str:
    track = f'<track kind="captions" srclang="en" label="English" src="{html.escape(captions)}" default>' if captions else ""
    note = f'<p class="note">{html.escape(disclosure)}</p>' if disclosure else ""
    paragraphs = "".join(f"<p>{html.escape(part)}</p>" for part in transcript.split("\n\n") if part.strip())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;color:#0f172a}}
video{{width:100%;background:#000}}.note{{color:#475569;font-size:14px}}</style>
<script src="../shared/scorm.js"></script></head>
<body onload="SoludeskScorm.start()" onunload="SoludeskScorm.finish()">
<h1>{html.escape(title)}</h1>
<video controls preload="metadata" src="{html.escape(video)}" crossorigin="anonymous"
 ontimeupdate="if(this.duration&&this.currentTime/this.duration>={COMPLETE_AT_FRACTION}){{SoludeskScorm.complete()}}"
 onended="SoludeskScorm.complete()">{track}</video>
{note}
<details><summary>Transcript</summary>{paragraphs}</details>
</body></html>
"""


def _manifest(*, version: str, package: dict, lesson_files: dict) -> str:
    course = package["course"]
    if version == "1.2":
        head = (
            '<manifest identifier="soludesk-{id}" version="1.0" '
            'xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2" '
            'xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<metadata><schema>ADL SCORM</schema><schemaversion>1.2</schemaversion></metadata>"
        )
        sco = 'adlcp:scormtype="sco"'
        asset = 'adlcp:scormtype="asset"'
    else:
        head = (
            '<manifest identifier="soludesk-{id}" version="1" '
            'xmlns="http://www.imsglobal.org/xsd/imscp_v1p1" '
            'xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_v1p3" '
            'xmlns:adlseq="http://www.adlnet.org/xsd/adlseq_v1p3" '
            'xmlns:adlnav="http://www.adlnet.org/xsd/adlnav_v1p3" '
            'xmlns:imsss="http://www.imsglobal.org/xsd/imsss" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<metadata><schema>ADL SCORM</schema><schemaversion>2004 4th Edition</schemaversion></metadata>"
        )
        sco = 'adlcp:scormType="sco"'
        asset = 'adlcp:scormType="asset"'
    items, resources = [], []
    for module in package["modules"]:
        children = []
        for lesson in module["lessons"]:
            ident = f"m{module['order']}_l{lesson['order']}"
            children.append(
                f'<item identifier="i_{ident}" identifierref="r_{ident}"><title>{html.escape(lesson["title"])}</title></item>'
            )
            files = "".join(f'<file href="{html.escape(path)}"/>' for path in lesson_files[lesson["id"]])
            resources.append(
                f'<resource identifier="r_{ident}" type="webcontent" {sco} href="lessons/{ident}.html">'
                f'{files}<dependency identifierref="shared"/></resource>'
            )
        items.append(
            f'<item identifier="i_m{module["order"]}"><title>{html.escape(module["title"])}</title>{"".join(children)}</item>'
        )
    resources.append(f'<resource identifier="shared" type="webcontent" {asset}><file href="shared/scorm.js"/></resource>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + head.format(id=course["id"])
        + '<organizations default="org"><organization identifier="org">'
        + f"<title>{html.escape(course['title'])}</title>{''.join(items)}</organization></organizations>"
        + f"<resources>{''.join(resources)}</resources></manifest>"
    )


def _srt_to_vtt(text: str) -> str:
    import re

    return "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)


def _scorm(*, course: Course, package: dict, workdir: Path) -> list:
    """Both SCORM exports, with the course's own media inside them (links
    would expire). Reused while the course's media is unchanged."""

    lessons = {str(lesson.id): lesson for lesson in Lesson.objects.filter(module__course=course)}
    captions = _captions(course)
    media_inputs = [
        (lesson_id, lessons[lesson_id].video_url, lessons[lesson_id].embedded_link, lessons[lesson_id].video_script_file, captions.get(lessons[lesson_id].id, ""))
        for lesson_id in sorted(lessons)
    ]
    structure = [
        (module["order"], module["title"], [(item["id"], item["order"], item["title"], item["transcript"]) for item in module["lessons"]])
        for module in package["modules"]
    ]
    assets = []
    staged: dict | None = None
    for version, kind in (("1.2", AssetKind.SCORM_12), ("2004", AssetKind.SCORM_2004)):
        key = asset_store.input_hash(
            course.id, "scorm", version, SCORM_TEMPLATE_VERSION, structure, media_inputs, package["disclosure"]
        )
        cached = asset_store.find(key)
        if cached is not None:
            assets.append(cached)
            continue
        if staged is None:
            staged = _stage_media(lessons=lessons, captions=captions, workdir=workdir)
        archive = workdir / f"{key}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            lesson_files = {}
            for module in package["modules"]:
                for lesson in module["lessons"]:
                    ident = f"m{module['order']}_l{lesson['order']}"
                    local = staged[lesson["id"]]
                    files = [f"lessons/{ident}.html"]
                    video_src = local["video_url"]
                    if local["video_path"]:
                        bundle.write(local["video_path"], f"media/{ident}.mp4", compress_type=zipfile.ZIP_STORED)
                        files.append(f"media/{ident}.mp4")
                        video_src = f"../media/{ident}.mp4"
                    caption_src = ""
                    if local["vtt"]:
                        bundle.writestr(f"media/{ident}.vtt", local["vtt"])
                        files.append(f"media/{ident}.vtt")
                        caption_src = f"../media/{ident}.vtt"
                    bundle.writestr(
                        f"lessons/{ident}.html",
                        _lesson_page(
                            title=lesson["title"], video=video_src, captions=caption_src,
                            transcript=lesson["transcript"], disclosure=package["disclosure"]["statement"],
                        ),
                    )
                    lesson_files[lesson["id"]] = files
            bundle.writestr("shared/scorm.js", SCORM_JS)
            bundle.writestr("imsmanifest.xml", _manifest(version=version, package=package, lesson_files=lesson_files))
        file_key = asset_store.put(archive, course_id=course.id, name=key, content_type="application/zip")
        assets.append(
            asset_store.record(
                key=key, kind=kind, course=course, file_key=file_key, content_type="application/zip",
                path=archive, data={"scorm_version": version},
            )
        )
    return assets


def _stage_media(*, lessons: dict, captions: dict, workdir: Path) -> dict:
    """Each lesson's video file (ours, downloaded) or external link, and its
    WebVTT captions."""

    staged = {}
    for lesson_id, lesson in lessons.items():
        url = lesson.video_url or lesson.embedded_link
        video_path = None
        if _ours(url):
            video_path = asset_store.fetch(url, workdir / f"{lesson_id}.mp4")
        vtt = ""
        if captions.get(lesson.id) and _ours(captions[lesson.id]):
            vtt = asset_store.fetch(captions[lesson.id], workdir / f"{lesson_id}.vtt").read_text()
        elif lesson.video_script_file:
            vtt = _srt_to_vtt(asset_store.fetch(lesson.video_script_file, workdir / f"{lesson_id}.srt").read_text())
        staged[lesson_id] = {"video_path": video_path, "video_url": "" if video_path else url, "vtt": vtt}
    return staged


# --- channels -------------------------------------------------------------------------------


def _fail(distribution: CourseDistribution, reason: str) -> None:
    distribution.status = DistributionStatus.FAILED
    distribution.failure_reason = reason
    distribution.save(update_fields=["status", "failure_reason", "updated_datetime"])


def _dig(data, path: str):
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _push(*, distribution: CourseDistribution, mapping, payload: dict) -> None:
    names = PUSH_ENDPOINTS.get(distribution.channel)
    url = getattr(settings, names[0], "") if names else ""
    if not url:
        _fail(distribution, f"No API endpoint is configured for {distribution.get_channel_display()}"
              + (f" (set {names[0]})." if names else "."))
        return
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {getattr(settings, names[1], '')}",
                "Idempotency-Key": asset_store.input_hash(distribution.course_id, mapping.version, payload),
            },
            timeout=PUSH_TIMEOUT_SECONDS,
        )
    except httpx.TransportError as exc:
        raise AIProviderError(detail=f"{distribution.get_channel_display()} is unreachable: {exc}", retryable=True) from exc
    if response.status_code in RETRYABLE_STATUSES:
        raise AIProviderError(detail=f"{distribution.get_channel_display()} answered HTTP {response.status_code}.", retryable=True)
    if not response.is_success:
        _fail(distribution, f"{distribution.get_channel_display()} refused the course (HTTP {response.status_code}): {response.text[:400]}")
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    external_id = _dig(body, mapping.response_id_path) if mapping.response_id_path else None
    distribution.status = DistributionStatus.PUBLISHED
    distribution.external_course_id = str(external_id or "")[:255]
    distribution.failure_reason = ""
    distribution.published_at = timezone.now()
    distribution.save(update_fields=["status", "external_course_id", "failure_reason", "published_at", "updated_datetime"])


KIT_README = """# {channel} upload kit: {title}

This kit was built by the SoluDesk Production Engine from mapping version {version}.
Upload the course by hand, then record the {channel} course id in SoluDesk
(Production > Distributions > Mark published).

## Files

- `payload.json`: every field {channel} asks for, already shaped for it.
- `curriculum.csv`: sections and lectures in order, with their video and caption files.
- `captions/`: one `.srt` file per lecture.
- Video files: download them from the course's package page in SoluDesk
  (links there are signed fresh each time; the links in this kit last 7 days).

## AI disclosure (paste into the course description)

{disclosure}

## Checklist

- [ ] Title, subtitle and description pasted from `payload.json`
- [ ] Every lecture's video uploaded in curriculum order
- [ ] Every lecture's captions uploaded
- [ ] Course image and promotional video uploaded
- [ ] AI disclosure included in the description
- [ ] Price set as in `payload.json`
"""


def _kit(*, course: Course, distribution: CourseDistribution, mapping, payload: dict, package: dict, workdir: Path):
    key = asset_store.input_hash(
        course.id, "kit", distribution.channel, mapping.version, _strip_links(payload), _strip_links(package)
    )
    cached = asset_store.find(key)
    if cached is not None:
        return cached
    lessons = Lesson.objects.filter(module__course=course).only("id", "video_script_file")
    srt_keys = {str(lesson.id): lesson.video_script_file for lesson in lessons}
    archive = workdir / f"{key}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("payload.json", json.dumps(payload, indent=2, default=str))
        bundle.writestr(
            "README.md",
            KIT_README.format(
                channel=distribution.get_channel_display(), title=course.title, version=mapping.version,
                disclosure=package["disclosure"]["statement"] or "No AI disclosure is needed for this course.",
            ),
        )
        rows = io.StringIO()
        writer = csv.writer(rows)
        writer.writerow(["section", "section_title", "lecture", "lecture_title", "minutes", "video_link", "captions_file"])
        for module in package["modules"]:
            for lesson in module["lessons"]:
                caption_name = ""
                if srt_keys.get(lesson["id"]):
                    caption_name = f"captions/{module['order']:02d}-{lesson['order']:02d}.srt"
                    srt = asset_store.fetch(srt_keys[lesson["id"]], workdir / f"{lesson['id']}.srt")
                    bundle.write(srt, caption_name)
                writer.writerow([module["order"], module["title"], lesson["order"], lesson["title"], lesson["duration_minutes"], lesson["video_url"], caption_name])
        bundle.writestr("curriculum.csv", rows.getvalue())
    file_key = asset_store.put(archive, course_id=course.id, name=key, content_type="application/zip")
    return asset_store.record(
        key=key, kind=AssetKind.UPLOAD_KIT, course=course, file_key=file_key, content_type="application/zip",
        path=archive, data={"channel": distribution.channel, "mapping_version": mapping.version},
    )


def _strip_links(value):
    """The package without its signed links (they change on every build)."""

    if isinstance(value, dict):
        return {key: ("" if key.endswith("_url") else _strip_links(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_links(item) for item in value]
    return value


def _deliver_channel(*, course: Course, distribution: CourseDistribution, package: dict, workdir: Path) -> None:
    if distribution.status == DistributionStatus.PUBLISHED:
        return
    mapping = channel_mapping_service.active_mapping(distribution.channel)
    if mapping is None:
        _fail(distribution, f"No active mapping for {distribution.get_channel_display()}; add one under channel mappings.")
        return
    if distribution.channel == DistributionChannel.UDEMY and package["disclosure"]["ai_generated_content"]:
        _fail(distribution, UDEMY_POLICY_REASON)
        return
    payload, gaps = channel_mapping_service.apply(mapping, package_for_channel(package, distribution.channel))
    if gaps:
        _fail(distribution, f"Mapping v{mapping.version} found gaps: " + "; ".join(gaps))
        return
    if mapping.delivery_method == DeliveryMethod.API_PUSH:
        _push(distribution=distribution, mapping=mapping, payload=payload)
        return
    _kit(course=course, distribution=distribution, mapping=mapping, payload=payload, package=package, workdir=workdir)
    distribution.status = DistributionStatus.QUEUED
    distribution.failure_reason = ""
    distribution.save(update_fields=["status", "failure_reason", "updated_datetime"])


def package_course(*, ledger: RunLedger) -> None:
    """Build the package and the SCORM exports, then deliver to every
    channel the Approver chose. A channel already published is left alone,
    so a retried run only redoes what failed."""

    course = ledger.run.course
    started = timezone.now()
    package = build_package(course)
    with tempfile.TemporaryDirectory(prefix="pe-package-") as tmp:
        workdir = Path(tmp)
        key = asset_store.input_hash(course.id, "package", _strip_links(package))
        if asset_store.find(key) is None:
            path = workdir / "package.json"
            path.write_text(json.dumps(package, indent=2, default=str))
            file_key = asset_store.put(path, course_id=course.id, name=key, content_type="application/json")
            asset_store.record(key=key, kind=AssetKind.PACKAGE, course=course, file_key=file_key, content_type="application/json", path=path)
        _scorm(course=course, package=package, workdir=workdir)
        ledger.step(stage=PipelineStage.ASSEMBLY_PACKAGING, step="package", started_at=started)
        ledger.checkpoint()

        started = timezone.now()
        failed = []
        for distribution in CourseDistribution.objects.filter(course=course).order_by("channel"):
            _deliver_channel(course=course, distribution=distribution, package=package, workdir=workdir)
            if distribution.status == DistributionStatus.FAILED:
                failed.append(f"{distribution.get_channel_display()}: {distribution.failure_reason}")
        ledger.step(
            stage=PipelineStage.ASSEMBLY_PACKAGING, step="distribute", started_at=started,
            error="; ".join(failed),
        )
    if failed:
        alert(ledger.run, title="Distribution needs attention", content=" | ".join(failed))


# --- manual channels ---------------------------------------------------------------------------


def record_publication(*, distribution_id, external_course_id: str, actor) -> CourseDistribution:
    """A person uploaded the course to a kit channel (or pushed it by hand):
    mark that channel live with the channel's own course id. Idempotent for
    the same id. 404 for an unknown distribution, 409 when its course is not
    published."""

    from django.db import transaction
    from rest_framework import exceptions

    from api.authentication.services.activity_service import log_activity
    from api.courses.enums import CourseStatus
    from api.production.exceptions import ProductionRunConflict
    from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums

    with transaction.atomic():
        distribution = (
            CourseDistribution.objects.select_for_update(of=("self",))
            .select_related("course")
            .filter(pk=distribution_id)
            .first()
        )
        if distribution is None:
            raise exceptions.NotFound("Distribution not found.")
        if distribution.course.status != CourseStatus.PUBLISHED:
            raise ProductionRunConflict("Only a published course can be recorded as live on a channel.")
        if distribution.status == DistributionStatus.PUBLISHED and distribution.external_course_id == external_course_id:
            return distribution
        distribution.status = DistributionStatus.PUBLISHED
        distribution.external_course_id = external_course_id
        distribution.failure_reason = ""
        distribution.published_at = timezone.now()
        distribution.save(update_fields=["status", "external_course_id", "failure_reason", "published_at", "updated_datetime"])
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.PRODUCTION,
            action=UserActivityActionEnums.DISTRIBUTION_RECORDED,
            summary=f"You recorded '{distribution.course.title}' as live on {distribution.get_channel_display()}."[:255],
            details={"distribution_id": str(distribution.id), "external_course_id": external_course_id},
            target=distribution.course,
        )
    return distribution


# --- reading ----------------------------------------------------------------------------------


def package_downloads(*, course: Course) -> dict:
    """Fresh, short-lived links to the course's newest package files and to
    each lesson's media, for the admin package page."""

    newest = {}
    for asset in course.production_assets.filter(kind__in=PACKAGE_ASSET_KINDS).order_by("-created_datetime"):
        label = asset.kind if asset.kind != AssetKind.UPLOAD_KIT else f"{asset.kind}:{asset.data.get('channel', '')}"
        newest.setdefault(label, asset)
    files = [
        {
            "kind": asset.kind,
            "channel": asset.data.get("channel", ""),
            "size_bytes": asset.size_bytes,
            "built_at": asset.created_datetime,
            "url": StorageService.generate_presigned_get(asset.file_key) or "",
        }
        for asset in newest.values()
    ]
    captions = _captions(course)
    lessons = [
        {
            "lesson_id": lesson.id,
            "title": lesson.title,
            "video_url": _signed_now(lesson.video_url),
            "captions_vtt_url": _signed_now(captions.get(lesson.id, "")),
            "captions_srt_url": _signed_now(lesson.video_script_file),
        }
        for lesson in Lesson.objects.filter(module__course=course).order_by("module__order", "order")
    ]
    return {"files": files, "lessons": lessons}


def _signed_now(value: str) -> str:
    if not value:
        return ""
    if value.startswith(("http://", "https://")) and not _ours(value):
        return value
    return StorageService.generate_presigned_get(value) or ""
