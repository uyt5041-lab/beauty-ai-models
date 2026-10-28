"""Read the text team's VLM result; never run a second analysis here."""
from typing import Literal

from pydantic import ConfigDict, Field

from .contracts import Contract


class ProductAnalysis(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)

    is_cosmetic: bool
    product_type: Literal["스킨케어", "색조", "바디", "헤어", "기타"]
    container_type: Literal["병", "튜브", "펌프", "스포이드", "자", "콤팩트",
                            "스프레이", "파우치", "어플리케이터", "스틱", "기타"]
    colors: list[str]
    label_text: list[str] = Field(repr=False)
    mood: list[str]
    is_transparent: bool


class ProductAnalysisResult(Contract):
    """Failure defaults can say is_cosmetic=True: check success first.

    Raw provider errors must not be shown to users or sent to image providers.
    This is a local receiving contract, not ownership of the text module.
    """
    model_config = ConfigDict(extra="forbid", strict=True)

    analysis: ProductAnalysis
    analysis_succeeded: bool
    error: str | None = Field(default=None, repr=False, exclude=True)
