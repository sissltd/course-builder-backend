"""OpenAPI pieces for the Studio delivery headers (studio.receipt.v1).

The rules live in studio_receipt_service; these only document the headers
the same way on every endpoint that takes them.
"""

from drf_spectacular.utils import OpenApiParameter

from api.mie.services.studio_receipt_service import (
    CONTENT_REVISION_HEADER,
    DELIVERY_ID_HEADER,
    PAYLOAD_SHA256_HEADER,
)

_STUDIO_ONLY = (
    "Studio account only; ignored for every other caller. Sending "
    f"{DELIVERY_ID_HEADER} asks for a signed studio.receipt.v1 callback once "
    "the delivery is stored."
)

DELIVERY_ID_PARAMETER = OpenApiParameter(
    name=DELIVERY_ID_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        f"{_STUDIO_ONLY} The Studio job UUID. A retry with the same id and "
        "body replays the stored result; the same id with another body is 409."
    ),
)

PAYLOAD_SHA256_PARAMETER = OpenApiParameter(
    name=PAYLOAD_SHA256_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        f"Required with {DELIVERY_ID_HEADER}. Lowercase hex SHA-256 of the "
        "exact request body bytes; a mismatch is 400."
    ),
)

CONTENT_REVISION_PARAMETER = OpenApiParameter(
    name=CONTENT_REVISION_HEADER,
    type=int,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        f"Required with {DELIVERY_ID_HEADER}. The revision being sent; it "
        "must equal the body's `revision`."
    ),
)

TOPIC_DELIVERY_PARAMETERS = [DELIVERY_ID_PARAMETER, PAYLOAD_SHA256_PARAMETER]
REVIEW_DELIVERY_PARAMETERS = [*TOPIC_DELIVERY_PARAMETERS, CONTENT_REVISION_PARAMETER]
