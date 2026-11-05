from hashlib import sha256
from pathlib import Path
from io import BytesIO
import pytest
from PIL import Image,ImageFont
from src.image_generation import CompositionInput,compose_ad_png,make_placement_layout
from src.image_generation.ad_resize import resize_ad_png
from src.image_generation.contracts import content_hash
from src.image_generation.layout_profiles import PROFILES
from src.image_generation.errors import GatewayError
from .helpers import FONT
from .test_backend_adapter import png

COPY={'plan':{},'ad_copy':{'headline':'맑게 반짝, 핑크빛','subline':'가볍게 발리고\n끈적임은 적게','cta':'제품 보러가기'},'caption':'캡션','hashtags':['#글로스']}

@pytest.mark.parametrize('key',list(PROFILES))
def test_selected_size_max_type_and_protected_pixels(key):
    p=PROFILES[key];placement,ratio=key.split('-');layout=make_placement_layout(ratio,placement)
    data=png(size=p.native)
    result=compose_ad_png(CompositionInput(job_id='selected-001',image_bytes=data,image_sha256=sha256(data).hexdigest(),copy_revision=1,copy_hash=content_hash(COPY),copy_output=COPY,layout=layout),font_path=FONT)
    baseline=Image.open(BytesIO(resize_ad_png(data,final_size=p.final).image_bytes)).convert('RGB')
    output=Image.open(BytesIO(result.image_bytes)).convert('RGB')
    assert output.size==p.final and result.receipt['provider_calls']==0
    assert output.crop(p.product).tobytes()==baseline.crop(p.product).tobytes()
    assert result.receipt['placements']['headline']['font_size']>64
    assert result.receipt['placements']['subline']['font_size']>48
    # Check one-pixel-larger size fails fit or is already the allowed maximum.
    from src.image_generation.composition import _fit
    for name,placement_info in result.receipt['placements'].items():
        region=layout.text_regions[name];size=placement_info['font_size']
        assert placement_info['line_count']<=region.max_lines
        assert _fit(COPY['ad_copy'][name],region,Path(FONT).read_bytes(),0,p.final)[-1]==size
        if size<region.max_font_size:
            with pytest.raises(GatewayError):_fit(COPY['ad_copy'][name],region.model_copy(update={'min_font_size':size+1,'max_font_size':size+1}),Path(FONT).read_bytes(),0,p.final)

@pytest.mark.parametrize('size',[(1080,1440),(1080,1920)])
def test_wrong_aspect_rejected_without_crop(size):
    with pytest.raises(GatewayError,match='cropping is disabled'):resize_ad_png(png(size=(1152,1440)),final_size=size)

def test_copy_not_truncated_to_make_fit():
    from src.image_generation.composition import _fit
    region=make_placement_layout('9:16','story').text_regions['headline']
    with pytest.raises(GatewayError):_fit('아름다움'*100,region,Path(FONT).read_bytes(),0,(1080,1920))

@pytest.mark.parametrize('key',list(PROFILES))
def test_selection_to_mock_generation_to_composition(tmp_path,key):
    import base64
    from uuid import uuid4
    import httpx
    from . import test_backend_adapter as backend
    p=PROFILES[key];placement,ratio=key.split('-');calls=[]
    def handler(req):
        calls.append(req.url)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png(size=p.native)).decode()}]})
    payload=backend.body(layout_selection={'aspect_ratio':ratio,'placement':placement},ad_copy=COPY['ad_copy'],copy_hash=content_hash(COPY))
    generated=backend.setup(tmp_path,handler).generate(payload,project_id=uuid4())
    selection=payload.layout_selection
    final=compose_ad_png(CompositionInput(job_id=generated.job_id,image_bytes=generated.image_bytes,image_sha256=generated.sha256,copy_revision=generated.copy_revision,copy_hash=generated.copy_hash,copy_output=COPY,layout=make_placement_layout(selection.aspect_ratio,selection.placement)),font_path=FONT)
    assert (final.width,final.height)==p.final and len(calls)==1
    assert final.receipt['provider_calls']==0


def test_changed_placement_cannot_reuse_paid_job(tmp_path):
    from uuid import uuid4
    from . import test_backend_adapter as backend
    adapter=backend.setup(tmp_path,lambda req:pytest.fail('No transport allowed'))
    project=uuid4()
    adapter._bind(backend.body(layout_selection={'aspect_ratio':'9:16','placement':'story'}),project)
    with pytest.raises(GatewayError,match='different inputs'):
        adapter._bind(backend.body(layout_selection={'aspect_ratio':'9:16','placement':'reels'}),project)

@pytest.mark.parametrize('key',['feed-4:5','reels-9:16','story-9:16'])
def test_native_cta_not_repeated_in_image(key):
    p=PROFILES[key];placement,ratio=key.split('-');layout=make_placement_layout(ratio,placement)
    assert layout.cta_placement=='platform' and 'cta' not in layout.text_regions
    data=png(size=p.native)
    result=compose_ad_png(CompositionInput(job_id='native-cta',image_bytes=data,image_sha256=sha256(data).hexdigest(),copy_revision=1,copy_hash=content_hash(COPY),copy_output=COPY,layout=layout),font_path=FONT)
    assert 'cta' not in result.receipt['placements']
    assert result.copy_hash==content_hash(COPY)  # Approved copy remains intact.
    assert result.receipt['omitted_image_fields']==['cta']

def test_organic_cta_smaller_than_ad_copy():
    layout=make_placement_layout('3:4','feed')
    assert layout.cta_placement=='image' and layout.text_regions['cta'].max_font_size==48


@pytest.mark.parametrize('key',list(PROFILES))
def test_only_rendered_cta_requires_font_glyphs(key):
    p=PROFILES[key];placement,ratio=key.split('-');layout=make_placement_layout(ratio,placement)
    approved={**COPY,'ad_copy':{**COPY['ad_copy'],'cta':'제품 보기 👉'}}
    data=png(size=p.native)
    body=CompositionInput(job_id='cta-font-boundary',image_bytes=data,
        image_sha256=sha256(data).hexdigest(),copy_revision=1,
        copy_hash=content_hash(approved),copy_output=approved,layout=layout)
    if layout.cta_placement=='image':
        with pytest.raises(GatewayError) as error:
            compose_ad_png(body,font_path=FONT)
        assert error.value.code=='COMPOSITION_GLYPH_MISSING'
    else:
        result=compose_ad_png(body,font_path=FONT)
        assert result.copy_hash==content_hash(approved)
        assert 'cta' not in result.receipt['placements']
        assert result.receipt['omitted_image_fields']==['cta']
