from copy import deepcopy
from pathlib import Path
from hashlib import sha256
from io import BytesIO
from PIL import Image, ImageDraw
from src.image_generation.composition import CompositionInput
from src.image_generation.contracts import content_hash
FONT = str(Path(__file__).parent / 'fixtures/fonts/NanumGothic-Regular.ttf')
def picture(size=(1152,1440),mode='RGB'):
    im=Image.new(mode,size,(255,238,120,255) if mode=='RGBA' else (255,238,120))
    d=ImageDraw.Draw(im);w,h=size
    d.rounded_rectangle((int(w*.59),int(h*.36),int(w*.82),int(h*.85)),radius=20,fill='#72BDB5')
    d.rectangle((int(w*.62),int(h*.29),int(w*.79),int(h*.36)),fill='#286B69')
    b=BytesIO();im.save(b,'PNG');return b.getvalue()
def copy_output():
    return {'plan':{'target':'성인','concept':'데일리 루틴','key_message':'간결한 일상','tone':'차분한','mood':['청량']},'ad_copy':{'headline':'오늘의 간결한 루틴','subline':'매일 편안하게 만나는 나만의 시간','cta':'제품 알아보기'},'caption':'내 일상에 어울리는 데일리 루틴.','hashtags':['#화장품','#데일리루틴']}
def layout():
    return {'text_regions':{'headline':dict(left=.06,top=.06,right=.94,bottom=.23), 'subline':dict(left=.06,top=.28,right=.46,bottom=.65,max_font_size=40), 'cta':dict(left=.06,top=.76,right=.46,bottom=.91,max_font_size=36)},'protected_regions':[dict(left=.52,top=.28,right=.90,bottom=.91)]}
def body(**change):
    data=picture();copy=copy_output()
    value=dict(job_id='composition-001',image_bytes=data,image_sha256=sha256(data).hexdigest(),copy_revision=1,copy_hash=content_hash(copy),copy_output=copy,layout=layout())
    value.update(change);return CompositionInput(**value)
