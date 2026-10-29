"""Maps the canonical course package onto one channel's shape.

A channel is described by data, not code: a JSON Schema of what it accepts
and a field map that says where each of its fields comes from in the
package. `apply` builds the payload and reports every gap against the
schema, so a channel that cannot take a course says exactly why.

Field map rules (target path -> rule):

* `{"from": "course.title"}` - a value from the package; dots walk objects.
  Optional `"transform": ["truncate:60", ...]` and `"default": value`.
* `{"const": value}` - a fixed value.
* `{"each": "modules", "map": {...}}` - one mapped object per list item;
  inside it, `from` paths start at the item, or at the package root when
  they begin with `/`.

Target paths use dots for nesting (`"pricing.amount"`). Transforms:
truncate:N, upper, lower, strip, join:SEP, count, first_sentence,
minutes_to_seconds, string, number, integer.
"""

import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from rest_framework import exceptions

from api.authentication.services.activity_service import log_activity
from api.production.models import ChannelMapping
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums

MISSING = object()

TRANSFORM_NAMES = {
    "truncate", "upper", "lower", "strip", "join", "count", "first_sentence",
    "minutes_to_seconds", "string", "number", "integer",
}


def _resolve(path: str, item, root):
    node = root if path.startswith("/") else item
    for part in path.lstrip("/").split("."):
        if not part:
            continue
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return MISSING
    return node


def _transform(value, steps: list[str]):
    for step in steps:
        name, _, argument = step.partition(":")
        if value is None:
            return None
        if name == "truncate":
            limit = int(argument)
            text = str(value)
            value = text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"
        elif name == "upper":
            value = str(value).upper()
        elif name == "lower":
            value = str(value).lower()
        elif name == "strip":
            value = str(value).strip()
        elif name == "join":
            value = (argument or ", ").join(str(part) for part in value)
        elif name == "count":
            value = len(value)
        elif name == "first_sentence":
            value = re.split(r"(?<=[.!?])\s", str(value).strip(), maxsplit=1)[0]
        elif name == "minutes_to_seconds":
            value = int(value) * 60
        elif name == "string":
            value = str(value)
        elif name == "number":
            value = float(Decimal(str(value)))
        elif name == "integer":
            value = int(Decimal(str(value)))
    return value


def _set(target: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def _map(field_map: dict, item, root) -> dict:
    out: dict = {}
    for target, rule in field_map.items():
        if "const" in rule:
            value = rule["const"]
        elif "each" in rule:
            source = _resolve(rule["each"], item, root)
            if source is MISSING or source is None:
                continue
            value = [_map(rule["map"], element, root) for element in source]
        else:
            value = _resolve(rule["from"], item, root)
            if value is MISSING:
                value = rule.get("default", MISSING)
                if value is MISSING:
                    continue
            value = _transform(value, rule.get("transform", []))
        _set(out, target, value)
    return out


def _gaps(schema: dict, payload: dict) -> list[str]:
    gaps = []
    for error in sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(part) for part in error.absolute_path) or "(payload)"
        gaps.append(f"{where}: {error.message}")
    return gaps


def apply(mapping: ChannelMapping, package: dict) -> tuple[dict, list[str]]:
    """The channel payload for `package`, and every way it falls short of
    the channel's schema (empty when it can be delivered)."""

    payload = _map(mapping.field_map, package, package)
    return payload, _gaps(mapping.target_schema, payload)


# --- managing mappings ----------------------------------------------------------


def check_definition(*, target_schema: dict, field_map: dict) -> None:
    """400 unless the schema is valid JSON Schema and every rule is
    well-formed."""

    try:
        Draft202012Validator.check_schema(target_schema)
    except SchemaError as exc:
        raise exceptions.ValidationError({"target_schema": f"Not a valid JSON Schema: {exc.message}"}) from exc
    problems = list(_rule_problems(field_map, prefix=""))
    if problems:
        raise exceptions.ValidationError({"field_map": problems})


def _rule_problems(field_map, *, prefix: str):
    if not isinstance(field_map, dict) or not field_map:
        yield f"{prefix or 'field_map'}: must be a non-empty object of target path -> rule."
        return
    for target, rule in field_map.items():
        where = f"{prefix}{target}"
        if not isinstance(rule, dict) or len({"from", "const", "each"} & rule.keys()) != 1:
            yield f"{where}: a rule needs exactly one of 'from', 'const' or 'each'."
            continue
        if "each" in rule:
            yield from _rule_problems(rule.get("map"), prefix=f"{where}[].")
        for step in rule.get("transform", []):
            if not isinstance(step, str) or step.partition(":")[0] not in TRANSFORM_NAMES:
                yield f"{where}: unknown transform {step!r}."


def active_mapping(channel: str) -> ChannelMapping | None:
    return ChannelMapping.objects.filter(channel=channel, is_active=True).first()


def create_version(*, channel: str, delivery_method: str, target_schema: dict, field_map: dict, response_id_path: str, notes: str, activate: bool, actor) -> ChannelMapping:
    """A new version of a channel's mapping (versions are never edited, so
    a delivery can always say which mapping shaped it)."""

    check_definition(target_schema=target_schema, field_map=field_map)
    with transaction.atomic():
        # Two simultaneous saves would collide on the (channel, version)
        # constraint rather than both landing.
        latest = ChannelMapping.objects.filter(channel=channel).aggregate(version=Max("version"))["version"]
        if activate:
            ChannelMapping.objects.filter(channel=channel, is_active=True).update(is_active=False)
        mapping = ChannelMapping.objects.create(
            channel=channel,
            version=(latest or 0) + 1,
            delivery_method=delivery_method,
            target_schema=target_schema,
            field_map=field_map,
            response_id_path=response_id_path,
            notes=notes,
            is_active=activate,
            created_by=actor,
        )
        _log(actor, mapping, f"saved {mapping}" + (" and made it active" if activate else ""))
    return mapping


def activate(*, mapping: ChannelMapping, actor) -> ChannelMapping:
    """Make `mapping` its channel's active version. Idempotent."""

    with transaction.atomic():
        ChannelMapping.objects.filter(channel=mapping.channel, is_active=True).exclude(pk=mapping.pk).update(is_active=False)
        if not mapping.is_active:
            mapping.is_active = True
            mapping.save(update_fields=["is_active", "updated_datetime"])
            _log(actor, mapping, f"made {mapping} active")
    return mapping


def _log(actor, mapping: ChannelMapping, what: str) -> None:
    log_activity(
        user=actor,
        category=UserActivityCategoryEnums.PRODUCTION,
        action=UserActivityActionEnums.CHANNEL_MAPPING_SAVED,
        summary=f"You {what}."[:255],
        details={"channel_mapping_id": str(mapping.id), "channel": mapping.channel, "version": mapping.version},
        target=mapping,
    )


def preview(*, mapping: ChannelMapping, course) -> tuple[dict, list[str]]:
    """What `mapping` would send for `course`, and its gaps, without
    delivering anything."""

    from api.production.services.packaging_service import build_package, package_for_channel

    return apply(mapping, package_for_channel(build_package(course), mapping.channel))


def mappings_queryset(*, channel: str | None = None):
    queryset = ChannelMapping.objects.select_related("created_by").order_by("channel", "-version")
    if channel:
        queryset = queryset.filter(channel=channel)
    return queryset


def get_mapping(*, mapping_id) -> ChannelMapping:
    mapping = ChannelMapping.objects.filter(pk=mapping_id).first()
    if mapping is None:
        raise exceptions.NotFound("Channel mapping not found.")
    return mapping
