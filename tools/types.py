"""Items returned by agent tools."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class SeenItem(BaseModel):
    """A citable tool result: a chunk (`c:<id>`) or a financial fact (`f:<id>`).

    `text` is untrusted document text (chunks) or a rendered fact line; it becomes the stored
    citation excerpt. `value` is set for facts only. `scale` is what the item's bare amounts
    are counted in (a fact's unit, or a KAP report's "Sunum Para Birimi: 1.000.000 TL");
    `unit_note` is that unit as the document states it, shown to the model.
    """

    model_config = ConfigDict(frozen=True)

    cite_id: str
    text: str
    source: dict[str, Any]
    value: Decimal | None = None
    scale: Decimal | None = None
    unit_note: str | None = None


class SandboxUnavailable(Exception):
    """The calculation sandbox service could not be reached or has no Docker (transient)."""
