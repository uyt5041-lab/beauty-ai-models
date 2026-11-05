"""A centered-copy preset paired with the existing single safe-zone contract.

This is local layout configuration, not a generated-image mask or approval.
Call compose_ad_png with a caller-configured licensed Gothic font and product
bounds reviewed on the actual candidate. The upper safe zone does not cover CTA.
"""
from __future__ import annotations

from .backend_adapter import BackendSafeZone
from .composition import CompositionLayout, CompositionRect, CompositionTextRegion


def make_a_template(*, protected_regions: list[CompositionRect] | None = None,
                    color: str = "#151515",
                    background: str | None = None) -> tuple[BackendSafeZone, CompositionLayout]:
    """Return fresh generation/composition settings for the A centered preset.

    Coordinates use the final 1080x1350 canvas. The existing 5% outer margin is
    retained. An explicit background can improve contrast after manual review;
    it is never applied automatically. Protection covers all three copy fields.
    Copy that cannot fit at the preset minimum size is rejected by the compositor.
    """
    safe_zone = BackendSafeZone(left=.06, top=.05, right=.94, bottom=.25)
    common = dict(align="center", color=color, background=background, padding=16)
    regions = {
        "headline": CompositionTextRegion(left=.08, top=.06, right=.92, bottom=.14,
                                         min_font_size=48, max_font_size=64, **common),
        "subline": CompositionTextRegion(left=.08, top=.15, right=.92, bottom=.25,
                                        min_font_size=45, max_font_size=48, **common),
        "cta": CompositionTextRegion(left=.08, top=.85, right=.92, bottom=.94,
                                    min_font_size=48, max_font_size=54, **common),
    }
    layout = CompositionLayout(text_regions=regions,
                               protected_regions=list(protected_regions or []))
    return safe_zone, layout
