"""Supported, headless JSON-task API for local assistants. Never records microphones."""
from pathlib import Path
import argparse,copy,datetime,importlib.util,json,math,os,re,shutil,subprocess,sys,threading,uuid
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.pycache_prefix=str(Path(__file__).resolve().parents[2]/'cache/pycache')
from studio_paths import PROJECTS,CACHE,LOGS,USER_RESOURCES,PACKAGE_ROOT,legacy_path,project_logs
from portable_bootstrap import ROOT,PACKAGE_ROOT,configure,pack_paths,unpack_paths
CFG=configure();CODE=Path(__file__).resolve().parent
sys.path[:0]=[str(CODE/'tts'),str(CODE/'video')]
import voice_store as voices
import subtitle_layout as captions
from portrait import config as portrait,service
from task_progress import export_copy

FIELDS={
 'clone':{'name','engine','reference','transcript','start','duration','settings','tags','note'},
 'voice_import':{'backup'},'voice_export':{'voice_id'},
 'speech':{'voice_id','text','text_file','segmentation'},
 'photo':{'audio','images','seconds','transition','motion','strength','route'},
 'portrait_prepare':{'image','framing','regions','engine','blink'},'portrait':{'audio','settings','preview','route','text_file','mouth_reference'},
 'align':{'audio','text_file','use_prompt','route','method'},'validate_subtitles':{'audio','subtitles'},
 'export':{'video','audio','subtitles','style','review','route'},'check':{'video','audio','subtitles','text_file','route'}}
REQUIRED={
 'clone':{'name','engine','reference','start','duration'},'voice_import':{'backup'},'voice_export':{'voice_id'},
 'speech':{'voice_id'},'photo':{'audio','images','seconds','transition','motion','strength'},
 'portrait_prepare':{'image','framing'},'portrait':{'audio','settings'},'align':{'audio','text_file'},
 'validate_subtitles':{'audio','subtitles'},'export':{'video','audio','subtitles','style','review'},'check':{'video','audio'}}
OUTPUTS={
 'clone':{'voice_id','profile'},'voice_import':{'voice_id','profile'},'voice_export':{'backup'},
 'speech':{'audio','text_file','voice_id'},'photo':{'video','audio','pictures','options'},
 'portrait_prepare':{'settings','template','image'},'portrait':{'video','audio','settings'},
 'align':{'subtitles','text_file','audio','review'},'validate_subtitles':{'cues','seconds'},
 'export':{'video','audio','subtitles','review'},'check':{'report','status'}}
REF=re.compile(r'^@([A-Za-z][A-Za-z0-9_-]*)\.([a-z_]+)$')

def log(message):print(str(message),file=sys.stderr,flush=True)
def read_text(path):
 raw=Path(path).read_bytes()
 if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return raw.decode('utf-16')
 try:return raw.decode('utf-8-sig')
 except UnicodeDecodeError:return raw.decode('gb18030')
def refs(value):
 if isinstance(value,dict):
  for v in value.values():yield from refs(v)
 elif isinstance(value,list):
  for v in value:yield from refs(v)
 elif isinstance(value,str) and REF.fullmatch(value):yield REF.fullmatch(value).groups()
def validate(task):
 if not isinstance(task,dict) or task.get('schema')!=1:raise ValueError('任务必须为schema=1的JSON对象。')
 if set(task)-{'schema','name','steps','output_dir'}:raise ValueError('任务含未知顶层字段。')
 if 'output_dir' in task and (not isinstance(task['output_dir'],str) or not task['output_dir'].strip()):raise ValueError('output_dir须为用户指定的非空目录。')
 if not isinstance(task.get('steps'),list) or not 1<=len(task['steps'])<=40:raise ValueError('steps须为1–40项。')
 previous={}
 for s in task['steps']:
  if not isinstance(s,dict):raise ValueError('每个步骤必须是对象。')
  action=s.get('action');sid=s.get('id','')
  if action not in FIELDS:raise ValueError('未知操作：'+str(action))
  if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,40}',sid) or sid in previous:raise ValueError('步骤id须唯一，使用英文字母开头的字母、数字、横线或下划线。')
  if set(s)-FIELDS[action]-{'id','action'}:raise ValueError(sid+'包含未知字段，请检查拼写。')
  if REQUIRED[action]-set(s):raise ValueError(sid+'缺少字段：'+','.join(sorted(REQUIRED[action]-set(s))))
  if action=='speech' and bool(s.get('text'))==bool(s.get('text_file')):raise ValueError('speech必须且只能提交text或text_file之一。')
  if action=='align' and s.get('method','standard') not in ('standard','verified'):raise ValueError('method仅支持standard或verified。')
  if action=='speech' and s.get('segmentation','auto') not in ('auto','whole','paragraph'):raise ValueError('segmentation仅支持auto、whole、paragraph。')
  if 'route' in s and s['route'] not in ('photo','portrait'):raise ValueError('route仅支持photo或portrait。')
  if action=='export' and s['review'] not in ('ai_checked','human_confirmed'):raise ValueError('导出须明确review=ai_checked或human_confirmed。AI检查不能冒充人工验收。')
  for rid,key in refs(s):
   if rid not in previous or key not in OUTPUTS[previous[rid]]:raise ValueError('引用须指向之前步骤的有效输出：@'+rid+'.'+key)
  previous[sid]=action
 return {'status':'validated','steps':[{'id':s['id'],'action':s['action']} for s in task['steps']],'inference_started':False}

def run_process(args,logfile):
 from collections import deque
 from process_diagnostics import failure_message
 tail=deque(maxlen=80)
 with Path(logfile).open('w',encoding='utf-8') as f:
  proc=subprocess.Popen(list(map(str,args)),cwd=ROOT,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   for line in proc.stdout:f.write(line);f.flush();tail.append(line);log(line.rstrip())
  except Exception:
   proc.terminate();proc.wait();raise
  if proc.wait():raise RuntimeError(failure_message(tail,'本地步骤未完成',logfile))

def check_subtitles(audio,path):
 cues=captions.read_cues(path);duration=portrait.duration_wav(audio);last=0
 if not cues:raise ValueError('字幕为空。')
 if Path(path).suffix.lower()=='.srt':
  blocks=re.split(r'\n\s*\n',read_text(path).strip())
  if len(blocks)!=len(cues) or any(len(b.splitlines())<3 or not b.splitlines()[0].isdigit() or '-->' not in b.splitlines()[1] for b in blocks):raise ValueError('SRT有不完整的段落。')
 for c in cues:
  a,b=map(captions.time_seconds,(c['start'],c['end']))
  if not all(map(math.isfinite,(a,b))) or a<last-.005 or b<=a or b>duration+.1:raise ValueError('字幕时间倒序、重叠或超出配音。')
  last=b
 return {'cues':len(cues),'seconds':duration}

class TaskRunner:
 def __init__(self,task,path):
  validate(task);self.task=task;self.base=Path(path).resolve().parent;name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','-',str(task.get('name','AI任务')))[:35]
  self.project=PROJECTS/('AI-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+name);self.project.mkdir(parents=True)
  self.results={};self.report={'schema':1,'status':'running','task':task,'project':str(self.project),'steps':[],'human_review_required':True};self.gui={'schema':2,'id':uuid.uuid4().hex,'name':name,'voice':{},'videos':{}}
 def resolve(self,value):
  if isinstance(value,dict):return {k:self.resolve(v) for k,v in value.items()}
  if isinstance(value,list):return [self.resolve(v) for v in value]
  match=REF.fullmatch(value) if isinstance(value,str) else None
  return copy.deepcopy(self.results[match[1]][match[2]]) if match else value
 def input(self,value):
  path=Path(unpack_paths(value)).expanduser();path=path if path.is_absolute() else self.base/path
  path=path.resolve(strict=True)
  if not path.is_file():raise ValueError('输入必须是文件：'+str(path))
  return path
 def copy_input(self,value,folder,name=None):
  source=self.input(value);target=folder/'输入'/(name or source.name);target.parent.mkdir(parents=True,exist_ok=True)
  if target.exists():
   if voices.sha256(source)==voices.sha256(target):
    if source.suffix.lower() in ('.mp4','.mov','.mkv'):
     from portrait.speech_constraints import copy_provenance
     copy_provenance(source,target)
    return target
   target=target.with_name(uuid.uuid4().hex[:8]+'-'+target.name)
  shutil.copy2(source,target)
  if source.suffix.lower() in ('.mp4','.mov','.mkv'):
   from portrait.speech_constraints import copy_provenance
   copy_provenance(source,target)
  return target
 def persist(self):
  voices.atomic_json(self.project/'AI执行结果.json',pack_paths(self.report));voices.atomic_json(self.project/'项目.json',pack_paths(self.gui))
 def execute(self,s,folder):
  action=s['action'];folder.mkdir(parents=True);py=Path(CFG['main_python'])
  if action in ('clone','voice_import','voice_export'):
   if action=='voice_export':
    backup=voices.export_voice(s['voice_id'],folder/'音色备份.zip');return {'backup':str(backup)}
   if action=='voice_import':entry=voices.import_voice(self.input(s['backup']))
   else:
    engine=s['engine']
    if engine not in voices.BUILTINS:raise ValueError('engine仅支持qwen或zipvoice。')
    preset=json.loads((CODE/'tts/tuning-presets.json').read_text(encoding='utf-8'))[voices.BUILTINS[engine]]
    for key in ('reference','reference_text','role','label','selection','base_sample','note'):preset.pop(key,None)
    preset.update(s.get('settings',{}));entry=voices.create_voice(s['name'],engine,self.copy_input(s['reference'],folder),s.get('transcript',''),preset,start=float(s['start']),duration=float(s['duration']),tags=s.get('tags',''),note=s.get('note',''))
   return {'voice_id':entry['id'],'profile':str(voices.profile_path(entry['id']))}
  if action=='speech':
   entry,p,_=voices.get_voice(s['voice_id'],verify_models=False);text=s['text'] if s.get('text') else read_text(self.input(s['text_file']))
   if not isinstance(text,str) or not text.strip():raise ValueError('正文不能为空。')
   script=folder/'配音.txt';script.write_text(text.strip(),encoding='utf-8');audio=folder/'配音.wav'
   run_process([py,'-X','utf8',CODE/'tts/local_voice.py','--voice-profile',voices.profile_path(entry['id']),'--profile-hash',entry['profile_sha256'],'--text-file',script,'--out',audio,'--segmentation',s.get('segmentation','auto')],folder/'生成.log')
   return {'audio':str(audio),'text_file':str(script),'voice_id':entry['id']}
  if action=='photo':
   if not isinstance(s['images'],list) or not s['images']:raise ValueError('images须明确列出至少一张本次图片。')
   spec=importlib.util.spec_from_file_location('photo_api',CODE/'video/app.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   images=folder/'图片';images.mkdir()
   for i,value in enumerate(s['images'],1):source=self.input(value);shutil.copy2(source,images/(f'{i:03d}-'+source.name))
   audio=self.copy_input(s['audio'],folder);video=folder/'无字幕母版.mp4';options={k:s[k] for k in ('seconds','transition','motion','strength')};options['size']='1080×1920'
   module.render({**options,'audio':str(audio),'pictures':str(images),'output':str(video)},lambda p,text:log(f'{p:.0f}% {text}'),threading.Event());return {'video':str(video),'audio':str(audio),'pictures':str(images),'options':options}
  if action=='portrait_prepare':
   if s['framing'] not in ('完整显示','居中裁切'):raise ValueError('请选择framing=完整显示或居中裁切。')
   image=self.copy_input(s['image'],folder);regions=folder/'区域.json';regions.write_text(json.dumps(s.get('regions',{}),ensure_ascii=False),encoding='utf-8');target=folder/'照片准备'
   run_process([service.PY,'-X','utf8',CODE/'portrait/prepare.py','--image',image,'--out-dir',target,'--regions',regions],folder/'照片检测.log')
   engine=s.get('engine','legacy_v4')
   if engine not in ('detail_v1','detail_v2','legacy_v4'):raise ValueError('engine支持detail_v2（推荐）、detail_v1或legacy_v4。')
   settings={**portrait.defaults(),'engine':engine,'blink':s.get('blink'),'image':str(image),'template':str(target/'模板.json'),'regions':s.get('regions',{}),'framing':s['framing'],'reference_mode':'auto' if engine=='detail_v2' else ''};return {'settings':settings,'template':settings['template'],'image':str(image)}
  if action=='portrait':
   settings=s['settings'];audio=self.copy_input(s['audio'],folder);video=folder/'无字幕母版.mp4';preview=s.get('preview')
   if preview is not None:
    if not isinstance(preview,dict) or set(preview)!={'start','seconds'}:raise ValueError('preview须为start、seconds对象；完整视频不提交此字段。')
    start,length=preview['start'],preview['seconds']
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (start,length)) or start<0 or not .5<=length<=30:raise ValueError('短样开始须非负，长度0.5–30秒。')
   if settings.get('engine')=='detail_v2':
    settings=copy.deepcopy(settings);script=s.get('text_file') or settings.get('text_file')
    if not script:raise ValueError('detail_v2需要text_file，内容须与配音一致。')
    settings['text_file']=str(self.copy_input(script,folder,'口播原稿.txt'))
    reference=s.get('mouth_reference') or settings.get('mouth_reference')
    if reference:settings.update(reference_mode='photo',mouth_reference=str(self.copy_input(reference,folder,'张嘴参考'+Path(str(reference)).suffix)))
    else:settings.update(reference_mode='auto',mouth_reference='')
   service.build(folder,audio,settings,video,log,preview=preview);return {'video':str(video),'audio':str(audio),'settings':settings}
  if action=='align' and s.get('method')=='verified':
   from verified_captions import make
   original_audio=self.input(s['audio']);audio=self.copy_input(s['audio'],folder);script=self.copy_input(s['text_file'],folder,'配套正文.txt');subs=make(audio,script,folder/'subtitle',log,source_audio=original_audio);check_subtitles(audio,subs);return {'subtitles':str(subs),'text_file':str(script),'audio':str(audio),'review':'draft'}
  if action=='align':
   audio=self.copy_input(s['audio'],folder);script=self.copy_input(s['text_file'],folder,'配套正文.txt');args=[py,'-X','utf8',CODE/'video/manual_subtitles.py','--mode','align','--audio',audio,'--text',script,'--out-dir',folder/'subtitle']
   if not s.get('use_prompt',False):args+=['--no-prompt']
   run_process(args,folder/'字幕识别.log');return {'subtitles':str(folder/'subtitle/subtitle.srt'),'text_file':str(script),'audio':str(audio),'review':'draft'}
  if action=='validate_subtitles':return check_subtitles(self.input(s['audio']),self.input(s['subtitles']))
  if action=='export':
   video=self.copy_input(s['video'],folder);audio=self.copy_input(s['audio'],folder);subs=self.copy_input(s['subtitles'],folder);check_subtitles(audio,subs)
   metadata=self.input(s['video']).with_suffix('.detail.json')
   if metadata.is_file():shutil.copy2(metadata,video.with_suffix('.detail.json'))
   # The same fixed-font and width checks used by the GUI are applied here.
   style=captions.load_style()
   if s['style']!='recommended':
    if not isinstance(s['style'],dict) or set(s['style'])-{'font_size','center_y','width'}:raise ValueError('style应为recommended或font_size/center_y/width对象。')
    for key,target,lo,hi in [('font_size','font_size',48,110),('center_y','center_y_px',700,1500),('width','max_width_px',600,840)]:
     value=s['style'].get(key,style[target])
     if not isinstance(value,(int,float)) or not math.isfinite(value) or value!=int(value) or not lo<=value<=hi:raise ValueError('字幕参数超出范围：'+key)
     style[target]=int(value)
    style['safe_margin_left_px']=style['safe_margin_right_px']=(1080-style['max_width_px'])//2
   # Validate the canvas instead of silently changing framing or stretching text.
   probe=subprocess.run([str(CODE/'video/ffmpeg.exe'),'-hide_banner','-i',str(video)],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   if not re.search(r'Video:.*\b1080x1920\b',probe.stderr):raise ValueError('字幕输出需要1080×1920母版。')
   target=folder/('待人工验收.mp4' if s['review']=='ai_checked' else '字幕成片.mp4');captions.prepare_ass(subs,target.with_suffix('.ass'),style);captions.burn(video,target.with_suffix('.ass'),target,progress=log)
   return {'video':str(target),'audio':str(audio),'subtitles':str(subs),'review':s['review']}
  if action=='check':
   video=self.copy_input(s['video'],folder);audio=self.copy_input(s['audio'],folder);subs=self.copy_input(s['subtitles'],folder) if s.get('subtitles') else None;script=self.copy_input(s['text_file'],folder) if s.get('text_file') else None
   original=self.input(s['video'])
   for suffix in ('.ass','.layout.json'):
    if original.with_suffix(suffix).is_file():shutil.copy2(original.with_suffix(suffix),video.with_suffix(suffix))
   result=service.inspect(folder,video,audio,subs,script,log)
   if result['status']!='passed':raise ValueError('成片检查未通过，请查看：'+str(ROOT/result['report']))
   return {'report':str(ROOT/result['report']),'status':result['status']}
  raise ValueError('未知操作')
 def update_gui(self,s,r):
  action=s['action'];route=s.get('route') or ('photo' if action=='photo' else 'portrait' if action=='portrait' else None)
  if action=='speech':self.gui['voice']['speech']={'voice_id':r['voice_id'],'audio':r['audio'],'text':read_text(r['text_file']),'segmentation':{'auto':'自动（短文整段，长文按句）','whole':'整段（须在音色安全长度内）','paragraph':'按段落（保留换行分段）'}[s.get('segmentation','auto')],'meta':{'script':r['text_file']}}
  if route:
   data=self.gui['videos'].setdefault(route,{})
   if action in ('photo','portrait'):
    data.update(audio=r['audio'],mother=r['video'],mother_signature=None)
    if action=='photo':data.update(pictures=r['pictures'],video=r['options'])
    else:data['portrait']=r['settings']
   elif action=='align':data.update(subtitle=r['subtitles'],script=r['text_file'],source_text=read_text(r['text_file']),reviewed=False)
   elif action=='export':
    style=s['style'] if isinstance(s['style'],dict) else {};data.update(final=r['video'],final_variant='subtitled',reviewed=s['review']=='human_confirmed',output_style={'font_size':str(style.get('font_size',84)),'center_y':str(style.get('center_y',1200)),'width':str(style.get('width',810))})
    if r.get('exported'):data['published']=r['exported'];data['export_dir']=str(Path(r['exported']).parent)
   elif action=='check':data['portrait_report']=r['report']
 def run(self):
  from resource_gate import production_slot
  with production_slot('自动制作任务',log):return self._run_steps()
 def _run_steps(self):
  self.persist()
  for source in self.task['steps']:
   state={'id':source['id'],'action':source['action'],'status':'running'};self.report['steps'].append(state);self.persist()
   try:
    step=self.resolve(source);result=self.execute(step,self.project/'执行步骤'/source['id']);self.results[source['id']]=result;state['result']=result
    if step['action']=='check' and result.get('status')!='passed':raise RuntimeError('成片检查发现需要复核的项目，输出已保留。报告：'+str(result.get('report','请查看任务目录')))
    if self.task.get('output_dir') and step['action'] in ('speech','photo','portrait','export','voice_export'):
     directory=Path(self.task['output_dir']);directory=directory if directory.is_absolute() else self.base/directory
     key='video' if 'video' in result else 'audio' if 'audio' in result else 'backup';src=Path(result[key]);result['exported']=str(export_copy(src,directory/source['id']/src.name,log,('.txt','.json','.audio-check.json') if key=='audio' else ('.ass','.layout.json') if key=='video' else ()))
    state.update(status='completed');self.update_gui(step,result);self.persist()
   except Exception as exc:state.update(status='failed',error=str(exc));self.report.update(status='failed',error=str(exc));self.persist();raise
  self.report['status']='completed';self.persist();return self.report

def diagnose():
 paths={'python':Path(CFG['main_python']),'qwen_runtime':ROOT/'runtime/qwen-tts/llama-tts.exe','tts_models':Path(CFG['model_home']),'subtitle_model':Path(CFG['subtitle_models']),'fonts':Path(CFG['fonts']),'voice_library':USER_RESOURCES/'voice_library/index.json',**service.prerequisites()}
 from recognition_support import review_descriptor
 review=review_descriptor()
 return {'status':'ready' if all(p.exists() for p in paths.values()) else 'missing_components','checks':{k:{'exists':p.exists(),'path':str(p)} for k,p in paths.items()},'optional_checks':{'offline_asr_review':{'available':review.get('available',True),'path':str(PACKAGE_ROOT/'models/subtitle/faster-whisper-small')}},'microphone_opened':False}
def main():
 parser=argparse.ArgumentParser(description='本地影音工作台：AI统一执行入口。所有操作均离线，不自动录音。')
 group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--diagnose',action='store_true');group.add_argument('--list-voices',action='store_true');group.add_argument('--task',type=Path)
 parser.add_argument('--dry-run',action='store_true',help='只校验任务结构与引用，不生成素材');parser.add_argument('--result',type=Path,help='额外保存结果JSON，不能覆盖已有文件');args=parser.parse_args();runner=None
 try:
  if args.result and args.result.exists():raise FileExistsError('结果文件已存在，请换一个名称。')
  if args.diagnose:result=diagnose()
  elif args.list_voices:result={'voices':voices.list_voices(),'default_selected':False}
  else:
   task=json.loads(args.task.read_text(encoding='utf-8-sig'));validate(task)
   if args.dry_run:result=validate(task)
   else:runner=TaskRunner(task,args.task);result=runner.run()
  code=0 if result.get('status') not in ('missing_components','failed') else 1
 except Exception as exc:result={'status':'failed','error':str(exc),'project':str(runner.project) if runner else None};code=1
 result=pack_paths(result);text=json.dumps(result,ensure_ascii=False,indent=2)
 if args.result:
  try:
   if args.result.exists():
    if result.get('status')!='failed':raise FileExistsError('结果文件已存在，未覆盖。')
   else:
    args.result.parent.mkdir(parents=True,exist_ok=True)
    with args.result.open('x',encoding='utf-8') as f:f.write(text)
  except Exception as exc:result.update(status='failed',result_file_error=str(exc));text=json.dumps(result,ensure_ascii=False,indent=2);code=1
 print(text,flush=True);return code
if __name__=='__main__':raise SystemExit(main())

