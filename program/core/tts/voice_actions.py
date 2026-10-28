from error_guidance import show_error
"""Desktop voice library and local reference-conditioned TTS."""
import copy,datetime,json,os,queue,subprocess,threading,tkinter as tk
from pathlib import Path
from tkinter import filedialog,messagebox,ttk
from tkinter.scrolledtext import ScrolledText
import voice_store as store
from resource_gate import production_slot

ROOT=Path(__file__).resolve().parent
from studio_paths import PROJECTS
OUTPUT=PROJECTS/'standalone_voice'

class VoiceActions:

    def output_dir(self,preview=False):
        path=Path(self.output_getter()) if self.output_getter else OUTPUT
        path.mkdir(parents=True,exist_ok=True)
        return path

    def button(self,parent,text,command,**pack):
        b=ttk.Button(parent,text=text,command=command);b.pack(**pack);self.action_buttons.append(b);return b



    def reset_recipe(self,event=None):
        engine='qwen' if self.engine_var.get().startswith('Qwen') else 'zipvoice'
        cfg=copy.deepcopy(self.presets[store.BUILTINS[engine]])
        for k in ['reference','reference_text','role','label','selection','base_sample','note']:cfg.pop(k,None)
        self.recipe_base=cfg;self.speed_var.set(f"{cfg.get('default_speed',1.):.2f}");self.show_recipe(cfg)
        self.style_applied=None
        if hasattr(self,'style_result'):self.style_result.set('推荐参数已载入。可输入“语速0.95，音高-1半音”，或描述“低沉一点、慢一些”。')
        self.reference_help.set('Qwen 本运行器不读取参考文字；可填写并保存，供归档与迁移。' if engine=='qwen' else 'ZipVoice 必填：文字须与截取片段逐字一致。当前 Emilia 权重仅限非商业测试。')

    def show_recipe(self,cfg):
        if hasattr(self,'pitch_var'):self.pitch_var.set(str(cfg.get('pitch_semitones',0.)))
        keys=['seed','temperature','top_p','repeat_penalty','chunk_limit','max_frames','gap_seconds','steps','guidance_scale','minimum_chars','maximum_chars','threads','light_denoise','denoise_nr','denoise_highpass_hz','denoise_lowpass_hz']
        self.recipe.delete('1.0','end');self.recipe.insert('1.0',json.dumps({k:cfg[k] for k in keys if k in cfg},ensure_ascii=False,indent=2))

    def current_recipe(self):
        cfg=copy.deepcopy(self.recipe_base)
        cfg.update(json.loads(self.recipe.get('1.0','end')))
        cfg['default_speed']=float(self.speed_var.get())
        cfg['pitch_semitones']=float(self.pitch_var.get()) if hasattr(self,'pitch_var') else cfg.get('pitch_semitones',0.)
        return store.validate_settings('qwen' if self.engine_var.get().startswith('Qwen') else 'zipvoice',cfg)

    def choose_reference(self):
        path=filedialog.askopenfilename(title='选择参考录音',filetypes=[('音频','*.wav *.mp3 *.m4a *.flac *.ogg'),('全部文件','*.*')])
        if path:self.reference_var.set(path)

    def current_id(self):
        if self.voice_var.get() not in self.voices:raise ValueError('请先选择已保存音色。')
        return self.voices[self.voice_var.get()]['id']



    def update_buttons(self):
        if self.busy:return
        try:vid=self.current_id()
        except ValueError:vid=None
        self.play_button.config(state='normal' if self.last_output else 'disabled')
        self.accept_button.config(state='normal' if vid in self.last_outputs else 'disabled')
        self.generate_button.config(state='normal' if vid else 'disabled')

    def background(self,work,done,status):
        if self.busy:return
        if self.can_start and not self.can_start():return
        self.busy=True;self.status.set(status)
        for b in self.action_buttons:b.config(state='disabled')
        self.voice_box.config(state='disabled');self.engine_box.config(state='disabled')
        def run():
            try:
                with production_slot(status,lambda line:self.events.put(('progress',None,line))):result=work()
                self.events.put(('done',done,result))
            except Exception as e:self.events.put(('error',None,str(e)))
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,callback,value=self.events.get_nowait()
                if kind=='progress':
                    if getattr(self,'on_progress',None):self.on_progress(value)
                    if '@@PROGRESS ' not in value:self.status.set(value)
                    continue
                self.busy=False
                self.last_job_success=kind=='done'
                for b in self.action_buttons:b.config(state='normal')
                self.voice_box.config(state='readonly');self.engine_box.config(state='readonly')
                try:
                    if kind=='done':callback(value)
                    else:self.status.set(value);show_error('操作未完成',value)
                except Exception as e:
                    self.status.set(str(e));show_error('操作未完成',str(e))
                self.update_buttons()
        except queue.Empty:pass
        self.app.after(150,self.poll)

    def clone_current(self):
        try:
            _,p,cfg=store.get_voice(self.current_id(),verify_models=False)
            self.engine_var.set('Qwen · 本地首选' if p['engine']=='qwen' else 'ZipVoice · 仅非商业测试');self.reset_recipe()
            self.name_var.set(p['name']+' v2');self.reference_var.set(cfg['reference']);self.start_var.set('0');self.duration_var.set(f"{p['reference_info']['seconds']:.5f}")
            self.transcript.delete('1.0','end');self.transcript.insert('1.0',Path(cfg['reference_text']).read_text(encoding='utf-8'))
            self.recipe_base=copy.deepcopy(p['settings']);self.speed_var.set(f"{p['settings'].get('default_speed',1.):.2f}");self.show_recipe(p['settings'])
            self.tags_var.set(p.get('tags',''));self.note_var.set('复制当前档案；改动后另存新版本');self.tabs.select(self.clone_page)
            if hasattr(self,'style_prompt'):self.style_prompt.delete('1.0','end');self.style_applied=None;self.style_result.set('已复制音色；可调整语速、音高，或填写自然语言描述。')
        except Exception as e:show_error('无法复制音色',str(e))

    def save_voice(self):
        try:
            if not self.start_var.get().strip() or not self.duration_var.get().strip():raise ValueError('请填写参考片段的开始秒数和长度，例如从0秒开始、取8秒。')
            engine='qwen' if self.engine_var.get().startswith('Qwen') else 'zipvoice'
            cfg=self.current_recipe()
            style_note=self.note_var.get().strip()
            if hasattr(self,'style_prompt') and self.style_prompt.get('1.0','end').strip():style_note=(style_note+'；' if style_note else '')+'自然语言调整：'+self.style_prompt.get('1.0','end').strip()
            params=dict(name=self.name_var.get().strip(),engine=engine,reference=self.reference_var.get(),transcript=self.transcript.get('1.0','end').strip(),settings=cfg,start=float(self.start_var.get()),duration=float(self.duration_var.get()),tags=self.tags_var.get(),note=style_note)
            if not params['name']:raise ValueError('请填写音色名称。')
            if not Path(params['reference']).is_file():raise ValueError('请先选择参考录音。')
            if engine=='zipvoice' and not params['transcript']:raise ValueError('ZipVoice 必须填写参考文字。')
        except Exception as e:show_error('请检查音色设置',str(e));return
        def done(entry):
            self.last_created_id=entry['id'];self.refresh_voices();_,p,_=store.get_voice(entry['id'],verify_models=False)
            if hasattr(self,'clone_output_hint'):self.clone_output_hint.set('已保存本次音色：'+entry['name']+'；可试听、检查并导出。')
            warning=' 参考存在满幅采样，建议复听原录音。' if p['reference_info']['clipped_samples'] else ''
            self.saved_hint.set('已保存：'+entry['name']+'。点击生成试听；满意后在生成页记录认可。'+warning)
            self.status.set('已保存独立音色档案，未覆盖已有音色。')
        self.background(lambda:store.create_voice(**params),done,'正在截取、校验并保存固定音色……')

    def preview_saved(self):
        if not self.last_created_id:messagebox.showinfo('先保存音色','请先保存当前克隆设置，再生成试听。');return
        self.generate(True,self.last_created_id)

    def generate(self,preview=False,voice_id=None):
        try:
            vid=voice_id or self.current_id();entry,p,cfg=store.get_voice(vid,verify_models=False)
            content=self.preview_var.get().strip() if preview else self.content.get('1.0','end').strip()
            if not content:raise ValueError('请输入需要朗读的正文。长文会按完整句自动分段生成。')
            choices={'自动（短文整段，长文按句）':'auto','整段（须在音色安全长度内）':'whole','按段落（保留换行分段）':'paragraph'}
            mode='auto' if preview else choices.get(self.segmentation_var.get())
            if not mode:raise ValueError('请为本次配音选择生成方式：通常使用“自动”，短文也可选“整段”。')
            profile=store.profile_path(vid);exe=store.python_path(p['engine'])
        except Exception as e:show_error('无法生成',str(e));return
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f');script=self.output_dir(preview=preview)/f"{stamp}-{'试听' if preview else '配音'}-{p['engine']}.txt"
        dest=script.with_suffix('.wav');script.write_text(content,encoding='utf-8')
        def work():
            env=os.environ.copy();env.update(PYTHONUTF8='1',OMP_NUM_THREADS='6')
            args=[str(exe),'-X','utf8','-u',str(ROOT/'local_voice.py'),'--voice-profile',str(profile),'--profile-hash',entry['profile_sha256'],'--text-file',str(script),'--out',str(dest),'--segmentation',mode]
            with script.with_suffix('.log').open('w',encoding='utf-8') as log:
                proc=subprocess.Popen(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                for line in proc.stdout:log.write(line);log.flush();self.events.put(('progress',None,line.strip()))
                code=proc.wait()
            if code:raise RuntimeError('生成失败，详情见输出文件夹内同名日志。')
            return dest
        def done(path):
            self.last_output=path;self.last_outputs[vid]=path
            if preview:
                self.clone_preview=path
                if hasattr(self,'clone_output_hint'):self.clone_output_hint.set('本次音色试听已生成：'+path.name)
            else:
                self.speech_output=path;self.speech_meta={'voice_id':vid,'script':str(script),'audio_sha256':store.sha256(path)}
                if hasattr(self,'output_hint'):
                    meta=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'));n=len(meta.get('parts',[]))
                    verified='；Qwen逐段验收通过 '+str(n)+'/'+str(n) if meta.get('quality_policy','').startswith('qwen-chunk-acceptance') else ''
                    self.output_hint.set('语音已生成：'+path.name+verified+'。请试听自然度与读音，再导出或发送到视频。')
            self.refresh_voices(None if preview else vid);self.status.set('已按固定档案生成：'+path.name)
            if self.on_generated and not preview:self.on_generated(path,vid)
        self.background(work,done,'正在校验固定音色并生成新文字……')

    def play_reference(self):
        try:_,_,cfg=store.get_voice(self.current_id(),verify_models=False);os.startfile(cfg['reference'])
        except Exception as e:show_error('无法播放',str(e))

    def play_latest(self):
        if self.last_output and self.last_output.is_file():os.startfile(self.last_output)

    def open_profile(self):
        try:os.startfile(store.profile_path(self.current_id()))
        except Exception as e:show_error('无法打开',str(e))


    def accept_current(self):
        try:vid=self.current_id();store.mark_accepted(vid,self.last_outputs[vid]);self.refresh_voices(vid);self.status.set('已记录人工认可；下次使用时请手动选择此档案。')
        except Exception as e:show_error('无法记录认可',str(e))

    def export_voice(self):
        try:vid=self.current_id()
        except ValueError as e:show_error('请选择音色',str(e));return
        dest=filedialog.asksaveasfilename(title='导出固定音色备份',defaultextension='.zip',initialfile='固定音色-'+vid[:6]+'.zip',filetypes=[('音色备份','*.zip')])
        if dest:self.background(lambda:store.export_voice(vid,dest),lambda p:self.status.set('已导出音色备份：'+str(p)),'正在导出参考、文字与固定参数……')

    def import_voice(self):
        source=filedialog.askopenfilename(title='导入固定音色备份',filetypes=[('音色备份','*.zip')])
        if not source:return
        def done(entry):self.refresh_voices(entry['id']);self.last_created_id=entry['id'];self.saved_hint.set('已导入副本：'+entry['name']+'；请重新试听确认。');self.status.set('备份已校验并导入，已有音色未覆盖。')
        self.background(lambda:store.import_voice(source),done,'正在核对备份与模型版本……')

    def delete_selected_voice(self, source='speech'):
        if self.busy:return
        try:
            entry=self.voices.get(self.clone_source_var.get()) if source=='clone' else self.voices.get(self.voice_var.get())
            if not entry:raise ValueError('请先选择要删除的音色。')
        except ValueError as exc:messagebox.showinfo('请选择音色',str(exc),parent=self.app);return
        accepted='\n此音色已有人工认可记录。' if entry.get('accepted') else ''
        if not messagebox.askyesno('删除所选音色',f"将删除音色：{entry['name']}（{entry['engine']} / {entry['id'][:6]}）{accepted}\n\n删除后将从声音克隆、语音生成及快捷制作的可选列表移除。\n档案保留，可点击“恢复已删除音色”找回；不会释放磁盘空间。\n已有音频、视频、项目及外部音色备份均保留。\n\n确定删除吗？",parent=self.app,default='no'):return
        voice_id=entry['id']
        def done(record):
            self.last_outputs.pop(voice_id,None)
            if self.last_created_id==voice_id:
                self.last_created_id=None;self.clone_preview=None
                self.saved_hint.set('本次音色已从列表删除，可恢复或保存新的音色。')
                if hasattr(self,'clone_output_hint'):self.clone_output_hint.set('本次音色已删除，历史试听文件保留。')
            self.refresh_voices()
            if getattr(self,'on_library_changed',None):self.on_library_changed()
            self.status.set('已从音色列表删除：'+record['entry']['name']+'。档案可恢复，已有作品保留。')
        self.next_mode=source
        self.background(lambda:store.delete_voice(voice_id),done,'正在从音色列表删除所选音色……')

    def restore_deleted_voice(self):
        if self.busy:return
        try:records=store.list_deleted_voices()
        except Exception as exc:show_error('无法读取已删除音色',str(exc),parent=self.app);return
        if not records:messagebox.showinfo('没有已删除音色','当前没有可恢复的音色。',parent=self.app);return
        dialog=tk.Toplevel(self.app);dialog.title('恢复已删除音色');dialog.transient(self.app)
        box=ttk.Frame(dialog,padding=16);box.pack(fill='both',expand=True)
        ttk.Label(box,text='选择要恢复的音色。恢复时会检查档案和参考文件，已有作品保留。',wraplength=540).pack(anchor='w',pady=(0,10))
        options={f"{r['entry']['name']} · {r['entry']['engine']} · {r['entry']['id'][:6]} · {r['deleted_at'][:10]}":r for r in records}
        choice=tk.StringVar();ttk.Combobox(box,textvariable=choice,values=list(options),state='readonly',width=70).pack(fill='x')
        def restore():
            record=options.get(choice.get())
            if not record:messagebox.showinfo('请选择音色','请先选择一个已删除音色。',parent=dialog);return
            if self.busy:messagebox.showinfo('任务进行中','请等待当前任务完成后再恢复音色。',parent=dialog);return
            def done(entry):
                self.refresh_voices()
                if getattr(self,'on_library_changed',None):self.on_library_changed()
                self.status.set('已恢复音色：'+entry['name']+'；使用时请手动选择。')
            self.next_mode=self.active_mode
            self.background(lambda:store.restore_voice(record['entry']['id']),done,'正在检查并恢复音色档案……')
            if self.busy:dialog.destroy()
        row=ttk.Frame(box);row.pack(fill='x',pady=(12,0))
        ttk.Button(row,text='恢复所选音色',command=restore).pack(side='left')
        ttk.Button(row,text='关闭',command=dialog.destroy).pack(side='right')

    def close(self):
        if self.busy:messagebox.showinfo('正在处理','请等待当前操作结束后关闭窗口。');return
        self.app.destroy()


