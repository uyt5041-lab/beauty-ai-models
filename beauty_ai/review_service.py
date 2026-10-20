from __future__ import annotations

import re

from .contracts import (AdOptions, CopyBundle, ProductFacts, ReviewIssue, ReviewReport,
                        RulePack, content_hash)
from .errors import GatewayError
from .ports import TextGateway


def review_snapshot_hash(facts: ProductFacts, copy: CopyBundle, options: AdOptions,
                         *, copy_revision: int, rules: RulePack,
                         prompt_version: str, model: str) -> str:
    return content_hash({
        "product": facts.model_dump(mode="json"), "copy": copy.model_dump(),
        "options": options.model_dump(), "copy_revision": copy_revision,
        "rule_pack_hash": content_hash(rules), "rule_pack_version": rules.version,
        "prompt_version": prompt_version, "review_model": model,
    })


def scan_rules(copy: CopyBundle, pack: RulePack) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    for rule in pack.rules:
        # Invalid rules are configuration errors, never a clean review.
        pattern = re.compile(rule.pattern, re.IGNORECASE)
        for field, text in copy.text_fields().items():
            for match in pattern.finditer(text):
                if match.group(0):
                    issues.append(ReviewIssue(
                        field=field, quote=match.group(0), severity=rule.severity,
                        reason=rule.reason, suggested_text=None, rule_ids=[rule.rule_id],
                    ))
    return issues


class ReviewService:
    """No image dependency: copy editing cannot accidentally invoke GPU inference."""
    def __init__(self, text: TextGateway):
        self.text = text

    async def review(self, facts: ProductFacts, copy: CopyBundle, options: AdOptions,
                     *, copy_revision: int, rules: RulePack) -> ReviewReport:
        if copy_revision < 1:
            raise ValueError("Copy revision must be positive")
        # Freeze independent input copies across awaits; DB revision must still be checked at commit.
        facts, copy, options, rules = (x.model_copy(deep=True) for x in (facts, copy, options, rules))
        snapshot = review_snapshot_hash(
            facts, copy, options, copy_revision=copy_revision, rules=rules,
            prompt_version=self.text.review_prompt_version, model=self.text.review_model,
        )
        base = dict(snapshot_hash=snapshot, copy_revision=copy_revision,
                    product_revision=facts.revision, rule_pack_version=rules.version,
                    prompt_version=self.text.review_prompt_version,
                    configured_review_model=self.text.review_model)
        try:
            deterministic = scan_rules(copy, rules)
        except re.error:
            return ReviewReport(**base, status="unavailable", issues=[],
                                uncertainties=["INVALID_RULE_CONFIGURATION"], meta=None)
        if rules.status != "reviewed":
            return ReviewReport(**base, status="unavailable", issues=deterministic,
                                uncertainties=["RULE_PACK_NOT_REVIEWED"], meta=None)
        try:
            response = await self.text.review_copy(facts, copy, options, rules, deterministic)
            known_ids = {r.rule_id for r in rules.rules}
            fields = copy.text_fields()
            for issue in response.data.issues:
                if (not issue.quote or issue.quote not in fields[issue.field]
                        or not set(issue.rule_ids).issubset(known_ids)):
                    raise GatewayError("INVALID_REVIEW_REFERENCES", "Untraceable finding")
        except GatewayError as exc:
            return ReviewReport(**base, status="unavailable", issues=deterministic,
                                uncertainties=[exc.code], meta=None)
        # Keep deterministic flags, even when the model returns no issues.
        issues = deterministic + response.data.issues
        uncertainties = list(response.data.uncertainties)
        if facts.functional_status == "unconfirmed":
            uncertainties.append("FUNCTIONAL_STATUS_UNCONFIRMED")
        status = ("blocked" if any(x.severity == "block" for x in issues) else
                  "warnings" if issues or uncertainties else "no_flags")
        return ReviewReport(**base, status=status, issues=issues,
                            uncertainties=uncertainties, meta=response.meta)


def require_current_review(report: ReviewReport, expected_hash: str) -> None:
    """Only a SERVER-STORED report is accepted; never trust a client-supplied report."""
    if report.snapshot_hash != expected_hash:
        raise GatewayError("STALE_REVIEW", "Copy, product facts or policy changed; review again.")
    if report.status in ("blocked", "unavailable"):
        raise GatewayError("REVIEW_NOT_READY", "Review requires resolution before approved export flow.")
    # 'warnings' requires a separate human acknowledgement policy. No legal approval is implied.
    if report.status == "warnings":
        raise GatewayError("HUMAN_REVIEW_REQUIRED", "Resolve or explicitly acknowledge warnings.")
