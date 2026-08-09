import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# RunSQL rather than RunPython: RunPython forces Django to render full
# historical ProjectState at this point, which trips over an unrelated
# pre-existing lazy FK (onboarding.CreatorProfile.primary_expertise_category
# -> courses.category, stale since Category moved to the categories app in
# 0002_move_category_to_categories_app). RunSQL executes directly and skips
# that state build entirely.
_BACKFILL_SQL = """
    UPDATE courses_topicreservationrequest AS request
    SET name = topic.name, category_id = topic.category_id
    FROM courses_topic AS topic
    WHERE request.topic_id = topic.id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0007_move_coursecollaborator_to_collaborators_app"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="topicreservationrequest",
            name="name",
            field=models.CharField(
                default="",
                help_text="Proposed name for the new topic.",
                max_length=255,
                verbose_name="Name",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="topicreservationrequest",
            name="category",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="topic_reservation_requests",
                to="categories.category",
                help_text="Category the proposed topic would belong to.",
                verbose_name="Category",
            ),
        ),
        migrations.AddField(
            model_name="topicreservationrequest",
            name="rejection_reason",
            field=models.TextField(
                blank=True,
                null=True,
                help_text="Why the reviewer rejected this request, e.g. a duplicate name.",
                verbose_name="Rejection Reason",
            ),
        ),
        migrations.AlterField(
            model_name="topicreservationrequest",
            name="topic",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reservation_requests",
                to="courses.topic",
                help_text="The Topic created once this request is approved.",
                verbose_name="Topic",
            ),
        ),
        migrations.RunSQL(sql=_BACKFILL_SQL, reverse_sql=migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="topicreservationrequest",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="topic_reservation_requests",
                to="categories.category",
                help_text="Category the proposed topic would belong to.",
                verbose_name="Category",
            ),
        ),
        migrations.AlterField(
            model_name="topicreservationrequest",
            name="requested_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="topic_reservation_requests",
                to=settings.AUTH_USER_MODEL,
                help_text="Creator who requested this topic.",
                verbose_name="Requested By",
            ),
        ),
    ]
