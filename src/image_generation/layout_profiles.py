"""Versioned target geometry shared by prompts and local composition.

Product rectangles are layout targets, never detected object bounds. Story B
is a user-selected local design; no platform certification is implied.
"""
from dataclasses import dataclass
import json

LAYOUT_VERSION = "instagram-copy-hierarchy-v2"

@dataclass(frozen=True)
class LayoutProfile:
    key: str
    native: tuple[int, int]
    final: tuple[int, int]
    safe: tuple[int, int, int, int]
    product: tuple[int, int, int, int]
    text: tuple[tuple[str, tuple[int, int, int, int]], ...]

    def normalized(self, box):
        w, h = self.final
        return [value / divisor for value, divisor in zip(box, (w,h,w,h))]

    def generation_box(self, box):
        w,h=self.final; nw,nh=self.native
        return [value * numerator / denominator for value,numerator,denominator in zip(box,(nw,nh,nw,nh),(w,h,w,h))]

PROFILES = {
    "feed-3:4": LayoutProfile("feed-3:4", (1152,1536),(1080,1440), (64,64,1016,1376),(463,564,609,1262), (
        ("headline",(86,86,994,236)),("subline",(86,252,994,448)),("cta",(86,1264,994,1368)))),
    "feed-4:5": LayoutProfile("feed-4:5", (1152,1440),(1080,1350), (64,64,1016,1286),(466,418,611,1183), (
        ("headline",(86,81,994,231)),("subline",(86,247,994,410)))),
    "reels-9:16": LayoutProfile("reels-9:16", (1152,2048),(1080,1920), (65,269,1015,1152),(641,343,803,1139), (
        ("headline",(86,288,610,550)),("subline",(86,580,610,830)))),
    "story-9:16": LayoutProfile("story-9:16", (1152,2048),(1080,1920), (65,269,1015,1420),(629,493,813,1407), (
        ("headline",(86,288,610,550)),("subline",(86,580,610,830)))),
}

def get_layout(aspect_ratio: str, placement: str) -> LayoutProfile:
    try:
        return PROFILES[placement+"-"+aspect_ratio]
    except KeyError:
        raise ValueError("Choose feed with 3:4/4:5, or reels/story with 9:16") from None

def render_layout_instruction(profile: LayoutProfile) -> str:
    regions={"UI_SAFE":profile.safe,"PRODUCT_TARGET":profile.product,**dict(profile.text)}
    data={"layout_version":LAYOUT_VERSION,"placement":profile.key,
          "generation_size":list(profile.native),"final_size":list(profile.final),
          "cta_placement":"image" if profile.key=="feed-3:4" else "platform",
          "normalized_ltrb":{k:profile.normalized(v) for k,v in regions.items()},
          "generation_px_ltrb":{k:profile.generation_box(v) for k,v in regions.items()}}
    return ("배치 지시(좌상단 원점, [왼쪽,위,오른쪽,아래) 좌표):\n"
        "UI_SAFE는 제품과 문구를 담는 UI 회피 영역이며 전체를 빈 배경으로 만들라는 뜻이 아니다. "
        "제품은 PRODUCT_TARGET 안에 전체가 보이게 배치한다. "
        "아래 좌표가 있는 문구 영역만 별도 합성을 위해 제품·소품·무늬·강한 반사광 없이 차분하게 비운다. "
        "플랫폼 CTA를 쓰는 배치는 이미지에 CTA 공간을 추가하지 않는다. 광고 글자를 직접 그리지 않는다. 모든 제품 좌표는 목표 구도이며 실제 결과는 검수해야 한다. "
        "이 배치 지시는 위의 일반적인 중앙 배치 설명보다 우선한다.\n"
        +json.dumps(data,ensure_ascii=False,separators=(",",":")))


def make_placement_layout(aspect_ratio: str, placement: str, *, protected_regions=None):
    """Largest fitting local type within fixed target boxes; never truncates.

    Default protection is a TARGET, not measured product detection. Pass actual
    reviewed object bounds in addition when composing an API result.
    """
    from .composition import CompositionLayout, CompositionRect, CompositionTextRegion
    profile=get_layout(aspect_ratio,placement)
    def rect(box):
        return dict(zip(("left","top","right","bottom"),profile.normalized(box)))
    targets=[CompositionRect(**rect(profile.product)),*(protected_regions or [])]
    caps={"headline":(128,2 if placement in {"reels","story"} else 1),"subline":(128,2),"cta":(48,1)}
    regions={name:CompositionTextRegion(**rect(box),min_font_size=36,
        max_font_size=caps[name][0],max_lines=caps[name][1],vertical_align="center",
        align="center",padding=16,color="#4D1832",background=None)
        for name,box in profile.text}
    return CompositionLayout(cta_placement="image" if profile.key=="feed-3:4" else "platform",canvas_size=profile.final,text_regions=regions,protected_regions=targets)
