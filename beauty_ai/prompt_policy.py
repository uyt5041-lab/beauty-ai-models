"""Immutable supplemental prompt policies, pinned into new image requests."""

WATERMARK_POLICY_ID = "exclude-v1"
WATERMARK_POLICY_TEXT = (
    "WATERMARK POLICY — exclude-v1\n"
    "Always output an image without visible watermarks. Do not add watermarks, "
    "stock-photo overlays, creator signatures, platform marks or attribution badges. "
    "Remove any watermark overlay from the input photographs rather than copying "
    "it into the result. Preserve the real product's printed brand logo, label text "
    "and packaging artwork; these are not watermark overlays."
)


def apply_watermark_policy(text: str, policy: str | None) -> str:
    """Do not reinterpret historical requests without this explicit policy."""
    if policy is None or WATERMARK_POLICY_TEXT in text:
        return text
    if policy != WATERMARK_POLICY_ID:
        raise ValueError("Unsupported watermark policy")
    return text + "\n\n" + WATERMARK_POLICY_TEXT
