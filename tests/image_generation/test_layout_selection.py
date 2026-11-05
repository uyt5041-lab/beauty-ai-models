import base64
from email import policy
from email.parser import BytesParser
from uuid import uuid4
import httpx
import pytest
from pydantic import ValidationError
from src.image_generation.contracts import ExternalImageRequest, LayoutSelection, content_hash
from src.image_generation.layout_profiles import PROFILES, get_layout
from . import test_backend_adapter as backend

@pytest.mark.parametrize('ratio,placement',[('3:4','feed'),('4:5','feed'),('9:16','reels'),('9:16','story')])
def test_transport_and_replay(tmp_path,ratio,placement):
    profile=get_layout(ratio,placement);calls=[]
    def handler(req):
        msg=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+req.headers['content-type'].encode()+b'\r\n\r\n'+req.read())
        fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in msg.iter_parts() if p.get_param('name',header='content-disposition')!='image[]'}
        calls.append(fields)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(backend.png(size=profile.native)).decode()}]})
    adapter=backend.setup(tmp_path,handler);project=uuid4();payload=backend.body(layout_selection={'aspect_ratio':ratio,'placement':placement})
    result=adapter.generate(payload,project_id=project)
    assert (result.width,result.height)==profile.native
    assert calls[0]['size'].decode()==f'{profile.native[0]}x{profile.native[1]}'
    prompt=calls[0]['prompt'].decode()
    for term in [profile.key,'PRODUCT_TARGET','UI_SAFE','headline','subline','cta_placement',payload.image_prompt.prompt]:assert term in prompt
    adapter.generate(payload,project_id=project);assert len(calls)==1

@pytest.mark.parametrize('ratio,placement',[('9:16','feed'),('3:4','story'),('4:5','reels')])
def test_invalid_selection(ratio,placement):
    with pytest.raises(ValidationError):LayoutSelection(aspect_ratio=ratio,placement=placement)

def test_conflicting_old_zone():
    with pytest.raises(ValidationError):backend.body(layout_selection={'aspect_ratio':'9:16','placement':'story'},safe_zone={'left':.1,'top':.1,'right':.9,'bottom':.3})

def test_legacy_hash_and_wrong_size():
    req=ExternalImageRequest(operation='generate',prompt={'positive':'한국어 배경','prompt_version':'v1','source_revision':1})
    old=req.model_dump(mode='json');assert 'layout_selection' not in old;assert content_hash(req)==content_hash(old)
    with pytest.raises(ValidationError):ExternalImageRequest(**{**old,'layout_selection':{'aspect_ratio':'9:16','placement':'story'}})

def test_story_b_and_geometry():
    assert PROFILES['story-9:16'].safe[3]==1420 and PROFILES['reels-9:16'].safe[3]==1152
    for p in PROFILES.values():
        for b in [p.product,*dict(p.text).values()]:
            s=p.safe;assert s[0]<=b[0]<b[2]<=s[2] and s[1]<=b[1]<b[3]<=s[3]
        for b in dict(p.text).values():
            q=p.product;assert min(b[2],q[2])<=max(b[0],q[0]) or min(b[3],q[3])<=max(b[1],q[1])
