"""Measured stage progress, elapsed time, and bounded-memory exports."""
import json,math,re,time,shutil,uuid,os
from pathlib import Path

PREFIX='@@PROGRESS '

# Internal progress events can contain model names, file paths or structured
# payloads. The bottom bar is a user-facing surface, so it only receives these
# short, stable Chinese descriptions.
_PHASE_LABELS=(
 ('快捷制作','正在准备快捷制作'),
 ('语音分段生成','正在生成配音'),
 ('逐段正文与尾部验收','正在检查配音内容'),
 ('音频合并与后处理','正在整理音频'),
 ('核对配音与原稿','正在核对配音与原稿'),
 ('备用识别核对原稿','正在用本地模型复核原稿'),
 ('原稿发音时间校准','正在校准发音时间'),
 ('检测本次照片中的人脸','正在检查人物照片'),
 ('动作与画面合成','正在合成动作与画面'),
 ('人物口型推理','正在生成口型'),
 ('生成同步嘴型','正在生成口型'),
 ('提取发音口型几何','正在分析发音与口型'),
 ('准备自然眨眼','正在准备眨眼'),
 ('编码原图唇纹与发声校准','正在合成人物画面'),
 ('图片视频制作','正在制作图片视频'),
 ('保存到导出位置','正在保存成品'),
 ('复用本项目','正在复用已有口型缓存'),
 ('复用口型缓存','正在复用已有口型缓存'),
)

def _looks_like_code(value):
 text=str(value or '').strip()
 if not text:return True
 if text.startswith(('{','[','@@','Traceback','File "')):return True
 if any(token in text for token in ('{\"','\\nTraceback','Exception:','raise ','import ','def ','->')):return True
 return False

def phase_label(phase):
 """Return a concise natural-language label suitable for the bottom bar."""
 text=str(phase or '').strip()
 if not text or _looks_like_code(text):return '正在处理当前步骤'
 for marker,label in _PHASE_LABELS:
  if marker in text:return label
 if len(text)>42:return '正在处理当前步骤'
 return text if any('\u4e00'<=char<='\u9fff' for char in text) else '正在处理当前步骤'

def status_from_log(line):
 """Convert a raw worker log line to a safe, human-readable status."""
 text=str(line or '').strip()
 if PREFIX in text:
  try:return phase_label(json.loads(text.split(PREFIX,1)[1]).get('phase'))
  except (ValueError,TypeError,AttributeError):return '正在处理当前步骤'
 if '复用本项目' in text:return '正在复用已有口型缓存'
 if re.search(r'(?:^|\|\s)\d+(?:\.\d+)?%\s*',text):return '正在制作图片视频'
 if re.search(r'动作合成 \d+/\d+ 帧',text):return '正在合成动作与画面'
 if re.search(r'(?:^|\|\s)\d+/\d+ [\d.]+s',text):return '正在生成口型'
 if _looks_like_code(text):return '正在处理当前步骤'
 # Keep short, already-human progress messages, but avoid exposing paths or
 # implementation details in the status area.
 if any(token in text for token in ('正在','开始','准备','检查','核对','生成','保存','完成','读取','检测','复用')) and len(text)<=60:
  return text
 return '正在处理当前步骤'

def event(phase,done,total,elapsed=None):
 data={'phase':phase,'done':done,'total':total}
 if elapsed is not None:data['elapsed']=elapsed
 return PREFIX+json.dumps(data,ensure_ascii=False)
def clock(seconds):
 seconds=max(0,int(seconds));hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
 return (f'{hours}小时' if hours else '')+(f'{minutes}分' if minutes or hours else '')+f'{seconds}秒'
class Tracker:
 def __init__(self,now=time.monotonic):self.now=now;self.active=False;self.phase='';self.phase_key='';self.done=0;self.total=0;self.eta=None;self.finished=''
 def start(self,label):
  self.active=True;self.started=self.now();self.phase=label;self.phase_key=label;self.phase_started=self.started;self.done=0;self.total=0;self.eta=None;self.finished='';self.baseline=0;self.last_event=self.started
 def feed(self,line):
  if not self.active:return
  payload=None
  if PREFIX in line:
   try:payload=json.loads(line.split(PREFIX,1)[1])
   except (ValueError,TypeError):return
  else:
   percent=re.search(r'(?:^|\|\s)(\d+(?:\.\d+)?)%\s*(.*)',line)
   frames=re.search(r'动作合成 (\d+)/(\d+) 帧',line)
   model=re.search(r'(?:^|\|\s)(\d+)/(\d+) ([\d.]+)s',line)
   if percent:payload={'phase':'图片视频制作','done':float(percent[1]),'total':100}
   elif frames:payload={'phase':'动作与画面合成','done':int(frames[1]),'total':int(frames[2])}
   elif model:payload={'phase':'人物口型推理','done':int(model[1]),'total':int(model[2]),'elapsed':float(model[3])}
   elif '复用本项目' in line:self.phase='复用口型缓存，准备合成';self.phase_key=self.phase;self.total=0;self.eta=None
  if not payload:return
  try:done,total=float(payload['done']),float(payload['total']);phase=str(payload['phase']);elapsed=payload.get('elapsed')
  except (KeyError,ValueError,TypeError):return
  if not math.isfinite(done) or not math.isfinite(total) or not 0<=done<=total or total<=0:return
  now=self.now()
  if phase!=self.phase_key or done<self.done:self.phase=phase;self.phase_key=phase;self.phase_started=now;self.baseline=done;self.eta=None
  self.done=done;self.total=total;self.last_event=now
  observed=now-self.phase_started;processed=done-self.baseline
  if elapsed is not None and isinstance(elapsed,(int,float)) and math.isfinite(elapsed) and elapsed>0:observed=elapsed;processed=done
  if processed>0 and observed>.05:self.eta=max(0,(total-done)*observed/processed)
 def finish(self,success=True):
  self.active=False;self.finished=('完成' if success else '未完成')+' · 用时'+clock(self.now()-self.started);self.eta=None
 def text(self):
  if not self.active:return self.finished or '提交任务后显示进度与已用时间。'
  result=phase_label(self.phase)+' · 已用'+clock(self.now()-self.started)
  if self.total:
   result+=f' · 本阶段{min(100,100*self.done/self.total):.0f}%'
   if self.eta is not None:
    remaining=self.eta-(self.now()-self.last_event)
    result+=(' · 超过上次预估，仍在处理' if remaining<0 and self.done<self.total else ' · 本阶段预计剩余'+clock(max(0,remaining)))
   else:result+=' · 正在估计剩余时间'
  else:result+=' · 准备或收尾中，暂不能可靠估时'
  return result

def export_copy(source,target,report=lambda line:None,sidecars=(),extra_files=()):
 """Export to a chosen filename with bounded buffers, preserving all originals."""
 source,target=Path(source),Path(target)
 if source.resolve()==target.resolve():return target
 target.parent.mkdir(parents=True,exist_ok=True)
 items=[(source,target)]+[(source.with_suffix(s),target.with_suffix(s)) for s in sidecars if source.with_suffix(s).is_file()]
 items.extend((Path(src),target.with_suffix(suffix)) for src,suffix in extra_files)
 import hashlib
 def same(src,dst):
  if not dst.is_file() or src.stat().st_size!=dst.stat().st_size:return False
  def digest(path):
   value=hashlib.sha256()
   with path.open('rb') as stream:
    while block:=stream.read(4*1024*1024):value.update(block)
   return value.digest()
  return digest(src)==digest(dst)
 unique={}
 for src,dst in items:
  if dst in unique and not same(src,unique[dst]):raise ValueError('导出配套文件名称冲突。')
  unique[dst]=src
 items=[(src,dst) for dst,src in unique.items()]
 reused={dst for src,dst in items if dst.exists() and same(src,dst)}
 if any(dst.exists() and dst not in reused for _,dst in items):raise FileExistsError('导出位置有内容不同的同名文件，请选择新文件名。')
 total=sum(src.stat().st_size for src,_ in items)
 remaining=sum(src.stat().st_size for src,dst in items if dst not in reused)
 if shutil.disk_usage(target.parent).free<remaining+16*1024*1024:raise OSError('所选导出磁盘空间不足，请选择其他位置。')
 completed=0
 for src,dst in items:
  if dst in reused:completed+=src.stat().st_size;report(event('保存到导出位置',completed,total));continue
  pending=dst.with_name(dst.name+'.'+uuid.uuid4().hex+'.partial')
  try:
   with src.open('rb') as inf,pending.open('xb') as outf:
    while True:
     data=inf.read(4*1024*1024)
     if not data:break
     outf.write(data);completed+=len(data);report(event('保存到导出位置',completed,total))
   os.rename(pending,dst)
  except Exception:pending.unlink(missing_ok=True);raise
 if source.suffix.lower() in ('.mp4','.mov','.mkv'):
  from portrait.speech_constraints import copy_provenance
  copy_provenance(source,target)
 return target

def guard_space(directory,required_bytes=0):
 path=Path(directory).resolve()
 while not path.exists():path=path.parent
 needed=max(0,int(required_bytes))+256*1024*1024
 if shutil.disk_usage(path).free<needed:raise OSError('制作磁盘空间不足：本阶段还需约'+str(math.ceil(needed/1024**2))+'MB（含256MB保留空间）。请清理空间或迁移工作台后再运行；已有素材不会被删除。')
