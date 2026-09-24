"""Seed demo accounts and DRAFT courses for the manual submit/review flow.

Creates (idempotently - every step is get_or_create based and safe to re-run):

* Two Course Creators and one Creator Reviewer account.
* One DRAFT course owned by the first creator.
* One DRAFT course owned by the first creator with the second creator added
  as a CourseCollaborator (SCCS PRD Section 14 collaboration).
* A ReviewerAvailability row for the reviewer so they can claim seats.

Nothing is submitted: the courses stay in DRAFT so the creator can submit
them manually via POST /api/v1/courses/{id}/submit/ and the reviewer can
then pick them up from GET /api/v1/review-queue/pending/.

Content is built to pass quality_check_service.validate_structural_standards
(module/lesson counts, word counts, objectives, assessments, duration) so the
manual submission does not bounce off the structural gate.

Usage:
    docker exec course-builder-backend-api-1 python manage.py seed_review_demo
"""

from datetime import timedelta
from typing import TypedDict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from api.catalog.enums import CategoryStatus
from api.catalog.models import Category, Topic
from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.courses.enums import AssessmentLevel, DifficultyLevel
from api.courses.models import (
    Assessment,
    Course,
    CourseVersion,
    Lesson,
    Module,
)
from api.courses.services import course_service
from api.users.enums import AccountStatus, QueueTrackFilter, UserRole
from api.users.models.reviewer_availability import ReviewerAvailability

User = get_user_model()

CREATOR_1_EMAIL = "amara.creator@demo.test"
CREATOR_2_EMAIL = "tunde.writer@demo.test"
REVIEWER_EMAIL = "ruby.reviewer@demo.test"
DEMO_PASSWORD = "DemoPass123!"

CATEGORY_NAME = "Web Development"
TOPIC_NAME = "React Development"

COURSE_1_TITLE = "Django for Beginners"
COURSE_2_TITLE = "React Development Bootcamp"

MODULE_COUNT = 4
LESSONS_PER_MODULE = 3
LESSON_MINUTES = 25
SCRIPT_WORDS = 600  # PRD lesson script band is 500-1500 words.

#: Facts the command prints once it is done, so the caller can hand them to
#: the human who will submit the courses and review them.
class DemoAccount(TypedDict):
    email: str
    role: str
    first_name: str
    last_name: str


def _description_at_least(base: str, min_words: int = 130) -> str:
    """Pad a course description past the submission gate's word minimum.

    The structural standards require course_description_word_min words
    (100 on current settings). Padding is appended, never truncating, so
    the result always passes regardless of which settings row is live.
    """

    closers = (
        "Every module closes with a quiz that checks what you just learned.",
        "Worked examples and exercises run throughout, so you practise as you go.",
        "Short recaps keep each concept anchored before the next one builds on it.",
        "The final exam consolidates all modules into one comprehensive assessment.",
        "Follow-up resources are listed at the end of every lesson script.",
    )
    words = base.split()
    index = 0
    while len(words) < min_words:
        words.extend(closers[index % len(closers)].split())
        index += 1
    return " ".join(words)


def _filler_words(topic_word: str, count: int) -> str:
    """Return `count` words of plausible lesson narration text."""

    sentences = (
        f"In this segment we work through the core ideas of {topic_word} step by step.",
        f"Each concept is introduced with a short explanation and a worked example.",
        f"Pay attention to how the pieces fit together before moving on.",
        f"Try the exercise yourself before reading the solution that follows.",
        f"By the end of this lesson you should be able to apply the pattern unaided.",
        f"Common mistakes are highlighted so you can avoid them in your own work.",
    )
    words: list[str] = []
    index = 0
    while len(words) < count:
        words.extend(sentences[index % len(sentences)].split())
        index += 1
    return " ".join(words[:count])


def _make_questions(prefix: str, count: int) -> list[dict]:
    """Multiple-choice question objects in the shape Assessment.questions holds."""

    return [
        {
            "type": "MULTIPLE_CHOICE",
            "question": f"{prefix} question {i}: which option is correct?",
            "points": 10,
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_index": i % 4,
        }
        for i in range(1, count + 1)
    ]


def _get_or_create_user(email: str, role: UserRole, first_name: str, last_name: str):
    user, _created = User.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "role": role,
            "status": AccountStatus.ACTIVE,
            "is_active": True,
            "terms_accepted_at": timezone.now(),
            "country": "NG",
            "timezone": "Africa/Lagos",
        },
    )
    # Re-running the command repairs a half-set-up row rather than reporting
    # success against something that cannot log in.
    changed = []
    if user.role != role:
        user.role = role
        changed.append("role")
    if user.status != AccountStatus.ACTIVE:
        user.status = AccountStatus.ACTIVE
        changed.append("status")
    if not user.is_active:
        user.is_active = True
        changed.append("is_active")
    if not user.has_usable_password() or not user.check_password(DEMO_PASSWORD):
        user.set_password(DEMO_PASSWORD)
        changed.append("password")
    if changed:
        user.save()
    return user, bool(changed)


def _get_or_create_catalog() -> tuple[Category, Topic, CourseVersion]:
    category, _ = Category.objects.get_or_create(
        name=CATEGORY_NAME,
        defaults={
            "slug": slugify(CATEGORY_NAME)[:160],
            "creator_price_beginner": "150000.00",
            "creator_price_intermediate": "160000.00",
            "creator_price_advanced": "170000.00",
            "status": CategoryStatus.ACTIVE,
        },
    )
    topic, _ = Topic.objects.get_or_create(
        category=category,
        name=TOPIC_NAME,
        defaults={
            "slug": slugify(TOPIC_NAME)[:160],
            "creator_price": category.creator_price_intermediate,
            "status": CategoryStatus.ACTIVE,
        },
    )
    version, _ = CourseVersion.objects.get_or_create(
        label="1.0", defaults={"is_active": True}
    )
    return category, topic, version


def _build_course_content(course: Course, topic_word: str) -> None:
    """Populate the module/lesson/assessment tree a submission must pass."""

    script = _filler_words(topic_word, SCRIPT_WORDS)
    for m in range(MODULE_COUNT):
        module = Module.objects.create(
            course=course,
            title=f"{topic_word} Module {m + 1}",
            order=m,
            description=f"Module {m + 1} of {topic_word}, building on the previous one.",
            learning_objectives=[f"Module {m + 1} objective {i}" for i in (1, 2)],
        )
        Assessment.objects.create(
            level=AssessmentLevel.MODULE,
            module=module,
            title=f"{topic_word} Module {m + 1} Quiz",
            questions=_make_questions(f"{topic_word} Module {m + 1}", 3),
        )
        for lesson_index in range(LESSONS_PER_MODULE):
            lesson = Lesson.objects.create(
                module=module,
                title=f"{topic_word} Lesson {m + 1}-{lesson_index + 1}",
                order=lesson_index,
                script=script,
                learning_objectives=["Apply the core pattern", "Explain the trade-offs"],
                duration_minutes=LESSON_MINUTES,
            )
            Assessment.objects.create(
                level=AssessmentLevel.LESSON,
                lesson=lesson,
                title=f"{topic_word} Lesson {m + 1}-{lesson_index + 1} Quiz",
                questions=_make_questions(f"{topic_word} Lesson {m + 1}-{lesson_index + 1}", 3),
            )

    Assessment.objects.create(
        level=AssessmentLevel.COURSE,
        course=course,
        title=f"{topic_word} Final Exam",
        questions=_make_questions(f"{topic_word} Final", 15),
    )
    course_service.recalculate_duration_estimate(course=course)


def _get_or_create_draft_course(
    *,
    creator,
    category: Category,
    topic,
    version: CourseVersion,
    title: str,
    description: str,
    difficulty: str,
) -> tuple[Course, bool]:
    course = Course.objects.filter(creator=creator, title=title).first()
    if course is not None:
        return course, False

    # draft_started_at backdates the draft past the (optional) minimum hold
    # rule so the manual submission is never blocked. Older deployments may
    # not have the field yet, so set it only when the model carries it.
    draft_field_used = "draft_started_at" in {
        f.name for f in Course._meta.get_fields()
    }

    kwargs = dict(
        creator=creator,
        category=category,
        topic=topic,
        title=title,
        description=_description_at_least(description),
        preview_video_url="https://example.com/demo/preview.mp4",
        thumbnail_url="https://example.com/demo/thumbnail.jpg",
        difficulty_level=difficulty,
        learning_objectives=[f"{title} objective {i}" for i in range(1, 6)],
        tags=[slugify(title)[:40], "demo"],
        planned_duration_seconds=12 * 3600,
        version=version,
        terms_accepted_at=timezone.now(),
        created_by=creator,
        updated_by=creator,
    )
    if draft_field_used:
        kwargs["draft_started_at"] = timezone.now() - timedelta(days=30)
    course = Course.objects.create(**kwargs)
    _build_course_content(course, topic_word=title.split()[0])
    return course, True


class Command(BaseCommand):
    help = (
        "Seed demo creators, a collaborated course (left unsubmitted), and a "
        "Creator Reviewer for the manual submit/review walkthrough."
    )

    def handle(self, *args, **options):
        creator1, creator1_changed = _get_or_create_user(
            CREATOR_1_EMAIL, UserRole.COURSE_CREATOR, "Amara", "Obi"
        )
        creator2, creator2_changed = _get_or_create_user(
            CREATOR_2_EMAIL, UserRole.COURSE_CREATOR, "Tunde", "Bello"
        )
        reviewer, reviewer_changed = _get_or_create_user(
            REVIEWER_EMAIL, UserRole.CREATOR_REVIEWER, "Ruby", "Nwosu"
        )
        ReviewerAvailability.objects.get_or_create(
            user=reviewer, defaults={"is_available": True}
        )
        if not reviewer.assigned_track:
            reviewer.assigned_track = QueueTrackFilter.ALL
            reviewer.save(update_fields=["assigned_track", "updated_datetime"])

        category, topic, version = _get_or_create_catalog()

        with transaction.atomic():
            course1, created1 = _get_or_create_draft_course(
                creator=creator1,
                category=category,
                topic=None,
                version=version,
                title=COURSE_1_TITLE,
                description=" ".join(
                    "Django for Beginners walks a new developer from a blank project "
                    "to a deployed web application. The target audience is anyone with "
                    "basic Python who wants to build server-rendered sites. The only "
                    "prerequisites are comfort with Python functions, elementary "
                    "command-line usage, and a willingness to debug. You will start "
                    "with project setup, then models, views, templates and forms, "
                    "before wiring authentication and the admin site. Outcomes: you "
                    "can design data models, write function-based and class-based "
                    "views, use the ORM confidently, structure templates, and ship an "
                    "application behind a real web server with confidence.".split()
                ),
                difficulty=DifficultyLevel.BEGINNER,
            )
            course2, created2 = _get_or_create_draft_course(
                creator=creator1,
                category=category,
                topic=topic,
                version=version,
                title=COURSE_2_TITLE,
                description=" ".join(
                    "React Development Bootcamp is a hands-on tour of modern React, "
                    "built jointly by Amara Obi and Tunde Bello. The audience is "
                    "developers comfortable with JavaScript who want component-driven "
                    "user-interface skills. The prerequisites are HTML, CSS and modern "
                    "JavaScript; no prior React experience is assumed. Outcomes: build "
                    "reusable components, manage state with hooks, fetch and cache "
                    "remote data, compose accessible forms, test components in "
                    "isolation, and deploy a production bundle to a real host with "
                    "confidence.".split()
                ),
                difficulty=DifficultyLevel.INTERMEDIATE,
            )

            collaborator, collab_created = CourseCollaborator.objects.get_or_create(
                course=course2,
                user=creator2,
                defaults={
                    "role": CollaboratorRole.COLLABORATOR,
                    "invited_by": creator1,
                },
            )
            if not collaborator.assigned_modules.exists():
                collaborator.assigned_modules.set(course2.modules.all())

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully.\n"))

        self.stdout.write(self.style.MIGRATE_HEADING("ACCOUNTS (password for all: DemoPass123!)"))
        for account in (
            ("Course Creator (primary)", CREATOR_1_EMAIL, creator1_changed),
            ("Course Creator (collaborator)", CREATOR_2_EMAIL, creator2_changed),
            ("Creator Reviewer", REVIEWER_EMAIL, reviewer_changed),
        ):
            label, email, changed = account
            suffix = " (created)" if changed else " (already existed)"
            self.stdout.write(f"  {label:<32} {email}{suffix}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("COURSES (both left in DRAFT - not submitted)"))
        for label, course, created in (
            ("Course 1 (Amara solo)", course1, created1),
            ("Course 2 (Amara x Tunde)", course2, created2),
        ):
            state = "created" if created else "already existed"
            self.stdout.write(f"  {label}: '{course.title}'  id={course.id}  status={course.status} ({state})")
        self.stdout.write(
            f"    Collaboration: {creator2.email} is a CourseCollaborator "
            f"(role={collaborator.role}) on '{course2.title}' with all "
            f"{collaborator.assigned_modules.count()} modules assigned."
        )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("HOW TO SUBMIT MANUALLY (as Amara Obi)"))
        self.stdout.write("  1. Login:  POST /api/v1/auth/login/  with amara.creator@demo.test / DemoPass123!")
        self.stdout.write(f"  2. Submit course 1:  POST /api/v1/courses/{course1.id}/submit/")
        self.stdout.write(f"  3. Submit course 2:  POST /api/v1/courses/{course2.id}/submit/")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("WHERE THE REVIEWER SEES THEM (login as Ruby Nwosu)"))
        self.stdout.write("  Pending queue:   GET /api/v1/review-queue/pending/   (Submitted courses awaiting a claim)")
        self.stdout.write("  In-review list:  GET /api/v1/review-queue/in-review/")
        self.stdout.write("  Course detail:   GET /api/v1/review-queue/{course_id}/")
        self.stdout.write("  Claim:           POST /api/v1/review-queue/{course_id}/claim/")
        self.stdout.write("  Then approve/reject: POST /api/v1/review-queue/{course_id}/approve/ | .../reject/")
