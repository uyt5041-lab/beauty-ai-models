"""Local Korean copy composition; no model calls, JobStore or approval API.

Serving must authenticate the caller and supply its approved image and full
AdCopyOutput snapshot. Hash checks bind those bytes/content; a supplied hash is
not proof of human approval. The full copy hash uses PR #4 canonical JSON.

Text regions must be explicit and inside the outer 5% margin. Those margins
are not a predefined text box. Optional protected regions are caller-declared
product bounds, not automatic product detection. Human layout QA is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import time
import unicodedata
from typing import Annotated, Literal

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version
from pydantic import ConfigDict, Field, JsonValue, model_validator

from .ad_resize import FINAL_SIZE, resize_ad_png
from .contracts import Contract, CopyRevision, content_hash
from .errors import GatewayError

VERSION = "korean-composition-v1"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CopyField = Literal["headline", "subline", "cta"]


class CompositionRect(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)
    left: float = Field(ge=0, le=1, allow_inf_nan=False)
    top: float = Field(ge=0, le=1, allow_inf_nan=False)
    right: float = Field(ge=0, le=1, allow_inf_nan=False)
    bottom: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def positive_area(self):
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("A region must have positive area")
        return self

    def pixels(self) -> tuple[int, int, int, int]:
        return (math.ceil(self.left * 1080), math.ceil(self.top * 1350),
                math.floor(self.right * 1080), math.floor(self.bottom * 1350))


class CompositionTextRegion(CompositionRect):
    min_font_size: int = Field(default=24, ge=18, le=128)
    max_font_size: int = Field(default=64, ge=18, le=128)
    padding: int = Field(default=16, ge=0, le=64)
    align: Literal["left", "center", "right"] = "left"
    color: str = Field(default="#151515", pattern=r"^#[0-9a-fA-F]{6}$")
    background: str | None = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def usable_text_region(self):
        if (min(self.left, self.top) < .05 or max(self.right, self.bottom) > .95
                or self.min_font_size > self.max_font_size):
            raise ValueError("Text must respect 5% margins and font size bounds")
        l, t, r, b = self.pixels()
        if r - l <= 2 * self.padding or b - t <= 2 * self.padding:
            raise ValueError("Text region has no usable pixels")
        return self


def _overlap(a: CompositionRect, b: CompositionRect) -> bool:
    return (max(a.left, b.left) < min(a.right, b.right)
            and max(a.top, b.top) < min(a.bottom, b.bottom))


class CompositionLayout(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)
    text_regions: dict[CopyField, CompositionTextRegion] = Field(min_length=1, max_length=3)
    protected_regions: list[CompositionRect] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def disjoint_regions(self):
        regions = list(self.text_regions.values())
        for i, region in enumerate(regions):
            if any(_overlap(region, other) for other in regions[i+1:] + self.protected_regions):
                raise ValueError("Text regions overlap another text or protected region")
        return self


class CompositionInput(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    image_bytes: bytes = Field(min_length=1, max_length=32*1024*1024, repr=False)
    image_sha256: Digest
    copy_revision: CopyRevision
    copy_hash: Digest
    # Preserve the WHOLE owner output, including plan. Do not hash only ad_copy.
    copy_output: dict[str, JsonValue] = Field(repr=False)
    layout: CompositionLayout


@dataclass(frozen=True)
class ComposedAd:
    image_bytes: bytes = field(repr=False)
    caption_bytes: bytes = field(repr=False)
    caption: str = field(repr=False)
    hashtags: tuple[str, ...] = field(repr=False)
    mime_type: str
    width: int
    height: int
    sha256: str
    source_image_sha256: str
    copy_hash: str
    copy_revision: CopyRevision
    job_id: str
    receipt: dict


def _reject(code: str) -> GatewayError:
    return GatewayError(code, "합성 입력·폰트·배치 조건을 확인해 주세요. 원문은 자르지 않습니다.")


def _copy_fields(body: CompositionInput) -> tuple[dict[str, str], str, list[str]]:
    if sha256(body.image_bytes).hexdigest() != body.image_sha256:
        raise _reject("COMPOSITION_IMAGE_HASH_MISMATCH")
    try:
        copy = body.copy_output
        if content_hash(copy) != body.copy_hash:
            raise _reject("COMPOSITION_COPY_HASH_MISMATCH")
        if not isinstance(copy.get("plan"), dict):
            raise ValueError
        ad, caption, tags = copy["ad_copy"], copy["caption"], copy["hashtags"]
        if (not isinstance(ad, dict) or set(ad) != {"headline", "subline", "cta"}
                or any(not isinstance(v, str) for v in ad.values())
                or not ad["headline"].strip()
                or not isinstance(caption, str) or len(caption) > 10000
                or not isinstance(tags, list) or len(tags) > 50
                or any(not isinstance(t, str) or len(t) > 200 for t in tags)):
            raise ValueError
        if any(len(ad[k]) > limit for k, limit in [("headline",600),("subline",2000),("cta",600)]):
            raise ValueError
        for text in [*ad.values(), caption, *tags]:
            text.encode("utf-8")
            if any(unicodedata.category(c).startswith("C") and c != "\n" for c in text):
                raise ValueError
        if any(text and name not in body.layout.text_regions for name, text in ad.items()):
            raise _reject("COMPOSITION_LAYOUT_MISSING_FIELD")
        return ad, caption, tags
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise _reject("COMPOSITION_COPY_INVALID") from None


def _font_data(path: str | Path, index: int, texts: list[str]) -> tuple[bytes, str]:
    try:
        if type(index) is not int or not 0 <= index <= 32:
            raise ValueError
        font_path = Path(path)
        if not 0 < font_path.stat().st_size <= 64*1024*1024:
            raise ValueError
        data = font_path.read_bytes()
        with TTFont(BytesIO(data), fontNumber=index, lazy=True) as font:
            cmap = font.getBestCmap() or {}
        required = {ord(c) for text in texts for c in text if c != "\n"}
        if not required <= cmap.keys() or any(cmap[c] == ".notdef" for c in required):
            raise _reject("COMPOSITION_GLYPH_MISSING")
        return data, sha256(data).hexdigest()
    except (OSError, ValueError, TypeError, TTLibError):
        raise _reject("COMPOSITION_FONT_UNAVAILABLE") from None


def _lines(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str] | None:
    """Soft wrap without dropping characters, spaces or explicit empty lines."""
    result = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            candidate = line + char
            bbox = font.getbbox(candidate)
            if max(font.getlength(candidate), bbox[2]-bbox[0]) > width:
                if not line:
                    return None
                result.append(line)
                line = char
                box = font.getbbox(line)
                if max(font.getlength(line), box[2]-box[0]) > width:
                    return None
            else:
                line = candidate
        result.append(line)
    return result


def _fit(text: str, region: CompositionTextRegion, data: bytes, index: int):
    l,t,r,b = region.pixels()
    width, height = r-l-2*region.padding, b-t-2*region.padding
    low, high, best = region.min_font_size, region.max_font_size, None
    while low <= high:
        size = (low+high)//2
        font = ImageFont.truetype(BytesIO(data), size, index=index)
        lines = _lines(text, font, width)
        boxes = [font.getbbox(line) for line in lines] if lines else []
        pitch = max(math.ceil(sum(font.getmetrics())*1.1),
                    max((box[3]-box[1] for box in boxes), default=0))
        used = (len(boxes)-1)*pitch + (boxes[-1][3]-boxes[-1][1] if boxes else 0)
        if lines is not None and used <= height:
            best = font, lines, boxes, pitch, size
            low = size+1
        else:
            high = size-1
    if best is None:
        raise _reject("COMPOSITION_TEXT_OVERFLOW")
    return best


def compose_ad_png(body: CompositionInput, *, font_path: str | Path,
                   font_index: int = 0) -> ComposedAd:
    """Render an approved snapshot locally. Caller owns approval/authentication.

    font_path is trusted server configuration, never an HTTP user-supplied path.
    No fallback font, truncation, model retry or file write is performed.
    """
    started = time.monotonic()
    body = CompositionInput.model_validate(body.model_dump(warnings=False))
    ad, caption, tags = _copy_fields(body)
    data, font_hash = _font_data(font_path, font_index, list(ad.values()))
    baseline = resize_ad_png(body.image_bytes)
    prepared = {}
    try:
        for name, text in ad.items():
            if text:
                prepared[name] = _fit(text, body.layout.text_regions[name], data, font_index)
    except (OSError, ValueError):
        raise _reject("COMPOSITION_FONT_UNAVAILABLE") from None
    placements = {}
    with Image.open(BytesIO(baseline.image_bytes)) as source:
        source.load()
        overlay = Image.new("RGBA", FINAL_SIZE)
        draw = ImageDraw.Draw(overlay)
        for name, (font, lines, boxes, pitch, size) in prepared.items():
            region = body.layout.text_regions[name]
            l,t,r,b = region.pixels()
            if region.background:
                draw.rectangle((l,t,r-1,b-1), fill=region.background)
            ink = []
            for i, (line, box) in enumerate(zip(lines, boxes)):
                width = box[2]-box[0]
                x = l+region.padding
                if region.align == "center": x = l+(r-l-width)//2
                if region.align == "right": x = r-region.padding-width
                y = t+region.padding+i*pitch
                draw.text((x-box[0],y-box[1]), line, fill=region.color, font=font)
                ink.append([x,y,x+width,y+box[3]-box[1]])
            placements[name] = {"region": [l,t,r,b], "font_size":size,
                                "line_count":len(lines), "ink_boxes":ink,
                                "text_sha256":sha256(ad[name].encode("utf-8")).hexdigest()}
        final = Image.alpha_composite(source.convert("RGBA"), overlay)
        if source.mode == "RGB": final = final.convert("RGB")
        output = BytesIO()
        final.save(output, "PNG", compress_level=6,
                   **({"icc_profile":source.info["icc_profile"]} if source.info.get("icc_profile") else {}))
    image_bytes = output.getvalue()
    with Image.open(BytesIO(image_bytes)) as decoded:
        decoded.load()
        if decoded.format != "PNG" or decoded.size != FINAL_SIZE:
            raise _reject("COMPOSITION_OUTPUT_INVALID")
        width,height = decoded.size
    caption_text = caption + ("\n\n" if caption and tags else "") + " ".join(tags)
    fingerprint = body.model_dump(exclude={"image_bytes"})
    fingerprint.update(renderer=VERSION,font_sha256=font_hash,font_index=font_index)
    receipt = {"renderer":VERSION,"pillow_version":pillow_version,
               "font_sha256":font_hash,"font_index":font_index,
               "input_sha256":content_hash(fingerprint),"baseline_sha256":baseline.sha256,
               "resize_method":baseline.method,"placements":placements,
               "protected_region_check":bool(body.layout.protected_regions),
               "needs_manual_check":True,"provider_calls":0,"provider_cost_usd":"0",
               "elapsed_ms":round((time.monotonic()-started)*1000),
               "warnings":[] if body.layout.protected_regions else ["PRODUCT_REGION_NOT_PROVIDED"]}
    return ComposedAd(image_bytes,caption_text.encode("utf-8"),caption,tuple(tags),
                      "image/png",width,height,sha256(image_bytes).hexdigest(),
                      body.image_sha256,body.copy_hash,body.copy_revision,body.job_id,receipt)
