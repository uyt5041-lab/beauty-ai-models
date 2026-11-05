from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import pytest
from PIL import Image,ImageChops,ImageDraw
from pydantic import ValidationError
from .helpers import body,copy_output,layout,picture,FONT
from src.image_generation.composition import compose_ad_png, _lines
from src.image_generation.ad_resize import resize_ad_png
from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError
from src.image_generation.openai_images import OpenAIImagesAdapter

@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(OpenAIImagesAdapter,'execute',lambda *a,**k:pytest.fail('composition cannot call provider'))

def decode(data):
    im=Image.open(BytesIO(data));im.load();return im

@pytest.mark.parametrize('mode',['RGB','RGBA'])
@pytest.mark.parametrize('long_copy',[False,True])
def test_korean_png_has_ink_and_preserves_outside_regions(mode,long_copy):
    raw=picture(mode=mode);c=copy_output()
    if long_copy:
        c['ad_copy']['headline']='오늘도 나를 위한 편안한 루틴 '+ '매일의 소중한 시간을 만나보세요 '*3
        c['ad_copy']['subline']='나에게 어울리는 데일리 케어를 만나보세요. '*8
    p=body(image_bytes=raw,image_sha256=sha256(raw).hexdigest(),copy_output=c,copy_hash=content_hash(c))
    before=decode(resize_ad_png(raw).image_bytes).convert('RGBA')
    result=compose_ad_png(p,font_path=FONT);after=decode(result.image_bytes).convert('RGBA')
    assert result.mime_type=='image/png' and (result.width,result.height)==(1080,1350)
    assert sha256(raw).hexdigest()==p.image_sha256==result.source_image_sha256
    assert sha256(result.image_bytes).hexdigest()==result.sha256
    assert result.caption==c['caption'] and list(result.hashtags)==c['hashtags']
    assert result.caption_bytes.decode('utf-8')==c['caption']+'\n\n'+' '.join(c['hashtags'])
    assert result.receipt['provider_calls']==0 and result.receipt['provider_cost_usd']=='0'
    mask=Image.new('L',after.size,255);d=ImageDraw.Draw(mask)
    for name,item in result.receipt['placements'].items():
        l,t,r,b=item['region'];d.rectangle((l,t,r-1,b-1),fill=0)
        assert l>=54 and t>=68 and r<=1026 and b<=1282
        assert item['text_sha256']==sha256(c['ad_copy'][name].encode()).hexdigest()
        for x,y,right,bottom in item['ink_boxes']:
            assert l<=x<=right<=r and t<=y<=bottom<=b
        pixels=after.crop((l,t,r,b)).convert('RGB')
        assert min(pixels.getextrema()[0]) < 100 # visible dark text, not an empty panel
    diff=ImageChops.difference(before,after)
    for channel in diff.split():assert ImageChops.multiply(channel,mask).getbbox() is None
    for rect in p.layout.protected_regions:
        assert before.crop(rect.pixels()).tobytes()==after.crop(rect.pixels()).tobytes()
    assert p.image_bytes==raw and p.copy_output==c
    if long_copy:assert result.receipt['placements']['headline']['line_count']>1


def test_line_breaks_and_spaces_are_preserved():
    from PIL import ImageFont
    font=ImageFont.truetype(FONT,24)
    original='한글  두 칸\n\n둘째 문장'
    lines=_lines(original,font,1000)
    assert lines==['한글  두 칸','','둘째 문장']
    text='한글 띄어쓰기 확인 '*8
    lines=_lines(text,font,110)
    assert ''.join(lines)==text


def test_repeat_same_input_is_deterministic():
    a=compose_ad_png(body(),font_path=FONT);b=compose_ad_png(body(),font_path=FONT)
    assert a.image_bytes==b.image_bytes and a.caption_bytes==b.caption_bytes
    assert a.receipt['input_sha256']==b.receipt['input_sha256']


def test_long_copy_is_rejected_not_truncated():
    c=copy_output();c['ad_copy']['headline']='아'*600
    p=body(copy_output=c,copy_hash=content_hash(c))
    with pytest.raises(GatewayError) as e:compose_ad_png(p,font_path=FONT)
    assert e.value.code=='COMPOSITION_TEXT_OVERFLOW'
    assert p.copy_output['ad_copy']['headline']=='아'*600

@pytest.mark.parametrize('kind',['absent','corrupt','missing_glyph'])
def test_bad_font_fails_without_fallback(tmp_path,kind):
    path=tmp_path/'font.ttf';p=body()
    if kind=='corrupt':path.write_bytes(b'not a font')
    elif kind=='missing_glyph':
        c=copy_output();c['ad_copy']['headline']='지원 안 되는 문자 '+chr(0x1F9F4)
        p=body(copy_output=c,copy_hash=content_hash(c));path=Path(FONT)
    with pytest.raises(GatewayError) as e:compose_ad_png(p,font_path=path)
    assert e.value.code in {'COMPOSITION_FONT_UNAVAILABLE','COMPOSITION_GLYPH_MISSING'}


def test_no_protected_region_is_explicitly_unverified():
    l=layout();l['protected_regions']=[]
    result=compose_ad_png(body(layout=l),font_path=FONT)
    assert result.receipt['protected_region_check'] is False
    assert result.receipt['needs_manual_check'] is True
    assert result.receipt['warnings']==['PRODUCT_REGION_NOT_PROVIDED']


def test_mutated_layout_cannot_bypass_validation():
    p=body();p.layout.text_regions['headline'].left=.01
    with pytest.raises(ValidationError):compose_ad_png(p,font_path=FONT)


def test_invalid_image_and_small_canvas_not_silently_fixed():
    for data,code in [(b'bad','INVALID_RESIZE_IMAGE'),(picture((1024,1280)),'RESIZE_UPSCALE_DISABLED')]:
        with pytest.raises(GatewayError) as e:compose_ad_png(body(image_bytes=data,image_sha256=sha256(data).hexdigest()),font_path=FONT)
        assert e.value.code==code

@pytest.mark.parametrize('align',['left','center','right'])
def test_alignment_and_transparent_background_fit(align):
    l=layout();l['text_regions']['headline'].update(align=align,background=None)
    result=compose_ad_png(body(layout=l),font_path=FONT)
    entry=result.receipt['placements']['headline'];left,top,right,bottom=entry['region']
    assert all(left<=box[0]<=box[2]<=right for box in entry['ink_boxes'])


def test_generate_visual_examples(tmp_path):
    out=tmp_path
    raw=picture();(out/'source.png').write_bytes(raw)
    for name in ['short','long']:
        c=copy_output()
        if name=='long':
            c['ad_copy']['headline']='매일 나를 위한 편안한 루틴\n지금 새로운 일상을 만나보세요'
            c['ad_copy']['subline']='나에게 어울리는 데일리 케어. '+ '소중한 일상의 시간을 천천히 채워보세요. '*5
        result=compose_ad_png(body(copy_output=c,copy_hash=content_hash(c)),font_path=FONT)
        (out/(name+'.png')).write_bytes(result.image_bytes)
        (out/(name+'-caption.txt')).write_bytes(result.caption_bytes)
        (out/(name+'-receipt.json')).write_text(json.dumps(result.receipt,ensure_ascii=False,indent=2)+'\n')
