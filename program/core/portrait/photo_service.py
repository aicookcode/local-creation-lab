"""Shared GUI/API orchestration for the generic photo-lip and speech recipe."""
from pathlib import Path
import ast,json,hashlib,datetime,uuid
from studio_paths import PROGRAM,CACHE
from portable_bootstrap import pack_paths,unpack_paths
from .config import inside,sha,template_path,signature,duration_wav,validate_blink
from .service import call,PY
from .detail_service import pins

RECIPE='photo-lip-speech-v2'

def save(p,data):
 p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.pending');q.write_text(json.dumps(pack_paths(data),ensure_ascii=False,indent=2),encoding='utf-8');q.replace(p)

def read(p):return unpack_paths(json.loads(Path(p).read_text(encoding='utf-8-sig')))
def key(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest()
def code_hash(name,methods):
 tree=ast.parse((PROGRAM/'core/portrait'/name).read_text(encoding='utf-8-sig'));nodes=[]
 for node in ast.walk(tree):
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in methods:nodes.append(ast.dump(node,include_attributes=False))
 return key(nodes)

def valid(folder,stage,required):
 folder=Path(folder);rec=folder/(stage+'-completed.json')
 try:
  r=read(rec)
  if not isinstance(r,dict) or r.get('status')!='completed' or r.get('stage')!=stage:return False
  files=r.get('files')
  if not isinstance(files,dict) or not files or not set(required)<=set(files):return False
  for name,digest in files.items():
   if not isinstance(name,str) or not isinstance(digest,str) or Path(name).is_absolute() or '..' in Path(name).parts:return False
   p=(folder/name).resolve()
   if not p.is_relative_to(folder.resolve()) or not p.is_file() or sha(p)!=digest:return False
  return True
 except (OSError,ValueError,TypeError,KeyError,AttributeError):return False

def receipt(folder,stage,names):save(folder/(stage+'-completed.json'),{'recipe':RECIPE,'stage':stage,'status':'completed','files':{name:sha(folder/name) for name in names}})

def caches_for(audio,settings,model_pins):
 assets=template_path(settings).parent;image_sha=sha(assets/'portrait.png');lm_sha=sha(assets/'landmarks68.npy');audio_sha=sha(audio);base={'source':image_sha,'landmarks':lm_sha}
 mouth_key={**base,'audio':audio_sha,'fps':30,'wav2lip':{k:v for k,v in model_pins.items() if 'wav2lip.pth' in k or 'alignment_WFLW' in k},'input_code':code_hash('detail_worker.py',['__init__','predict_stage']),'geometry_code':code_hash('photo_worker.py',['geometry_stage']),'precision':'FP32'}
 eye_key={**base,'models':{k:v for k,v in model_pins.items() if 'LivePortrait/' in k},'generator_code':code_hash('detail_worker.py',['blink_stage']),'retarget_code':code_hash('photo_worker.py',['blink_stage','patch_path']),'closure_profile':sha(PROGRAM/'core/portrait/blink_timing.py')}
 ref=settings.get('mouth_reference');reference_sha=sha(inside(ref)) if ref else 'auto';ref_key={**base,'framing':settings['framing'],'reference':reference_sha,'method':code_hash('photo_worker.py',['reference_stage']),'models':{k:v for k,v in model_pins.items() if 'GFPGAN' in k or 'alignment_WFLW' in k or 'wav2lip.pth' in k}}
 root=CACHE/'portrait';mouth=root/'口型'/key(mouth_key);eye=root/'眼睑'/key(eye_key);reference=root/'口腔参考'/key(ref_key)
 alignment_key={'audio':audio_sha,'script':sha(inside(settings['text_file'])),'model':sha(PROGRAM.parent/'models/subtitle/faster-whisper-base/model.bin'),'code':sha(PROGRAM/'core/portrait/speech_alignment.py'),'precision':'FP32'}
 from recognition_support import review_descriptor
 alignment_key['review_model']=review_descriptor()
 alignment=root/'transcript_timing'/key(alignment_key)
 render_key={'recipe':RECIPE,'alignment_key':key(alignment_key),'mouth':key(mouth_key),'eye':key(eye_key),'reference':key(ref_key),'script':sha(inside(settings['text_file'])),'blink':validate_blink(settings['blink'],duration_wav(audio)),'framing':settings['framing'],'worker':sha(PROGRAM/'core/portrait/photo_worker.py'),'texture':sha(PROGRAM/'core/portrait/photo_texture.py'),'constraints':sha(PROGRAM/'core/portrait/speech_constraints.py'),'alignment':sha(PROGRAM/'core/portrait/speech_alignment.py'),'alignment_model':sha(PROGRAM.parent/'models/subtitle/faster-whisper-base/model.bin')}
 render=root/'画面'/key(render_key)
 for p in (mouth,eye,reference,render,alignment):p.mkdir(parents=True,exist_ok=True)
 return mouth,eye,reference,render,alignment,{'source':image_sha,'landmarks':lm_sha,'audio':audio_sha,'reference':reference_sha,'render_key':render_key}

def validate_job_inputs(data):
 assets=inside(data['template']).parent
 if sha(assets/'landmarks68.npy')!=data.get('landmarks_sha256',read(inside(data['template']))['files']['landmarks68.npy']) or sha(assets/'portrait.png')!=data['source_sha256'] or sha(inside(data['audio']))!=data['audio_sha256'] or sha(inside(data['text_file']))!=data['script_sha256']:raise ValueError('照片、配音或原稿已改变，不能混用旧口型。')
 if data.get('mouth_reference') and sha(inside(data['mouth_reference']))!=data['reference_sha256']:raise ValueError('张嘴参考图已改变，请重新生成。')

def build(project,audio,settings,output,log,preview=None):
 if not settings.get('text_file'):raise ValueError('发声校准方案需要本次音频实际朗读的正文，请先确认本页音频与正文。')
 sig=signature(audio,settings);weights=pins();mouth,eye,reference,render,alignment,ids=caches_for(audio,settings,weights);project=inside(project);area=project/'05-人物口播/任务'/('原图校准-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'));area.mkdir(parents=True);job=area/'制作任务.json'
 # Verify transcript before expensive model work; PCM publishing stays atomic.
 pcm=mouth/'analysis16k.f32'
 if not valid(mouth,'analysis',['analysis16k.f32']):
  if not valid(mouth,'predict',['predictions.npy','analysis16k.f32']):
   tmp=pcm.with_suffix('.pending');call([PROGRAM/'core/video/ffmpeg.exe','-v','error','-nostdin','-y','-i',audio,'-ac','1','-ar','16000','-f','f32le',tmp],log);tmp.replace(pcm)
  receipt(mouth,'analysis',['analysis16k.f32'])
 if not valid(alignment,'alignment',['alignment.json']):
  call([PROGRAM/'runtime/python/python.exe','-X','utf8','-u',PROGRAM/'core/portrait/speech_alignment.py','--pcm',pcm,'--script',inside(settings['text_file']),'--output',alignment/'alignment.json'],log);receipt(alignment,'alignment',['alignment.json'])
 else:log('复用已校验缓存：原稿时间（与照片、眨眼、字幕样式无关）')
 ref_template=None
 if settings.get('mouth_reference'):
  prep=reference/'准备';pointer=reference/'prepare-pointer.json'
  if pointer.exists():
   try:prep=inside(read(pointer)['folder'])
   except (OSError,ValueError,TypeError,KeyError):log('口腔参考缓存指针失效，将重新准备；原素材保留。')
  if not (prep/'模板.json').is_file():
   if prep.exists():prep=reference/('准备-'+uuid.uuid4().hex[:8])
   call([PY,'-X','utf8','-u',PROGRAM/'core/portrait/prepare.py','--image',inside(settings['mouth_reference']),'--out-dir',prep],log);save(pointer,{'folder':str(prep)})
  ref_template=prep/'模板.json'
 data={'schema':4,'recipe':RECIPE,'template':str(template_path(settings)),'audio':str(audio),'text_file':str(inside(settings['text_file'])),'cache':str(mouth),'eye_cache':str(eye),'ref_cache':str(reference),'render_cache':str(render),'alignment_cache':str(alignment),'framing':settings['framing'],'blink':validate_blink(settings['blink'],duration_wav(audio)),'preview':preview,'signature':sig,'source_sha256':ids['source'],'landmarks_sha256':ids['landmarks'],'audio_sha256':ids['audio'],'script_sha256':sha(inside(settings['text_file'])),'reference_sha256':ids['reference'],'mouth_reference':settings.get('mouth_reference'),'reference_template':str(ref_template) if ref_template else None,'key':ids['render_key']};save(job,data);worker=PROGRAM/'core/portrait/photo_worker.py'
 def stage(name,folder,needed):
  if valid(folder,name,needed):log('复用已校验缓存：'+name);return
  call([PY,'-X','utf8','-u',worker,'--job',job,'--stage',name],log);receipt(folder,name,needed)
 stage('predict',mouth,['predictions.npy','analysis16k.f32']);stage('geometry',mouth,['raw-landmarks.npy']);stage('reference',reference,['anchor.png','landmarks.npy','reference.json']);stage('plan',render,['plan.npz','screening.json'])
 from .blink_timing import starts,closure
 import numpy as np
 needed_starts=starts(data['blink'],round(duration_wav(audio)*30),ids['audio']+ids['source']);states=sorted({closure(k,[0]) for k in range(10)}-{0.}) if needed_starts else []
 if states:stage('blink',eye,['eyelid-map.json',*['eyelids/closure-'+np.float32(c).tobytes().hex()+'.png' for c in states]])
 validate_job_inputs(data);call([PY,'-X','utf8','-u',worker,'--job',job,'--stage','encode','--output',output],log);return read(job.with_name('制作结果.json'))

def burn_cached(video,subtitles,output,log):
 meta=read(Path(video).with_suffix('.detail.json'))
 if meta.get('recipe')!=RECIPE or meta['video_sha256']!=sha(video):raise ValueError('母版版本或校验值不一致，不能复用画面。')
 job=inside(meta['job'])
 if meta.get('job_sha256') and meta['job_sha256']!=sha(job):raise ValueError('母版对应的制作设置已改变，请重新生成母版。')
 data=read(job);validate_job_inputs(data)
 for folder,stage,names in [(inside(data['cache']),'geometry',['raw-landmarks.npy']),(inside(data['ref_cache']),'reference',['anchor.png','landmarks.npy','reference.json']),(inside(data['render_cache']),'plan',['plan.npz','screening.json'])]:
  if not valid(folder,stage,names):raise ValueError('原始画面缓存缺失，请重新生成母版。')
 from .blink_timing import starts,closure
 import numpy as np
 if starts(data['blink'],round(duration_wav(inside(data['audio']))*30),data['audio_sha256']+data['source_sha256']):
  states=sorted({closure(k,[0]) for k in range(10)}-{0.})
  if not valid(inside(data['eye_cache']),'blink',['eyelid-map.json',*['eyelids/closure-'+np.float32(c).tobytes().hex()+'.png' for c in states]]):raise ValueError('眼睑缓存缺失，请重新生成母版。')
 call([PY,'-X','utf8','-u',PROGRAM/'core/portrait/photo_worker.py','--job',job,'--stage','encode','--output',output,'--ass',subtitles],log)
