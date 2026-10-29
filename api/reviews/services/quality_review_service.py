"""Persisted baseline quality checks used by the reviewer workspace.

External plagiarism and duplicate scanners can add their own QualityCheckRun;
until then their status intentionally remains NOT_RUN rather than pretending a
course has been scanned.
"""

from api.reviews.enums import FindingSeverity, QualityCheckStatus, QualityRiskLevel
from api.courses.models import Course
from api.reviews.models import QualityCheckRun, QualityFinding
from api.reviews.services import quality_check_service


def run_baseline_checks(*, course: Course) -> QualityCheckRun:
    failures = quality_check_service.validate_structural_standards(course)
    errors = len(failures)
    score = max(0, 100 - errors * 10)
    risk = (
        QualityRiskLevel.CRITICAL
        if errors >= 5
        else QualityRiskLevel.HIGH
        if errors >= 3
        else QualityRiskLevel.MEDIUM
        if errors
        else QualityRiskLevel.LOW
    )
    run = QualityCheckRun.objects.create(
        course=course,
        provider="SCCS_BASELINE",
        overall_score=score,
        risk_level=risk,
        status=QualityCheckStatus.FAIL if errors else QualityCheckStatus.PASS,
        raw_report={"structural_failure_count": errors},
    )
    QualityFinding.objects.bulk_create(
        [
            QualityFinding(
                course=course,
                check_run=run,
                code="STRUCTURAL_STANDARD",
                severity=FindingSeverity.ERROR,
                message=message,
            )
            for message in failures
        ]
    )
    return run


def required_media_failures(*, course: Course) -> list[str]:
    """Return final-QA blockers based on the required media inventory."""

    assets = list(course.media_assets.all())
    failures: list[str] = []
    preview = [asset for asset in assets if asset.kind == "PREVIEW_VIDEO"]
    thumbnail = [asset for asset in assets if asset.kind == "THUMBNAIL"]
    if not preview:
        failures.append("A preview-video media asset is required.")
    if not thumbnail:
        failures.append("A thumbnail media asset is required.")

    lesson_ids = set(course.modules.values_list("lessons__id", flat=True)) - {None}
    video_lesson_ids = {asset.lesson_id for asset in assets if asset.kind == "VIDEO"}
    for lesson_id in lesson_ids - video_lesson_ids:
        failures.append(f"Lesson {lesson_id} is missing a video media asset.")

    for asset in assets:
        if asset.kind not in {"VIDEO", "PREVIEW_VIDEO"}:
            continue
        missing = []
        if not asset.mime_type:
            missing.append("MIME type")
        if asset.duration_seconds is None:
            missing.append("duration")
        if not asset.resolution:
            missing.append("resolution")
        if not asset.subtitle_url:
            missing.append("subtitle URL")
        if asset.caption_accuracy_percent is None:
            missing.append("caption accuracy")
        if asset.audio_lufs is None:
            missing.append("audio level")
        if asset.audio_video_drift_ms is None:
            missing.append("audio/video drift")
        if not asset.accessibility:
            missing.append("accessibility metadata")
        if missing:
            failures.append(f"Media asset {asset.id} is missing: {', '.join(missing)}.")
    return failures
