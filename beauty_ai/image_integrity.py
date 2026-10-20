from PIL import Image, ImageChops


def to_diffusers_inpaint_mask(preserve_mask: Image.Image) -> Image.Image:
    """Our white=keep convention -> Diffusers white=repaint convention.

    Alpha matte (transparency), preserve mask (protection) and inpaint mask (generation)
    are distinct assets/semantics. Do not silently substitute one for another.
    """
    return ImageChops.invert(preserve_mask.convert("L"))


def composite_approved_foreground(scene_underlay: Image.Image,
                                  approved_product_rgba: Image.Image) -> Image.Image:
    """Restore the approved foreground after generation at IDENTICAL working resolution.

    Fully opaque pixels match the approved foreground. Partial alpha pixels blend;
    transparent/glass products still require visual QA. No resizing/relighting here.
    """
    if approved_product_rgba.mode != "RGBA":
        raise ValueError("Approved foreground must contain an explicit alpha channel")
    if scene_underlay.size != approved_product_rgba.size:
        raise ValueError("Images must share a working canvas; approve resizing separately")
    base = scene_underlay.convert("RGB").convert("RGBA")
    return Image.alpha_composite(base, approved_product_rgba)
