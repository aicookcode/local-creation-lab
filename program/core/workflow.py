from error_guidance import show_error
from studio_paths import PROJECTS,CACHE,LOGS,USER_RESOURCES,PACKAGE_ROOT,legacy_path,project_logs
"""Single-window local production workspace; original four tools remain untouched."""
import datetime,hashlib,importlib.util,json,os,queue,re,subprocess,sys,threading,traceback,uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter.scrolledtext import ScrolledText

ROOT=Path(__file__).resolve().parent.parent
CONFIG=ROOT/'config/dependency_paths.json'
from portable_bootstrap import configure,pack_paths,unpack_paths
CFG=configure()
sys.path[:0]=[str(ROOT/'core/tts'),str(ROOT/'core/video')]
import voice_store as store
import voice_gui
import subtitle_layout as captions
from portrait import config as portrait_config,service as portrait_service
spec=importlib.util.spec_from_file_location('photo_engine',ROOT/'core/video/app.py')
photo=importlib.util.module_from_spec(spec);spec.loader.exec_module(photo)

def now():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
def stamp():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
def digest(path):return store.sha256(Path(path))
def available(path):return bool(path) and Path(path).is_file()
def video_signature(audio,pictures,options):
    return {'audio_sha256':digest(audio),'pictures':{p.name:digest(p) for p in photo.image_paths(pictures)},
            'options':options,'framing':json.loads((Path(pictures)/'取景参数.json').read_text(encoding='utf-8')) if (Path(pictures)/'取景参数.json').is_file() else None}

class WorkflowCore:

    def button(self,parent,label,callback,**kwargs):
        b=ttk.Button(parent,text=label,command=callback);b.pack(**kwargs);self.buttons.append(b);return b
    def tab(self,key):self.tabs.select(self.pages[key])
    def idle(self):
        if self.busy or (hasattr(self,'tts') and self.tts.busy):messagebox.showinfo('任务进行中','请等待当前任务完成，再开始下一步或切换项目。');return False
        return True
    def can_start_tts(self):
        if self.busy:messagebox.showinfo('任务进行中','当前正在制作视频或字幕，请等待完成。');return False
        self.persist();return True
    def open_path(self,path):
        try:
            p=Path(path);p.mkdir(parents=True,exist_ok=True) if not p.suffix else None
            os.startfile(p)
        except Exception as e:show_error('无法打开',str(e))
    def audio_output(self):return self.project/'01-配音'





    def save_clicked(self):
        if self.idle():
            try:self.persist();self.status.set('项目已保存。')
            except Exception as e:show_error('保存未完成',str(e))
    def create_dirs(self):
        for d in ['01-配音','02-图片视频','03-字幕','04-成片','05-人物口播','logs']:(self.project/d).mkdir(parents=True,exist_ok=True)
    def choose_project(self):
        if not self.idle():return
        path=filedialog.askopenfilename(title='选择项目.json',initialdir=PROJECTS,filetypes=[('projects','*.json')])
        if path:
            try:self.persist();self.load_project(Path(path))
            except Exception as e:show_error('无法打开项目',str(e))
    def load_project(self,path):
        path=path.resolve();raw=json.loads(path.read_text(encoding='utf-8-sig'))
        source_root=next((p for p in path.parents if (p/'config/dependency_paths.json').is_file() and (p/'core').is_dir()),ROOT)
        d=unpack_paths(raw,source_root)
        if d.get('schema')!=1 or not d.get('id') or 'text' not in d:raise ValueError('不是本工作台的项目文件。')
        directory=path.parent
        if not directory.is_relative_to(PACKAGE_ROOT):
            import shutil
            target=PROJECTS/('导入-'+stamp()+'-'+directory.name[:35]);shutil.copytree(directory,target)
            def rebase(v):
                if isinstance(v,dict):return {k:rebase(x) for k,x in v.items()}
                if isinstance(v,list):return [rebase(x) for x in v]
                if isinstance(v,str) and Path(v).is_absolute():
                    try:return str(target/Path(v).relative_to(directory))
                    except ValueError:pass
                return v
            d=rebase(d);directory=target
            pictures=Path(d.get('pictures',''))
            if pictures.is_dir() and not pictures.is_relative_to(ROOT):
                dest=directory/'00-配图'/pictures.name;dest.mkdir(parents=True,exist_ok=True)
                for image in photo.image_paths(pictures):shutil.copy2(image,dest/image.name)
                if (pictures/'取景参数.json').is_file():shutil.copy2(pictures/'取景参数.json',dest/'取景参数.json')
                d['pictures']=str(dest)
        self.project=directory;self.data=d;self.create_dirs();self.apply_project();self.persist()
    def set_pictures(self,path):
        if not self.idle():return
        source=Path(path).resolve()
        if not source.is_dir() or not photo.image_paths(source):
            show_error('未选中图片','请选择当前层包含本组图片的文件夹。');return
        if not source.is_relative_to(PACKAGE_ROOT):
            import shutil
            target=self.project/'00-配图'/stamp()/source.name;target.mkdir(parents=True)
            for image in photo.image_paths(source):shutil.copy2(image,target/image.name)
            if (source/'取景参数.json').is_file():shutil.copy2(source/'取景参数.json',target/'取景参数.json')
            source=target
        self.pictures.set(str(source));self.persist();self.status.set('配图已保存在工作台内，可随整个文件夹迁移。')
    def choose_pictures(self):
        if not self.idle():return
        p=filedialog.askdirectory(title='选择只包含本组图片的文件夹',initialdir=USER_RESOURCES/'示例图集')
        if p:self.set_pictures(p)
    def audio_generated(self,path,voice_id):
        self.adopt_audio(Path(path),Path(path).with_suffix('.txt'));self.data['voice_id']=voice_id;self.persist();self.status.set('配音已带入第2、3步，可继续制作。')
    def adopt_audio(self,audio,script):
        self.data.update(audio=str(audio),script=str(script),mother='',subtitle='',final='',mother_signature=None,subtitle_signature=None)
        self.reviewed.set(False);self.load_editor();self.persist();self.refresh()
    def import_audio(self):
        if not self.idle():return
        audio=filedialog.askopenfilename(title='选择已有配音',filetypes=[('音频','*.wav *.mp3 *.m4a *.flac *.ogg')])
        if not audio:return
        script=filedialog.askopenfilename(title='选择这份配音实际朗读的正文TXT',initialdir=Path(audio).parent,filetypes=[('正文','*.txt')])
        if not script:return
        dest=self.project/'01-配音'/('导入-'+stamp()+Path(audio).suffix)
        import shutil
        shutil.copy2(audio,dest);shutil.copy2(script,dest.with_suffix('.txt'))
        self.tts.content.delete('1.0','end');self.tts.content.insert('1.0',Path(script).read_text(encoding='utf-8-sig'))
        self.adopt_audio(dest,dest.with_suffix('.txt'));self.status.set('已有配音与配套正文已复制到本项目。')
    def play(self,key):
        p=self.data.get(key)
        if not available(p):messagebox.showinfo('文件未就绪','请先生成或导入对应文件。');return
        self.open_path(p)

    def log(self,line):self.events.put(('log',line))
    def job(self,label,work,done):
        if not self.idle():return
        self.persist();self.busy=True;self.status.set(label+'……')
        for b in self.buttons:b.config(state='disabled')
        def run():
            try:self.events.put(('done',done,work()))
            except Exception as e:self.events.put(('error',str(e),traceback.format_exc()))
        threading.Thread(target=run,daemon=True).start()
    def poll(self):
        try:
            while True:
                e=self.events.get_nowait()
                if e[0]=='log':self.logs.insert('end',e[1]+'\n');self.logs.see('end');self.status.set('正在处理当前步骤。');continue
                self.busy=False
                for b in self.buttons:b.config(state='normal')
                try:
                    if e[0]=='done':e[1](e[2]);self.persist();self.refresh()
                    else:
                        self.logs.insert('end',e[2]+'\n');(project_logs(self.project)/('失败-'+stamp()+'.txt')).write_text(e[2],encoding='utf-8');self.status.set('任务未完成，请查看日志中的详细原因。');show_error('任务未完成',e[1])
                except Exception as exc:self.status.set('结果处理未完成，请查看日志中的详细原因。');show_error('结果处理未完成',str(exc))
        except queue.Empty:pass
        self.root.after(120,self.poll)
    def cli(self,args,label):
        args=list(args)
        if Path(args[0]).name.lower()=='python.exe':args[1:1]=['-X','utf8']
        logfile=project_logs(self.project)/(stamp()+'-'+label+'.log')
        env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',OMP_NUM_THREADS='4')
        with logfile.open('w',encoding='utf-8') as f:
            proc=subprocess.Popen([str(a) for a in args],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for line in proc.stdout:f.write(line);f.flush();self.log(line.strip())
            rc=proc.wait()
        if rc:raise RuntimeError(label+'失败。请查看日志：'+str(logfile))
    def require_audio(self):
        if not available(self.data.get('audio')):raise ValueError('先在第1步生成配音，或到项目页导入已有配音。')
        return Path(self.data['audio'])
    def plan(self):
        if not self.idle():return
        try:
            pictures=Path(self.pictures.get());selection=self.transition.get()
            if not pictures.is_dir():raise ValueError('请选择有效图片文件夹。')
            images=photo.image_paths(pictures)
            if not images:raise ValueError('选定文件夹这一层没有图片。')
        except Exception as e:show_error('无法分析',str(e));return
        def done(result):self.plan_view.delete('1.0','end');self.plan_view.insert('1.0',json.dumps(result,ensure_ascii=False,indent=2));self.status.set('已显示转场安排，尚未生成视频。')
        self.job('分析转场',lambda:photo.plan_transitions(images,selection),done)
    def render_mother(self):
        if not self.idle():return
        try:
            audio=self.require_audio();pictures=Path(self.pictures.get());opt=self.options()
            if not pictures.is_dir() or not photo.image_paths(pictures):raise ValueError('请选择包含本组图片的文件夹。')
            if not 1<=opt['seconds']<=30:raise ValueError('每张停留时间须为1–30秒。')
        except Exception as e:show_error('无法制作',str(e));return
        output=self.project/'02-图片视频'/('无字幕母版-'+stamp()+'.mp4')
        conf={**opt,'audio':str(audio),'pictures':str(pictures),'output':str(output)}
        def work():
            sig=video_signature(audio,pictures,opt)
            photo.render(conf,lambda percent,msg:self.log(f'{percent:.0f}% {msg}'),threading.Event())
            if sig!=video_signature(audio,pictures,opt):raise ValueError('制作期间输入素材发生变化，请重新生成母版。')
            return sig
        def done(sig):self.data.update(mother=str(output),mother_signature=sig,final='');self.status.set('无字幕母版已保存，可在本流程校对字幕或导出。')
        self.job('制作无字幕母版',work,done)
    def align(self):
        if not self.idle():return
        try:
            audio=self.require_audio();script=Path(self.data.get('script',''))
            if not script.is_file():raise ValueError('缺少与这份配音配套的正文TXT，请重新导入。')
        except Exception as e:show_error('无法生成字幕',str(e));return
        folder=self.project/'03-字幕'/('版本-'+stamp());sig={'audio':digest(audio),'script':digest(script)}
        prompt_args=[] if self.subtitle_prompt.get() else ['--no-prompt']
        def work():
            self.cli([CFG['subtitle_python'],ROOT/'core/video/manual_subtitles.py','--mode','align','--audio',audio,'--text',script,'--out-dir',folder,*prompt_args],'字幕识别')
            if sig!={'audio':digest(audio),'script':digest(script)}:raise ValueError('识别期间配音或正文发生变化，请重新生成字幕。')
        def done(_):self.data.update(subtitle=str(folder/'字幕.srt'),subtitle_signature=sig,final='');self.data.pop('subtitle_draft',None);self.reviewed.set(False);self.load_editor();self.status.set('字幕初稿已载入编辑框，请复听、修改并保存。')
        self.job('识别语音并生成字幕',work,done)
    def caption_modified(self,event=None):
        if self.editor.edit_modified():
            if not self.loading_editor:self.reviewed.set(False);self.caption_hint.set('字幕有未确认修改，导出前请保存并复听。')
            self.editor.edit_modified(False)
    def load_editor(self):
        self.loading_editor=True;self.editor.delete('1.0','end')
        path=self.data.get('subtitle')
        if available(path):self.editor.insert('1.0',Path(path).read_text(encoding='utf-8-sig'));self.caption_hint.set('当前字幕：'+path)
        else:self.caption_hint.set('尚未生成字幕')
        self.editor.edit_modified(False);self.loading_editor=False
    def save_captions(self):
        path=self.data.get('subtitle')
        if not available(path):raise ValueError('请先生成或导入字幕。')
        text=self.editor.get('1.0','end').strip()+'\n'
        # Parse a temporary file before replacing the user's current SRT.
        temp=Path(path).with_name('.validate-'+uuid.uuid4().hex+'.srt')
        try:
            temp.write_text(text,encoding='utf-8-sig');cues=captions.read_cues(temp);last=0
            if not cues:raise ValueError('字幕为空或SRT格式错误。')
            blocks=re.split(r'\n\s*\n',text.strip())
            if len(cues)!=len(blocks) or any(len(block.strip().splitlines())<3 or not block.strip().splitlines()[0].isdigit() or '-->' not in block.strip().splitlines()[1] for block in blocks):
                raise ValueError('每段SRT都需要序号、时间行和文字，段间留空行；有一段格式不完整。')
            duration=portrait_config.duration_wav(self.data['audio']) if available(self.data.get('audio')) else None
            for cue in cues:
                a,b=captions.time_seconds(cue['start']),captions.time_seconds(cue['end'])
                if a<last-.005 or b<=a:raise ValueError('字幕时间倒序、重叠或结束时间不正确。')
                if duration is not None and b>duration+.1:raise ValueError('字幕结束时间超出本次配音时长，请修改后保存。')
                last=b
        finally:temp.unlink(missing_ok=True)
        p=Path(path)
        if p.read_text(encoding='utf-8-sig').strip()!=text.strip():
            import shutil
            shutil.copy2(p,p.with_name('修改前-'+stamp()+'.srt'));p.write_text(text,encoding='utf-8-sig')
        self.caption_hint.set(f'已保存并检查时间格式，共{len(cues)}段；仍需复听实际同步效果。');self.persist()
        return p
    def save_captions_clicked(self):
        if not self.idle():return
        try:self.save_captions();self.status.set('字幕修改已保存。')
        except Exception as e:show_error('字幕未保存',str(e))
    def to_export(self):
        if not self.idle():return
        try:self.save_captions();self.tab('export')
        except Exception as e:show_error('请先完成字幕',str(e))
    def import_srt(self):
        if not self.idle():return
        p=filedialog.askopenfilename(title='导入与本次配音对应的SRT',filetypes=[('字幕','*.srt')])
        if not p:return
        import shutil
        folder=self.project/'03-字幕'/('导入-'+stamp());folder.mkdir();shutil.copy2(p,folder/'字幕.srt')
        self.data.update(subtitle=str(folder/'字幕.srt'),subtitle_signature=None,final='');self.reviewed.set(False);self.load_editor();self.persist();self.refresh()
    def import_mother(self):
        if not self.idle():return
        p=filedialog.askopenfilename(title='导入使用本次配音的1080×1920无字幕母版',filetypes=[('母版','*.mp4')])
        if not p:return
        import shutil
        dest=self.project/'02-图片视频'/('导入母版-'+stamp()+'.mp4');shutil.copy2(p,dest)
        self.data.update(mother=str(dest),mother_signature=None,final='');self.reviewed.set(False);self.persist();self.refresh()
    def export(self):
        if not self.idle():return
        try:
            if not self.reviewed.get():raise ValueError('请先复听字幕，并勾选校对确认。')
            subs=self.save_captions();video=Path(self.data.get('mother',''))
            if not video.is_file():raise ValueError('请先制作或导入无字幕母版。')
            if self.data.get('subtitle_signature'):
                sig={'audio':digest(self.data['audio']),'script':digest(self.data['script'])}
                if sig!=self.data['subtitle_signature']:raise ValueError('配音或配套正文已变化，请重新生成字幕。')
            previous=self.data.get('mother_signature')
            if previous is not None:
                current=portrait_config.signature(self.data['audio'],self.portrait.serialize()) if previous.get('kind')=='portrait' else video_signature(self.data['audio'],Path(self.pictures.get()),self.options())
                if previous!=current:raise ValueError('当前配音、配图或动作参数已变化，请先重新制作母版。')
        except Exception as e:show_error('无法输出',str(e));return
        output=self.project/'04-成片'/('成片-字幕版-'+stamp()+'.mp4')
        def work():self.cli([CFG['main_python'],ROOT/'core/video/manual_subtitles.py','--mode','burn','--video',video,'--subtitles',subs,'--output',output],'字幕压制')
        def done(_):self.data['final']=str(output);self.status.set('成片已保存，可播放检查。');messagebox.showinfo('成片已完成',str(output))
        self.job('输出带字幕成片',work,done)
    def diagnostics(self):
        if not self.idle():return
        checks={'主程序Python':Path(CFG['main_python']),'字幕Python':Path(CFG['subtitle_python']),'模型目录':Path(CFG['model_home']),'字幕模型缓存':Path(CFG['subtitle_models']),'新音色库':USER_RESOURCES/'voice_library/index.json','FFmpeg副本':ROOT/'core/video/ffmpeg.exe'}
        checks.update(portrait_service.prerequisites())
        lines=[('正常' if p.exists() else '缺失')+' | '+label+' | '+str(p) for label,p in checks.items()]
        self.logs.insert('end','\n'.join(lines)+'\n');self.logs.see('end');self.status.set('路径检查完成。模型文件校验还会在每次配音生成前执行。')
    def close(self):
        if not self.idle():return
        try:
            if available(self.data.get('subtitle')):self.save_captions()
            self.persist()
        except Exception as e:show_error('请先保存项目',str(e));return
        self.root.destroy()



