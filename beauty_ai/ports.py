from typing import Protocol

from .contracts import (AdOptions, CampaignDraft, CopyBundle, LLMReview, ModelResult,
                        ProductFacts, ProductObservation, ReviewIssue, RulePack)


class TextGateway(Protocol):
    review_model: str
    review_prompt_version: str

    async def analyze_product(self, images: list[str]) -> ModelResult[ProductObservation]: ...

    async def draft_campaign(self, facts: ProductFacts, options: AdOptions,
                             count: int = 3) -> ModelResult[CampaignDraft]: ...

    async def review_copy(self, facts: ProductFacts, copy: CopyBundle, options: AdOptions,
                          rules: RulePack,
                          deterministic_findings: list[ReviewIssue]) -> ModelResult[LLMReview]: ...

    async def rewrite_copy(self, facts: ProductFacts, copy: CopyBundle, options: AdOptions,
                           issues: list[ReviewIssue]) -> ModelResult[CopyBundle]: ...
