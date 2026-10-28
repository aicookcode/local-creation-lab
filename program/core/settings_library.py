"""Versioned, input-free parameter records and per-execution history."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'tts'))
import copy,datetime,hashlib,json,math,uuid
from studio_paths import USER_RESOURCES
from portable_bootstrap import pack_paths,unpack_paths

LIBRARY=USER_RESOURCES/'presets'
# ``workflow`` is kept for opening older records. New records used by 快捷制作
# are scoped to the selected output route so unrelated video settings cannot be
# loaded together.
SCOPES={'workflow':'历史整套制作（兼容）','photo':'图片轮转','portrait':'人物口播','speech':'语音生成','clone':'声音克隆'}
QUALITY={'resolution':[1080,1920],'fps':30,'video_codec':'H.264','crf':14,'audio_bitrate':'192k','portrait_precision':'FP32'}
SEGMENTS={'自动（短文整段，长文按句）':'auto','整段（须在音色安全长度内）':'whole','按段落（保留换行分段）':'paragraph'}
def now():return datetime.datetime.now().astimezone().isoformat()
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(pack_paths(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)
def read(path):
    path=Path(path)
    if path.stat().st_size>1024*1024:raise ValueError('参数文件过大，不能作为参数记录导入。')
    return unpack_paths(json.loads(path.read_text(encoding='utf-8-sig')))
def number(value,lo,hi,label,integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi or (integer and int(value)!=value):raise ValueError('参数不在允许范围：'+label)
def validate(scope,data):
    if scope not in SCOPES or not isinstance(data,dict):raise ValueError('参数记录类型不正确。')
    allowed={'speech','photo','portrait','subtitles','quality','clone'}
    if set(data)-allowed:raise ValueError('参数记录含未知字段；不能把素材、路径或执行命令作为参数导入。')
    if 'quality' in data and data['quality']!=QUALITY:raise ValueError('不支持降低或改变固定输出质量的参数包。')
    if 'speech' in data:
        if set(data['speech'])!={'segmentation'} or data['speech']['segmentation'] not in SEGMENTS.values():raise ValueError('请选择有效的语音分段方式。')
    if 'photo' in data:
        p=data['photo']
        if set(p)!={'seconds','transition','motion','strength'}:raise ValueError('图片参数须包含停留、转场、运动和幅度。')
        from workflow import photo
        number(p['seconds'],1,30,'图片停留')
        if p['transition'] not in photo.TRANSITIONS or p['motion'] not in photo.MOTIONS or p['strength'] not in ('轻柔','明显'):raise ValueError('图片转场或运动参数无效。')
    if 'portrait' in data:
        p=data['portrait']
        if set(p)!={'engine','framing','blink','reference_mode'} or p['engine'] not in ('detail_v1','detail_v2') or p['framing'] not in ('完整显示','居中裁切') or p['reference_mode'] not in ('auto','photo'):raise ValueError('人物参数不完整；区域坐标与头部动作应随原项目保存。')
        from portrait.config import validate_blink
        validate_blink(p['blink'],max([0,*p['blink'].get('times',[])])+1)
    if 'subtitles' in data:
        p=data['subtitles']
        if set(p)!={'font_size','center_y','width'}:raise ValueError('字幕参数须包含字号、中心高度和最大宽度。')
        for key,lo,hi in [('font_size',48,110),('center_y',700,1500),('width',600,840)]:number(p[key],lo,hi,key,True)
    if 'clone' in data:
        import voice_store as voices
        p=data['clone']
        if set(p)!={'engine','settings'} or p['engine'] not in ('qwen','zipvoice'):raise ValueError('声音模型参数无效。')
        forbidden={'reference','reference_text','name','note','tags','role','selection','base_sample','target','accepted_at','acceptance','label'}
        if set(p['settings'])&forbidden:raise ValueError('声音参数不得夹带参考录音、文字或身份信息。')
        voices.validate_settings(p['engine'],p['settings'])
    required={'workflow':{'speech','photo','portrait','subtitles','quality'},'photo':{'photo'},'portrait':{'portrait'},'speech':{'speech'},'clone':{'clone'}}[scope]
    if not required<=set(data):raise ValueError('本类型缺少必要参数：'+','.join(sorted(required-set(data))))
    if scope!='workflow' and set(data)-required-{'speech','subtitles','quality'}:raise ValueError('此参数类型包含其他流程的配置。')
    return copy.deepcopy(data)

def route_record(record,route):
    """Return a validated record for one video route.

    Older 5.5 records contain both routes and are projected to the requested
    route only when opening an existing task or using the Python API. The quick
    panel never lists those legacy records as selectable options.
    """
    if route not in ('photo','portrait'):raise ValueError('输出形式必须是图片轮转或人物口播。')
    rec=checked(copy.deepcopy(record))
    if rec['scope']==route:return rec
    if rec['scope']!='workflow':raise ValueError('当前输出形式请选择对应的'+SCOPES[route]+'参数。')
    payload=rec['payload'];route_payload={route:copy.deepcopy(payload[route])}
    for key in ('speech','subtitles','quality'):
        if key in payload:route_payload[key]=copy.deepcopy(payload[key])
    route_payload=validate(route,route_payload)
    rec['scope']=route;rec['payload']=route_payload;rec['payload_sha256']=digest(route_payload)
    rec['note']=(rec.get('note','')+'（由历史整套记录按输出形式拆分）').strip()
    return rec
def save_record(name,scope,payload,*,source='user',note='',identifier=None):
    payload=validate(scope,payload);name=str(name).strip()
    if not name or len(name)>100:raise ValueError('参数名称须为1–100字。')
    rec={'schema':1,'id':identifier or uuid.uuid4().hex,'name':name,'scope':scope,'created_at':now(),'source':source,'note':str(note)[:3000],'payload':payload,'payload_sha256':digest(payload)}
    path=LIBRARY/'records'/(rec['id']+'.json')
    if path.exists():raise FileExistsError('记录已存在；修改参数请另存新版本。')
    atomic(path,rec);return rec
def checked(rec):
    if rec.get('schema')!=1 or not isinstance(rec.get('id'),str) or not rec.get('name') or rec.get('payload_sha256')!=digest(rec.get('payload')):raise ValueError('参数文件格式或校验值不正确。')
    validate(rec['scope'],rec['payload']);return rec
def records():
    result=[]
    for path in (LIBRARY/'records').glob('*.json'):
        try:result.append(checked(read(path)))
        except Exception as exc:result.append({'id':path.stem,'name':'不可用记录：'+path.stem,'scope':'invalid','created_at':'','error':str(exc)})
    return sorted(result,key=lambda r:r.get('created_at',''),reverse=True)
def get(identifier):
    if not isinstance(identifier,str) or not all(c.isalnum() or c in '-_' for c in identifier):raise ValueError('参数记录编号无效。')
    return checked(read(LIBRARY/'records'/(identifier+'.json')))
def history(limit=200):
    paths=sorted((LIBRARY/'usage_records').glob('*.json'),reverse=True)[:limit];out=[]
    for p in paths:
        try:out.append(read(p))
        except Exception:continue
    return out
def begin_usage(label,scope,payload,*,source='manual',project=None,record=None,voice=None):
    item={'schema':1,'id':datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid.uuid4().hex[:8],'started_at':now(),'label':label,'scope':scope,'parameters':copy.deepcopy(payload),'parameter_sha256':digest(payload),'source':source,'status':'running','project':str(project) if project else None,'record':{'id':record['id'],'name':record['name'],'payload_sha256':record['payload_sha256']} if record else None,'voice':voice}
    path=LIBRARY/'usage_records'/(item['id']+'.json');atomic(path,item);return path
def finish_usage(path,status,*,result=None,error=None):
    item=read(path);item.update(status=status,finished_at=now(),result=result,error=error);atomic(path,item)
def voice_info(voice_id):
    import voice_store as voices
    entry,profile,settings=voices.get_voice(voice_id,verify_models=False)
    return {'id':entry['id'],'name':entry['name'],'engine':profile['engine'],'profile_sha256':entry['profile_sha256'],'settings':profile['settings'],'reference_info':profile['reference_info'],'reference_sha256':profile['reference_sha256'],'transcript_sha256':profile['transcript_sha256'],'model_sha256':profile['model_sha256']}
def initialize():
    base={'speech':{'segmentation':'auto'},'photo':{'seconds':3,'transition':'智能匹配','motion':'连续呼吸运镜','strength':'轻柔'},'portrait':{'engine':'detail_v2','framing':'完整显示','blink':{'mode':'natural'},'reference_mode':'auto'},'subtitles':{'font_size':84,'center_y':1200,'width':810},'quality':QUALITY}
    note='固定84号粗体字幕、宽810px、中心1200px。使用用户本次主动选择的固定音色档案，不更改音色。'
    for identifier,name,transition in [('approved-v55-photo-auto','已确认 · 图片轮转 · 通用自动匹配','智能匹配'),('approved-v55-photo-smooth','已确认 · 图片轮转 · 平滑推移','平滑推移'),('approved-v55-photo-soft','已确认 · 图片轮转 · 轻柔柔焦','轻柔柔焦')]:
        if not (LIBRARY/'records'/(identifier+'.json')).exists():
            data={k:copy.deepcopy(base[k]) for k in ('speech','subtitles','quality','photo')};data['photo']['transition']=transition
            save_record(name,'photo',data,source='approved',identifier=identifier,note='图片轮转使用整图运动与转场；'+note)
    portrait_id='approved-v55-portrait-auto'
    if not (LIBRARY/'records'/(portrait_id+'.json')).exists():
        data={k:copy.deepcopy(base[k]) for k in ('speech','subtitles','quality','portrait')}
        save_record('已确认 · 人物口播 · 原图唇纹＋发声校准', 'portrait', data, source='approved', identifier=portrait_id, note='人物口播使用嘴型、发声校准与自然眨眼；'+note)
    # The immutable voice archive remains the source of reference samples. Save only its already accepted numeric recipe here.
    import voice_store as voices
    for entry in voices.list_voices():
        if not entry.get('accepted') or entry.get('builtin_key') not in ('qwen-primary','zip-identity-distill'):continue
        identifier='approved-v55-'+entry['engine']
        if (LIBRARY/'records'/(identifier+'.json')).exists():continue
        _,p,_=voices.get_voice(entry['id'],verify_models=False);cfg=copy.deepcopy(p['settings'])
        for k in ('reference','reference_text','name','note','tags','role','selection','base_sample','target','accepted_at','acceptance','label'):cfg.pop(k,None)
        save_record('已确认 · '+entry['engine']+'声音配置','clone',{'clone':{'engine':entry['engine'],'settings':cfg}},source='approved',identifier=identifier,note='仅保存模型调优参数。声音身份与参考保留在原音色档案；导入参数不会自动选择样本或覆盖档案。')
