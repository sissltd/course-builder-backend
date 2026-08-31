"""Renders the /documentation payload as a formatted PDF.

Same content, different medium: this consumes exactly the dict that
documentation_service.build_documentation returns, so the PDF can never
say something the JSON does not. There is no second copy of the prose.

The layout is deliberately plain - a title page, a contents list, then
one section per top-level key, with tables for anything tabular and
monospaced blocks for anything that must be copy-pasted verbatim (curl
commands, code samples, JSON bodies). reportlab is pure Python, so this
adds no system-level build dependency to the image.
"""

import json
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from xml.sax.saxutils import escape

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - (2 * PAGE_MARGIN)

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5f6b7a")
ACCENT = colors.HexColor("#1f4e79")
RULE = colors.HexColor("#d5dbe3")
CODE_BG = colors.HexColor("#f4f6f8")

SECTION_TITLES = {
    "meta": "About This Document",
    "api": "API At A Glance",
    "your_account": "Your Account",
    "quickstart": "Quickstart",
    "integration_flow": "The Integration Flow, End To End",
    "authentication": "Authentication",
    "reference_scheme": "The Reference Scheme",
    "submission_lifecycle": "Submission Lifecycle",
    "deduplication": "Deduplication",
    "plan_and_payouts": "Plan And Payouts",
    "endpoints": "Endpoint Reference",
    "webhooks": "Webhooks",
    "errors": "Errors",
    "rate_limits": "Rate Limits",
    "pagination": "Pagination",
    "go_live_checklist": "Go-Live Checklist",
    "faq": "FAQ",
}

# Sections that read better starting on a fresh page.
PAGE_BREAK_BEFORE = {"endpoints", "webhooks", "errors"}


def build_documentation_pdf(documentation: dict, *, account) -> bytes:
    """Render `documentation` to PDF bytes."""

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN + (6 * mm),
        title="MIE Developer Integration Reference",
        author=documentation.get("api", {}).get("name", "MIE"),
        subject=f"Integration reference for {account.email}",
    )

    styles = _styles()
    story = _cover(documentation, account, styles)
    story += _contents(documentation, styles)

    for key, title in SECTION_TITLES.items():
        if key not in documentation:
            continue
        if key in PAGE_BREAK_BEFORE:
            story.append(PageBreak())
        story.append(Paragraph(title, styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.8, color=RULE, spaceAfter=8))
        story += _render_section(key, documentation[key], styles)

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def filename_for(account) -> str:
    """Stable, filesystem-safe attachment name for this account."""

    handle = account.email.split("@")[0]
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in handle)
    return f"mie-integration-reference-{safe}.pdf"


# ── Styles and page furniture ────────────────────────────────────────


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "MieTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=31,
            textColor=ACCENT,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "MieSubtitle",
            parent=base["Normal"],
            fontSize=12,
            leading=17,
            textColor=MUTED,
            spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "MieH1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=ACCENT,
            spaceBefore=16,
            spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "MieH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=INK,
            spaceBefore=11,
            spaceAfter=3,
        ),
        "h3": ParagraphStyle(
            "MieH3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=MUTED,
            spaceBefore=8,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "MieBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.5,
            textColor=INK,
            spaceAfter=5,
        ),
        "bullet": ParagraphStyle(
            "MieBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.5,
            textColor=INK,
            leftIndent=11,
            bulletIndent=2,
            spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "MieCode",
            parent=base["Code"],
            fontName="Courier",
            fontSize=7.8,
            leading=10.5,
            textColor=INK,
            backColor=CODE_BG,
            borderPadding=6,
            leftIndent=2,
            spaceBefore=3,
            spaceAfter=7,
        ),
        "cell": ParagraphStyle(
            "MieCell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.2,
            leading=11,
            textColor=INK,
            spaceAfter=0,
        ),
        "cell_head": ParagraphStyle(
            "MieCellHead",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=11,
            textColor=colors.white,
            spaceAfter=0,
        ),
        "cell_key": ParagraphStyle(
            "MieCellKey",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.2,
            leading=11,
            textColor=INK,
            spaceAfter=0,
        ),
    }


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        PAGE_MARGIN,
        12 * mm,
        "MIE Developer Integration Reference - generated from live server constants",
    )
    canvas.drawRightString(A4[0] - PAGE_MARGIN, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _cover(documentation: dict, account, styles: dict) -> list:
    meta = documentation.get("meta", {})
    api = documentation.get("api", {})
    return [
        Spacer(1, 30 * mm),
        Paragraph("MIE Developer<br/>Integration Reference", styles["title"]),
        Paragraph(
            _text(meta.get("read_this_if", "")),
            styles["subtitle"],
        ),
        HRFlowable(width="100%", thickness=1, color=ACCENT, spaceAfter=14),
        _kv_table(
            [
                ("Account", account.email),
                ("Account status", account.status),
                ("Plan", account.plan_type),
                ("API base URL", api.get("base_url", "")),
                ("Document version", meta.get("documentation_version", "")),
                ("Generated at", meta.get("generated_at", "")),
                ("Support", api.get("support_email", "")),
            ],
            styles,
        ),
        Spacer(1, 10 * mm),
        Paragraph(_text(meta.get("generated_from", "")), styles["body"]),
        PageBreak(),
    ]


def _contents(documentation: dict, styles: dict) -> list:
    present = [
        (key, title) for key, title in SECTION_TITLES.items() if key in documentation
    ]
    rows = [
        [
            Paragraph(str(index), styles["cell_key"]),
            Paragraph(_text(title), styles["cell"]),
        ]
        for index, (_key, title) in enumerate(present, start=1)
    ]
    table = Table(rows, colWidths=[12 * mm, CONTENT_WIDTH - (12 * mm)], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [
        Paragraph("Contents", styles["h1"]),
        HRFlowable(width="100%", thickness=0.8, color=RULE, spaceAfter=8),
        table,
        PageBreak(),
    ]


# ── Section dispatch ─────────────────────────────────────────────────


def _render_section(key: str, value, styles: dict) -> list:
    """Route a top-level section to the renderer that suits its shape."""

    if key == "quickstart":
        return _render_quickstart(value, styles)
    if key == "endpoints":
        return _render_endpoints(value, styles)
    if key == "webhooks":
        return _render_webhooks(value, styles)
    if key == "integration_flow":
        return _render_flow(value, styles)
    if key == "go_live_checklist":
        return _render_checklist(value, styles)
    if key == "faq":
        return _render_faq(value, styles)
    return _render_generic(value, styles)


def _render_quickstart(section: dict, styles: dict) -> list:
    story = [Paragraph(_text(section.get("goal", "")), styles["body"])]
    for step in section.get("steps", []):
        block = [
            Paragraph(
                f"Step {step['step']} &mdash; {_text(step['title'])}", styles["h2"]
            ),
            Paragraph(_text(step["detail"]), styles["body"]),
        ]
        if step.get("curl"):
            block.append(_code(step["curl"], styles))
        story.append(KeepTogether(block))
    if section.get("common_first_mistakes"):
        story.append(Paragraph("Common first mistakes", styles["h2"]))
        story += _bullets(section["common_first_mistakes"], styles)
    return story


def _render_flow(stages: list, styles: dict) -> list:
    story = []
    for stage in stages:
        rows = [
            ("Actor", stage.get("actor")),
            ("What happens", stage.get("what_happens")),
            ("Your move", stage.get("your_move")),
            ("Authenticated?", "Yes" if stage.get("you_can_authenticate") else "No"),
            ("Webhook fired", stage.get("webhook_fired") or "None"),
        ]
        story.append(
            KeepTogether(
                [
                    Paragraph(
                        f"Stage {stage['stage']} &mdash; {_text(stage['name'])}",
                        styles["h2"],
                    ),
                    _kv_table(rows, styles),
                    Spacer(1, 5),
                ]
            )
        )
    return story


def _render_endpoints(endpoints: list, styles: dict) -> list:
    story = []
    for endpoint in endpoints:
        story.append(
            Paragraph(
                f"{_text(endpoint['name'])} "
                f"<font color='#5f6b7a'>&mdash; {endpoint['method']} "
                f"{_text(endpoint['path'])}</font>",
                styles["h2"],
            )
        )
        story.append(Paragraph(_text(endpoint.get("purpose", "")), styles["body"]))
        story.append(
            _kv_table(
                [
                    ("Auth", endpoint.get("auth")),
                    ("Rate limit", endpoint.get("rate_limit")),
                    ("Success status", endpoint.get("success_status")),
                    ("Content type", endpoint.get("response_content_type")),
                ],
                styles,
            )
        )

        if endpoint.get("request_body"):
            story.append(Paragraph("Request body", styles["h3"]))
            story.append(_dict_table(endpoint["request_body"], styles))
        if endpoint.get("query_parameters"):
            story.append(Paragraph("Query parameters", styles["h3"]))
            story.append(_records_table(endpoint["query_parameters"], styles))
        if endpoint.get("request_example"):
            story.append(Paragraph("Request example", styles["h3"]))
            story.append(_code(_json(endpoint["request_example"]), styles))
        if endpoint.get("response_example"):
            story.append(Paragraph("Response example", styles["h3"]))
            story.append(_code(_json(endpoint["response_example"]), styles))
        if endpoint.get("response_fields"):
            story.append(Paragraph("Response fields", styles["h3"]))
            story.append(_dict_table(endpoint["response_fields"], styles))
        if endpoint.get("errors"):
            story.append(Paragraph("Errors", styles["h3"]))
            story.append(_records_table(endpoint["errors"], styles))
        if endpoint.get("notes"):
            story.append(Paragraph("Notes", styles["h3"]))
            story += _bullets(endpoint["notes"], styles)
        story.append(Spacer(1, 7))
    return story


def _render_webhooks(section: dict, styles: dict) -> list:
    story = [
        Paragraph(_text(section.get("why", "")), styles["body"]),
        _kv_table([("Your endpoint", section.get("your_endpoint"))], styles),
    ]

    for key, heading in (
        ("delivery", "Delivery"),
        ("headers", "Headers"),
        ("envelope", "Event envelope"),
        ("verification", "Signature verification"),
        ("retries", "Retries"),
        ("idempotency", "Idempotency"),
        ("account_state_effects", "Account status effects"),
        ("receiver_requirements", "What your receiver must do"),
    ):
        if key not in section:
            continue
        story.append(Paragraph(heading, styles["h2"]))
        story += _render_generic(section[key], styles)

    if section.get("events"):
        story.append(PageBreak())
        story.append(Paragraph("Event catalogue", styles["h2"]))
        for event in section["events"]:
            story.append(
                KeepTogether(
                    [
                        Paragraph(_text(event["type"]), styles["h3"]),
                        Paragraph(_text(event["fires_when"]), styles["body"]),
                        _kv_table(
                            [
                                (
                                    "Resulting status",
                                    event.get("resulting_status") or "unchanged",
                                ),
                                (
                                    "Extra fields",
                                    ", ".join(event["extra_submission_fields"]) or "none",
                                ),
                            ],
                            styles,
                        ),
                        _code(_json(event["sample_body"]), styles),
                    ]
                )
            )
    return story


def _render_checklist(items: list, styles: dict) -> list:
    rows = [("Check", "Why it matters")] + [
        (item["item"], item["why"]) for item in items
    ]
    return [_grid(rows, styles, widths=[0.45, 0.55])]


def _render_faq(items: list, styles: dict) -> list:
    story = []
    for entry in items:
        story.append(
            KeepTogether(
                [
                    Paragraph(_text(entry["question"]), styles["h3"]),
                    Paragraph(_text(entry["answer"]), styles["body"]),
                ]
            )
        )
    return story


def _render_generic(value, styles: dict, depth: int = 0) -> list:
    """Render an arbitrary JSON-ish value the way its shape suggests."""

    if isinstance(value, dict):
        return _render_dict(value, styles, depth)
    if isinstance(value, list):
        return _render_list(value, styles, depth)
    return [Paragraph(_text(value), styles["body"])]


def _render_dict(value: dict, styles: dict, depth: int) -> list:
    """Scalars collapse into one label/value table; everything with real
    structure gets its own sub-heading below it."""

    inline = {
        key: item for key, item in value.items() if _renders_inline(item)
    }
    story = []
    if inline:
        story.append(_dict_table(inline, styles))
    for key, item in value.items():
        if key in inline:
            continue
        story.append(Paragraph(_humanise(key), styles["h3" if depth else "h2"]))
        if _is_code(item):
            story.append(_code(item, styles))
        else:
            story += _render_generic(item, styles, depth + 1)
    return story


def _render_list(value: list, styles: dict, depth: int) -> list:
    if not value:
        return []
    if all(not isinstance(item, (dict, list)) for item in value):
        return _bullets(value, styles)
    if all(isinstance(item, dict) for item in value):
        return [_records_table(value, styles)]
    story = []
    for item in value:
        story += _render_generic(item, styles, depth + 1)
    return story


MAX_INLINE_LENGTH = 220
"""Above this, a value gets its own block rather than a table cell."""


def _is_code(value) -> bool:
    """Multi-line strings are source or payloads - never table cells."""

    return isinstance(value, str) and "\n" in value


def _renders_inline(value) -> bool:
    """Whether a value belongs in a label/value table row.

    Anything long, multi-line, or structured is pulled out into its own
    block so code stays monospaced and long lists stay readable as
    bullets instead of one comma-joined paragraph.
    """

    if _is_code(value):
        return False
    if isinstance(value, str):
        return len(value) <= MAX_INLINE_LENGTH
    if isinstance(value, dict):
        return False
    if isinstance(value, list):
        return _is_scalar_list(value) and len(_text(value)) <= MAX_INLINE_LENGTH
    return True


# ── Table and text primitives ────────────────────────────────────────


def _kv_table(rows, styles: dict) -> Table:
    """Two-column label/value table; rows with empty values are dropped."""

    data = [
        [
            Paragraph(_humanise(str(label)), styles["cell_key"]),
            Paragraph(_text(value), styles["cell"]),
        ]
        for label, value in rows
        if value not in (None, "", [])
    ]
    if not data:
        data = [[Paragraph("-", styles["cell_key"]), Paragraph("-", styles["cell"])]]

    label_width = 38 * mm
    table = Table(
        data, colWidths=[label_width, CONTENT_WIDTH - label_width], hAlign="LEFT"
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _dict_table(value: dict, styles: dict) -> Table:
    return _kv_table(list(value.items()), styles)


def _records_table(records: list, styles: dict) -> Table:
    """A table over a list of dicts, using the union of keys as columns."""

    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)

    rows = [tuple(_humanise(column) for column in columns)]
    for record in records:
        rows.append(tuple(record.get(column, "") for column in columns))
    return _grid(rows, styles)


def _grid(rows, styles: dict, widths=None) -> Table:
    """A headed grid. `rows[0]` is the header; `widths` are fractions."""

    header, *body = rows
    if widths is None:
        widths = _weigh_columns(rows)
    col_widths = [CONTENT_WIDTH * fraction for fraction in widths]

    data = [[Paragraph(_text(cell), styles["cell_head"]) for cell in header]]
    data += [
        [Paragraph(_text(cell), styles["cell"]) for cell in row] for row in body
    ]

    table = Table(data, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
            ]
        )
    )
    return table


def _bullets(items, styles: dict) -> list:
    return [
        Paragraph(_text(item), styles["bullet"], bulletText="•") for item in items
    ]


def _code(text: str, styles: dict) -> Paragraph:
    """A monospaced block that keeps its line breaks and indentation.

    Only leading whitespace becomes non-breaking - interior spaces stay
    breakable so an over-long line wraps between words rather than being
    chopped mid-token.
    """

    lines = []
    for line in str(text).split("\n"):
        stripped = line.lstrip(" ")
        indent = "&nbsp;" * (len(line) - len(stripped))
        lines.append(indent + escape(stripped))
    return Paragraph("<br/>".join(lines), styles["code"])


def _weigh_columns(rows) -> list[float]:
    """Column width fractions proportional to the content each must hold.

    Each column gets a share of the width based on its longest cell, so a
    column of HTTP status codes stops claiming the same space as a column
    of prose. Clamped so no column collapses or dominates.
    """

    column_count = len(rows[0])
    longest = [
        max(len(_text(row[index])) for row in rows) for index in range(column_count)
    ]
    floor = 0.10
    weights = [max(length, 1) ** 0.6 for length in longest]
    total = sum(weights)
    shares = [max(weight / total, floor) for weight in weights]
    scale = sum(shares)
    return [share / scale for share in shares]


def _json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _is_scalar_list(value) -> bool:
    return isinstance(value, list) and all(
        not isinstance(item, (dict, list)) for item in value
    )


def _text(value) -> str:
    """XML-escaped display text for any JSON value."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return escape(", ".join(str(item) for item in value))
    return escape(str(value))


ACRONYMS = {
    "api": "API",
    "faq": "FAQ",
    "hmac": "HMAC",
    "http": "HTTP",
    "https": "HTTPS",
    "id": "ID",
    "ids": "IDs",
    "ip": "IP",
    "iso": "ISO",
    "json": "JSON",
    "openapi": "OpenAPI",
    "pdf": "PDF",
    "tls": "TLS",
    "url": "URL",
    "urls": "URLs",
    "utc": "UTC",
    "uuid": "UUID",
}


def _humanise(key: str) -> str:
    """Turn a snake_case key into a display label, respecting acronyms.

    A label that is already written for humans (it has a space or an
    internal capital) is passed through untouched.
    """

    text = str(key).strip()
    if " " in text or any(char.isupper() for char in text[1:]):
        return escape(text)

    words = text.replace("_", " ").split()
    rendered = [
        ACRONYMS.get(word.lower(), word.lower() if index else word.capitalize())
        for index, word in enumerate(words)
    ]
    return escape(" ".join(rendered))
