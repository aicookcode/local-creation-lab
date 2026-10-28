from pathlib import Path
import json,hashlib,math,uuid,wave,subprocess,re
import os
from studio_paths import PACKAGE_ROOT,legacy_path

ROOT=Path(__file__).resolve().parents[2]
ACTION_TYPES={'头部向左轻倾':('head',-1),'头部向右轻倾':('head',1),'前臂向左轻摆':('forearm',-1),'前臂向右轻摆':('forearm',1)}

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def inside(path):
 from portable_bootstrap import unpack_paths
 path=unpack_paths(str(path))
 path=legacy_path(path)
 if not path.is_relative_to(PACKAGE_ROOT):raise ValueError('人物制作资源必须先导入工作台文件夹。')
 return path

def relative(path):return Path(os.path.relpath(inside(path),ROOT)).as_posix()
def template_path(settings):
 value=settings.get('template')
 if not value:raise ValueError('请先提交本次人物照片并完成照片检测。')
 return inside(value)
def template(settings):return json.loads(template_path(settings).read_text(encoding='utf-8'))
def defaults():return {'schema':3,'engine':'','blink':None,'template':'','image':'','framing':'','regions':{},'events':[],'preview_start':None,'preview_seconds':None,'text_file':'','reference_mode':'','mouth_reference':''}

def validate_blink(value,duration):
 if not isinstance(value,dict) or value.get('mode') not in ('natural','off','custom'):raise ValueError('请选择自然眨眼、不添加眨眼或自定义时间。')
 times=value.get('times',[])
 if not isinstance(times,list) or any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 or x+.3>duration+.001 for x in times):raise ValueError('眨眼时间须在配音范围内，并留出0.3秒完成一次眨眼。')
 if value['mode']=='custom' and not times:raise ValueError('请填写至少一个自定义眨眼开始时间。')
 if any(b-a<.5 for a,b in zip(sorted(times),sorted(times)[1:])):raise ValueError('两次眨眼至少相隔0.5秒。')
 return {'mode':value['mode'],'times':sorted(times) if value['mode']=='custom' else []}
def duration_wav(path):
 try:
  with wave.open(str(path)) as f:return f.getnframes()/f.getframerate()
 except (wave.Error,EOFError):
  probe=subprocess.run([str(ROOT/'core/video/ffmpeg.exe'),'-hide_banner','-i',str(path)],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  match=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe.stderr)
  if not match:raise ValueError('无法读取配音时长，请重新导入有效音频。')
  h,m,s=map(float,match.groups());return h*3600+m*60+s

def action_name(event):
 if event['part']=='forearm':return '前臂向左轻摆' if event['degree']<0 else '前臂向右轻摆'
 return '头部向左轻倾' if event['degree']<0 else '头部向右轻倾'

def validate_events(events,duration):
 if not math.isfinite(duration) or duration<=0:raise ValueError('配音时长无效。')
 result=[];ids=set()
 for index,source in enumerate(events,1):
  if source.get('enabled',True) is False:continue
  try:
   event={k:source[k] for k in ('part','begin','peak','end','degree')}
   for k in ('begin','peak','end','degree'):event[k]=float(event[k])
  except (KeyError,TypeError,ValueError):raise ValueError(f'第{index}条动作缺少有效的时间或幅度。')
  if event['part'] not in ('head','forearm'):raise ValueError('只支持头部轻倾和前臂带动手掌。')
  if not all(math.isfinite(event[k]) for k in ('begin','peak','end','degree')):raise ValueError('时间和幅度不能是空值或无穷大。')
  if not 0<=event['begin']<event['peak']<event['end']<=duration+.001:raise ValueError(f'第{index}条动作须满足：0≤开始<峰值<结束≤配音时长（{duration:.2f}秒）。')
  if min(event['peak']-event['begin'],event['end']-event['peak'])<.25:raise ValueError(f'第{index}条动作的渐入、回落各需至少0.25秒，避免突然跳动。')
  limit=3 if event['part']=='head' else 9
  if not 0<abs(event['degree'])<=limit:raise ValueError(f'第{index}条动作的幅度应大于0，且不超过{limit}度。')
  event.update(id=source.get('id') or uuid.uuid4().hex,enabled=True,label=str(source.get('label',''))[:100])
  if event['id'] in ids:raise ValueError('动作编号重复，请使用“复制动作”生成新的编号。')
  ids.add(event['id']);result.append(event)
 # A single action was already bounded above. Only overlapping intervals of
 # the same body part can violate the summed limit. Keep the exact old 30fps
 # arithmetic/order inside those intervals, rather than scanning silent hours.
 intervals=[]
 for part in ('head','forearm'):
  bounds={}
  for e in result:
   if e['part']==part:
    bounds[e['begin']]=bounds.get(e['begin'],0)+1;bounds[e['end']]=bounds.get(e['end'],0)-1
  active=0;previous=0.
  for point,delta in sorted(bounds.items()):
   if active>=2:intervals.append((max(0,math.floor(previous*30)),min(math.ceil(duration*30),math.ceil(point*30))))
   active+=delta;previous=point
 ranges=[]
 for begin,end in sorted(intervals):
  if ranges and begin<=ranges[-1][1]+1:ranges[-1]=(ranges[-1][0],max(end,ranges[-1][1]))
  else:ranges.append((begin,end))
 for frame in (i for begin,end in ranges for i in range(begin,end+1)):
  t=min(duration,frame/30);sums={'head':0.,'forearm':0.}
  for e in result:sums[e['part']]+=e['degree']*pulse(t,e)
  for part,limit in [('head',3),('forearm',9)]:
   if abs(sums[part])>limit+.001:raise ValueError(f'{t:.2f}秒附近的动作叠加超过{limit}度，请错开同类动作或减小幅度。')
 return sorted(result,key=lambda e:e['begin'])

def pulse(t,e):
 if not e['begin']<t<e['end']:return 0.
 x=(t-e['begin'])/(e['peak']-e['begin']) if t<=e['peak'] else (e['end']-t)/(e['end']-e['peak'])
 return x**3*(10-15*x+6*x*x)

def starting_events(duration,mode):
 if mode=='仅口型':return []
 result=[]
 fractions=[.22,.56,.82] if duration>=15 else [.5]
 for i,fraction in enumerate(fractions):
  peak=duration*fraction
  for part,angle,shift in [('head',[-2.5,1.2,-1.8][i],0),('forearm',[-8.,-6.,-5.][i],.3)]:
   if mode=='少量动作' and ((i+int(part=='forearm'))%2):continue
   p=min(duration-.6,max(.6,peak+shift));before=min(1.5,p);after=min(1.8,duration-p)
   result.append({'id':uuid.uuid4().hex,'part':part,'begin':round(p-before,3),'peak':round(p,3),'end':round(p+after,3),'degree':angle,'enabled':True,'label':'按时间分布，可结合正文调整'})
 return validate_events(result,duration)

def signature(audio,settings):
 seconds=duration_wav(audio);events=validate_events(settings.get('events',[]),seconds)
 manifest=template(settings)
 engine=settings.get('engine','legacy_v4')
 if engine not in ('legacy_v4','detail_v1','detail_v2'):raise ValueError('请选择本次人物口播的生成方案。')
 if engine in ('detail_v1','detail_v2') and events:raise ValueError('原图细节方案保持头部与身体固定；请移除旧版头部或前臂动作。')
 for event in events:
  if event['part'] not in manifest:raise ValueError('所选动作尚未标注对应区域，请先标注区域并重新准备照片。')
 if settings.get('framing') not in ('完整显示','居中裁切'):raise ValueError('请选择本次人物画面的取景方式。')
 compact=[{k:e[k] for k in ('part','begin','peak','end','degree')} for e in events]
 result={'kind':'portrait','audio_sha256':sha(audio),'template_id':manifest['id'],'template_manifest_sha256':sha(template_path(settings)),'events':compact,'fps':30,'framing':settings['framing']}
 if engine in ('detail_v1','detail_v2'):result.update(engine=engine,blink=validate_blink(settings.get('blink'),seconds))
 if engine=='detail_v2':
  script=settings.get('text_file')
  if not script or not inside(script).is_file():raise ValueError('请填写并确认本页音频实际朗读的正文，发声校准需要原稿。')
  refmode=settings.get('reference_mode')
  if refmode not in ('auto','photo'):raise ValueError('请选择口腔参考方式，或主动应用推荐参数。')
  reference=settings.get('mouth_reference')
  if refmode=='photo' and (not reference or not inside(reference).is_file()):raise ValueError('请选择同人物张嘴参考图。')
  result.update(script_sha256=sha(inside(script)),reference_mode=refmode,mouth_reference_sha256=sha(inside(reference)) if refmode=='photo' else None,blink_profile='natural-short-v2')
 return result
