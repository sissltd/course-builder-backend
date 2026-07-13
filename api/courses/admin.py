from django.contrib import admin

from api.courses.models import Assessment, Category, Course, Lesson, Module, ReviewAction


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "creator_price", "track_preference", "status", "created_datetime")
    list_filter = ("status", "track_preference")
    search_fields = ("name",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "creator", "category", "status", "created_datetime")
    list_filter = ("status", "category")
    search_fields = ("title", "creator__email")


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
