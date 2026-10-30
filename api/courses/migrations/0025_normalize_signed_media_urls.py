from django.db import migrations

from shared.services.storage_service import StorageService

# Media fields that may hold a presigned URL a client saved from an upload
# response. Those expire after minutes; the durable form is signed afresh on
# every read instead.
MEDIA_FIELDS = {
    "Course": ("preview_video_url", "thumbnail_url"),
    "Lesson": ("video_url",),
    "LessonContentBlock": ("media_url",),
}


def normalize_signed_media_urls(apps, schema_editor):
    for model_name, fields in MEDIA_FIELDS.items():
        model = apps.get_model("courses", model_name)
        for field in fields:
            rows = model.objects.filter(**{f"{field}__contains": "X-Amz-"})
            for row in rows.only("pk", field).iterator():
                durable = StorageService.durable_media_ref(getattr(row, field))
                if durable != getattr(row, field):
                    model.objects.filter(pk=row.pk).update(**{field: durable})


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0024_video_processing_status"),
    ]

    operations = [
        migrations.RunPython(normalize_signed_media_urls, migrations.RunPython.noop),
    ]
