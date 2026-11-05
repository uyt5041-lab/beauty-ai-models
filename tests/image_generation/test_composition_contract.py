from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from .helpers import body,layout,copy_output
from src.image_generation.composition import CompositionInput, _copy_fields
from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError

def test_full_copy_hash_matches_team_canonical_contract():
    vector=json.loads((Path(__file__).parent/'fixtures/canonical_copy_hash.json').read_text())
    assert content_hash(vector['copy_output'])==vector['copy_sha256']
    item=body(copy_output=vector['copy_output'],copy_hash=vector['copy_sha256'])
    ad,caption,tags=_copy_fields(item)
    assert (ad,caption,tags)==(item.copy_output['ad_copy'],item.copy_output['caption'],item.copy_output['hashtags'])

@pytest.mark.parametrize('target',['image','ad_copy','plan','caption','hashtags','partial_hash'])
def test_modified_approved_content_is_rejected(target):
    p=body()
    if target=='image':p.image_bytes+=b'changed'
    elif target=='ad_copy':p.copy_output['ad_copy']['headline']='다른 문구'
    elif target=='plan':p.copy_output['plan']['concept']='다른 콘셉트'
    elif target=='caption':p.copy_output['caption']='다른 캡션'
    elif target=='hashtags':p.copy_output['hashtags'].append('#추가')
    else:p.copy_hash=content_hash(p.copy_output['ad_copy'])
    with pytest.raises(GatewayError) as e:_copy_fields(p)
    assert e.value.code==('COMPOSITION_IMAGE_HASH_MISMATCH' if target=='image' else 'COMPOSITION_COPY_HASH_MISMATCH')

@pytest.mark.parametrize('change',[{'left':.049},{'top':.049},{'right':.951},{'bottom':.951},{'left':.95},{'left':float('nan')},{'min_font_size':70},{'padding':64,'right':.07}])
def test_layout_invalid_margins_and_geometry_rejected(change):
    l=layout();l['text_regions']['headline'].update(change)
    with pytest.raises(ValidationError):body(layout=l)

def test_overlap_with_text_or_product_is_rejected():
    for other in ['text','product']:
        l=layout()
        l['text_regions']['headline']=dict(l['text_regions']['subline']) if other=='text' else dict(left=.60,top=.40,right=.80,bottom=.50)
        with pytest.raises(ValidationError):body(layout=l)

def test_active_field_requires_explicit_region():
    l=layout();del l['text_regions']['cta']
    with pytest.raises(GatewayError,match='합성') as e:_copy_fields(body(layout=l))
    assert e.value.code=='COMPOSITION_LAYOUT_MISSING_FIELD'

@pytest.mark.parametrize('field,value',[('headline',''),('subline',None),('cta',42),('headline','금지\x00문자')])
def test_bad_copy_is_not_silently_coerced(field,value):
    c=copy_output();c['ad_copy'][field]=value
    with pytest.raises(GatewayError) as e:_copy_fields(body(copy_output=c,copy_hash=content_hash(c)))
    assert e.value.code=='COMPOSITION_COPY_INVALID'

def test_empty_optional_copy_does_not_need_region():
    c=copy_output();c['ad_copy']['subline']='';c['ad_copy']['cta']=''
    l=layout();l['text_regions']={'headline':l['text_regions']['headline']}
    assert _copy_fields(body(copy_output=c,copy_hash=content_hash(c),layout=l))[0]==c['ad_copy']
