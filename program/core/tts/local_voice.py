"""Offline, fixed-reference TTS. No account, cloud request, or secret is required."""
import argparse,hashlib,json,os,pathlib,re,subprocess,time,wave,uuid,math
import sys
sys.path[:0]=[str(pathlib.Path(__file__).resolve().parent),str(pathlib.Path(__file__).resolve().parents[1])]
from portable_bootstrap import configure
from task_progress import event,guard_space
from segment_cache import SegmentCache,key_for,wav_data,sha as file_sha
from voice_style import pitch_filters
configure()
import voice_store as store
ROOT=pathlib.Path(os.environ.get('ZMYG_MODEL_HOME', pathlib.Path(__file__).resolve().parent))
PROFILE=pathlib.Path(os.environ['ZMYG_LIBRARY']).parent/'reference_samples/zhimingyouguang-local-test'

from contextlib import contextmanager
@contextmanager
def model_working_directory(path):
 old=os.getcwd();os.chdir(path)
 try:yield
 finally:os.chdir(old)

def chunks(text,limit=44):
 answer=[]
 for paragraph in text.splitlines():
  parts=re.findall(r'[^。！？!?；;]+[。！？!?；;]?',paragraph)
  pending=''
  for part in parts:
   part=part.strip()
   if pending and len(pending)+len(part)>limit:answer.append(pending);pending=''
   while len(part)>limit:
    split=max(part.rfind(p,0,limit+1) for p in '，,:：、 ')
    if split<limit//2:split=limit-1
    answer.append(part[:split+1].strip());part=part[split+1:].strip()
   pending+=part
  if pending:answer.append(pending)
 return answer
def plan_parts(text,limit,segmentation='auto'):
 if segmentation not in ('auto','whole','paragraph'):raise ValueError('分段方式应为auto、whole或paragraph。')
 normalized=' '.join(s.strip() for s in text.splitlines() if s.strip())
 if segmentation=='whole':
  if len(normalized)>limit:raise ValueError(f'整段模式最多{limit}字符（沿用当前音色的安全片段长度）。长文请选择自动分段；不取消模型防失控保护。')
  return [normalized] if normalized else []
 return chunks(normalized if segmentation=='auto' else text,limit)

def synthesize(engine,text,out,preset='baseline',speed=None,voice_profile=None,profile_hash=None,segmentation='auto'):
 settings={}
 fixed_profile=None
 if voice_profile:
  from voice_store import load_profile,sha256 as profile_sha
  fixed_profile,settings=load_profile(voice_profile,expected_profile_hash=profile_hash)
  if engine and engine!=fixed_profile['engine']:raise ValueError('模型与固定音色档案不匹配。')
  engine=fixed_profile['engine'];preset='saved-voice-profile'
  if speed is not None and speed!=settings.get('default_speed',1.):raise ValueError('固定音色已锁定语速；请在音色管理中另存为新版本。')
 elif preset!='baseline':
  presets=json.loads(pathlib.Path(__file__).with_name('tuning-presets.json').read_text(encoding='utf-8'))
  if preset not in presets or presets[preset]['engine']!=engine:raise ValueError('调音模式与模型不匹配。')
  settings=presets[preset]
 speed=settings.get('default_speed',1.) if speed is None else speed
 if not .85<=speed<=1.15:raise ValueError('语速请设在0.85至1.15之间。')
 ref_path=store.reference_path(settings['reference']) if settings.get('reference') else PROFILE/'reference.wav'
 out=pathlib.Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
 if out.exists() or out.with_suffix('.json').exists():raise FileExistsError('输出文件或配套记录已存在，请换一个文件名，以免覆盖。')
 if len(text.strip())==0:raise ValueError('请填写口播正文。')
 start=time.monotonic();records=[]
 guard_space(out.parent)
 work=out.parent/(out.stem+'-parts');work.mkdir(exist_ok=True)
 # Cache the exact completed raw waveform, not a newly synthesized substitute.
 # Keep strict model/reference verification at both ends of every invocation.
 from voice_store import fingerprint
 reference_hash=file_sha(ref_path)
 recipe={'engine':engine,'speed':speed,'settings':{k:v for k,v in settings.items() if k not in ('reference','reference_text')},
         'models':fixed_profile['model_sha256'] if fixed_profile else fingerprint(engine,settings),
         'reference':reference_hash,'producer':file_sha(__file__),'cache_code':file_sha(pathlib.Path(__file__).with_name('segment_cache.py'))}
 if engine=='zipvoice':
  reference_text=store.reference_path(settings['reference_text']) if settings.get('reference_text') else PROFILE/'transcript.txt'
  recipe['reference_text']=file_sha(reference_text)
 cache_store=SegmentCache(pathlib.Path(os.environ['ZMYG_CACHE_ROOT'])/'voice_segments')
 inspector=None
 if engine=='qwen':
  from chunk_quality import ChunkInspector,POLICY
  inspector=ChunkInspector()
  recipe['quality_code']=file_sha(pathlib.Path(__file__).with_name('chunk_quality.py'))
  recipe['quality_policy']=POLICY
  from recognition_support import review_descriptor
  recipe['review_model']=review_descriptor()
 def save_chunk(key,wav,record):
  try:cache_store.store(key,wav,record)
  except (OSError,ValueError,wave.Error,EOFError) as exc:print('缓存未写入；本次已生成音频继续保留：'+str(exc),flush=True)
 if engine=='qwen':
  from collections import deque
  from chunk_quality import ChunkRejected
  from speech_recovery import recovery_segments,archive_rejected
  recipe['recovery_code']=file_sha(pathlib.Path(__file__).with_name('speech_recovery.py'))
  c=ROOT/'candidates'/'qwen06-llamacpp'
  cache=pathlib.Path(os.environ['ZMYG_CACHE_ROOT'])/'qwen';cache.mkdir(parents=True,exist_ok=True)
  env=os.environ.copy();env.update(LLAMA_CACHE=str(cache),OMP_NUM_THREADS='6')
  parts=plan_parts(text,settings.get('chunk_limit',44),segmentation)
  print(f'分段方式：{segmentation}，共{len(parts)}段；每段验收后才合并。',flush=True)
  if not parts:raise ValueError('正文没有可朗读的文字。')
  units=lambda text:sum(not c.isspace() for c in text)
  total_chars=sum(map(units,parts));done_chars=0;inference_elapsed=0.
  pending=deque((part,0,j,None) for j,part in enumerate(parts));attempts={}
  print(event('语音分段生成',0,total_chars),flush=True)
  while pending:
   part,depth,group,recovery=pending.popleft();i=len(records)+1
   guard_space(out.parent)
   wav=work/f'{i:03d}.wav'
   key=key_for(recipe,part);cached=cache_store.restore(key,wav)
   if cached and cached.get('quality',{}).get('policy')==POLICY and cached['quality'].get('status')=='accepted' and cached['quality'].get('output_sha256')==file_sha(wav):
    (work/f'{i:03d}.quality.json').write_text(json.dumps(cached['quality'],ensure_ascii=False,indent=2),encoding='utf-8')
    records.append({**cached,'text':part,'elapsed_s':0.,'cache_reused':True,'recovery':recovery});done_chars+=units(part)
    print(f'复用已校验voice_segments，累计{i}段',flush=True)
    print(event('语音分段生成',done_chars,total_chars),flush=True)
    continue
   limit=settings.get('max_frames',300)
   # The native CLI's fopen is not Unicode-aware on Windows. An ASCII
   # relative output name keeps Chinese user-selected folders working.
   executable=pathlib.Path(os.environ.get('ZMYG_QWEN_RUNTIME',c/'runtime'))/'llama-tts.exe'
   args=[str(executable),'-m',str(c/'model.gguf'),'-mm',str(c/'mmproj.gguf'),'--offline','--tts-lang','zh','--tts-speaker-file',str(ref_path),'-p',part,'-o',wav.name,'-t','6','-tb','6','-ngl','0','--no-mmproj-offload','-c','1024','-b','128','-ub','128','-n',str(limit),'--seed',str(settings.get('seed',20261017)),'--temp',str(settings.get('temperature',.7)),'--top-k','50','--top-p',str(settings.get('top_p',1.)),'--min-p','0','--repeat-penalty',str(settings.get('repeat_penalty',1.05))]
   t=time.monotonic()
   if attempts.get(group,0)>=12:raise RuntimeError('该片段已达到有限恢复次数，已保存失败记录；请检查本次发音。')
   attempts[group]=attempts.get(group,0)+1
   with open(work/f'{i:03d}.log','w',encoding='utf-8') as f:subprocess.run(args,cwd=work,env=env,stdout=f,stderr=f,check=True)
   duration=wav_data(wav)['seconds']
   if duration>=limit*.08-.1:raise RuntimeError('该句达到安全时长上限，请检查分段，未输出成品。')
   print(event('逐段正文与尾部验收',done_chars,total_chars),flush=True)
   try:quality=inspector.inspect(wav,part)
   except ChunkRejected as exc:
    smaller=recovery_segments(part,exc.report,depth)
    if not smaller or attempts[group]>=12:raise
    evidence=archive_rejected(wav)
    for child in reversed(smaller):pending.appendleft((child,depth+1,group,{'source_text':part,'rejected_files':evidence,'strategy':'shorter_original_text','depth':depth+1}))
    print('片段复核未通过，已保留原片段，按原文字边界有限重生成；不更改音色或正文。',flush=True)
    continue
   record=dict(text=part,elapsed_s=round(time.monotonic()-t,3),audio_s=wav_data(wav)['seconds'],cache_reused=False,quality=quality,recovery=recovery)
   save_chunk(key,wav,record);records.append(record);done_chars+=units(part);inference_elapsed+=record['elapsed_s']
   print(f'已验收{i}个voice_segments',flush=True)
   print(event('语音分段生成',done_chars,total_chars),flush=True)
 else:
  import sherpa_onnx
  import soundfile as sf
  c=ROOT/'candidates'/'zipvoice-int8'
  m=ROOT/settings['model_directory'] if settings.get('model_directory') else c/'sherpa-onnx-zipvoice-distill-int8-zh-en-emilia'
  encoder=m/settings['encoder_file'] if settings.get('encoder_file') else next(m.glob('encoder*.onnx'))
  decoder=m/settings['decoder_file'] if settings.get('decoder_file') else next(m.glob('decoder*.onnx'))
  if not (m/'lexicon.txt').is_file():raise FileNotFoundError('缺少中文发音词典，已停止生成，避免漏读中文。')
  # Native espeak/ONNX paths use ASCII relative names; Unicode cwd is set by Python.
  with model_working_directory(m):
   cfg=sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(tokens='tokens.txt',encoder=encoder.name,decoder=decoder.name,data_dir='espeak-ng-data',lexicon='lexicon.txt',vocoder=os.path.relpath(c/'vocos_24khz.onnx',m)),num_threads=settings.get('threads',6),provider='cpu'))
   if not cfg.validate():raise RuntimeError('模型文件校验失败。')
   # Load the ONNX model only if a chunk actually needs inference.
   tts=None
   with ref_path.open('rb') as ref_file:ref,sr=sf.read(ref_file,dtype='float32')
   ref_text_path=store.reference_path(settings['reference_text']) if settings.get('reference_text') else PROFILE/'transcript.txt'
   gc=sherpa_onnx.GenerationConfig();gc.reference_audio=ref;gc.reference_sample_rate=sr;gc.reference_text=ref_text_path.read_text(encoding='utf-8');gc.num_steps=settings.get('steps',4);gc.speed=speed;gc.silence_scale=1.
   gc.extra={'min_char_in_sentence':str(settings.get('minimum_chars',30)),'max_char_in_sentence':str(settings.get('maximum_chars',80)),'guidance_scale':str(settings.get('guidance_scale',1.))}
   # Longer coherent paragraphs give ZipVoice more prosodic context.
   parts=plan_parts(text,settings.get('chunk_limit',80),segmentation)
   if not parts:raise ValueError('正文没有可朗读的文字。')
   total_chars=sum(map(len,parts));done_chars=0
   print(event('语音分段生成',0,total_chars),flush=True)
   for i,part in enumerate(parts,1):
    guard_space(out.parent)
    wav=work/f'{i:03d}.wav';key=key_for(recipe,part);cached=cache_store.restore(key,wav)
    if cached:
     records.append({**cached,'text':part,'elapsed_s':0.,'cache_reused':True});done_chars+=len(part)
     print(f'复用已校验voice_segments {i}/{len(parts)}',flush=True);print(event('语音分段生成',done_chars,total_chars),flush=True)
     continue
    if tts is None:tts=sherpa_onnx.OfflineTts(cfg)
    t=time.monotonic();audio=tts.generate(part,gc)
    if not len(audio.samples):raise RuntimeError('模型返回空音频。')
    import numpy as np
    samples=np.asarray(audio.samples,dtype=np.float32);raw_peak=float(np.max(np.abs(samples)));peak_scale=min(1.,.97/max(raw_peak,1e-9)) if settings else 1.
    with wav.open('wb') as wav_file:sf.write(wav_file,samples*peak_scale,audio.sample_rate,format='WAV',subtype='PCM_16')
    record=dict(text=part,elapsed_s=round(time.monotonic()-t,3),audio_s=len(audio.samples)/audio.sample_rate,raw_peak=raw_peak,peak_scale=peak_scale,cache_reused=False)
    save_chunk(key,wav,record);records.append(record);done_chars+=len(part);print(f'完成分段 {i}',flush=True)
    print(event('语音分段生成',done_chars,total_chars),flush=True)
 if inspector is not None:
  # Release recognition model before merge/postprocessing; it never changes voice conditioning.
  del inspector
 wavs=[work/f'{i:03d}.wav' for i in range(1,len(records)+1)]
 if engine=='qwen' and any(r.get('quality',{}).get('status')!='accepted' or r['quality'].get('output_sha256')!=file_sha(w) for r,w in zip(records,wavs)):
  raise RuntimeError('有片段尚未通过正文/尾部验收或已被改动，禁止合并。')
 print(event('音频合并与后处理',0,1),flush=True)
 if len(wavs)!=len(records):raise RuntimeError('分段数量异常，请选择新的输出名称。')
 with wave.open(str(wavs[0])) as f:params=f.getparams()
 frames_total=0
 for path in wavs:
  with wave.open(str(path)) as part:frames_total+=part.getnframes()
 frames_total+=round(settings.get('gap_seconds',.15)*params.framerate)*max(0,len(wavs)-1)
 estimate=frames_total/(speed if engine=='qwen' and speed<1 else 1)*params.nchannels*params.sampwidth
 if estimate>0xffffff00:raise ValueError('合并后的音频超过标准WAV约4GB容量，请分为多个任务。已生成的分段完整保留。')
 guard_space(out.parent,required_bytes=math.ceil(estimate)*2)
 pending=work/('merged-'+uuid.uuid4().hex+'.wav')
 with wave.open(str(pending),'wb') as target:
  target.setparams(params)
  for i,p in enumerate(wavs):
   with wave.open(str(p)) as f:
    if (f.getnchannels(),f.getsampwidth(),f.getframerate())!=(params.nchannels,params.sampwidth,params.framerate):raise RuntimeError('音频规格不一致。')
    if (target.tell()+f.getnframes())*params.nchannels*params.sampwidth>0xffffff00:raise ValueError('音频超过标准WAV约4GB的格式容量，请分为多个任务导出。已完成分段保留在parts目录。')
    target.writeframes(f.readframes(f.getnframes()))
   if i<len(wavs)-1:target.writeframes(bytes(round(settings.get('gap_seconds',.15)*params.framerate)*params.nchannels*params.sampwidth))
 filters=pitch_filters(settings,speed if engine=='qwen' else 1.)
 if settings.get('light_denoise'):
  filters.extend([f"highpass=f={settings.get('denoise_highpass_hz',65)}",f"afftdn=nr={settings.get('denoise_nr',6)}:nf=-40:tn=1:gs=5",f"lowpass=f={settings.get('denoise_lowpass_hz',10000)}"])
 if filters:
  processed=work/'postprocessed.wav';ff=pathlib.Path(os.environ.get('ZMYG_FFMPEG', ROOT.parent/'legacy_photo_video_tool'/'ffmpeg.exe'))
  subprocess.run([str(ff),'-y','-hide_banner','-loglevel','error','-i',str(pending),'-af',','.join(filters),'-ar',str(params.framerate),'-c:a','pcm_s16le',str(processed)],check=True)
  os.replace(processed,pending)
 duration=wav_data(pending)['seconds']
 if fixed_profile:
  # Detect changes during a multi-paragraph generation as well as before it.
  load_profile(voice_profile,expected_profile_hash=profile_hash)
 if file_sha(ref_path)!=reference_hash:raise ValueError('生成期间参考录音发生变化，未输出成品。')
 manifest=dict(engine=engine,preset=preset,speed=speed,settings=settings,segmentation=segmentation,quality_policy=POLICY if engine=='qwen' else 'PCM-integrity-only',postprocess_filters=filters,voice=fixed_profile['name'] if fixed_profile else 'zhimingyouguang-local-test',reference_sha256=reference_hash,text=text,elapsed_s=round(time.monotonic()-start,3),audio_s=duration,parts=records,cache_hits=sum(bool(r.get('cache_reused')) for r in records),output_sha256=file_sha(pending),note='参考条件生成，未训练或改写基础模型。识别验收不等于自然度或人工试听通过。')
 if fixed_profile:
  manifest['voice_profile']={'id':fixed_profile['id'],'name':fixed_profile['name'],'path':str(pathlib.Path(voice_profile).resolve()),'profile_sha256':profile_sha(voice_profile),'settings_sha256':fixed_profile['settings_sha256'],'model_sha256':fixed_profile['model_sha256'],'transcript_sha256':fixed_profile['transcript_sha256'],'identity_locked':True}
 meta=work/('manifest-'+uuid.uuid4().hex+'.json');meta.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 # Windows rename refuses to overwrite a concurrently created final file.
 os.rename(pending,out);os.rename(meta,out.with_suffix('.json'))
 print('已保存：'+str(out),flush=True)
 return manifest
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--engine',choices=['qwen','zipvoice']);ap.add_argument('--text-file',required=True);ap.add_argument('--out',required=True);ap.add_argument('--preset',default='baseline');ap.add_argument('--speed',type=float,default=None);ap.add_argument('--voice-profile');ap.add_argument('--profile-hash');ap.add_argument('--segmentation',choices=['auto','whole','paragraph'],default='auto');a=ap.parse_args()
 if not a.engine and not a.voice_profile:ap.error('请提供 --engine 或 --voice-profile')
 synthesize(a.engine,pathlib.Path(a.text_file).read_text(encoding='utf-8-sig'),a.out,a.preset,a.speed,a.voice_profile,a.profile_hash,a.segmentation)


