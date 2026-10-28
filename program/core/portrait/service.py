"""Standard-library orchestration usable from the main GUI's Python runtime."""
from pathlib import Path
import subprocess,os,json,datetime,hashlib,uuid
from .config import ROOT,sha,inside,relative,template,template_path,signature,validate_events,duration_wav
from task_progress import guard_space

PY=ROOT/'runtime/portrait-python310/python.exe'
MODEL=ROOT.parent/'models/portrait/Wav2Lip-personal-research-only'
FF=ROOT/'core/video/ffmpeg.exe'

def call(args,log):
 from collections import deque
 from process_diagnostics import failure_message
 from studio_paths import LOGS
 folder=LOGS/'subprocess';folder.mkdir(parents=True,exist_ok=True)
 logfile=folder/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid.uuid4().hex[:6]+'.log');tail=deque(maxlen=80)
 env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
 with logfile.open('w',encoding='utf-8') as stream:
  proc=subprocess.Popen(list(map(str,args)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   for line in proc.stdout:stream.write(line);stream.flush();tail.append(line);log(line.rstrip())
  except Exception:
   proc.terminate();proc.wait();raise
 if proc.wait():raise RuntimeError(failure_message(tail,'人物或字幕步骤未完成',logfile))

def prerequisites():
 result={'人物运行环境':PY,'口型模型':MODEL/'wav2lip.pth','口型模型校验':MODEL/'integrity.json','人脸检测模型':ROOT.parent/'models/portrait/facexlib/detection_Resnet50_Final.pth','关键点模型':ROOT.parent/'models/portrait/facexlib/alignment_WFLW_4HG.pth','口腔局部修复':ROOT.parent/'models/portrait/facexlib/GFPGANv1.4.pth','视频工具':FF}
 base=ROOT.parent/'models/portrait/LivePortrait/liveportrait'
 for name in ('appearance_feature_extractor','motion_extractor','spade_generator','warping_module'):result['眼睑组件-'+name]=base/'base_models'/(name+'.pth')
 result['眼睑重定向']=base/'retargeting_models/stitching_retargeting_module.pth'
 return result

def verified_assets(settings):
 from model_manager import ensure
 ensure('portrait')
 for label,path in prerequisites().items():
  if not path.is_file():raise FileNotFoundError(label+'缺失：'+str(path))
 manifest=template(settings);assets=template_path(settings).parent
 for name,expected in manifest['files'].items():
  path=assets/name
  if not path.is_file() or sha(path)!=expected:raise ValueError('人物模板资源有改动或缺失：'+name)
 pin=json.loads((MODEL/'integrity.json').read_text(encoding='utf-8'))
 if not pin.get('verified') or sha(MODEL/'wav2lip.pth')!=pin['expected']:raise ValueError('口型模型校验未通过。')
 return manifest

def cache_key(audio,settings):
 assets=template_path(settings).parent
 key={'audio':sha(audio),'image':sha(assets/'portrait.png'),'landmarks':sha(assets/'landmarks68.npy'),'fps':30,'sharpen':.6,'worker_version':4}
 return hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:24]

def build(project,audio,settings,output,log=print,preview=None):
 project,audio,output=map(inside,(project,audio,output))
 guard_space(project)
 if not project.is_dir() or not audio.is_file():raise ValueError('请先准备项目与配音。')
 if output.exists():raise FileExistsError('输出已存在，请另存新版本。')
 if settings.get('engine')=='detail_v2':
  verified_assets(settings)
  from .photo_service import build as photo_build
  return photo_build(project,audio,settings,output,log,preview)
 if settings.get('engine')=='detail_v1':
  verified_assets(settings)
  from .detail_service import build as detail_build
  return detail_build(project,audio,settings,output,log,preview)
 seconds=duration_wav(audio);events=validate_events(settings.get('events',[]),seconds)
 sig=signature(audio,settings);manifest=verified_assets(settings);assets=template_path(settings).parent
 area=project/'05-人物口播';area.mkdir(exist_ok=True)
 folder=area/'任务'/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
 cache=area/'口型缓存'/cache_key(audio,settings);cache.mkdir(parents=True,exist_ok=True)
 base=None
 for attempt in sorted(cache.glob('版本-*'),reverse=True):
  info=attempt/'run.json';video=attempt/'result.mp4'
  if info.is_file() and video.is_file() and json.loads(info.read_text(encoding='utf-8')).get('status')=='completed':base=video;break
 reused=base is not None
 if base is None:
  log('首次为这份配音生成全段口型，之后只调动作可复用；无需重新配音。')
  normalized=folder/'配音-24k.wav'
  call([FF,'-v','error','-i',audio,'-ac','1','-ar','24000','-c:a','pcm_s16le',normalized],log)
  attempt=cache/('版本-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
  call([PY,'-X','utf8','-u',ROOT/'core/portrait_lab/run_wav2lip.py','--audio',normalized,'--out-dir',attempt,'--fps','30','--aligned','--sharpen','.6','--threads','2','--image',assets/'portrait.png','--landmarks',assets/'landmarks68.npy','--model-manifest',MODEL/'integrity.json'],log)
  base=attempt/'result.mp4'
 else:log('复用本项目已生成的全段口型缓存。')
 if sha(audio)!=sig['audio_sha256']:raise ValueError('制作期间配音发生变化，请重新生成。')
 job={'schema':2,'template':relative(template_path(settings)),'source':relative(base),'audio':relative(audio),'output':relative(output),'events':events,'preview':preview,'signature':sig,'cache_reused':reused,'framing':settings['framing']}
 config_path=folder/'制作任务.json';config_path.write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
 log('正在按已保存的动作时间制作'+('短样预览。' if preview else '完整无字幕母版。'))
 call([PY,'-X','utf8','-u',ROOT/'core/portrait/worker.py',config_path],log)
 result=json.loads(config_path.with_name('制作结果.json').read_text(encoding='utf-8'))
 if sha(audio)!=sig['audio_sha256']:raise ValueError('制作期间配音发生变化，请重新生成。')
 result['signature']=sig;return result

def inspect(project,video,audio,subtitle=None,script=None,log=print):
 project,video,audio=map(inside,(project,video,audio))
 folder=project/'05-人物口播'/'检查'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f');folder.mkdir(parents=True)
 args=[PY,'-X','utf8','-u',ROOT/'core/portrait/check_output.py','--video',video,'--audio',audio,'--out-dir',folder]
 if subtitle:args+=['--subtitles',inside(subtitle)]
 if script:args+=['--script',inside(script)]
 call(args,log)
 return json.loads((folder/'检查结果.json').read_text(encoding='utf-8'))
