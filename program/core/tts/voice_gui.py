from error_guidance import show_error
"""Guided voice creation; saved profiles are opt-in, never startup inputs."""
from voice_actions import *
from ui_kit import heading,section,field
from voice_style import parse_style_prompt,style_summary

class ModeSelector:
 def __init__(self,owner):self.owner=owner
 def select(self,page=None):
  if page is not None and self.owner.on_mode:self.owner.on_mode('clone' if page==self.owner.clone_page else 'speech')

class VoiceApp(VoiceActions):
 def __init__(self,app,bootstrap=False,parent=None,output_getter=None,on_generated=None,can_start=None,clone_parent=None,speech_parent=None,on_mode=None,on_transfer=None,on_progress=None,on_library_changed=None):
  self.app=app;self.output_getter=output_getter;self.on_generated=on_generated;self.can_start=can_start;self.on_progress=on_progress;self.on_library_changed=on_library_changed;self.last_job_success=True
  self.events=queue.Queue();self.busy=False;self.voices={};self.last_outputs={};self.last_output=None;self.last_created_id=None;self.action_buttons=[];self.speech_output=None;self.clone_preview=None;self.on_mode=on_mode;self.on_transfer=on_transfer;self.speech_meta={}
  self.presets=json.loads(Path(__file__).with_name('tuning-presets.json').read_text(encoding='utf-8'))
  self.clone_page=clone_parent if clone_parent is not None else ttk.Frame(parent if parent is not None else app)
  self.generate_page=speech_parent if speech_parent is not None else ttk.Frame(parent if parent is not None else app)
  if clone_parent is None:self.clone_page.pack(fill='both',expand=True)
  if speech_parent is None:self.generate_page.pack(fill='both',expand=True)
  heading(self.clone_page,'声音克隆','提交参考录音，建立或调整一个可复用音色。保存、试听、校验与音色包导出都在此完成。')
  heading(self.generate_page,'语音生成','手动选择已有音色并输入正文，生成、试听、检查并导出音频。可独立使用，也可主动发送到视频生成。')
  self.status=tk.StringVar();self.clone_status=tk.StringVar(value='提交参考录音，或复制已有音色开始调整。');self.speech_status=tk.StringVar(value='选择本次音色并填写正文。');self.active_mode='clone';self.operation_mode='clone';self.next_mode=None;self.last_status=''
  for page,var in ((self.clone_page,self.clone_status),(self.generate_page,self.speech_status)):ttk.Label(page,textvariable=var,wraplength=850,style='Good.TLabel').pack(anchor='w',pady=(0,12))
  self.tabs=ModeSelector(self);self.build_generate();self.build_clone();self.build_finishers()
  app.after(150,self.poll);self.refresh_voices()
 def build_generate(self):
  p=self.generate_page;box=section(p,'选择本次音色','从已有档案中手动选择，或使用“声音克隆”提交新的参考录音。')
  row=ttk.Frame(box);row.pack(fill='x');self.voice_var=tk.StringVar();self.voice_box=ttk.Combobox(row,textvariable=self.voice_var,state='readonly');self.voice_box.pack(side='left',fill='x',expand=True)
  self.voice_box.bind('<<ComboboxSelected>>',self.voice_changed);self.button(row,'刷新列表',self.refresh_voices,side='left',padx=7)
  self.info_var=tk.StringVar();ttk.Label(box,textvariable=self.info_var,wraplength=820,style='Good.TLabel').pack(anchor='w',pady=8)
  row=ttk.Frame(box);row.pack(fill='x')
  for label,fn in [('听参考录音',self.play_reference),('复制并调整',self.clone_current),('导入音色备份',self.import_voice),('导出所选音色',self.export_voice)]:self.button(row,label,fn,side='left',padx=(0,7))
  row=ttk.Frame(box);row.pack(fill='x',pady=(7,0));self.button(row,'删除所选音色',self.delete_selected_voice,side='left');self.button(row,'恢复已删除音色',self.restore_deleted_voice,side='left',padx=7)
  ttk.Label(box,text='删除只从音色列表移除，可恢复；不清理磁盘空间，已有音频、视频和项目保留。',wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=5)
  box=section(p,'输入本次正文','填写实际需要朗读的内容，不要输入“温柔地说”等舞台提示。先用约25–30字验证，再生成长内容。')
  self.content=ScrolledText(box,height=8,wrap='word',font=('Microsoft YaHei UI',11,'normal'),undo=True);self.content.pack(fill='x')
  ttk.Label(box,text='建议使用自然标点。逗号与句号会影响停顿；固定参考有助于稳定音色，但不同文本的韵律仍会变化。',wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=8)
  self.segmentation_var=tk.StringVar()
  choice_row=ttk.Frame(box);choice_row.pack(fill='x',pady=5);ttk.Label(choice_row,text='生成方式',width=16).pack(side='left')
  self.segmentation_box=ttk.Combobox(choice_row,textvariable=self.segmentation_var,values=['自动（短文整段，长文按句）','整段（须在音色安全长度内）','按段落（保留换行分段）'],state='readonly');self.segmentation_box.pack(side='left',fill='x',expand=True)
  ttk.Label(box,text='请为本次任务选择。Qwen每段检查正文和结尾后才合并；无法确认的杂音会停止生成，原片段保留。',wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=6)
  row=ttk.Frame(box);row.pack(fill='x')
  self.generate_button=self.button(row,'生成本次配音',lambda:self.generate(False),side='left');self.generate_button.configure(style='Primary.TButton')
  self.play_button=self.button(row,'播放配音',self.play_latest,side='left',padx=8);self.accept_button=self.button(row,'记录音色认可',self.accept_current,side='left')
 def build_clone(self):
  p=self.clone_page;box=section(p,'新建音色，或从已有档案调整','新建时直接提交参考录音。调整旧音色时，手动选择一个档案复制为新版本。')
  row=ttk.Frame(box);row.pack(fill='x');self.clone_source_var=tk.StringVar();self.clone_source_box=ttk.Combobox(row,textvariable=self.clone_source_var,state='readonly');self.clone_source_box.pack(side='left',fill='x',expand=True);self.button(row,'复制为新版本',self.copy_selected_profile,side='left',padx=6);self.button(row,'导入音色包',self.import_voice,side='left')
  row=ttk.Frame(box);row.pack(fill='x',pady=(7,0));self.button(row,'删除所选音色',lambda:self.delete_selected_voice('clone'),side='left');self.button(row,'恢复已删除音色',self.restore_deleted_voice,side='left',padx=7)
  ttk.Label(box,text='删除前会显示音色名称供确认；只移出列表，可恢复，不删除已有作品或释放磁盘空间。',wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=5)
  box=section(p,'提交参考录音','使用单人、清晰、无音乐的完整句子，建议5–10秒。片段范围与参考文字须对应；不要用多个人混说的音频。')
  self.name_var=tk.StringVar();self.reference_var=tk.StringVar();self.tags_var=tk.StringVar();self.note_var=tk.StringVar()
  field(box,'音色名称',self.name_var,width=35,note='用于保存和检索；请自己命名。')
  self.engine_var=tk.StringVar(value='Qwen · 本地首选');self.engine_box=field(box,'生成模型',self.engine_var,['Qwen · 本地首选','ZipVoice · 仅非商业测试'],note='Qwen为推荐起点；ZipVoice可作为另一种音色表现的尝试。');self.engine_box.bind('<<ComboboxSelected>>',self.reset_recipe)
  row=ttk.Frame(box);row.pack(fill='x',pady=5);ttk.Label(row,text='参考录音',width=16).pack(side='left');ttk.Entry(row,textvariable=self.reference_var).pack(side='left',fill='x',expand=True);self.button(row,'选择音频',self.choose_reference,side='left',padx=7);self.button(row,'麦克风录音',self.record_reference,side='left')
  self.start_var=tk.StringVar();self.duration_var=tk.StringVar();field(box,'片段开始（秒）',self.start_var,width=10,note='例如0，从音频开头开始。请明确填写。');field(box,'片段长度（秒）',self.duration_var,width=10,note='建议5–10秒；范围2–30秒，尽量截取完整句。')
  ttk.Label(box,text='参考文字（与所截片段一致）').pack(anchor='w',pady=(8,3));self.transcript=ScrolledText(box,height=3,wrap='word',font=('Microsoft YaHei UI',10,'normal'));self.transcript.pack(fill='x')
  self.reference_help=tk.StringVar();ttk.Label(box,textvariable=self.reference_help,wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=5)
  field(box,'标签（可选）',self.tags_var,width=30);field(box,'备注（可选）',self.note_var,width=40)
  box=section(p,'模型推荐配置与调优','推荐参数已载入，不含参考录音或正文。调整后保存为一个新版本，既有音色档案不会被改写。')
  limits=ttk.LabelFrame(box,text='调整范围与能力限制',padding=(10,8));limits.pack(fill='x',pady=(0,10))
  self.voice_limits_label=ttk.Label(limits,text='语速：0.85–1.15倍；音高：-3至+3半音，0保留原声。两种模型均支持。\n音高通过音频后处理改变高低及声音色彩，不能精确指定年龄、性别；偏移过大可能不自然。\n“沧桑”只以-1半音、0.94倍速作为近似试听起点；沙哑、气声、温柔、悲伤、方言等需要合适参考录音，不能保证靠参数实现。\n自然语言按本地常见词规则解析，不是通用对话理解。请核对解析结果；未识别项保留原值，含否定或冲突的相关要求不自动应用，越界数值会限制并提示。\n高级采样参数只调整生成变化，不能可靠控制情绪。调整后另存新音色，用同一句话试听；满意后在语音生成或快捷制作中选择新版本。',wraplength=790,justify='left',style='Muted.TLabel');self.voice_limits_label.pack(fill='x',anchor='w')
  self.speed_var=tk.StringVar();field(box,'语速',self.speed_var,width=10,note='1.00为原速；更小更慢。范围0.85–1.15，建议每次只改0.02–0.05。')
  self.pitch_var=tk.StringVar(value='0');field(box,'音高偏移（半音）',self.pitch_var,width=10,note='范围-3至+3；负数更低沉，正数更明亮，0保留原声。通过音频后处理调整，不能精确改变年龄或性别。')
  ttk.Label(box,text='自然语言调整（可选）').pack(anchor='w',pady=(8,3));self.style_prompt=ScrolledText(box,height=2,wrap='word',font=('Microsoft YaHei UI',10,'normal'));self.style_prompt.pack(fill='x')
  self.style_result=tk.StringVar();self.style_applied=None;ttk.Label(box,textvariable=self.style_result,wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=5)
  self.button(box,'解析并应用自然语言调整',self.apply_style_prompt,anchor='w',pady=4)
  self.advanced=tk.BooleanVar(value=False);ttk.Checkbutton(box,text='展开高级模型参数',variable=self.advanced,command=self.toggle_advanced).pack(anchor='w',pady=5)
  self.recipe=ScrolledText(box,height=7,font=('Consolas',11,'bold'),wrap='word')
  ttk.Label(box,text='temperature：升高会更丰富，也更不稳定；降低更保守。\ntop_p：降低可收窄采样变化；seed固定有助于复现。\nZipVoice steps增加通常更慢；降噪更强可能损失细节与韵味。\n先用推荐配置，每次只改一项，用同一句话试听比较。',wraplength=820,style='Muted.TLabel').pack(anchor='w',pady=8)
  row=ttk.Frame(box);row.pack(fill='x');self.button(row,'恢复模型推荐参数',self.reset_recipe,side='left');self.button(row,'保存为新音色',self.save_voice,side='right')
  box=section(p,'为本次音色准备试听')
  self.preview_var=tk.StringVar();field(box,'试听文字',self.preview_var,width=48,note='请填写本次测试句子，不会自动带入旧文案。')
  self.button(box,'用刚保存的音色生成试听',self.preview_saved,anchor='w',pady=7)
  self.saved_hint=tk.StringVar();ttk.Label(box,textvariable=self.saved_hint,wraplength=820,style='Muted.TLabel').pack(anchor='w')
  self.reset_recipe()
 def fit_page(self):
  pass
 def toggle_advanced(self):
  self.recipe.pack(fill='x',pady=8) if self.advanced.get() else self.recipe.pack_forget();self.app.after_idle(self.fit_page)
 def refresh_voices(self,prefer=None):
  if not isinstance(prefer,str):prefer=None
  try:old=self.current_id()
  except ValueError:old=None
  ix=store.index();self.voices={f"{v['name']}  · {v['engine']} · {v['id'][:6]}":v for v in ix['voices']};self.voice_box['values']=list(self.voices)
  self.clone_source_box['values']=list(self.voices)
  if self.clone_source_var.get() not in self.voices:self.clone_source_var.set('')
  selected=prefer or old;self.voice_var.set(next((label for label,v in self.voices.items() if v['id']==selected),''));self.voice_changed()
 def voice_changed(self,event=None):
  try:
   entry,p,cfg=store.get_voice(self.current_id(),verify_models=False)
   self.info_var.set(f"模型 {p['engine']}  /  语速 {cfg.get('default_speed',1):.2f}  /  参考片段 {p['reference_info']['seconds']:.2f}秒\n需要改变参数时，点“复制并调整”并保存新版本。")
  except ValueError:self.info_var.set('尚未选择音色。')
  except Exception as e:self.info_var.set('档案不可用：'+str(e))
  self.update_buttons()
 def reset_session(self):
  self.voice_var.set('');self.clone_source_var.set('');self.last_output=None;self.last_outputs={};self.last_created_id=None;self.speech_output=None;self.clone_preview=None;self.speech_meta={}
  self.content.delete('1.0','end');self.transcript.delete('1.0','end')
  self.segmentation_var.set('')
  for var in (self.name_var,self.reference_var,self.tags_var,self.note_var,self.start_var,self.duration_var,self.preview_var):var.set('')
  self.style_prompt.delete('1.0','end');self.style_result.set('例如：语气沧桑一点、慢一些；年龄描述会提示限制，不会伪造音色变化。')
  self.engine_var.set('Qwen · 本地首选');self.reset_recipe();self.saved_hint.set('提交本次参考录音并保存，或手动选择已有档案。');self.info_var.set('尚未选择音色。');self.status.set('');self.last_status='';self.clone_status.set('提交参考录音，或复制已有音色开始调整。');self.speech_status.set('选择本次音色并填写正文。');self.output_hint.set('尚无生成结果。');self.clone_output_hint.set('尚未保存本次音色。');self.update_buttons()
 def background(self,work,done,status):
  if self.busy:return
  self.operation_mode=self.next_mode or self.active_mode;self.next_mode=None
  import settings_library as parameters
  snapshot=None;voice=None
  try:
   if self.operation_mode=='speech':
    snapshot={'speech':{'segmentation':parameters.SEGMENTS[self.segmentation_var.get()]}};voice=parameters.voice_info(self.current_id())
   else:
    cfg=self.current_recipe()
    for key in ('reference','reference_text','role','label','selection','base_sample','note','target','accepted_at','acceptance'):cfg.pop(key,None)
    snapshot={'clone':{'engine':'qwen' if self.engine_var.get().startswith('Qwen') else 'zipvoice','settings':cfg}}
   parameters.validate(self.operation_mode,snapshot)
  except Exception:snapshot=None
  mode=self.operation_mode
  def recorded_work():
   usage=parameters.begin_usage(status,mode,snapshot,project=self.output_dir(mode=='clone'),voice=voice) if snapshot else None
   try:
    result=work()
    if usage:parameters.finish_usage(usage,'completed')
    return result
   except Exception as exc:
    if usage:parameters.finish_usage(usage,'failed',error=str(exc))
    raise
  super().background(recorded_work,done,status)
 def poll(self):
  was_busy=self.busy;super().poll();text=self.status.get()
  if text!=self.last_status:
   mode=self.operation_mode if was_busy or self.busy else self.active_mode
   (self.clone_status if mode=='clone' else self.speech_status).set(text);self.last_status=text
 def generate(self,preview=False,voice_id=None):
  if self.busy:return
  if self.can_start and not self.can_start():return
  self.next_mode='clone' if preview else 'speech'
  try:super().generate(preview,voice_id)
  finally:self.next_mode=None
 def save_voice(self):
  if self.busy:return
  signature=(self.engine_var.get(),self.style_prompt.get('1.0','end').strip())
  if signature[1] and signature!=self.style_applied:
   if not self.apply_style_prompt():return
  self.next_mode='clone'
  try:super().save_voice()
  finally:self.next_mode=None
 def build_finishers(self):
  box=section(self.clone_page,'试听、检查与导出','保存音色后可独立导出备份包。音色包包含参考、文字和参数，不包含基础模型。')
  self.clone_output_hint=tk.StringVar(value='尚未保存本次音色。');ttk.Label(box,textvariable=self.clone_output_hint,wraplength=820,style='Good.TLabel').pack(anchor='w')
  row=ttk.Frame(box);row.pack(fill='x',pady=8)
  for label,fn in [('播放本次试听',self.play_clone_preview),('检查本次音色档案',self.check_clone),('导出本次音色包',self.export_clone),('用于语音生成',self.use_clone)]:self.button(row,label,fn,side='left',padx=(0,6))
  box=section(self.generate_page,'音频检查与导出','导出的是上次实际生成的文件；修改正文或音色选择后，需要重新生成才会得到新结果。')
  self.output_hint=tk.StringVar(value='尚无生成结果。');ttk.Label(box,textvariable=self.output_hint,wraplength=820,style='Good.TLabel').pack(anchor='w')
  row=ttk.Frame(box);row.pack(fill='x',pady=8)
  for label,fn in [('检查生成音频',self.check_audio),('导出音频与正文',self.export_audio),('发送到图片视频',lambda:self.send_audio('photo')),('发送到人物口播',lambda:self.send_audio('portrait'))]:self.button(row,label,fn,side='left',padx=(0,6))
 def output_dir(self,preview=False):
  path=Path(self.output_getter(preview)) if self.output_getter else OUTPUT
  path.mkdir(parents=True,exist_ok=True);return path
 def copy_selected_profile(self):
  entry=self.voices.get(self.clone_source_var.get())
  if not entry:messagebox.showinfo('请选择档案','请先选择需要复制的音色。');return
  try:
   _,profile,settings=store.get_voice(entry['id'],verify_models=False);self.engine_var.set('Qwen · 本地首选' if profile['engine']=='qwen' else 'ZipVoice · 仅非商业测试');self.reset_recipe();self.name_var.set(profile['name']+' v2');self.reference_var.set(settings['reference']);self.start_var.set('0');self.duration_var.set(f"{profile['reference_info']['seconds']:.5f}");self.transcript.delete('1.0','end');self.transcript.insert('1.0',Path(settings['reference_text']).read_text(encoding='utf-8'));self.recipe_base=copy.deepcopy(profile['settings']);self.speed_var.set(f"{profile['settings'].get('default_speed',1):.2f}");self.show_recipe(profile['settings']);self.tags_var.set(profile.get('tags',''));self.note_var.set('由已有档案复制；保存将创建新版本。');self.style_prompt.delete('1.0','end');self.style_result.set('已载入旧档案；可输入自然语言调整后另存新版本。')
  except Exception as e:show_error('档案未复制',str(e))
 def apply_style_prompt(self):
  try:
   engine='qwen' if self.engine_var.get().startswith('Qwen') else 'zipvoice'
   prompt=self.style_prompt.get('1.0','end').strip();result=parse_style_prompt(prompt,engine)
   cfg=self.current_recipe();cfg.update(result['changes']);cfg=store.validate_settings(engine,cfg)
   if 'default_speed' in result['changes']:self.speed_var.set(str(cfg['default_speed']))
   self.show_recipe(cfg)
   self.recipe_base=copy.deepcopy(cfg);self.style_result.set(style_summary(result));self.style_applied=(self.engine_var.get(),prompt)
   return True
  except Exception as e:
   self.style_result.set('未应用：'+str(e));show_error('自然语言调整未应用',str(e));return False
 def choose_reference(self):
  path=filedialog.askopenfilename(title='提交声音克隆的参考录音',filetypes=[('音频','*.wav *.mp3 *.m4a *.flac *.ogg')])
  if not path:return
  try:
   import shutil
   target=self.output_dir(True)/'参考输入'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')/Path(path).name;target.parent.mkdir(parents=True);shutil.copy2(path,target);self.reference_var.set(str(target))
  except Exception as e:show_error('参考录音未提交',str(e))
 def record_reference(self):
  if self.busy:return
  if self.can_start and not self.can_start():return
  if hasattr(self,'recorder_dialog') and self.recorder_dialog.window.winfo_exists():self.recorder_dialog.window.lift();return
  from recorder_dialog import RecorderDialog
  def adopt(path,seconds,text):
   self.reference_var.set(str(path));self.start_var.set('0');self.duration_var.set(f'{seconds:.5f}');self.transcript.delete('1.0','end');self.transcript.insert('1.0',text);self.last_created_id=None;self.clone_preview=None;self.clone_output_hint.set('本次参考录音已更新，请保存为新音色。');self.saved_hint.set('请保存当前录音为新音色后，再生成试听。');self.clone_status.set('麦克风录音已采用。请填写音色名称，确认参考文字，再保存为新音色。')
  self.recorder_dialog=RecorderDialog(self.app,lambda:self.output_dir(True),adopt)
 def update_buttons(self):
  super().update_buttons()
  if not self.busy and hasattr(self,'speech_output'):self.play_button.configure(state='normal' if self.speech_output and Path(self.speech_output).is_file() else 'disabled')
 def play_latest(self):
  if self.speech_output and Path(self.speech_output).is_file():os.startfile(self.speech_output)
 def play_clone_preview(self):
  if self.clone_preview and Path(self.clone_preview).is_file():os.startfile(self.clone_preview)
  else:messagebox.showinfo('尚无试听','请先保存本次音色并生成试听。')
 def use_clone(self):
  if not self.last_created_id:messagebox.showinfo('尚无本次音色','请先保存一个音色。');return
  self.refresh_voices(self.last_created_id);self.tabs.select(self.generate_page)
 def send_audio(self,kind):
  if not self.speech_output or not Path(self.speech_output).is_file():messagebox.showinfo('尚无生成音频','请先在语音生成中生成本次音频。');return
  if self.on_transfer:self.on_transfer(kind)
 def export_clone(self):
  if not self.last_created_id:messagebox.showinfo('尚无本次音色','请先保存本次音色。');return
  dest=filedialog.asksaveasfilename(title='导出本次音色包',defaultextension='.zip',initialfile='音色备份.zip',filetypes=[('音色备份','*.zip')])
  if dest:
   self.next_mode='clone';self.background(lambda:store.export_voice(self.last_created_id,dest),lambda p:self.clone_output_hint.set('音色包已导出：'+Path(p).name),'正在校验并导出音色包……')
 def check_clone(self):
  if not self.last_created_id:messagebox.showinfo('尚无本次音色','请先保存音色。');return
  def done(result):
   _,profile,_=result;info=profile['reference_info'];self.clone_output_hint.set(f'档案及模型校验通过 / 参考{info["seconds"]:.2f}秒 / 峰值{info["peak"]:.3f}。请试听确认音色。')
  self.next_mode='clone';self.background(lambda:store.get_voice(self.last_created_id,verify_models=True),done,'正在校验本次音色的参考与模型……')
 def check_audio(self):
  if not self.speech_output or not Path(self.speech_output).is_file():messagebox.showinfo('尚无音频','请先生成音频。');return
  path=Path(self.speech_output)
  def work():
   import array,math,wave
   with wave.open(str(path)) as f:
    channels,rate,width,frames=f.getnchannels(),f.getframerate(),f.getsampwidth(),f.getnframes()
    if width!=2 or channels!=1 or not frames:raise ValueError('生成音频不是有效的单声道16位PCM，请查看生成日志。')
    peak=0;energy=0.;clipped=0;count=0
    while True:
     raw=f.readframes(65536)
     if not raw:break
     samples=array.array('h');samples.frombytes(raw);peak=max(peak,max(abs(x) for x in samples));energy+=sum(float(x)*x for x in samples);clipped+=sum(x in (-32768,32767) for x in samples);count+=len(samples)
   peak/=32768;rms=math.sqrt(energy/count)/32768
   result={'seconds':frames/rate,'sample_rate':rate,'channels':channels,'peak':peak,'rms_dbfs':20*math.log10(max(rms,1e-9)),'clipped_samples':clipped,'sha256':store.sha256(path)}
   path.with_suffix('.audio-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
  def done(r):self.output_hint.set(f'文件检查完成 / {r["seconds"]:.2f}秒 / {r["sample_rate"]}Hz / 剪切采样{r["clipped_samples"]}。自然度与读音请试听。')
  self.next_mode='speech';self.background(work,done,'正在检查生成音频……')
 def export_audio(self):
  if not self.speech_output or not Path(self.speech_output).is_file():messagebox.showinfo('尚无音频','请先生成音频。');return
  source=Path(self.speech_output);dest=filedialog.asksaveasfilename(title='导出生成音频及配套正文',defaultextension='.wav',initialfile='配音.wav',filetypes=[('音频','*.wav')])
  if not dest:return
  from task_progress import export_copy
  def report(line):
   if self.on_progress:self.on_progress(line)
  script=Path(self.speech_meta.get('script',''));extra=[(script,'.txt')] if not source.with_suffix('.txt').is_file() and script.is_file() else []
  self.next_mode='speech';self.background(lambda:export_copy(source,Path(dest),report,('.txt','.json','.audio-check.json'),extra),lambda path:self.output_hint.set('已导出音频与正文：'+str(path)),'正在导出音频与正文……')
 def snapshot(self):
  try:vid=self.current_id()
  except ValueError:vid=None
  fields={k:v.get() for k,v in [('name',self.name_var),('reference',self.reference_var),('tags',self.tags_var),('note',self.note_var),('start',self.start_var),('duration',self.duration_var),('preview_text',self.preview_var),('engine',self.engine_var),('speed',self.speed_var),('pitch',self.pitch_var)]}
  fields['style_prompt']=self.style_prompt.get('1.0','end').strip()
  return {'speech':{'voice_id':vid,'text':self.content.get('1.0','end').strip(),'segmentation':self.segmentation_var.get(),'audio':str(self.speech_output) if self.speech_output else '', 'meta':self.speech_meta},'clone':{'fields':fields,'style_applied':self.style_applied,'style_result':self.style_result.get(),'recipe_base':self.recipe_base,'transcript':self.transcript.get('1.0','end'),'recipe':self.recipe.get('1.0','end'),'last_created_id':self.last_created_id,'preview':str(self.clone_preview) if self.clone_preview else ''}}
 def load(self,data):
  self.reset_session();speech=data.get('speech',{});clone=data.get('clone',{});self.content.insert('1.0',speech.get('text',''));self.refresh_voices(speech.get('voice_id'));self.speech_output=Path(speech['audio']) if speech.get('audio') else None;self.speech_meta=speech.get('meta',{})
  self.segmentation_var.set(speech.get('segmentation',''))
  fields=clone.get('fields',{});variables={'name':self.name_var,'reference':self.reference_var,'tags':self.tags_var,'note':self.note_var,'start':self.start_var,'duration':self.duration_var,'preview_text':self.preview_var,'engine':self.engine_var,'speed':self.speed_var,'pitch':self.pitch_var}
  if fields:
   self.engine_var.set(fields.get('engine','Qwen · 本地首选'));self.reset_recipe()
   for key,var in variables.items():
    if key in fields:var.set(fields[key])
  if clone.get('recipe_base'):self.recipe_base=copy.deepcopy(clone['recipe_base'])
  self.style_prompt.insert('1.0',fields.get('style_prompt',''))
  applied=clone.get('style_applied');self.style_applied=tuple(applied) if isinstance(applied,(list,tuple)) and len(applied)==2 else None
  if clone.get('style_result'):self.style_result.set(clone['style_result'])
  self.transcript.insert('1.0',clone.get('transcript',''))
  if clone.get('recipe'):self.recipe.delete('1.0','end');self.recipe.insert('1.0',clone['recipe'])
  self.last_created_id=clone.get('last_created_id');self.clone_preview=Path(clone['preview']) if clone.get('preview') else None
  if self.speech_output and self.speech_output.is_file():self.last_output=self.speech_output;self.output_hint.set('已手动载入生成结果：'+self.speech_output.name)
  if self.last_created_id:self.clone_output_hint.set('已载入本次音色，可试听、检查或导出。')
  self.update_buttons()
