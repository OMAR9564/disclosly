"""Items returned by agent tools."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class SeenItem(BaseModel):
    """A citable tool result: a chunk (`c:<id>`) or a financial fact (`f:<id>`).

    `text` is untrusted document text (chunks) or a rendered fact line; it becomes the stored
    citation excerpt. `value` is set for facts only.
    """

    model_config = ConfigDict(frozen=True)

    cite_id: str
    text: str
    source: dict[str, Any]
    value: Decimal | None = None
