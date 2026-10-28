"""Serial, recoverable orchestration for the approved detail recipe."""
from pathlib import Path
import json,hashlib,datetime,uuid,os,shutil
from studio_paths import PROGRAM,CACHE
from portable_bootstrap import pack_paths,unpack_paths
from .config import inside,relative,sha,signature,template_path,duration_wav,validate_blink
STAGES=['blink','predict','cavity','track']
def pins():
 config=json.loads((PROGRAM/'config/portrait_model_integrity.json').read_text(encoding='utf-8'))
 for rel,digest in config['files'].items():
  path=PROGRAM.parent/'models'/rel
  if not path.is_file() or sha(path)!=digest:raise ValueError('人物模型缺失或发生变化：'+rel)
 return config['files']
def save(path,data):
 tmp=path.with_suffix(path.suffix+'.pending');tmp.write_text(json.dumps(pack_paths(data),ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def import_compatible_cache(key,cache,log):
 """Import verified immutable stage artifacts for an explicitly tested upgrade.

 The old key/receipts are kept unchanged. The new key remains bound to the
 installed worker, and a SHA-bound compatibility declaration authorizes reuse.
 """
 if any((cache/(s+'-completed.json')).exists() for s in STAGES):return
 declaration=PROGRAM/'config/portrait_cache_compatibility.json'
 if not declaration.is_file():return
 try:
  record=json.loads(declaration.read_text(encoding='utf-8'))
  if record.get('installed_worker_sha256')!=key['worker'] or record.get('recipe')!=key['recipe']:return
 except (OSError,ValueError):return
 for prior in record.get('compatible_worker_sha256',[]):
  old_key=dict(key,worker=prior);digest=hashlib.sha256(json.dumps(old_key,sort_keys=True).encode()).hexdigest();old=CACHE/'portrait_detail'/digest
  if old==cache or not old.is_dir():continue
  receipts={};files={}
  for stage in STAGES:
   receipt=old/(stage+'-completed.json')
   if not receipt.is_file():break
   try:
    data=json.loads(receipt.read_text(encoding='utf-8'));items=data.get('files',{})
    if data.get('status')!='completed' or data.get('recipe')!=key['recipe'] or not isinstance(items,dict) or not items:break
    valid=True
    for name,expected in items.items():
     if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:valid=False;break
     path=(old/name).resolve()
     if old.resolve() not in path.parents or not path.is_file() or sha(path)!=expected:valid=False;break
    if not valid:break
   except (OSError,ValueError,TypeError):break
   receipts[stage]=receipt;files.update(items)
  if not receipts:continue
  for name,expected in files.items():
   source=old/name;dest=cache/name;dest.parent.mkdir(parents=True,exist_ok=True)
   if dest.exists():
    if sha(dest)!=expected:raise ValueError('升级缓存与本次未完成缓存不一致，未覆盖任何旧作品；请检查人物细节缓存。')
    continue
   pending=dest.with_name(dest.name+'.import.pending')
   if pending.exists():
    if sha(pending)==expected:pending.replace(dest);continue
    pending.unlink()
   try:os.link(source,pending)
   except OSError:shutil.copy2(source,pending)
   if sha(pending)!=expected:raise ValueError('升级缓存复制校验失败，已保留原缓存。')
   pending.replace(dest)
  for stage,source in receipts.items():
   dest=cache/source.name;pending=dest.with_suffix('.import.pending');shutil.copy2(source,pending);pending.replace(dest)
  save(cache/'upgrade-cache-import.json',{'status':'completed','old_key':old_key,'new_key':key,'from_cache':str(old),'stages':list(receipts),'compatibility_sha256':sha(declaration),'source_receipts_unchanged':True})
  log('复用升级前已校验画面缓存：'+','.join(receipts)+'；旧缓存和旧成片保持原样。');return
def build(project,audio,settings,output,log,preview=None):
 from .service import call,PY
 signature_value=signature(audio,settings);model_pins=pins();assets=template_path(settings).parent
 key={'recipe':'original-lip-smooth-fp32-v1','worker':sha(PROGRAM/'core/portrait/detail_worker.py'),'source':sha(assets/'portrait.png'),'landmarks':sha(assets/'landmarks68.npy'),
      'template':sha(template_path(settings)),'audio':sha(audio),'models':model_pins,'framing':settings['framing'],'blink':validate_blink(settings.get('blink'),duration_wav(audio)),'fps':30}
 digest=hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest();cache=CACHE/'portrait_detail'/digest
 cache.mkdir(parents=True,exist_ok=True);import_compatible_cache(key,cache,log)
 folder=project/'05-人物口播/任务'/('细节-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'));folder.mkdir(parents=True)
 job=folder/'制作任务.json';data={'schema':3,'template':str(template_path(settings)),'audio':str(audio),'cache':str(cache),'framing':settings['framing'],'blink':key['blink'],'preview':preview,'signature':signature_value,'key':key}
 save(job,data);worker=PROGRAM/'core/portrait/detail_worker.py'
 from .photo_service import valid as verified_cache
 for stage in STAGES:
  receipt=cache/(stage+'-completed.json');valid=False
  if receipt.exists():
   try:
    valid=verified_cache(cache,stage,[])
   except (OSError,ValueError,TypeError,KeyError):valid=False
   if stage=='blink' and key['blink']['mode']=='off':valid=True
  if valid:log('复用已校验缓存：'+{'blink':'自然眨眼','predict':'嘴型','cavity':'口腔与原图口周','track':'平滑下唇轨迹'}[stage]);continue
  if receipt.exists():
   log('细节缓存校验未通过，保留旧缓存并重新计算：'+stage)
  call([PY,'-X','utf8','-u',worker,'--job',job,'--stage',stage],log)
 if sha(audio)!=signature_value['audio_sha256']:raise ValueError('配音在制作中发生变化。')
 call([PY,'-X','utf8','-u',worker,'--job',job,'--stage','encode','--output',output],log)
 return unpack_paths(json.loads(job.with_name('制作结果.json').read_text(encoding='utf-8')))
def burn_cached(video,subtitles,output,log):
 from .service import call,PY
 meta=unpack_paths(json.loads(Path(video).with_suffix('.detail.json').read_text(encoding='utf-8')))
 if meta.get('recipe')=='photo-lip-speech-v2':
  from .photo_service import burn_cached as photo_burn
  return photo_burn(video,subtitles,output,log)
 if meta.get('recipe')!='original-lip-smooth-fp32-v1':raise ValueError('原始画面版本不兼容，请重新生成母版。')
 if sha(video)!=meta['video_sha256']:raise ValueError('视频已变更，不能使用原细节画面缓存。')
 job=inside(meta['job']);data=unpack_paths(json.loads(job.read_text(encoding='utf-8')));cache=inside(data['cache'])
 assets=inside(data['template']).parent
 if sha(assets/'portrait.png')!=data['key']['source'] or sha(assets/'landmarks68.npy')!=data['key']['landmarks'] or sha(inside(data['audio']))!=data['key']['audio']:raise ValueError('原始照片、关键点或配音已变化，不能混用旧画面缓存。')
 for stage in STAGES:
  record=json.loads((cache/(stage+'-completed.json')).read_text(encoding='utf-8'))
  if not all((cache/name).is_file() and sha(cache/name)==value for name,value in record['files'].items()):raise ValueError('用于单次编码的原始画面缓存缺失或已改变，请重新生成母版。')
 call([PY,'-X','utf8','-u',PROGRAM/'core/portrait/detail_worker.py','--job',job,'--stage','encode','--output',output,'--ass',subtitles],log)
