"""Version 1 of each channel's mapping.

SoluDesk's is a draft shaped like the canonical package, to be replaced by
a new version once SoluDesk's own course schema is shared. Udemy's encodes
its published publishing minimums (title length, five lectures, thirty
minutes of video, course image and promo video) and goes out as an upload
kit, as does Coursera's: neither offers a course-creation API.
"""

from django.db import migrations

LESSON_MAP = {
    "position": {"from": "order"},
    "title": {"from": "title"},
    "duration_minutes": {"from": "duration_minutes"},
    "video_url": {"from": "video_url"},
    "captions_url": {"from": "captions_vtt_url"},
    "transcript": {"from": "transcript"},
    "quiz": {"from": "quiz"},
}

SOLUDESK = {
    "channel": "SOLUDESK",
    "delivery_method": "API_PUSH",
    "response_id_path": "id",
    "notes": "Draft until SoluDesk shares its course schema; add a new version then.",
    "field_map": {
        "external_id": {"from": "course.id"},
        "slug": {"from": "course.slug"},
        "title": {"from": "course.title"},
        "description": {"from": "course.description"},
        "category": {"from": "course.category"},
        "topic": {"from": "course.topic"},
        "level": {"from": "course.level", "transform": ["lower"]},
        "language": {"from": "course.language"},
        "duration_minutes": {"from": "course.duration_minutes"},
        "learning_objectives": {"from": "course.learning_objectives"},
        "thumbnail_url": {"from": "course.thumbnail_url"},
        "trailer_url": {"from": "course.trailer_url"},
        "price.amount": {"from": "channel.price", "transform": ["number"]},
        "price.promotional_amount": {"from": "channel.promotional_price", "transform": ["number"]},
        "price.model": {"from": "channel.pricing_model"},
        "ai_disclosure": {"from": "disclosure"},
        "modules": {
            "each": "modules",
            "map": {
                "position": {"from": "order"},
                "title": {"from": "title"},
                "description": {"from": "description"},
                "quiz": {"from": "quiz"},
                "lessons": {"each": "lessons", "map": LESSON_MAP},
            },
        },
        "final_quiz": {"from": "final_quiz"},
    },
    "target_schema": {
        "type": "object",
        "required": ["external_id", "title", "description", "modules", "price"],
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "price": {
                "type": "object",
                "required": ["amount"],
                "properties": {"amount": {"type": "number", "minimum": 0}},
            },
            "modules": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["title", "lessons"],
                    "properties": {
                        "lessons": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "required": ["title", "video_url"],
                                "properties": {"video_url": {"type": "string", "minLength": 1}},
                            },
                        }
                    },
                },
            },
        },
    },
}

UDEMY = {
    "channel": "UDEMY",
    "delivery_method": "UPLOAD_KIT",
    "response_id_path": "",
    "notes": "Udemy has no course-creation API: an upload kit. Crawler (fully AI) courses are refused by policy before this mapping runs.",
    "field_map": {
        "title": {"from": "course.title", "transform": ["truncate:60"]},
        "subtitle": {"from": "course.description", "transform": ["first_sentence", "truncate:120"]},
        "description": {"from": "course.description"},
        "ai_disclosure": {"from": "disclosure.statement"},
        "language": {"const": "English"},
        "level": {"from": "course.level", "transform": ["lower"]},
        "category": {"from": "course.category"},
        "what_you_will_learn": {"from": "course.learning_objectives"},
        "price": {"from": "channel.price", "transform": ["number"]},
        "course_image_url": {"from": "course.thumbnail_url"},
        "promo_video_url": {"from": "course.trailer_url"},
        "lecture_count": {"from": "course.lesson_count"},
        "total_video_minutes": {"from": "course.duration_minutes"},
        "sections": {
            "each": "modules",
            "map": {
                "title": {"from": "title", "transform": ["truncate:80"]},
                "lectures": {
                    "each": "lessons",
                    "map": {
                        "title": {"from": "title", "transform": ["truncate:80"]},
                        "video_url": {"from": "video_url"},
                        "captions_url": {"from": "captions_srt_url"},
                        "minutes": {"from": "duration_minutes"},
                    },
                },
            },
        },
    },
    "target_schema": {
        "type": "object",
        "required": ["title", "subtitle", "description", "price", "course_image_url", "promo_video_url", "sections"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 60},
            "subtitle": {"type": "string", "minLength": 1, "maxLength": 120},
            "description": {"type": "string", "minLength": 200},
            "what_you_will_learn": {"type": "array", "minItems": 4},
            "price": {"type": "number", "minimum": 0},
            "course_image_url": {"type": "string", "minLength": 1},
            "promo_video_url": {"type": "string", "minLength": 1},
            "lecture_count": {"type": "integer", "minimum": 5},
            "total_video_minutes": {"type": "integer", "minimum": 30},
            "sections": {"type": "array", "minItems": 1},
        },
    },
}

COURSERA = {
    "channel": "COURSERA",
    "delivery_method": "UPLOAD_KIT",
    "response_id_path": "",
    "notes": "Coursera publishes through partners only: an upload kit for the partner submission.",
    "field_map": {
        "title": {"from": "course.title"},
        "description": {"from": "course.description"},
        "ai_disclosure": {"from": "disclosure.statement"},
        "level": {"from": "course.level", "transform": ["lower"]},
        "language": {"from": "course.language"},
        "learning_objectives": {"from": "course.learning_objectives"},
        "modules": {
            "each": "modules",
            "map": {"title": {"from": "title"}, "lessons": {"each": "lessons", "map": LESSON_MAP}},
        },
    },
    "target_schema": {
        "type": "object",
        "required": ["title", "description", "modules"],
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "modules": {"type": "array", "minItems": 1},
        },
    },
}


def seed(apps, schema_editor):
    ChannelMapping = apps.get_model("production", "ChannelMapping")
    for definition in (SOLUDESK, UDEMY, COURSERA):
        ChannelMapping.objects.get_or_create(
            channel=definition["channel"],
            version=1,
            defaults={**definition, "is_active": not ChannelMapping.objects.filter(channel=definition["channel"], is_active=True).exists()},
        )


def unseed(apps, schema_editor):
    ChannelMapping = apps.get_model("production", "ChannelMapping")
    ChannelMapping.objects.filter(version=1, channel__in=["SOLUDESK", "UDEMY", "COURSERA"], created_by__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("production", "0002_assets_mappings_rework")]

    operations = [migrations.RunPython(seed, unseed)]
