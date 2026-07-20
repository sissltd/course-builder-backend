from django.contrib import admin

from api.courses.models import (
    Assessment,
    Category,
    CategoryRequest,
    Course,
    CourseCollaborator,
    Lesson,
    Module,
    ReviewAction,
    Topic,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "creator_price",
        "track_preference",
        "status",
        "created_datetime",
    )
    list_filter = ("status", "track_preference")
    search_fields = ("name",)


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "category", "creator_price", "status", "created_datetime")
    list_filter = ("status", "category")
    search_fields = ("name",)


@admin.register(CategoryRequest)
class CategoryRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "requested_by",
        "status",
        "resulting_category",
        "created_datetime",
    )
    list_filter = ("status",)
    search_fields = ("name", "requested_by__email")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "creator", "category", "status", "created_datetime")
    list_filter = ("status", "category")
    search_fields = ("title", "creator__email")


@admin.register(CourseCollaborator)
class CourseCollaboratorAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "user", "role", "created_datetime")
    list_filter = ("role",)


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "course", "order")
    list_filter = ("course",)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "module", "order", "duration_minutes")


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "level", "lesson", "module", "course")
    list_filter = ("level",)


@admin.register(ReviewAction)
class ReviewActionAdmin(admin.ModelAdmin):
    list_display = ("id", "course", "reviewer", "action", "created_datetime")
    list_filter = ("action",)
