from error_guidance import show_error
from studio_paths import PROJECTS,CACHE,LOGS,USER_RESOURCES,PACKAGE_ROOT,legacy_path,project_logs
from workflow import *
import copy,shutil,math
from PIL import Image
from ui_kit import ScrollPage,heading,section,field,COLORS
from portrait.panel import PortraitPanel
from task_progress import export_copy

class VideoWorkspace(WorkflowCore):
 def __init__(self,manager,parent,kind):
  self.manager=manager;self.root=manager.root;self.kind=kind;self.label='图片视频' if kind=='photo' else '人物口播';self.status=manager.status;self.data={};self.portrait=None
  self.loading_editor=False;self.buttons=manager.buttons;self.name=manager.name;self.pictures=tk.StringVar();self.project_label=tk.StringVar();self.source_info=tk.StringVar();self.summary=tk.StringVar()
  self.tabs=ttk.Notebook(parent);self.tabs.pack(fill='both',expand=True);self.pages={};self.surfaces={}
  for key,label in [('video' if kind=='photo' else 'portrait','画面制作'),('captions','字幕校对'),('export','导出与检查')]:
   page=ScrollPage(self.tabs);self.tabs.add(page,text='  '+label+'  ');self.pages[key]=page.body;self.surfaces[key]=page
  if kind=='photo':self.build_video()
  else:self.portrait=PortraitPanel(self,self.pages['portrait'])
  self.build_captions();self.build_export()
 @property
 def project(self):return self.manager.project/'视频生成'/self.label
 @property
 def busy(self):return self.manager.busy or self.manager.voice.busy
 def idle(self):return self.manager.idle()
 def button(self,*args,**kwargs):return self.manager.button(*args,**kwargs)
 def bar(self,parent):return self.manager.bar(parent)
 def tab(self,key):
  self.tabs.select(self.surfaces[key]);self.refresh()
 def persist(self):self.manager.persist()
 def log(self,line):self.manager.log(self.label+' | '+str(line))
 def job(self,label,work,done):
  self.create_dirs();self.manager.run(self,self.label+' · '+label,work,done)
 def build_source(self,parent):
  box=section(parent,'本视频的音频与正文','本页输入独立保存。可导入音频或主动接收生成结果；仅导出无字幕时正文可留空。更换音频会重置本页字幕与输出状态，旧文件和输入记录保留。')
  row=self.bar(box);self.button(row,'选择音频',self.choose_audio,side='left');self.button(row,'使用本次生成的配音',lambda:self.manager.transfer_audio(self.kind),side='left',padx=7);self.button(row,'播放本页音频',lambda:self.play('audio'),side='left')
  ttk.Label(box,textvariable=self.source_info,wraplength=850,style='Good.TLabel').pack(anchor='w',pady=7)
  ttk.Label(box,text='配套正文：填写音频实际朗读的文字，或导入TXT。').pack(anchor='w',pady=(4,4))
  self.source_editor=ScrolledText(box,height=3,wrap='word',font=('Microsoft YaHei UI',11,'normal'),undo=True);self.source_editor.pack(fill='x')
  row=self.bar(box);self.button(row,'导入正文TXT',self.import_transcript,side='left');self.button(row,'确认本页音频与正文',self.confirm_source,side='left',padx=7)
 def choose_audio(self):
  if not self.idle():return
  path=filedialog.askopenfilename(title=self.label+'：选择本次音频',filetypes=[('音频','*.wav *.mp3 *.m4a *.flac *.ogg')])
  if path:
   try:self.set_source(Path(path))
   except Exception as e:show_error('音频未提交',str(e))
 def set_source(self,audio,text=''):
  if not self.idle():return
  duration=portrait_config.duration_wav(audio)
  if not duration>0:raise ValueError('音频时长无效，请换一个文件。')
  self.create_dirs()
  if self.data.get('audio'):
   history=self.project/'历史输入'/(stamp()+'.json');store.atomic_json(history,pack_paths(self.snapshot()))
  dest=self.project/'01-配音'/('输入-'+stamp()+Path(audio).suffix);shutil.copy2(audio,dest)
  self.data.update(audio=str(dest),audio_seconds=duration,script='',mother='',subtitle='',final='',portrait_preview='',portrait_report='',mother_signature=None,subtitle_signature=None,subtitle_draft='',final_variant='')
  self.source_editor.delete('1.0','end');self.source_editor.insert('1.0',text);self.reviewed.set(False)
  if text:self.commit_source(True)
  self.load_editor();self.persist();self.refresh();self.status.set(self.label+'：音频已提交，其他视频页签不受影响。')
 def import_transcript(self):
  if not self.idle():return
  path=filedialog.askopenfilename(title='选择与本页音频对应的正文',filetypes=[('正文','*.txt')])
  if path:
   try:self.source_editor.delete('1.0','end');self.source_editor.insert('1.0',self.manager.read_text(Path(path)));self.persist()
   except Exception as e:show_error('正文未导入',str(e))
 def require_audio(self):
  if not available(self.data.get('audio')):raise ValueError('请在本视频的“画面制作”页提交音频，或主动接收本次生成的配音。')
  return Path(self.data['audio'])
 def commit_source(self,required=True):
  audio=self.require_audio();text=self.source_editor.get('1.0','end').strip()
  if required and not text:raise ValueError('请填写本页音频实际朗读的正文；生成字幕需要这份文字。')
  if not text:return
  old=Path(self.data.get('script',''))
  if old.is_file() and old.read_text(encoding='utf-8-sig').strip()==text:return
  script=self.project/'01-配音'/('正文-'+stamp()+'.txt');script.parent.mkdir(parents=True,exist_ok=True);script.write_text(text,encoding='utf-8-sig')
  self.data.update(script=str(script),subtitle='',subtitle_signature=None,subtitle_draft='',final='');self.reviewed.set(False);self.load_editor()
 def confirm_source(self):
  if not self.idle():return
  try:self.commit_source(True);self.persist();self.refresh();self.status.set(self.label+'：音频与配套正文已保存。')
  except Exception as e:show_error('请检查本页输入',str(e))
 def align(self):
  if not self.idle():return
  try:self.commit_source(True)
  except Exception as e:show_error('无法生成字幕',str(e));return
  super().align()
 def load_editor(self):
  self.loading_editor=True;self.editor.delete('1.0','end');path=self.data.get('subtitle');draft=self.data.get('subtitle_draft')
  text=draft if draft is not None else (Path(path).read_text(encoding='utf-8-sig') if available(path) else '')
  if text:self.editor.insert('1.0',text)
  self.caption_hint.set('当前字幕：'+Path(path).name if available(path) else '尚未生成字幕。未保存的编辑会作为任务草稿保留。');self.editor.edit_modified(False);self.loading_editor=False
 def save_captions(self):
  result=super().save_captions();self.data['subtitle_draft']=self.editor.get('1.0','end');return result
 def import_srt(self):
  if not self.idle():return
  self.create_dirs();self.data.pop('subtitle_draft',None);super().import_srt()
 def import_mother(self):
  if not self.idle():return
  self.create_dirs();super().import_mother()
 def snapshot(self):
  self.data.update(pictures=self.pictures.get(),source_text=self.source_editor.get('1.0','end').strip(),subtitle_draft=self.editor.get('1.0','end'),subtitle_use_prompt=self.subtitle_prompt.get(),reviewed=self.reviewed.get(),export_dir=self.export_dir.get(),output_style={k:v.get() for k,v in [('font_size',self.font_size),('center_y',self.caption_y),('width',self.caption_width)]})
  if self.kind=='photo':self.data['video']=self.options()
  else:
   self.data['portrait']=self.portrait.serialize()
   for key in ('image','template','mouth_reference','text_file'):
    if self.data['portrait'].get(key):self.data['portrait'][key]=str(portrait_config.inside(self.data['portrait'][key]))
  return copy.deepcopy(self.data)
 def load(self,data):
  self.data=copy.deepcopy(data or {});self.export_dir.set(self.data.get('export_dir',''));self.pictures.set(self.data.get('pictures',''));self.source_editor.delete('1.0','end')
  text=self.data.get('source_text')
  if text is None and available(self.data.get('script')):text=self.manager.read_text(Path(self.data['script']))
  self.source_editor.insert('1.0',text or '')
  if self.kind=='photo':
   for k,v in [('seconds',self.seconds),('transition',self.transition),('motion',self.motion),('strength',self.strength)]:
    value=self.data.get('video',{}).get(k) or '';v.set(photo.normalize_transition(value) if k=='transition' else value)
   self.plan_view.delete('1.0','end')
  else:self.portrait.load(self.data.get('portrait'))
  self.subtitle_prompt.set(bool(self.data.get('subtitle_use_prompt',False)))
  for k,v in [('font_size',self.font_size),('center_y',self.caption_y),('width',self.caption_width)]:v.set(self.data.get('output_style',{}).get(k,''))
  self.load_editor();self.reviewed.set(bool(self.data.get('reviewed',False)));self.refresh()
 def refresh(self):
  audio=self.data.get('audio');name=Path(audio).name if available(audio) else '未提交'
  self.source_info.set('本页音频：'+name+(f' / {self.data["audio_seconds"]:.2f}秒' if self.data.get('audio_seconds') else ''))
  if hasattr(self,'audio_display'):self.audio_display.set('音频：'+name+'；字幕和导出结果仅属于'+self.label+'。')
  self.caption_source.set('正在校对：'+self.label+' / '+name)
  self.export_sources.set('本流程：'+self.label+'\n母版：'+(Path(self.data['mother']).name if available(self.data.get('mother')) else '未制作')+'\n字幕：'+(Path(self.data['subtitle']).name if available(self.data.get('subtitle')) else '未生成')+'\n成片：'+(Path(self.data['final']).name if available(self.data.get('final')) else '未导出'))
  if self.kind=='photo':
   old=self.image_list.curselection();self.image_list.delete(0,'end');paths=self.image_files();names={}
   if paths and (paths[0].parent/'图片名称.json').is_file():names=json.loads((paths[0].parent/'图片名称.json').read_text(encoding='utf-8'))
   for i,path in enumerate(paths,1):self.image_list.insert('end',f'{i:02d}   {Path(names.get(path.name,path.name)).name}')
   if old and old[0]<len(paths):self.image_list.selection_set(old[0])
  elif self.portrait:self.portrait.refresh()
 def validate_mother(self):
  video=Path(self.data.get('mother',''))
  if not video.is_file():raise ValueError('请先在本流程制作或导入画面母版。')
  previous=self.data.get('mother_signature')
  if previous is not None:
   current=portrait_config.signature(self.data['audio'],self.portrait.serialize()) if previous.get('kind')=='portrait' else video_signature(self.data['audio'],Path(self.pictures.get()),self.options())
   if previous!=current:raise ValueError('本流程的音频、画面或动作已变化，请重新生成母版。')
  return video
 def export_plain(self):
  if not self.idle():return
  try:video=self.validate_mother()
  except Exception as e:show_error('尚不能导出',str(e));return
  output=self.project/'04-成片'/('无字幕成片-'+stamp()+'.mp4')
  target=self.publish_target(output)
  def work():export_copy(video,output,self.log);return self.publish(output,target)
  def done(result):self.data.update(final=str(output),final_variant='plain');self.publish_done(result)
  self.job('导出无字幕视频',work,done)
 def check_current(self):
  if not self.idle():return
  try:
   audio=self.require_audio();video=self.data.get('final')
   if not available(video):raise ValueError('请先在本流程导出成片。')
   subs=None if self.data.get('final_variant')=='plain' else self.data.get('subtitle');script=self.data.get('script') if subs else None
  except Exception as e:show_error('尚不能检查',str(e));return
  def done(result):self.data['portrait_report']=str(portrait_config.inside(result['report']));self.status.set(self.label+'：检查完成，'+('基础指标通过，仍需人工观看。' if result['status']=='passed' else '存在需复核项目，请打开报告。'))
  self.job('检查导出视频',lambda:portrait_service.inspect(self.project,video,audio,subs,script,self.log),done)
 def build_video(self):
  p=self.pages['video'];heading(p,'图片视频','提交本次配图，选择顺序、停留时长和运动方式。图像运动不会产生人物嘴型。')
  self.build_source(p)
  self.audio_display=tk.StringVar()
  box=section(p,'提交图片','支持PNG / JPG / WebP；按列表顺序播放，最后一张会衔接回第一张。')
  row=self.bar(box);self.button(row,'添加本次图片',self.choose_images,primary=True,side='left');self.button(row,'使用文件夹中的图片',self.choose_pictures,side='left',padx=8)
  self.image_list=tk.Listbox(box,height=5,font=('Microsoft YaHei UI',10,'normal'),bg=COLORS['surface'],fg=COLORS['text'],relief='flat',highlightthickness=1,highlightbackground=COLORS['line'],exportselection=False);self.image_list.pack(fill='x')
  row=self.bar(box)
  for label,fn in [('上移',lambda:self.reorder(-1)),('下移',lambda:self.reorder(1)),('移出本次列表',self.remove_image)]:self.button(row,label,fn,side='left',padx=(0,7))
  box=section(p,'设置节奏与画面','先应用推荐值，再按内容调整；这是本次任务的设置，不会在下次启动时自动带入。')
  self.seconds=tk.StringVar();self.transition=tk.StringVar();self.motion=tk.StringVar();self.strength=tk.StringVar()
  self.button(box,'应用推荐画面参数',self.photo_recipe,anchor='w',pady=(0,8))
  field(box,'每张停留（秒）',self.seconds,width=10,note='建议3秒。更短更紧凑；更长更舒缓。范围1–30秒。')
  field(box,'转场',self.transition,list(photo.TRANSITIONS),note='智能匹配相邻画面；平滑推移动感更明显，轻柔柔焦更克制。')
  field(box,'画面运动',self.motion,photo.MOTIONS,note='推近突出主体；平移展示环境；静止更适合构图已满的图片。')
  field(box,'运动幅度',self.strength,['轻柔','明显'],note='幅度越大，裁切越多；建议先用轻柔检查头部留白。')
  row=self.bar(box);self.button(row,'分析转场安排',self.plan,side='left');self.button(row,'制作图片视频母版',self.render_mother,primary=True,side='left',padx=8);self.button(row,'播放母版',lambda:self.play('mother'),side='left')
  self.plan_view=ScrolledText(p,height=5,wrap='word',font=('Microsoft YaHei UI',10,'normal'));self.plan_view.pack(fill='x',pady=8)
  self.button(p,'打开本视频的字幕校对',lambda:self.tab('captions'),anchor='e',pady=8)

 def photo_recipe(self):
  if not self.idle():return
  self.seconds.set('3');self.transition.set('智能匹配');self.motion.set('连续呼吸运镜');self.strength.set('轻柔');self.persist();self.status.set('已应用推荐节奏，可继续调整后制作。')

 def options(self):
  try:seconds=float(self.seconds.get())
  except ValueError:seconds=None
  if seconds is not None and not math.isfinite(seconds):seconds=None
  return {'seconds':seconds,'transition':self.transition.get(),'motion':self.motion.get(),'strength':self.strength.get(),'size':'1080×1920'}

 def render_mother(self):
  if not self.idle():return
  try:self.commit_source(False)
  except Exception as e:show_error('请检查配音输入',str(e));return
  o=self.options()
  if o['seconds'] is None or not math.isfinite(o['seconds']) or not 1<=o['seconds']<=30 or o['transition'] not in photo.TRANSITIONS or o['motion'] not in photo.MOTIONS or o['strength'] not in ['轻柔','明显']:
   show_error('请完成画面设置','请先应用推荐画面参数，或填写有效的停留时间并选择转场、运动和幅度。');return
  super().render_mother()

 def build_captions(self):
  p=self.pages['captions'];heading(p,'字幕校对','先生成对时初稿，再对照配音逐句检查。画面与字幕可分别重做。')
  self.caption_source=tk.StringVar();ttk.Label(p,textvariable=self.caption_source,style='Good.TLabel',wraplength=850).pack(anchor='w')
  row=self.bar(p);self.button(row,'生成字幕初稿',self.align,primary=True,side='left');self.button(row,'导入SRT',self.import_srt,side='left',padx=8);self.button(row,'播放配音',lambda:self.play('audio'),side='left')
  self.subtitle_prompt=tk.BooleanVar(value=False);ttk.Checkbutton(p,text='原稿辅助识别（默认关闭；漏字时可尝试开启后重做）',variable=self.subtitle_prompt).pack(anchor='w',pady=5)
  ttk.Label(p,text='编辑格式：序号 → 开始时间 --> 结束时间 → 字幕文字，段间空一行。\n时间不能重叠、倒序或超出配音。声音读错须重新配音；字幕不能修正声音。',wraplength=850,style='Muted.TLabel').pack(anchor='w',pady=7)
  self.editor=ScrolledText(p,height=15,wrap='word',font=('Microsoft YaHei UI',11,'normal'),undo=True);self.editor.pack(fill='x');self.editor.bind('<<Modified>>',self.caption_modified)
  self.caption_hint=tk.StringVar();ttk.Label(p,textvariable=self.caption_hint,style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=8)
  row=self.bar(p);self.button(row,'保存字幕修改',self.save_captions_clicked,side='left');self.button(row,'打开本视频的导出与检查',self.to_export,primary=True,side='right')

 def build_export(self):
  p=self.pages['export'];heading(p,'导出与检查','确认母版、字幕和本次配音一致，选择输出样式，保存一个新版本。')
  self.export_sources=tk.StringVar();ttk.Label(p,textvariable=self.export_sources,wraplength=850,style='Good.TLabel',justify='left').pack(anchor='w',pady=(0,14))
  box=section(p,'成片导出位置','可选择任意文件夹；留空时保存在当前任务内。任务中保留母版与成片原件，便于继续编辑和检查。')
  self.export_dir=tk.StringVar();row=self.bar(box);ttk.Entry(row,textvariable=self.export_dir,state='readonly').pack(side='left',fill='x',expand=True);self.button(row,'选择文件夹',self.choose_export_dir,side='left',padx=7);self.button(row,'使用任务目录',lambda:self.export_dir.set(''),side='left')
  box=section(p,'输出与字幕样式','输出为1080×1920、30FPS、H.264 MP4。字幕用内置中文粗体；固定字号，长句换行。')
  self.font_size=tk.StringVar();self.caption_y=tk.StringVar();self.caption_width=tk.StringVar()
  self.button(box,'应用竖屏安全区推荐值',self.output_recipe,anchor='w',pady=(0,8))
  field(box,'字幕字号',self.font_size,width=10,note='推荐84；变大会更醒目，也更容易换行。范围48–110。')
  field(box,'字幕中心距顶部',self.caption_y,width=10,note='推荐1200px；数值越小越靠上。范围700–1500px。')
  field(box,'字幕最大宽度',self.caption_width,width=10,note='推荐810px；更窄可避开平台两侧遮挡。范围600–840px。')
  self.reviewed=tk.BooleanVar(value=False);ttk.Checkbutton(box,text='我已复听并校对字幕，确认当前母版对应这份配音',variable=self.reviewed).pack(anchor='w',pady=12)
  row=self.bar(box);self.button(row,'导出字幕成片',self.export,primary=True,side='left');self.button(row,'播放成片',lambda:self.play('final'),side='left',padx=8);self.button(row,'打开输出文件夹',lambda:self.open_path(Path(self.export_dir.get()) if self.export_dir.get() else self.project/'04-成片'),side='left');self.button(row,'另存已有成片',self.save_final_as,side='left',padx=7)
  box=section(p,'检查后再发布','自动检查可发现黑帧、解码、音轨与字幕边界问题；动作自然度和发布页遮挡仍需人工复核。')
  row=self.bar(box);self.button(row,'运行成片检查',self.check_current,side='left');self.button(row,'打开检查报告',lambda:self.play('portrait_report'),side='left',padx=8)
  self.button(p,'已有无字幕视频？导入1080×1920母版',self.import_mother,anchor='w',pady=8)
  self.button(p,'导出无字幕版本（无需正文或字幕）',self.export_plain,anchor='w',pady=8)

 def output_recipe(self):
  if not self.idle():return
  self.font_size.set('84');self.caption_y.set('1200');self.caption_width.set('810');self.persist();self.status.set('已应用安全区字幕样式，可按实际平台预览调整。')

 def export_style(self):
  try:values=[float(v.get()) for v in (self.font_size,self.caption_y,self.caption_width)]
  except ValueError:raise ValueError('请应用推荐输出样式，或填写字号、字幕高度和宽度。')
  for value,(lo,hi) in zip(values,[(48,110),(700,1500),(600,840)]):
   if not math.isfinite(value) or value!=int(value) or not lo<=value<=hi:raise ValueError('字幕样式须为界面所示范围内的整数。')
  cfg=captions.load_style();margin=(1080-int(values[2]))//2;cfg.update(font_size=int(values[0]),center_y_px=int(values[1]),max_width_px=int(values[2]),safe_margin_left_px=margin,safe_margin_right_px=margin);return cfg

 def export(self):
  if not self.idle():return
  try:
   self.commit_source(True);style=self.export_style()
   if not self.reviewed.get():raise ValueError('请先复听、校对并勾选确认。')
   subs=self.save_captions();video=Path(self.data.get('mother',''))
   if not video.is_file():raise ValueError('请先制作或导入无字幕母版。')
   previous=self.data.get('mother_signature')
   if previous is not None:
    current=portrait_config.signature(self.data['audio'],self.portrait.serialize()) if previous.get('kind')=='portrait' else video_signature(self.data['audio'],Path(self.pictures.get()),self.options())
    if previous!=current:raise ValueError('画面参数、素材或动作已变化，请先重新生成母版。')
   if self.data.get('subtitle_signature') and self.data['subtitle_signature']!={'audio':digest(self.data['audio']),'script':digest(self.data['script'])}:raise ValueError('配音或配套文字已变化，请重新生成字幕。')
  except Exception as e:show_error('尚不能导出',str(e));return
  output=self.project/'04-成片'/('字幕成片-'+stamp()+'.mp4')
  target=self.publish_target(output)
  def work():
   captions.prepare_ass(subs,output.with_suffix('.ass'),style);captions.burn(video,output.with_suffix('.ass'),output,progress=self.log);return self.publish(output,target)
  def done(result):self.data.update(final=str(output),final_variant='subtitled');self.publish_done(result)
  self.job('导出字幕成片',work,done)

 def choose_export_dir(self):
  if not self.idle():return
  path=filedialog.askdirectory(title='选择本视频成片的导出目录')
  if path:self.export_dir.set(path);self.persist()
 def publish_target(self,output):return Path(self.export_dir.get())/output.name if self.export_dir.get() else None
 def publish(self,output,target):
  if not target:return {'published':str(output)}
  try:return {'published':str(export_copy(output,target,self.log,('.ass','.layout.json')))}
  except Exception as exc:return {'published':'','error':str(exc)}
 def publish_done(self,result):
  self.data['published']=result.get('published','');self.status.set('成片已保存，可打开导出与检查查看文件。')
  if result.get('error'):messagebox.showwarning('成片已保留，外部导出未完成',result['error']+'\n任务内成片仍完整保留，可使用“另存已有成片”重试。')
 def save_final_as(self):
  if not self.idle():return
  source=Path(self.data.get('final',''))
  if not source.is_file():messagebox.showinfo('尚无成片','请先制作并导出成片。');return
  path=filedialog.asksaveasfilename(title='另存已有成片，不重新生成',defaultextension='.mp4',initialfile=source.name,filetypes=[('视频','*.mp4')])
  if path:self.job('另存已有成片',lambda:export_copy(source,Path(path),self.log,('.ass','.layout.json')),lambda target:self.publish_done({'published':str(target)}))

 def image_files(self):
  path=self.pictures.get()
  return photo.image_paths(Path(path)) if path and Path(path).is_dir() else []

 def assign_images(self,paths):
  if not self.idle():return
  try:
   if not paths:self.pictures.set('');self.data['image_files']=[];self.persist();self.refresh();return
   dest=self.project/'00-素材/图片'/stamp();dest.mkdir(parents=True)
   names={}
   for i,path in enumerate(paths,1):
    path=Path(path);original=path.name;metadata=path.parent/'图片名称.json'
    if metadata.is_file():original=Path(str(json.loads(metadata.read_text(encoding='utf-8')).get(path.name,path.name))).name or path.name
    with Image.open(path) as im:im.verify()
    target=dest/(f'{i:03d}-'+original);shutil.copy2(path,target);names[target.name]=original
   (dest/'图片名称.json').write_text(json.dumps(names,ensure_ascii=False,indent=2),encoding='utf-8')
   self.pictures.set(str(dest));self.data['image_files']=[str(x) for x in photo.image_paths(dest)];self.data['final']='';self.reviewed.set(False);self.persist();self.refresh()
  except Exception as e:show_error('图片未提交',str(e))

 def choose_images(self):
  if not self.idle():return
  files=filedialog.askopenfilenames(title='提交本次图片，可多选',filetypes=[('图片','*.png *.jpg *.jpeg *.webp *.bmp')])
  if files:self.assign_images(self.image_files()+list(map(Path,files)))

 def set_pictures(self,path):self.assign_images(photo.image_paths(Path(path)))

 def choose_pictures(self):
  if not self.idle():return
  path=filedialog.askdirectory(title='选择本次图片所在文件夹')
  if path:
   images=photo.image_paths(Path(path))
   if not images:show_error('没有可用图片','所选文件夹当前层没有支持的图片，请换一个文件夹。');return
   self.assign_images(images)

 def reorder(self,direction):
  selected=self.image_list.curselection();paths=self.image_files()
  if not selected:return
  i=selected[0];j=i+direction
  if not 0<=j<len(paths):return
  paths[i],paths[j]=paths[j],paths[i];self.assign_images(paths);self.image_list.selection_set(j)

 def remove_image(self):
  selected=self.image_list.curselection();paths=self.image_files()
  if selected:self.assign_images([p for i,p in enumerate(paths) if i!=selected[0]])
