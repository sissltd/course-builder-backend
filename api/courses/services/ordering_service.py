"""Bulk reordering for the builder's drag-and-drop surfaces.

Both Module and Lesson carry a non-deferrable unique constraint on
(parent, order) - `unique_module_order_per_course` and
`unique_lesson_order_per_module`. Writing new positions one row at a time
violates that constraint mid-transaction: swapping two adjacent items
means both briefly hold the same order.

`reorder` therefore writes in two passes inside one transaction. The
first parks every affected row in a disjoint band far above any real
position; the second writes the final values into the space that frees
up. No migration and no schema change - the alternative would be marking
the constraints DEFERRABLE, which is Postgres-only and rewrites two
tables.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import exceptions

PARK_OFFSET = 1_000_000
"""Temporary band the first pass parks rows in.

Far above any realistic order value, so parked rows cannot collide with
the final positions being written by the second pass.
"""


def reorder(*, queryset, items, actor=None) -> list:
    """Apply an explicit ordering to every row in `queryset`.

    `items` is a list of {"id": ..., "order": ...}. It must name the
    complete sibling set: a partial reorder would silently leave the
    omitted rows holding stale positions that collide with the new ones.
    Returns the reordered rows.
    """

    model = queryset.model
    existing = {str(pk) for pk in queryset.values_list("id", flat=True)}
    submitted = [str(item["id"]) for item in items]

    _validate_complete_permutation(existing, submitted, model)
    _validate_orders(items)

    positions = {str(item["id"]): item["order"] for item in items}

    with transaction.atomic():
        # Pass 1: vacate the target band so pass 2 cannot collide with a
        # row that has not been moved yet.
        queryset.update(order=F("order") + PARK_OFFSET)

        # Pass 2: write final positions. select_for_update is unnecessary -
        # the parked band is already exclusive to this transaction.
        rows = list(queryset.all())
        # bulk_update bypasses auto_now and the UserHistory mixin, so the
        # audit fields are stamped by hand rather than silently going stale.
        now = timezone.now()
        for row in rows:
            row.order = positions[str(row.id)]
            row.updated_datetime = now
            if actor is not None:
                row.updated_by = actor
        fields = ["order", "updated_datetime"] + (["updated_by"] if actor else [])
        model.objects.bulk_update(rows, fields)

    return sorted(rows, key=lambda row: row.order)


def _validate_complete_permutation(existing: set, submitted: list, model) -> None:
    noun = model._meta.verbose_name_plural

    duplicates = {pk for pk in submitted if submitted.count(pk) > 1}
    if duplicates:
        raise exceptions.ValidationError(
            {"order": [f"Duplicate ids in payload: {', '.join(sorted(duplicates))}."]}
        )

    submitted_set = set(submitted)
    unknown = submitted_set - existing
    if unknown:
        raise exceptions.ValidationError(
            {
                "order": [
                    f"These ids do not belong to this parent: "
                    f"{', '.join(sorted(unknown))}."
                ]
            }
        )

    missing = existing - submitted_set
    if missing:
        raise exceptions.ValidationError(
            {
                "order": [
                    f"Every one of the {len(existing)} {noun} must be listed; "
                    f"missing: {', '.join(sorted(missing))}."
                ]
            }
        )


def _validate_orders(items) -> None:
    orders = [item["order"] for item in items]
    if len(set(orders)) != len(orders):
        raise exceptions.ValidationError(
            {"order": ["Each item must be given a distinct order value."]}
        )
