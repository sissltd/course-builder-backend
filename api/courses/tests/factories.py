"""Plain-function test object builders (no factory_boy dependency).

Centralizes construction of Users/Categories/Courses so structural-standard
thresholds (module/lesson counts, word counts, quiz counts) are derived once
here rather than re-guessed in every test file.
"""

from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model
from django.utils import timezone

from api.catalog.enums import CategoryStatus, TrackPreference
from api.catalog.models import Category, Topic, TopicReservationRequest
from api.courses.enums import AssessmentLevel
from api.courses.models import (
    Assessment,
    Course,
    CourseAppeal,
    Lesson,
    Module,
)
from api.users.enums import UserRole

User = get_user_model()

_sequence = count(1)


def make_user(role=UserRole.COURSE_CREATOR, **kwargs):
    n = next(_sequence)
    defaults = {
        "email": f"user{n}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "role": role,
    }
    defaults.update(kwargs)
    return User.objects.create_user(password="testpass123", **defaults)


def make_category(**kwargs):
    n = next(_sequence)
    defaults = {
        "name": f"Category {n}",
        "description": "A test category.",
        "creator_price": Decimal("100.00"),
        "track_preference": TrackPreference.OPEN,
        "status": CategoryStatus.ACTIVE,
    }
    defaults.update(kwargs)
    return Category.objects.create(**defaults)


def make_topic(*, category=None, **kwargs):
    n = next(_sequence)
    category = category or make_category()
    defaults = {
        "category": category,
        "name": f"Topic {n}",
        "creator_price": Decimal("25.00"),
        "status": CategoryStatus.ACTIVE,
    }
    defaults.update(kwargs)
    return Topic.objects.create(**defaults)


def make_topic_reservation_request(*, requested_by=None, category=None, **kwargs):
    n = next(_sequence)
    defaults = {
        "requested_by": requested_by or make_user(),
        "name": f"Requested Topic {n}",
        "category": category or make_category(),
    }
    defaults.update(kwargs)
    return TopicReservationRequest.objects.create(**defaults)


def make_rejected_course(*, creator=None, category=None, **kwargs):
    defaults = {"rejected_at": timezone.now()}
    defaults.update(kwargs)
    return make_draft_course(creator=creator, category=category, **defaults)


def make_course_appeal(*, course=None, submitted_by=None, **kwargs):
    n = next(_sequence)
    course = course or make_rejected_course()
    defaults = {
        "course": course,
        "submitted_by": submitted_by or course.creator,
        "title": f"Appeal {n}",
        "email": "creator@example.com",
        "description": "word " * 30,
    }
    defaults.update(kwargs)
    return CourseAppeal.objects.create(**defaults)


def make_draft_course(*, creator=None, category=None, **kwargs):
    creator = creator or make_user()
    category = category or make_category()
    defaults = {
        "creator": creator,
        "category": category,
        "title": "Test Course",
        "description": "word "
        * 150,  # 150 words: within COURSE_DESCRIPTION_WORD_MIN/MAX (100-500)
        "preview_video_url": "https://example.com/preview.mp4",
        "terms_accepted_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Course.objects.create(**defaults)


def make_questions(count_):
    return [
        {
            "type": "MULTIPLE_CHOICE",
            "question": f"Question {i}?",
            "points": 10,
            "options": [
                {"text": "A", "explanation": "Why A is right or wrong."},
                {"text": "B", "explanation": "Why B is right or wrong."},
                {"text": "C", "explanation": "Why C is right or wrong."},
                {"text": "D", "explanation": "Why D is right or wrong."},
            ],
            "correct_index": 0,
        }
        for i in range(count_)
    ]


def build_compliant_course(
    *, creator=None, category=None, module_count=4, lessons_per_module=3
):
    """Build a Course whose module/lesson/assessment tree passes
    course_validation_service.validate_structural_standards, so tests that
    need a submittable course don't have to re-derive PRD thresholds."""

    course = make_draft_course(creator=creator, category=category)
    script = "word " * 600  # 600 words: within LESSON_SCRIPT_WORD_MIN/MAX (500-1500)

    for m in range(module_count):
        module = Module.objects.create(course=course, title=f"Module {m}", order=m)
        Assessment.objects.create(
            level=AssessmentLevel.MODULE,
            module=module,
            title=f"Module {m} Quiz",
            questions=make_questions(3),
        )
        for lesson_index in range(lessons_per_module):
            lesson = Lesson.objects.create(
                module=module,
                title=f"Lesson {m}-{lesson_index}",
                order=lesson_index,
                script=script,
                learning_objectives=["Objective 1", "Objective 2"],
                duration_minutes=20,
            )
            Assessment.objects.create(
                level=AssessmentLevel.LESSON,
                lesson=lesson,
                title=f"Lesson {m}-{lesson_index} Quiz",
                questions=make_questions(3),
            )

    Assessment.objects.create(
        level=AssessmentLevel.COURSE,
        course=course,
        title="Final Exam",
        questions=make_questions(15),
    )
    return course
