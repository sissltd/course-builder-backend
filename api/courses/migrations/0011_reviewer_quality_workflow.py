# Generated manually for the reviewer queue and QA-verification workflow.
import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0010_courseappeal"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="source",
            field=models.CharField(
                choices=[("CREATOR", "Creator"), ("AI", "AI")],
                default="CREATOR",
                help_text="Whether this course was submitted by a creator or AI pipeline.",
                max_length=10,
                verbose_name="Source",
            ),
        ),
        migrations.AlterField(
            model_name="course",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"), ("SUBMITTED", "Submitted"),
                    ("IN_REVIEW", "In Review"), ("QA_VERIFICATION", "QA Verification"),
                    ("APPROVED", "Approved"), ("PUBLISHED", "Published"),
                    ("REJECTED", "Rejected"),
                ],
                default="DRAFT", help_text="Current lifecycle status of the course.",
                max_length=15, verbose_name="Status",
            ),
        ),
        migrations.AlterField(
            model_name="course",
            name="creator",
            field=models.ForeignKey(blank=True, help_text="Course Creator who owns this course; blank for AI courses.", null=True, on_delete=django.db.models.deletion.CASCADE, related_name="courses", to=settings.AUTH_USER_MODEL, verbose_name="Creator"),
        ),
        migrations.AddField(
            model_name="reviewaction",
            name="stage",
            field=models.CharField(
                choices=[("CONTENT", "Content Review"), ("QA", "QA Verification")],
                default="CONTENT",
                help_text="Quality gate at which this decision was made.",
                max_length=10,
                verbose_name="Review Stage",
            ),
        ),
        migrations.CreateModel(
            name="QualityCheckRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_datetime", models.DateTimeField(auto_now_add=True, help_text="datetime of object creation", verbose_name="Created datetime")),
                ("updated_datetime", models.DateTimeField(auto_now=True, help_text="datetime of object update", verbose_name="Updated datetime")),
                ("provider", models.CharField(default="SCCS", max_length=100)),
                ("overall_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("risk_level", models.CharField(choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")], default="MEDIUM", max_length=10)),
                ("status", models.CharField(choices=[("NOT_RUN", "Not Run"), ("PASS", "Pass"), ("WARNING", "Warning"), ("FAIL", "Fail")], default="NOT_RUN", max_length=10)),
                ("plagiarism_status", models.CharField(choices=[("NOT_RUN", "Not Run"), ("PASS", "Pass"), ("WARNING", "Warning"), ("FAIL", "Fail")], default="NOT_RUN", max_length=10)),
                ("plagiarism_score", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("duplicate_status", models.CharField(choices=[("NOT_RUN", "Not Run"), ("PASS", "Pass"), ("WARNING", "Warning"), ("FAIL", "Fail")], default="NOT_RUN", max_length=10)),
                ("duplicate_score", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("raw_report", models.JSONField(blank=True, default=dict)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quality_check_runs", to="courses.course")),
            ],
            options={"ordering": ["-created_datetime"]},
        ),
        migrations.CreateModel(
            name="ReviewAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_datetime", models.DateTimeField(auto_now_add=True, help_text="datetime of object creation", verbose_name="Created datetime")),
                ("updated_datetime", models.DateTimeField(auto_now=True, help_text="datetime of object update", verbose_name="Updated datetime")),
                ("stage", models.CharField(choices=[("CONTENT", "Content Review"), ("QA", "QA Verification")], max_length=10)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_assignments", to="courses.course")),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="course_review_assignments", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ReviewComment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_datetime", models.DateTimeField(auto_now_add=True, help_text="datetime of object creation", verbose_name="Created datetime")),
                ("updated_datetime", models.DateTimeField(auto_now=True, help_text="datetime of object update", verbose_name="Updated datetime")),
                ("stage", models.CharField(choices=[("CONTENT", "Content Review"), ("QA", "QA Verification")], max_length=10)),
                ("severity", models.CharField(choices=[("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")], default="INFO", max_length=10)),
                ("reason_code", models.CharField(blank=True, default="", max_length=80)),
                ("comment", models.TextField()),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_comments", to="courses.course")),
                ("lesson", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="review_comments", to="courses.lesson")),
                ("module", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="review_comments", to="courses.module")),
                ("reviewer", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="course_review_comments", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MediaAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_datetime", models.DateTimeField(auto_now_add=True, help_text="datetime of object creation", verbose_name="Created datetime")),
                ("updated_datetime", models.DateTimeField(auto_now=True, help_text="datetime of object update", verbose_name="Updated datetime")),
                ("kind", models.CharField(choices=[("VIDEO", "Video"), ("AUDIO", "Audio"), ("SUBTITLE", "Subtitle"), ("THUMBNAIL", "Thumbnail"), ("PREVIEW_VIDEO", "Preview Video")], max_length=20)),
                ("url", models.URLField()),
                ("mime_type", models.CharField(blank=True, default="", max_length=100)),
                ("duration_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("resolution", models.CharField(blank=True, default="", max_length=20)),
                ("subtitle_url", models.URLField(blank=True, default="")),
                ("caption_accuracy_percent", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("audio_lufs", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("audio_video_drift_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("accessibility", models.JSONField(blank=True, default=dict)),
                ("verification", models.JSONField(blank=True, default=dict)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="media_assets", to="courses.course")),
                ("lesson", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="media_assets", to="courses.lesson")),
                ("verified_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="verified_media_assets", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="QualityFinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_datetime", models.DateTimeField(auto_now_add=True, help_text="datetime of object creation", verbose_name="Created datetime")),
                ("updated_datetime", models.DateTimeField(auto_now=True, help_text="datetime of object update", verbose_name="Updated datetime")),
                ("code", models.CharField(max_length=80)),
                ("severity", models.CharField(choices=[("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")], max_length=10)),
                ("message", models.TextField()),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("check_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="findings", to="courses.qualitycheckrun")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quality_findings", to="courses.course")),
                ("lesson", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="quality_findings", to="courses.lesson")),
                ("module", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="quality_findings", to="courses.module")),
            ],
        ),
        migrations.AddConstraint(model_name="reviewassignment", constraint=models.UniqueConstraint(fields=("course", "stage"), name="unique_course_review_stage")),
        migrations.AddIndex(model_name="reviewassignment", index=models.Index(fields=["stage", "reviewer"], name="reviewassign_stage_reviewer_ix")),
        migrations.AddIndex(model_name="mediaasset", index=models.Index(fields=["course", "kind"], name="media_asset_course_kind_idx")),
        migrations.AddIndex(model_name="qualityfinding", index=models.Index(fields=["course", "severity"], name="quality_finding_course_sev_idx")),
    ]
