"""Public catalogue shapes: only what anyone may see of a published course."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.models import Course, CourseDistribution, Lesson, Module
from api.courses.services import catalogue_service
from api.production.services.packaging_service import disclosure_for


class PublicDisclosureSerializer(serializers.Serializer):
    ai_narration = serializers.BooleanField(help_text="The narration is an AI-generated (synthetic) voice.")
    ai_generated_content = serializers.BooleanField(help_text="The course content was generated with AI.")
    statement = serializers.CharField(help_text="The disclosure to show learners; empty when none is needed.")


class PublicChannelSerializer(serializers.ModelSerializer):
    channel = serializers.CharField(help_text="Where the course is live: SOLUDESK, UDEMY or COURSERA.")
    channel_label = serializers.CharField(source="get_channel_display", help_text="The channel's display name.")
    price = serializers.DecimalField(source="learner_price", max_digits=12, decimal_places=2, help_text="Learner price on this channel.")
    promotional_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True, help_text="Promotional price, when one is set.")
    pricing_model = serializers.CharField(help_text="ONE_TIME or SUBSCRIPTION.")
    external_course_id = serializers.CharField(help_text="The channel's own id for the course, when it gave one.")
    published_at = serializers.DateTimeField(help_text="When the course went live on this channel.")

    class Meta:
        model = CourseDistribution
        fields = ["channel", "channel_label", "price", "promotional_price", "pricing_model", "external_course_id", "published_at"]


class PublicCourseSerializer(serializers.ModelSerializer):
    """One row of the catalogue."""

    category = serializers.CharField(source="category.name", help_text="Category name.")
    category_slug = serializers.CharField(source="category.slug", help_text="Category slug, for the category filter.")
    topic = serializers.CharField(source="topic.name", default="", help_text="Topic name, when the course has one.")
    level = serializers.CharField(source="difficulty_level", help_text="BEGINNER, INTERMEDIATE or ADVANCED.")
    duration_minutes = serializers.IntegerField(source="duration_estimate_minutes", help_text="Total runtime in minutes.")
    thumbnail_url = serializers.SerializerMethodField(help_text="Course image; a signed link valid for an hour.")
    disclosure = serializers.SerializerMethodField(help_text="How the course was made.")
    channels = serializers.SerializerMethodField(help_text="Where the course is live, with its price there.")

    class Meta:
        model = Course
        fields = [
            "slug", "title", "description", "category", "category_slug", "topic", "level",
            "duration_minutes", "thumbnail_url", "disclosure", "channels", "published_at",
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, obj) -> str:
        return catalogue_service.public_media(obj.thumbnail_url)

    @extend_schema_field(PublicDisclosureSerializer)
    def get_disclosure(self, obj):
        return disclosure_for(obj)

    @extend_schema_field(PublicChannelSerializer(many=True))
    def get_channels(self, obj):
        return PublicChannelSerializer(obj.live_channels, many=True).data


class PublicLessonSerializer(serializers.ModelSerializer):
    title = serializers.CharField(help_text="Lesson title.")
    duration_minutes = serializers.IntegerField(help_text="Lesson runtime in minutes.")

    class Meta:
        model = Lesson
        fields = ["title", "order", "duration_minutes", "content_type"]
        read_only_fields = fields


class PublicModuleSerializer(serializers.ModelSerializer):
    lessons = PublicLessonSerializer(many=True, help_text="Lesson titles, in order (the outline only).")

    class Meta:
        model = Module
        fields = ["title", "order", "lessons"]
        read_only_fields = fields


class PublicCourseDetailSerializer(PublicCourseSerializer):
    """The course page: the row plus objectives, trailer, outline and JSON-LD."""

    learning_objectives = serializers.ListField(child=serializers.CharField(), help_text="What a learner will be able to do.")
    trailer_url = serializers.SerializerMethodField(help_text="The 60-120 s trailer; a signed link valid for an hour.")
    outline = PublicModuleSerializer(source="modules", many=True, help_text="Modules and lesson titles, in order.")
    json_ld = serializers.SerializerMethodField(help_text="schema.org Course markup for the page's <head>.")

    class Meta(PublicCourseSerializer.Meta):
        fields = PublicCourseSerializer.Meta.fields + ["learning_objectives", "trailer_url", "outline", "json_ld"]
        read_only_fields = fields

    def get_trailer_url(self, obj) -> str:
        return catalogue_service.public_media(obj.preview_video_url)

    @extend_schema_field(serializers.DictField())
    def get_json_ld(self, obj):
        return catalogue_service.json_ld(obj)
