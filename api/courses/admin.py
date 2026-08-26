from django.contrib import admin

from api.courses.models import (
    Assessment,
    Course,
    CourseDistribution,
    CourseAppeal,
    CourseThumbnail,
    CourseVersion,
    Lesson,
    LessonContentBlock,
    LessonImage,
    LessonRequirement,
    Module,
    PublishedCourseSnapshot,
)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "creator", "category", "status", "created_datetime")
    list_filter = ("status", "category")
    search_fields = ("title", "creator__email")


@admin.register(CourseDistribution)
class CourseDistributionAdmin(admin.ModelAdmin):
    list_display = ("course", "channel", "learner_price", "pricing_model", "status")
    list_filter = ("channel", "pricing_model", "status")
    search_fields = ("course__title", "external_course_id")


@admin.register(CourseVersion)
class CourseVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "is_active", "created_datetime")
    list_filter = ("is_active",)
    search_fields = ("label",)


@admin.register(PublishedCourseSnapshot)
class PublishedCourseSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "version", "published_at")
    search_fields = ("course__title",)


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "course", "order", "locked_by", "lock_expires_at")
    list_filter = ("course",)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "module", "order", "duration_minutes")


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "level", "lesson", "module", "course")
    list_filter = ("level",)


@admin.register(CourseAppeal)
class CourseAppealAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "course",
        "submitted_by",
        "status",
        "created_datetime",
    )
    list_filter = ("status",)
    search_fields = ("course__title", "submitted_by__email", "title")


@admin.register(LessonContentBlock)
class LessonContentBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "lesson", "block_type", "order")
    list_filter = ("block_type",)


@admin.register(LessonImage)
class LessonImageAdmin(admin.ModelAdmin):
    list_display = ("id", "lesson", "source_type", "order")


@admin.register(LessonRequirement)
class LessonRequirementAdmin(admin.ModelAdmin):
    list_display = ("id", "lesson", "order")


@admin.register(CourseThumbnail)
class CourseThumbnailAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "source", "media_type", "is_active")
    list_filter = ("source", "media_type", "is_active")
