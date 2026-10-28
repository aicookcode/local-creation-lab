"""Resolve every executable, model, cache and font within this bundle."""
from pathlib import Path
import os,sys,json
from studio_paths import CACHE,USER_RESOURCES,PACKAGE_ROOT,legacy_path,initialize
ROOT=Path(__file__).resolve().parent.parent
PACKAGE_ROOT=ROOT.parent
MODEL_ROOT=PACKAGE_ROOT/'models'
_dll_handles=[]
_audit_installed=False

def pack_paths(value,root=ROOT):
    if isinstance(value,dict):return {k:pack_paths(v,root) for k,v in value.items()}
    if isinstance(value,list):return [pack_paths(v,root) for v in value]
    if isinstance(value,str) and Path(value).is_absolute():
        try:return 'bundle://'+Path(value).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            try:return 'models://'+Path(value).resolve().relative_to(root.parent/'models').as_posix()
            except ValueError:
                try:return 'package://'+Path(value).resolve().relative_to(root.parent.resolve()).as_posix()
                except ValueError:pass
    return value

def unpack_paths(value,root=ROOT):
    if isinstance(value,dict):return {k:unpack_paths(v,root) for k,v in value.items()}
    if isinstance(value,list):return [unpack_paths(v,root) for v in value]
    if isinstance(value,str) and value.startswith('bundle://'):
        relative=value[9:]
        if (root/'core').is_dir() and relative.startswith('models/'):
            model_root=(root.parent/'models').resolve();result=(root.parent/relative).resolve()
            if not result.is_relative_to(model_root):raise ValueError('旧任务模型路径越出了模型目录。')
            return str(result)
        if (root/'core').is_dir() and relative.startswith('program/'):relative='core/'+relative[3:]
        result=legacy_path(relative,root)
        if not result.is_relative_to(root.parent.resolve()):raise ValueError('项目资源路径越出了工作台目录。')
        return str(result)
    if isinstance(value,str) and value.startswith('package://'):
        package=root.parent.resolve();result=(package/value[10:]).resolve()
        if not result.is_relative_to(package):raise ValueError('项目资源路径越出了工作台目录。')
        return str(result)
    if isinstance(value,str) and value.startswith('models://'):
        model_root=(root.parent/'models').resolve();result=(model_root/value[9:]).resolve()
        if not result.is_relative_to(model_root):raise ValueError('模型路径越出了模型目录。')
        return str(result)
    if isinstance(value,str) and Path(value).is_absolute() and (root/'core').is_dir():
        resolved=legacy_path(value,root)
        if resolved!=Path(value).resolve():return str(resolved)
        # Older projects could store absolute paths alongside bundle:// values.
        # Relocate only known previous-layout directories, without rewriting media.
        try:
            relative=Path(value).resolve().relative_to(root.parent.resolve())
            if relative.parts and relative.parts[0] in ('projects','resources','cache','runtime','config','tests','validation','licenses','docs','backups'):return str(legacy_path(relative,root))
            if len(relative.parts)>1 and relative.parts[0]=='program' and relative.parts[1] in ('tts','video','portrait','portrait_lab','launcher'):return str(root/'core'/Path(*relative.parts[1:]))
        except ValueError:pass
    return value

def configure(gui=False):
    global _audit_installed
    config=json.loads((ROOT/'config/dependency_paths.json').read_text(encoding='utf-8'))
    for key in ['model_home','subtitle_models','main_python','subtitle_python','library','fonts']:
        p=(ROOT/config[key]).resolve()
        if not p.is_relative_to(PACKAGE_ROOT.resolve()):raise ValueError('便携依赖必须位于工作台目录内：'+key)
        config[key]=str(p)
    initialize();cache=CACHE;(cache/'temp').mkdir(parents=True,exist_ok=True)
    sys.pycache_prefix=str(cache/'pycache')
    runtime=ROOT/'runtime/python'
    qwen_runtime=ROOT/'runtime/qwen-tts'
    config['library']=str(USER_RESOURCES/'voice_library')
    os.environ.update(ZMYG_MODEL_HOME=config['model_home'],ZMYG_LIBRARY=config['library'],
        ZMYG_FFMPEG=str(ROOT/'core/video/ffmpeg.exe'),ZMYG_SOURCE_BASE=str(ROOT),
        ZMYG_SUBTITLE_MODEL=config['subtitle_models'],ZMYG_BUNDLE_ROOT=str(ROOT),ZMYG_PACKAGE_ROOT=str(PACKAGE_ROOT),
        ZMYG_PYTHON=config['main_python'],ZMYG_QWEN_RUNTIME=str(qwen_runtime),HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',
        ZMYG_CACHE_ROOT=str(cache),ZMYG_LOG_ROOT=str(PACKAGE_ROOT/'logs'),ZMYG_LAZY_MODELS='1',HF_HOME=str(cache/'huggingface'),TORCH_HOME=str(cache/'torch'),MPLCONFIGDIR=str(cache/'matplotlib'),XDG_CACHE_HOME=str(cache),PYTHONNOUSERSITE='1',PYTHONPYCACHEPREFIX=str(cache/'pycache'),NUMBA_CACHE_DIR=str(cache/'portrait-numba'),
        TCL_LIBRARY=str(runtime/'tcl/tcl8.6'),TK_LIBRARY=str(runtime/'tcl/tk8.6'),
        TMP=str(cache/'temp'),TEMP=str(cache/'temp'),PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    for key in ['PYTHONHOME','PYTHONPATH']:os.environ.pop(key,None)
    windir=Path(os.environ.get('SystemRoot','C:/Windows'))
    os.environ['PATH']=os.pathsep.join([str(runtime),str(runtime/'DLLs'),str(qwen_runtime),str(windir/'System32'),str(windir)])
    from xml.sax.saxutils import escape
    fontconfig=cache/'fonts.conf'
    fontconfig.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>'+escape((ROOT/'resources/fonts').as_posix())+'</dir><cachedir>'+escape((cache/'fontconfig').as_posix())+'</cachedir></fontconfig>',encoding='utf-8')
    os.environ['FONTCONFIG_FILE']=str(fontconfig)
    if os.environ.get('ZMYG_PORTABLE_AUDIT')=='1' and not _audit_installed:
        def audit(event,args):
            if event=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
                p=Path(os.fsdecode(args[0])).resolve()
                if not (p.is_relative_to(PACKAGE_ROOT) or p.is_relative_to(windir)):
                    raise PermissionError('便携自检禁止读取包外文件：'+str(p))
            if event=='subprocess.Popen':
                executable=args[0]
                if executable is None:
                    command=args[1]
                    if isinstance(command,(list,tuple)):executable=command[0]
                    else:executable=command[1:command.index('"',1)] if command.startswith('"') else command.split()[0]
                exe=Path(executable).resolve()
                if not exe.is_relative_to(ROOT):raise PermissionError('便携自检禁止调用包外程序：'+str(exe))
        sys.addaudithook(audit);_audit_installed=True
    if os.name=='nt':
        for p in [runtime,runtime/'DLLs']:_dll_handles.append(os.add_dll_directory(str(p)))
        if gui:
            import ctypes
            from ui_display import enable_dpi_awareness
            enable_dpi_awareness()
            for font in (ROOT/'resources/fonts').glob('*.otf'):
                if not ctypes.windll.gdi32.AddFontResourceExW(str(font),0x10,0):raise RuntimeError('内置字体未能载入：'+font.name)
    return config
