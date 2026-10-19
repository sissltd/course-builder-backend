# Creator payout: frontend handover

Creators are paid a fixed price per course, straight into their wallet, at
the moment the course earns it. This page covers what changed for the
screens. The backend reference is
[../backend/CREATOR_PAYOUT.md](../backend/CREATOR_PAYOUT.md).

## In one table

| How the course was made | Price paid | Paid when |
|---|---|---|
| The creator wrote it and left the video to the Production Engine | **Without video** | Second Review approves it (it moves on to the Verifier) |
| The creator wrote it and added the video | **With video** | QA approves it |
| The creator built it with our AI | **AI-built** | Second Review if the engine made the video, QA if the creator did |
| A developer pushed it (it carries its own video) | **With video** | QA approves it, unless the developer's plan doesn't pay (see below) |

The money appears in the creator's wallet immediately, in the same step as
the approval. There's no waiting period any more.

## Admin → Categories: three prices instead of three levels

Prices no longer depend on the course's difficulty. Every category has
three prices:

| Field | Label to show |
|---|---|
| `creator_price_without_video` | Without video |
| `creator_price_with_video` | With video |
| `creator_price_ai_built` | AI-built |

`creator_price_beginner`, `creator_price_intermediate` and
`creator_price_advanced` are **gone**. Replace those three columns and
inputs with the three above. Existing categories were migrated with all
three new prices set to their old Beginner price.

- `GET /api/v1/categories/` and `GET /api/v1/categories/{id}/`: the three fields on every row.
- `POST /api/v1/categories/`: all three are required.
- `PATCH /api/v1/categories/{id}/`: send any of them.
- `?ordering=` accepts `creator_price_without_video`, `creator_price_with_video` and `creator_price_ai_built`.

**Who can set prices.** Only holders of the new permission
`catalog.set_creator_prices` ("Set Creator Prices"): Writer, Admin and
Super Admin by default. Anyone else gets **403** when the request changes a
price. Hide or disable the price inputs for users without it; the user's
permissions are on `GET /api/v1/users/me/`.

## Admin → Topics: optional overrides

A topic has the same three fields. Each is **nullable**: `null` means "use
the category's price". Show the category's price as the placeholder when a
topic's own price is null.

- `PATCH /api/v1/topics/{id}/` with any price needs `catalog.set_creator_prices`.
- Changing the name, status or anything else still needs `catalog.manage_topics`.
- Only fields whose value actually changes are checked, so a full form that
  re-sends unchanged values is fine. A Writer can therefore reprice a topic
  without being able to rename it, and a topic manager can edit it without
  touching prices.
- Creating a topic with any price set also needs `catalog.set_creator_prices`. Creating one without prices doesn't.
- Repricing a topic also re-prices that topic's **unpaid courses still in
  the review queue**. Courses further along keep the prices they locked.
- A topic created by approving a topic reservation has no overrides.

The single field `creator_price` on topics is **gone**.

## Admin → Category requests: approve with three prices

`POST /api/v1/category-requests/{id}/approve/` (and the
`/admin/category-requests/` twin) now takes:

```json
{
  "creator_price_without_video": "100000.00",
  "creator_price_with_video": "150000.00",
  "creator_price_ai_built": "60000.00",
  "track_preference": "OPEN"
}
```

All three prices are required; `creator_price` is no longer accepted. The
approver needs `catalog.set_creator_prices` (**403** otherwise).

## Courses: what the creator will be paid

Course detail and the review-queue rows carry:

| Field | Meaning |
|---|---|
| `creator_price_snapshot` | The amount the creator is (or will be) paid. Null until the course is submitted. |
| `payout_type` | `WITHOUT_VIDEO`, `WITH_VIDEO` or `AI_BUILT`; blank until submitted. |
| `creator_paid_at` | When the wallet was credited; null until paid. |

- **Prices lock at submission.** A course locks all three of its prices when
  it is submitted, so later price changes don't affect it.
- **The type can change once, at the video decision.** When a creator
  chooses on `POST /courses/{id}/video-decision/`, `payout_type` and the
  amount switch between with and without video. Show the creator the amount
  next to each choice; they're in the locked prices.
- **Paid badge:** show "Paid" with the date when `creator_paid_at` is set.

## Notifications

The creator gets an in-app **"Payment received"** notification:
"{amount} has been added to your wallet for '{course}'". Its metadata
carries `course_id`, `amount` and `payout_type`. The old "Course approved"
message that announced the delayed credit is gone.

## Settings → Payments

`auto_credit_duration_hours` has been **removed** from
`GET` / `PATCH /api/v1/platform-settings/`. Remove that input.
`withdrawal_require_verification` is unchanged.

## Developers (MIE)

A developer's course is paid like a creator's course with its own video (at
QA, with-video price) into the developer's linked creator account. It's
paid only when their plan allows:

| `plan_type` | Paid? |
|---|---|
| `PAID_PER_SUBMISSION` | Yes |
| `BYPASS_PER_SUBMISSION` | Yes, unless that submission's `payout_bypass` is true |
| `BYPASS_ACCOUNT` | Never (the crawler is on this plan) |

## QA checklist

- [ ] A Writer edits all three prices on a category; a user without the
      permission gets 403 on a price but can still rename.
- [ ] A topic price left empty shows the category's price; a topic edit by a
      reviewer can rename but not reprice.
- [ ] A course submitted shows `payout_type` and the amount; choosing "leave
      it to video production" switches it to the without-video price.
- [ ] An engine-video course: the wallet goes up the moment Second Review
      approves; QA approval later adds nothing.
- [ ] A creator's own video: nothing at Second Review; credited at QA.
- [ ] "Payment received" appears; `creator_paid_at` is set; the Payments
      settings no longer show the credit delay.
