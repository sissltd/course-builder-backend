from django.core.management.base import BaseCommand

from api.courses.models import Course, Lesson, LessonContentBlock
from api.courses.services import video_optimization_service


class Command(BaseCommand):
    help = (
        "Queue streaming optimisation for creator videos uploaded before it "
        "existed: every lesson and course preview video of ours that has no "
        "processing status yet, plus every video content block. Safe to run "
        "again; an already optimised file is only checked."
    )

    def handle(self, *args, **options):
        targets = [
            (
                Lesson.objects.exclude(video_url="").filter(video_processing_status=""),
                "video_url",
            ),
            (
                Course.objects.exclude(preview_video_url="").filter(
                    preview_video_processing_status=""
                ),
                "preview_video_url",
            ),
            (
                LessonContentBlock.objects.filter(block_type="VIDEO").exclude(
                    media_url=""
                ),
                "media_url",
            ),
        ]
        for queryset, field in targets:
            for instance in queryset.iterator():
                video_optimization_service.queue_if_changed(
                    instance=instance, field=field, previous=""
                )
        self.stdout.write(self.style.SUCCESS("Queued creator video optimisation."))
