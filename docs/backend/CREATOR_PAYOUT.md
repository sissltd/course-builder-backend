# Creator payout: reference

How much a creator is paid for a course, when, and how the money moves.
For screens and endpoints, see
[../frontend/CREATOR_PAYOUT_HANDOVER.md](../frontend/CREATOR_PAYOUT_HANDOVER.md).

## 1. Prices

Every category carries three creator prices, one per
`catalog.enums.CreatorPriceType`:

| Type | Field | For |
|---|---|---|
| `WITHOUT_VIDEO` | `creator_price_without_video` | A creator who writes the course and leaves the video to the Production Engine |
| `WITH_VIDEO` | `creator_price_with_video` | A creator who adds the video, and a paid developer |
| `AI_BUILT` | `creator_price_ai_built` | A creator who builds the course with our AI |

- **Topic overrides.** A topic may override any of them (nullable fields of
  the same names). `Topic.price_for(type)` returns the topic's own price or
  falls back to its category's.
- **Who sets them.** Only holders of `catalog.set_creator_prices` (Writer,
  Admin, Super Admin) may set prices. The check is in the services
  (`category_service.require_price_setter`), so it applies to the API,
  category-request approval and the Django admin alike.

## 2. Locking and the payout type

- **At submission,** `course_payout_service.lock_rates` copies the course's
  three current prices into `Course.creator_rates_snapshot` and sets the
  expected payout. A later price change doesn't touch it. The one exception
  is a topic repriced while the course is still in the review queue
  (`SUBMITTED` / `IN_REVIEW`) and unpaid: it re-locks, as topic repricing
  always has.
- **The payout type** (`Course.payout_type`) comes from how the course is
  made (`price_type_for`):
  - `AI_GENERATED` source → `AI_BUILT`;
  - otherwise, video by the Production Engine → `WITHOUT_VIDEO`;
  - otherwise → `WITH_VIDEO`.
- **`Course.creator_price_snapshot`** is the locked price of that type: the
  amount to be paid.
- **When the type is re-picked,** `refresh_expected_payout` runs:
  - when the creator makes the video decision;
  - when First Review approves the text of a developer's or the crawler's
    course, since that's when its video provider is set.

## 3. When it is paid

`course_payout_service.pay_on_approval(course, seat)` is called inside the
approving transaction, with the course row locked:

| Video by | Paid at | Called from |
|---|---|---|
| The Production Engine | Second Review approval (the course passes to the Verifier) | `review_service.approve_content` |
| The creator or a developer | QA approval | `review_service.approve_qa` |

- **Catching up.** A course is also paid at any later seat if it wasn't
  paid at its own, for example an engine course already past Second Review
  when these rules shipped.
- **Once only.** A course is paid once: `creator_paid_at` is checked under
  the row lock, and `approve_qa` now locks the course too.
- **Nothing for nothing.** A course priced at zero, or with no price, pays
  nothing.

There is no delay and no background task: the old `auto_credit_duration_hours`
setting and the `payments.release_course_payment` task are removed.

## 4. Who is paid

- **Creators.** Course creators are always paid (`CREATOR_UPLOADED`,
  `AI_GENERATED`, `DOCUMENT_IMPORTED`).
- **Developers.** A developer's course (`DEVELOPER_API`) is paid into the
  developer's linked creator account only when the plan pays
  (`is_payable`):
  - `PAID_PER_SUBMISSION`: always;
  - `BYPASS_PER_SUBMISSION`: unless the submission's `payout_bypass` is
    true;
  - `BYPASS_ACCOUNT`: never. The platform's crawler is on this plan.

## 5. How the money moves

A payout is a double-entry ledger transfer
(`transaction_services.internal_transfer`):
- **Debit:** the internal account `course_payment`, seeded by
  `payments/0010`.
- **Credit:** the creator's wallet.
- **Reference:** both entries share `course_payment_<course id>`.

Then, in the same transaction:
- `Course.creator_paid_at` is set;
- the creator gets a "Payment received" notification;
- the activity log records `COURSE_PAYOUT_CREDITED` (category WALLET) on the
  creator.

If the `course_payment` account is missing, the approval fails and nothing
is saved. Approval and payment succeed or fail together.

## 6. Migrations

| Migration | What it does |
|---|---|
| `catalog/0009` | Adds the three prices, copying each category's Beginner price into all three. Turns each topic's single price into all three overrides, so nobody's pay changes until a Writer sets new prices. Removes the level fields. Reversible. |
| `courses/0021` | Adds `payout_type`, `creator_rates_snapshot` and `creator_paid_at`, then backfills (below). |
| `platform/0022` | Removes `auto_credit_duration_hours`. |
| `authorization/0008` | Grants `catalog.set_creator_prices` to Writer, Admin and Super Admin. Reversible. |
| `users/0046` | Adds the `COURSE_PAYOUT_CREDITED` activity action. |

The `courses/0021` backfill:
- **Already paid:** a course whose creator's wallet has a credit for it is
  marked paid, so it's never paid twice.
- **Unpaid, still in flight:** it keeps the amount it was promised.
- **AI-generated:** these were never priced before; they're locked at their
  AI-built price.

## 7. Where the code is

| Path | What |
|---|---|
| `api/payments/services/course_payout_service.py` | Prices, locking, type, payout seat, eligibility, the payment |
| `api/catalog/models/category.py`, `topic.py` | The prices and `price_for` |
| `api/catalog/services/category_service.py`, `topic_service.py` | Who may set prices; topic re-locking |
| `api/reviews/services/review_service.py` | The two calls to `pay_on_approval` |
| `api/courses/services/course_service.py` | Locking at submit; re-pricing at the video decision |
| `api/payments/tests/test_course_payout.py` | Every payout path over the real review endpoints |
