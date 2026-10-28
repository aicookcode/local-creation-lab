"""Verified model installation for the full-preload workbench.

The code distribution contains no model weights.  A model group is installed
at startup or when a repair is requested. Downloads are streamed to temporary
files, verified, and atomically renamed into the model tree so an interrupted
download can never look like a usable model.
"""
from pathlib import Path
import hashlib,json,os,time,urllib.request,urllib.error,tarfile

PROGRAM=Path(__file__).resolve().parents[1]
PACKAGE_ROOT=PROGRAM.parent
MANIFEST=PROGRAM/'config/model_download_manifest.json'
OVERRIDES=PROGRAM/'config/model_download_sources.json'
LOCK=PACKAGE_ROOT/'cache/model_download.lock'

class ModelInstallRequired(RuntimeError):
    """Raised when a required model has no configured download source."""

def _sha(path):
 value=hashlib.sha256()
 with Path(path).open('rb') as stream:
  while block:=stream.read(4*1024*1024):value.update(block)
 return value.hexdigest()

def _load(path,default):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError,TypeError):return default

def _target(relative):
 path=(PACKAGE_ROOT/str(relative)).resolve()
 if not path.is_relative_to(PACKAGE_ROOT.resolve()):raise ValueError('模型下载目标越出工作台目录。')
 return path

def _manifest():
 data=_load(MANIFEST,{})
 if not isinstance(data,dict) or not isinstance(data.get('groups'),dict):raise ValueError('模型下载清单结构无效。')
 return data

def _overrides():
 data=_load(OVERRIDES,{})
 return data if isinstance(data,dict) else {}

def _artifact_url(group,name,artifact,overrides):
 group_overrides=overrides.get(group,{}) if isinstance(overrides.get(group,{}),dict) else {}
 value=group_overrides.get(name,artifact.get('url'))
 if not value:return ''
 if str(value).startswith('hf://'):
  value=str(value)[5:];parts=value.split('/',2)
  if len(parts)<3:raise ValueError('hf模型地址必须包含组织、仓库和文件名。')
  repo=parts[0]+'/'+parts[1];file=parts[2]
  return f'https://huggingface.co/{repo}/resolve/main/{file}?download=true'
 return str(value)

def _stream(url,target,group,report,label=''):
 request=urllib.request.Request(url,headers={'User-Agent':'LocalMediaStudio/7 model installer'})
 with urllib.request.urlopen(request,timeout=120) as response, Path(target).open('wb') as stream:
  expected=response.headers.get('Content-Length');expected=int(expected) if expected and expected.isdigit() else None
  done=0;last=0
  while True:
   block=response.read(4*1024*1024)
   if not block:break
   stream.write(block);done+=len(block)
   if done-last>=32*1024*1024 or (expected and done==expected):
    suffix=f'/{expected/1024/1024:.0f}MB' if expected else ''
    report(f'正在下载{group}模型：{label or Path(target).name}（{done/1024/1024:.0f}MB{suffix}）');last=done

def _extract_archive(url,target,group,report):
 base,member=url[8:].split('#',1)
 folder=PACKAGE_ROOT/'cache/model_archives';folder.mkdir(parents=True,exist_ok=True)
 archive=folder/(hashlib.sha256(base.encode()).hexdigest()[:24]+'.archive')
 if not archive.is_file():
  partial=archive.with_suffix('.partial')
  try:_stream(base,partial,group,report,'model archive');os.replace(partial,archive)
  except Exception:
   partial.unlink(missing_ok=True);raise
 target=Path(target);partial=target.with_name(target.name+'.partial');target.parent.mkdir(parents=True,exist_ok=True)
 try:
  with tarfile.open(archive,'r:*') as bundle:
   item=bundle.getmember(member);source=bundle.extractfile(item)
   if source is None:raise ValueError('archive member is not a file: '+member)
   with partial.open('wb') as stream:
    while block:=source.read(4*1024*1024):stream.write(block)
  return partial
 except Exception:
  partial.unlink(missing_ok=True);raise

def _local_source(artifact):
 """Reuse a verified model installation from a sibling workbench package."""
 siblings=[]
 try:siblings=[p for p in PACKAGE_ROOT.parent.iterdir() if p.is_dir() and p.resolve()!=PACKAGE_ROOT.resolve() and (p/'models').is_dir()]
 except OSError:pass
 for previous in siblings:
  candidate=(previous/str(artifact['path'])).resolve()
  if candidate.is_file() and candidate.is_relative_to(previous.resolve()):return candidate
 return None

def _valid(artifact):
 path=_target(artifact['path'])
 if not path.is_file():return False
 size=artifact.get('size')
 if isinstance(size,int) and path.stat().st_size!=size:return False
 digest=artifact.get('sha256')
 return not digest or _sha(path)==str(digest).lower()

def status(group):
 data=_manifest();info=data['groups'].get(group)
 if not info:raise KeyError('未知模型组：'+str(group))
 files=info.get('artifacts',[])
 ready=[a for a in files if _valid(a)]
 return {'group':group,'label':info.get('label',group),'ready':len(ready),'total':len(files),'complete':len(ready)==len(files),'missing':[a['path'] for a in files if a not in ready]}

def _lock():
 LOCK.parent.mkdir(parents=True,exist_ok=True)
 handle=LOCK.open('a+b')
 if os.name!='nt':return handle
 import msvcrt
 deadline=time.monotonic()+30
 while True:
  try:
   handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);return handle
  except OSError:
   if time.monotonic()>deadline:handle.close();raise TimeoutError('模型下载正在被另一个工作台窗口使用，请稍后重试。')
   time.sleep(.2)

def _unlock(handle):
 try:
  if os.name=='nt':
   handle.seek(0);__import__('msvcrt').locking(handle.fileno(),__import__('msvcrt').LK_UNLCK,1)
 finally:handle.close()

def _download(group,artifact,url,report):
 target=_target(artifact['path']);target.parent.mkdir(parents=True,exist_ok=True)
 partial=target.with_name(target.name+'.partial')
 try:
  if url.startswith('archive+'):
   generated=_extract_archive(url,target,group,report)
   if generated!=partial:os.replace(generated,partial)
  else:_stream(url,partial,group,report,target.name)
  if not _valid({**artifact,'path':str(partial.relative_to(PACKAGE_ROOT))}):raise ValueError('下载完成但校验不通过：'+target.name)
  os.replace(partial,target)
 except Exception:
  partial.unlink(missing_ok=True);raise

def ensure(group,report=print):
 """Install and return the requested model files; never runs at import time."""
 data=_manifest();info=data['groups'].get(group)
 if not info:raise KeyError('未知模型组：'+str(group))
 if status(group)['complete']:return [_target(a['path']) for a in info.get('artifacts',[])]
 handle=_lock()
 try:
  overrides=_overrides();missing=[]
  for artifact in info.get('artifacts',[]):
   if _valid(artifact):continue
   if artifact.get('generated'):
    target=_target(artifact['path']);target.parent.mkdir(parents=True,exist_ok=True)
    wav2lip=next((a for a in info.get('artifacts',[]) if Path(a['path']).name=='wav2lip.pth'),None)
    expected=str((wav2lip or {}).get('sha256',''))
    target.write_text(json.dumps({'schema':1,'verified':True,'expected':expected},indent=2),encoding='utf-8')
    if not _valid(artifact):raise RuntimeError('无法生成模型完整性记录：'+str(target))
    continue
   name=Path(artifact['path']).name;local=_local_source(artifact)
   if local:
    target=_target(artifact['path']);target.parent.mkdir(parents=True,exist_ok=True);partial=target.with_name(target.name+'.partial')
    report(f"正在复用本机已有的{info.get('label',group)}：{name}")
    try:
     import shutil
     shutil.copy2(local,partial)
     if not _valid({**artifact,'path':str(partial.relative_to(PACKAGE_ROOT))}):raise ValueError('本机已有文件校验不通过。')
     os.replace(partial,target);continue
    except Exception:
     partial.unlink(missing_ok=True)
   url=_artifact_url(group,name,artifact,overrides)
   if not url:missing.append(artifact['path']);continue
   report(f"正在准备{info.get('label',group)}，首次使用会下载模型文件……")
   try:_download(group,artifact,url,report)
   except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:raise RuntimeError(f"{info.get('label',group)}下载失败：{exc}。网络恢复后可直接重试，已完成的文件会保留。") from exc
  if missing:
   source=OVERRIDES.relative_to(PACKAGE_ROOT).as_posix()
   raise ModelInstallRequired(f"{info.get('label',group)}尚未安装。请在{source}填写对应下载地址后重试；需要文件：{', '.join(missing)}")
  if not status(group)['complete']:raise RuntimeError(f"{info.get('label',group)}安装后校验仍未通过，请检查下载源和磁盘空间。")
  record=PACKAGE_ROOT/'models'/'install_records.json';installed=_load(record,{})
  if not isinstance(installed,dict):installed={}
  installed[group]={'installed_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'files':[a['path'] for a in info.get('artifacts',[])]}
  record.parent.mkdir(parents=True,exist_ok=True);record.write_text(json.dumps(installed,ensure_ascii=False,indent=2),encoding='utf-8')
  return [_target(a['path']) for a in info.get('artifacts',[])]
 finally:_unlock(handle)

def ensure_all(report=print):
 """Prepare every model group during the version-7 startup preflight."""
 groups=list(_manifest()['groups'])
 for group in groups:ensure(group,report)
 return {group:status(group) for group in groups}


