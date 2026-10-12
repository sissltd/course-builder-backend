"""Creator payout: how much and when.

A course is paid its locked price of its type, straight into the creator's
wallet, in the approval that earns it:

* the Production Engine makes the video (without video): when Second Review
  approves it and it passes to the Verifier;
* the creator or a developer supplies the video (with video): when QA
  approves it;
* an AI-built course is paid its AI price, at whichever of the two its video
  follows.

A developer is paid only when their plan pays for the submission. Every
approval goes through the real review endpoints.
"""

from decimal import Decimal

import pytest

from api.authorization.models import RolePermission
from api.catalog.enums import CreatorPriceType
from api.courses.enums import CourseSourceType, CourseStatus, VideoProvider
from api.courses.models import Course, Lesson
from api.courses.tests.factories import build_compliant_course, make_category
from api.mie.enums import MiePlanType
from api.mie.tests.factories import make_approved_developer, make_decided_submission
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.payments.services import course_payout_service
from api.platform.services import platform_settings_service
from api.reviews.enums import MediaAssetKind, ReviewStage
from api.reviews.models import MediaAsset
from api.users.enums import UserActivityActionEnums, UserRole
from api.users.models import UserActivityLog
from api.wallet.services import wallet_service

QUEUE = "/api/v1/review-queue/"
PRICES = {
    "creator_price_without_video": Decimal("400.00"),
    "creator_price_with_video": Decimal("600.00"),
    "creator_price_ai_built": Decimal("250.00"),
}


@pytest.fixture
def staged(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def writer(make_user):
    return make_user(role=UserRole.STAFF_WRITER)


@pytest.fixture
def qa(make_user):
    return make_user(role=UserRole.QA_REVIEWER)


def _priced_course(creator, *, source=CourseSourceType.CREATOR_UPLOADED) -> Course:
    """A submitted course with its three prices locked."""

    course = build_compliant_course(creator=creator, category=make_category(**PRICES))
    Course.objects.filter(pk=course.pk).update(source_type=source, preview_video_url="")
    course.refresh_from_db()
    from api.courses.services import course_service

    return course_service.submit_course(course=course, actor=creator)


def _at_seat(course, seat, *, provider) -> Course:
    """Move a submitted course to `seat` with its video supplied by `provider`
    (past the text and video steps, which are tested elsewhere)."""

    course.video_provider = provider
    course_payout_service.refresh_expected_payout(course)
    values = {
        "video_provider": provider,
        "payout_type": course.payout_type,
        "creator_price_snapshot": course.creator_price_snapshot,
    }
    if seat == ReviewStage.QA:
        values.update(status=CourseStatus.QA_VERIFICATION, review_stage="")
        _complete_media(course)
    else:
        values.update(status=CourseStatus.SUBMITTED, review_stage=seat)
    Course.objects.filter(pk=course.pk).update(**values)
    course.refresh_from_db()
    return course


def _complete_media(course):
    """The QA evidence every course needs before QA may approve it."""

    evidence = {
        "mime_type": "video/mp4",
        "duration_seconds": 300,
        "resolution": "1920x1080",
        "subtitle_url": "https://example.com/c.vtt",
        "caption_accuracy_percent": Decimal("99"),
        "audio_lufs": Decimal("-16"),
        "audio_video_drift_ms": 10,
        "accessibility": {"captions": True},
    }
    rows = [
        MediaAsset(course=course, lesson=lesson, kind=MediaAssetKind.VIDEO, url="https://example.com/l.mp4", **evidence)
        for lesson in Lesson.objects.filter(module__course=course)
    ]
    rows.append(MediaAsset(course=course, kind=MediaAssetKind.PREVIEW_VIDEO, url="https://example.com/p.mp4", **evidence))
    rows.append(MediaAsset(course=course, kind=MediaAssetKind.THUMBNAIL, url="https://example.com/t.png"))
    MediaAsset.objects.bulk_create(rows)


def _approve_seat(client, reviewer, course):
    client.force_authenticate(reviewer)
    assert client.post(f"{QUEUE}{course.id}/claim/", {}, format="json").status_code == 200
    return client.post(f"{QUEUE}{course.id}/approve/", {}, format="json")


def _approve_qa(client, reviewer, course):
    client.force_authenticate(reviewer)
    assert client.post(f"{QUEUE}{course.id}/qa-claim/", {}, format="json").status_code == 200
    return client.post(f"{QUEUE}{course.id}/qa-approve/", {}, format="json")


def _balance(user) -> Decimal:
    return wallet_service.get_or_create_wallet(user=user).balance


# --- without video: paid at Second Review --------------------------------------------------


@pytest.mark.django_db
def test_a_creator_who_left_the_video_to_the_engine_is_paid_when_second_review_approves(
    api_client, staged, creator, writer
):
    course = _at_seat(_priced_course(creator), ReviewStage.SECOND_REVIEW, provider=VideoProvider.PRODUCTION_ENGINE)
    ledger = InternalAccount.objects.get(code_name=course_payout_service.COURSE_PAYMENT_ACCOUNT)
    ledger_before = ledger.balance

    response = _approve_seat(api_client, writer, course)

    assert response.status_code == 200
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (CourseStatus.SUBMITTED, ReviewStage.VERIFICATION)
    assert course.payout_type == CreatorPriceType.WITHOUT_VIDEO
    assert course.creator_paid_at is not None
    assert _balance(creator) == Decimal("400.00")
    ledger.refresh_from_db()
    assert ledger_before - ledger.balance == Decimal("400.00")  # double entry
    assert Transaction.objects.filter(reference=course_payout_service.reference_for(course)).count() == 2
    assert Notification.objects.filter(receiver=creator, title="Payment received").exists()
    assert UserActivityLog.objects.filter(user=creator, action=UserActivityActionEnums.COURSE_PAYOUT_CREDITED).exists()


@pytest.mark.django_db
def test_an_engine_video_course_is_not_paid_again_at_qa(api_client, staged, creator, writer, qa):
    course = _at_seat(_priced_course(creator), ReviewStage.SECOND_REVIEW, provider=VideoProvider.PRODUCTION_ENGINE)
    _approve_seat(api_client, writer, course)
    course = _at_seat(course, ReviewStage.QA, provider=VideoProvider.PRODUCTION_ENGINE)

    response = _approve_qa(api_client, qa, course)

    assert response.status_code == 200
    assert _balance(creator) == Decimal("400.00")


@pytest.mark.django_db
def test_an_engine_video_course_still_unpaid_by_qa_is_paid_there(api_client, staged, creator, qa):
    """A course already past Second Review when these rules shipped."""

    course = _at_seat(_priced_course(creator), ReviewStage.QA, provider=VideoProvider.PRODUCTION_ENGINE)

    _approve_qa(api_client, qa, course)

    assert _balance(creator) == Decimal("400.00")


# --- with video: paid at QA --------------------------------------------------------------


@pytest.mark.django_db
def test_a_creator_with_their_own_video_is_paid_at_qa_and_not_before(api_client, staged, creator, writer, qa):
    course = _at_seat(_priced_course(creator), ReviewStage.SECOND_REVIEW, provider=VideoProvider.CREATOR)

    _approve_seat(api_client, writer, course)
    assert _balance(creator) == Decimal("0")  # not at Second Review

    course = _at_seat(course, ReviewStage.QA, provider=VideoProvider.CREATOR)
    response = _approve_qa(api_client, qa, course)

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.APPROVED
    assert course.payout_type == CreatorPriceType.WITH_VIDEO
    assert _balance(creator) == Decimal("600.00")


@pytest.mark.django_db
def test_the_video_decision_sets_the_price(api_client, staged, creator):
    course = _priced_course(creator)
    Course.objects.filter(pk=course.pk).update(status=CourseStatus.AWAITING_VIDEO, review_stage="")
    api_client.force_authenticate(creator)

    api_client.post(f"/api/v1/courses/{course.id}/video-decision/", {"will_provide": False}, format="json")
    course.refresh_from_db()
    left_to_engine = (course.payout_type, course.creator_price_snapshot)
    api_client.post(f"/api/v1/courses/{course.id}/video-decision/", {"will_provide": True}, format="json")
    course.refresh_from_db()

    assert left_to_engine == (CreatorPriceType.WITHOUT_VIDEO, Decimal("400.00"))
    assert (course.payout_type, course.creator_price_snapshot) == (CreatorPriceType.WITH_VIDEO, Decimal("600.00"))


# --- AI-built -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_ai_built_course_with_engine_video_is_paid_its_ai_price_at_second_review(api_client, staged, creator, writer):
    course = _priced_course(creator, source=CourseSourceType.AI_GENERATED)
    course = _at_seat(course, ReviewStage.SECOND_REVIEW, provider=VideoProvider.PRODUCTION_ENGINE)

    _approve_seat(api_client, writer, course)

    assert _balance(creator) == Decimal("250.00")


@pytest.mark.django_db
def test_an_ai_built_course_with_the_creators_video_is_paid_its_ai_price_at_qa(api_client, staged, creator, qa):
    course = _priced_course(creator, source=CourseSourceType.AI_GENERATED)
    course = _at_seat(course, ReviewStage.QA, provider=VideoProvider.CREATOR)

    _approve_qa(api_client, qa, course)

    assert _balance(creator) == Decimal("250.00")


# --- developers ----------------------------------------------------------------------------


def _developer_course(creator, plan, *, bypass=False) -> Course:
    developer, _key = make_approved_developer(plan_type=plan)
    course = _priced_course(creator, source=CourseSourceType.DEVELOPER_API)
    make_decided_submission(developer=developer, resulting_course=course, payout_bypass=bypass)
    return _at_seat(course, ReviewStage.QA, provider=VideoProvider.DEVELOPER)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("plan", "bypass", "paid"),
    [
        (MiePlanType.PAID_PER_SUBMISSION, False, Decimal("600.00")),
        (MiePlanType.BYPASS_PER_SUBMISSION, False, Decimal("600.00")),
        (MiePlanType.BYPASS_PER_SUBMISSION, True, Decimal("0")),
        (MiePlanType.BYPASS_ACCOUNT, False, Decimal("0")),
    ],
)
def test_a_developer_is_paid_at_qa_only_when_their_plan_pays(api_client, staged, creator, qa, plan, bypass, paid):
    course = _developer_course(creator, plan, bypass=bypass)

    response = _approve_qa(api_client, qa, course)

    assert response.status_code == 200
    assert _balance(creator) == paid
    course.refresh_from_db()
    assert (course.creator_paid_at is not None) == bool(paid)


# --- once, and never for nothing -----------------------------------------------------------


@pytest.mark.django_db
def test_a_course_is_paid_once_whatever_calls_it(staged, creator):
    course = _at_seat(_priced_course(creator), ReviewStage.QA, provider=VideoProvider.CREATOR)

    first = course_payout_service.pay_on_approval(course=course, seat=ReviewStage.QA)
    second = course_payout_service.pay_on_approval(course=course, seat=ReviewStage.QA)

    assert (first, second) == (True, False)
    assert _balance(creator) == Decimal("600.00")


@pytest.mark.django_db
def test_a_course_priced_at_zero_pays_nothing(staged, creator):
    course = _at_seat(_priced_course(creator), ReviewStage.QA, provider=VideoProvider.CREATOR)
    Course.objects.filter(pk=course.pk).update(creator_price_snapshot=Decimal("0"))
    course.refresh_from_db()

    assert course_payout_service.pay_on_approval(course=course, seat=ReviewStage.QA) is False
    assert not Transaction.objects.filter(course=course).exists()


# --- who sets prices -----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_writer_sets_category_prices_and_others_may_not(api_client, writer, make_user):
    category = make_category(**PRICES)
    api_client.force_authenticate(writer)

    allowed = api_client.patch(f"/api/v1/categories/{category.id}/", {"creator_price_ai_built": "300.00"}, format="json")

    assert allowed.status_code == 200
    category.refresh_from_db()
    assert category.creator_price_ai_built == Decimal("300.00")

    # A role that manages categories but may not set prices: names yes, prices no.
    RolePermission.objects.filter(role__system_key=UserRole.STAFF_WRITER, codename="catalog.set_creator_prices").delete()
    renamed = api_client.patch(f"/api/v1/categories/{category.id}/", {"name": "Renamed"}, format="json")
    repriced = api_client.patch(f"/api/v1/categories/{category.id}/", {"creator_price_ai_built": "1.00"}, format="json")
    created = api_client.post(
        "/api/v1/categories/", {"name": "New", **{key: str(value) for key, value in PRICES.items()}}, format="json"
    )

    assert renamed.status_code == 200
    assert repriced.status_code == 403 and repriced.json()["errors"][0]["code"] == "permission_denied"
    assert created.status_code == 403
    category.refresh_from_db()
    assert category.creator_price_ai_built == Decimal("300.00")
