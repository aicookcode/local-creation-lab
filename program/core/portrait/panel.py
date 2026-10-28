from error_guidance import show_error
from studio_paths import project_logs
"""Photo submission, optional motion calibration, and an explicit action timeline."""
import copy,datetime,json,math,shutil,uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from PIL import Image,ImageOps,ImageTk
from ui_kit import heading,section,field
from . import config as cfg,service

def stamp():return datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')

class PortraitPanel:
 def __init__(self,wb,page):
  self.wb=wb;self.settings=cfg.defaults();self.history=[];self.cues=[];self.audio_cache=None
  heading(page,'人物口播','提交照片与配音，选择本次生成方案，先做短样，再校对字幕并导出。推荐方案保留原图细节，只驱动嘴型与局部眼睑。')
  if hasattr(wb,'build_source'):wb.build_source(page)
  self.info=tk.StringVar();ttk.Label(page,textvariable=self.info,wraplength=850,style='Good.TLabel').pack(anchor='w',pady=(0,12))
  box=section(page,'提交照片并准备画面','使用清晰、单人、正脸照片，嘴部无遮挡。人脸过小会降低口型细节；优先半身或近景。')
  self.image=tk.StringVar();row=ttk.Frame(box);row.pack(fill='x');ttk.Entry(row,textvariable=self.image,state='readonly').pack(side='left',fill='x',expand=True);self.button(row,'选择本次照片',self.choose_image)
  self.framing=tk.StringVar();field(box,'取景方式',self.framing,['完整显示','居中裁切'],note='完整显示保留全部画面并补边；居中裁切铺满竖屏，但可能裁掉边缘。')
  row=ttk.Frame(box);row.pack(fill='x',pady=7);self.button(row,'应用推荐取景与短样参数',self.recommend);self.button(row,'检测并准备照片',self.prepare);self.button(row,'查看检测预览',self.view_detection)
  self.photo_hint=tk.StringVar(value='尚未提交人物照片。');ttk.Label(box,textvariable=self.photo_hint,style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=5)
  box=section(page,'选择生成方案与眨眼','推荐方案按本次原稿与人声校准发音，保留原图唇纹，自动准备照片专属口腔参考。头部、身体与视线固定。')
  self.mode=tk.StringVar();self.blink=tk.StringVar();self.blink_times=tk.StringVar()
  field(box,'生成方案',self.mode,['原图唇纹＋发声校准（推荐）','原图细节＋下唇平滑（兼容）','旧版二维动作兼容'],width=32,note='旧版用于打开历史动作任务；不会混入推荐方案。')
  self.blink_box=field(box,'眼睑动作',self.blink,['自然眨眼','不添加眨眼','自定义眨眼时间'],note='推荐方案自然间隔约2.1–3.1秒，每次约0.3秒；只改变眼睑，不改变视线。')
  self.blink_input=field(box,'自定义开始时间（秒）',self.blink_times,note='仅选择“自定义”时填写，例如1.4, 6；每次眨眼占0.3秒，时间须在本次配音内。')
  self.reference_mode=tk.StringVar();self.reference_image=tk.StringVar()
  self.reference_box=field(box,'口腔参考',self.reference_mode,['自动准备','使用同人物张嘴照片'],note='自动参考由当前照片与模型生成，牙齿细节为推测；清晰的同人物、同角度张嘴图可改善口腔细节。')
  rr=ttk.Frame(box);rr.pack(fill='x',pady=5);ttk.Entry(rr,textvariable=self.reference_image,state='readonly').pack(side='left',fill='x',expand=True);self.reference_button=self.button(rr,'选择张嘴参考图',self.choose_reference)
  self.legacy_boxes=[]
  box=section(page,'可选：标注动作区域','仅做嘴型可跳过。二维轻摆不会生成新的手指或三维姿态；角度越大，边缘接缝风险越高。')
  self.legacy_boxes.append(box)
  row=ttk.Frame(box);row.pack(fill='x');self.button(row,'标注头部区域与颈部支点',lambda:self.calibrate('head'));self.button(row,'标注前臂区域与肘部支点',lambda:self.calibrate('forearm'))
  self.region_hint=tk.StringVar();ttk.Label(box,textvariable=self.region_hint,wraplength=850,style='Muted.TLabel').pack(anchor='w',pady=7)
  box=section(page,'安排本次动作时间','时间相对配音开头，单位为秒。开始→峰值为渐入；峰值→结束为回落。没有动作时只制作嘴型。')
  self.legacy_boxes.append(box)
  columns=('on','type','begin','peak','end','amp','label');self.tree=ttk.Treeview(box,columns=columns,show='headings',height=5,selectmode='browse')
  for k,title,width in [('on','启用',45),('type','动作',130),('begin','开始',65),('peak','峰值',65),('end','结束',65),('amp','幅度°',65),('label','对应句子 / 备注',230)]:self.tree.heading(k,text=title);self.tree.column(k,width=width,stretch=k=='label',minwidth=35)
  self.tree.pack(fill='x');self.tree.bind('<Double-1>',lambda e:self.edit())
  row=ttk.Frame(box);row.pack(fill='x',pady=7)
  for label,fn in [('添加动作',lambda:self.edit(new=True)),('编辑选中',self.edit),('复制',self.duplicate),('启停',self.toggle),('移出列表',self.remove),('撤销',self.undo)]:self.button(row,label,fn)
  self.cue_choice=tk.StringVar();self.cue_box=ttk.Combobox(box,textvariable=self.cue_choice,state='readonly');self.cue_box.pack(fill='x',pady=5)
  row=ttk.Frame(box);row.pack(fill='x');self.button(row,'按这句添加动作',self.from_cue);self.button(row,'先生成字幕初稿',wb.align);self.button(row,'去校对字幕',lambda:wb.tab('captions'))
  box=section(page,'短样预览与完整母版','新配音首次建立全段口型缓存较慢；之后只改动作可复用缓存。建议用7秒短内容先检查。')
  self.start=tk.StringVar();self.length=tk.StringVar();field(box,'预览开始（秒）',self.start,width=10,note='填写本次要看的音频位置；0代表从开头。');field(box,'预览长度（秒）',self.length,width=10,note='建议7秒，范围0.5–30秒。到音频末尾会自动截短。')
  row=ttk.Frame(box);row.pack(fill='x',pady=7)
  for label,fn in [('生成短样',lambda:self.render(True)),('预览选中动作',self.preview_selected),('播放短样',lambda:wb.play('portrait_preview')),('生成完整母版',lambda:self.render(False))]:self.button(row,label,fn)
  row=ttk.Frame(box);row.pack(fill='x',pady=5);self.button(row,'播放当前母版',lambda:wb.play('mother'));self.button(row,'保存本次设置',self.save);self.button(row,'打开本视频的字幕校对',lambda:wb.tab('captions'))
  self.hint=tk.StringVar();ttk.Label(page,textvariable=self.hint,wraplength=850,style='Muted.TLabel').pack(anchor='w',pady=8)
  self.reference_mode.trace_add('write',lambda *_:self.mode_visibility());self.mode.trace_add('write',lambda *_:self.mode_visibility());self.blink.trace_add('write',lambda *_:self.mode_visibility());self.mode_visibility()
 def mode_visibility(self):
  detail=self.mode.get() in ('原图唇纹＋发声校准（推荐）','原图细节＋下唇平滑（兼容）');self.blink_box.configure(state='readonly' if detail else 'disabled');self.blink_input.configure(state='normal' if detail and self.blink.get()=='自定义眨眼时间' else 'disabled')
  modern=self.mode.get()=='原图唇纹＋发声校准（推荐）';self.reference_box.configure(state='readonly' if modern else 'disabled');self.reference_button.configure(state='normal' if modern and self.reference_mode.get()=='使用同人物张嘴照片' else 'disabled')
  for box in self.legacy_boxes:
   if self.mode.get()=='旧版二维动作兼容':box.pack(fill='x',pady=7)
   else:box.pack_forget()
 def button(self,parent,label,fn):return self.wb.button(parent,label,fn,side='left',padx=(0,6))
 def guard(self,fn):
  if not self.wb.idle():return
  try:fn()
  except Exception as e:show_error('请检查人物设置',str(e),parent=self.wb.root)
 def serialize(self):
  result=copy.deepcopy(self.settings);result.update(image=self.image.get(),framing=self.framing.get())
  result['engine']={'原图唇纹＋发声校准（推荐）':'detail_v2','原图细节＋下唇平滑（兼容）':'detail_v1','旧版二维动作兼容':'legacy_v4'}.get(self.mode.get(),'')
  result['blink']={'mode':{'自然眨眼':'natural','不添加眨眼':'off','自定义眨眼时间':'custom'}.get(self.blink.get(),''),'times':[]}
  if result['blink']['mode']=='custom':
   try:result['blink']['times']=[float(x.strip()) for x in self.blink_times.get().replace('，',',').split(',') if x.strip()]
   except ValueError:result['blink']['times']=['invalid']
  if result['engine'] in ('detail_v1','detail_v2'):result['legacy_events']=result.get('events',[]);result['events']=[]
  result['text_file']=self.wb.data.get('script','') if result['engine']=='detail_v2' else '';result['reference_mode']={'自动准备':'auto','使用同人物张嘴照片':'photo'}.get(self.reference_mode.get(),'');result['mouth_reference']=self.reference_image.get() if result['reference_mode']=='photo' else ''
  for key,var in [('preview_start',self.start),('preview_seconds',self.length)]:
   try:
    value=float(var.get());result[key]=value if math.isfinite(value) else None
   except ValueError:result[key]=None
  return result
 def load(self,data):
  self.settings={**cfg.defaults(),**copy.deepcopy(data or {})};self.history=[];self.audio_cache=None
  engine=self.settings.get('engine') or ('legacy_v4' if (data or {}).get('template') else '')
  self.mode.set({'detail_v2':'原图唇纹＋发声校准（推荐）','detail_v1':'原图细节＋下唇平滑（兼容）','legacy_v4':'旧版二维动作兼容'}.get(engine,''))
  blink=self.settings.get('blink') or {};self.blink.set({'natural':'自然眨眼','off':'不添加眨眼','custom':'自定义眨眼时间'}.get(blink.get('mode'),''));self.blink_times.set(', '.join(map(str,blink.get('times',[]))))
  if self.settings.get('legacy_events') and not self.settings.get('events'):self.settings['events']=self.settings['legacy_events']
  self.reference_mode.set({'auto':'自动准备','photo':'使用同人物张嘴照片'}.get(self.settings.get('reference_mode'),''));self.reference_image.set(self.settings.get('mouth_reference',''))
  self.image.set(self.settings.get('image',''));self.framing.set(self.settings.get('framing',''))
  for key,var in [('preview_start',self.start),('preview_seconds',self.length)]:var.set('' if self.settings.get(key) is None else str(self.settings[key]))
  for e in self.settings['events']:e.setdefault('id',uuid.uuid4().hex)
  self.hint.set('');self.refresh()
 def duration(self):
  p=self.wb.require_audio();key=(str(p),p.stat().st_mtime_ns,p.stat().st_size)
  if not self.audio_cache or self.audio_cache[0]!=key:self.audio_cache=(key,cfg.duration_wav(p))
  return self.audio_cache[1]
 def refresh(self):
  selected=self.tree.selection();self.tree.delete(*self.tree.get_children())
  for e in self.settings['events']:self.tree.insert('','end',iid=e['id'],values=('是' if e.get('enabled',True) else '否',cfg.action_name(e),*[f'{float(e[k]):.2f}' for k in ('begin','peak','end')],f"{abs(float(e['degree'])):g}",e.get('label','')))
  if selected and self.tree.exists(selected[0]):self.tree.selection_set(selected[0])
  try:self.info.set(f'本次配音：{Path(self.wb.data["audio"]).name}  /  {self.duration():.2f}秒')
  except Exception:self.info.set('尚无本次配音。请在上方选择音频，或主动接收语音生成的结果。')
  self.photo_hint.set(('照片已准备，可查看检测预览。' if self.settings.get('template') else '照片尚未检测；更换照片或标注后，请重新检测并准备。') if self.image.get() else '尚未提交本次人物照片。')
  self.region_hint.set('已标注：'+('、'.join('头部' if k=='head' else '前臂' for k in self.settings.get('regions',{})) or '无，可先只做嘴型'))
  self.cues=[];path=self.wb.data.get('subtitle')
  if path and Path(path).is_file():
   import subtitle_layout
   try:self.cues=subtitle_layout.read_cues(Path(path))
   except Exception:pass
  values=[f'{i+1:02d}  {c["start"]} — {c["end"]}  {c["text"].replace(chr(10)," ")}' for i,c in enumerate(self.cues)]
  previous=self.cue_box.current();self.cue_box['values']=values
  if values:self.cue_box.current(min(max(0,previous),len(values)-1))
  else:self.cue_choice.set('生成或导入字幕后，可在这里选择句子')
 def choose_image(self):
  if not self.wb.idle():return
  source=filedialog.askopenfilename(title='提交本次人物照片',filetypes=[('图片','*.png *.jpg *.jpeg *.webp *.bmp')])
  if source:self.submit_image(Path(source))
 def submit_image(self,source):
  def do():
   with Image.open(source) as im:im.verify()
   dest=self.wb.project/'00-素材/人物'/stamp()/source.name;dest.parent.mkdir(parents=True);shutil.copy2(source,dest)
   self.settings=cfg.defaults();self.reference_mode.set('');self.reference_image.set('');self.image.set(cfg.relative(dest));self.framing.set('');self.start.set('');self.length.set('');self.history=[];self.wb.reviewed.set(False);self.wb.persist();self.refresh()
  self.guard(do)
 def choose_reference(self):
  if not self.wb.idle():return
  path=filedialog.askopenfilename(title='选择同人物、同角度的张嘴参考照片',filetypes=[('图片','*.png *.jpg *.jpeg *.webp')])
  if not path:return
  def do():
   with Image.open(path) as im:im.verify()
   dest=self.wb.project/'00-素材/口腔参考'/stamp()/Path(path).name;dest.parent.mkdir(parents=True);shutil.copy2(path,dest);self.reference_image.set(cfg.relative(dest));self.reference_mode.set('使用同人物张嘴照片');self.wb.reviewed.set(False);self.wb.persist()
  self.guard(do)
 def recommend(self):
  if not self.wb.idle():return
  self.mode.set('原图唇纹＋发声校准（推荐）');self.reference_mode.set('自动准备');self.blink.set('自然眨眼');self.framing.set('完整显示');self.start.set('0');self.length.set('7');self.wb.persist();self.hint.set('已应用原图唇纹、发声校准、自动口腔参考、较自然频率的眨眼、完整显示和7秒短样。头部与身体保持固定。')
 def prepare(self):
  if not self.wb.idle():return
  try:
   if not self.image.get():raise ValueError('请先提交本次人物照片。')
   source=cfg.inside(self.image.get())
   if not source.is_file():raise ValueError('本次照片缺失，请重新提交。')
   out=self.wb.project/'05-人物口播/照片准备'/stamp();regions=out.with_suffix('.regions.json');regions.parent.mkdir(parents=True,exist_ok=True);regions.write_text(json.dumps(self.settings.get('regions',{}),ensure_ascii=False),encoding='utf-8')
  except Exception as e:show_error('照片尚未就绪',str(e));return
  def work():self.wb.cli([service.PY,cfg.ROOT/'core/portrait/prepare.py','--image',source,'--out-dir',out,'--regions',regions],'照片检测')
  def done(_):self.settings['template']=cfg.relative(out/'模板.json');self.hint.set('检测完成。请查看预览，确认人脸框与关键点；仅做嘴型即可开始短样。')
  self.wb.job('检测照片与准备动作区域',work,done)
 def view_detection(self):
  self.guard(lambda:self.wb.open_path(cfg.template_path(self.serialize()).with_name('检测预览.png')))
 def calibrate(self,part):
  if not self.wb.idle():return
  try:
   if not self.image.get():raise ValueError('请先提交本次人物照片。')
   im=ImageOps.exif_transpose(Image.open(cfg.inside(self.image.get()))).convert('RGB');w,h=im.size;im=im.crop((0,0,w-w%2,h-h%2));w,h=im.size
  except Exception as e:show_error('无法标注',str(e));return
  dialog=tk.Toplevel(self.wb.root);dialog.title('标注'+('头部' if part=='head' else '前臂'));dialog.transient(self.wb.root)
  size=min(670/w,620/h);display=im.resize((round(w*size),round(h*size)),Image.Resampling.LANCZOS)
  container=ttk.Frame(dialog,padding=14);container.pack();hint=tk.StringVar(value='①沿移动部位轮廓依次点击，至少3点；请包含完整头发或前臂，避开不动背景。')
  ttk.Label(container,textvariable=hint,wraplength=700).pack(anchor='w',pady=6)
  canvas=tk.Canvas(container,width=display.width,height=display.height,bg='#17253e',highlightthickness=0);canvas.pack();photo=ImageTk.PhotoImage(display);canvas.image=photo
  state={'polygon':[],'pivot':None,'closed':False}
  def redraw():
   canvas.delete('all');canvas.create_image(0,0,image=photo,anchor='nw');pts=state['polygon'];flat=[v*size for xy in pts for v in xy]
   if len(pts)>1:canvas.create_line(*flat,fill='#25d9a7',width=2)
   if state['closed']:canvas.create_line(pts[-1][0]*size,pts[-1][1]*size,pts[0][0]*size,pts[0][1]*size,fill='#25d9a7',width=2)
   for x,y in pts:canvas.create_oval(x*size-3,y*size-3,x*size+3,y*size+3,fill='#25d9a7',outline='')
   if state['pivot']:
    x,y=[v*size for v in state['pivot']];canvas.create_oval(x-6,y-6,x+6,y+6,fill='#ffb347')
  def click(e):
   point=[min(w-1,max(0,e.x/size)),min(h-1,max(0,e.y/size))]
   if state['closed']:state['pivot']=point;hint.set('③支点已选择。确认橙点位于颈部连接处或肘部，保存后重新准备照片。')
   else:state['polygon'].append(point)
   redraw()
  canvas.bind('<Button-1>',click)
  def boundary():
   if len(state['polygon'])<3:show_error('边界点不足','请至少点击3个轮廓点。',parent=dialog);return
   state['closed']=True;hint.set('②点击旋转支点：头部选颈部连接处；前臂选肘部。然后保存。');redraw()
  def undo():
   if state['pivot']:state['pivot']=None
   elif state['closed']:state['closed']=False
   elif state['polygon']:state['polygon'].pop()
   redraw()
  def save():
   if not state['closed'] or not state['pivot']:show_error('标注未完成','请完成边界并点击旋转支点。',parent=dialog);return
   self.settings.setdefault('regions',{})[part]={'polygon':state['polygon'],'pivot':state['pivot']};self.settings['template']='';self.wb.reviewed.set(False);self.wb.persist();self.refresh();dialog.destroy()
  row=ttk.Frame(container);row.pack(fill='x',pady=9)
  for label,fn in [('完成边界，选择支点',boundary),('撤销一点',undo),('保存标注',save),('取消',dialog.destroy)]:ttk.Button(row,text=label,command=fn).pack(side='left',padx=4)
  redraw();dialog.update_idletasks();dialog.geometry(f'+{max(0,self.wb.root.winfo_rootx()+80)}+{max(0,self.wb.root.winfo_rooty()+30)}');dialog.grab_set()
 def selected(self):
  selected=self.tree.selection();return next((e for e in self.settings['events'] if selected and e['id']==selected[0]),None)
 def commit(self,events):
  cfg.validate_events(events,self.duration());self.history.append(copy.deepcopy(self.settings['events']));self.settings['events']=events;self.wb.reviewed.set(False);self.wb.persist();self.refresh();self.hint.set('本次动作已保存，重新生成母版后生效。旧视频文件保留。')
 def undo(self):
  def do():
   if self.history:self.settings['events']=self.history.pop();self.wb.reviewed.set(False);self.wb.persist();self.refresh()
  self.guard(do)
 def save(self):
  def do():cfg.signature(self.wb.require_audio(),self.serialize());self.wb.persist();self.hint.set('本次设置已保存。需手动打开任务后恢复；下次启动仍为空白。')
  self.guard(do)
 def duplicate(self):
  e=self.selected()
  if e:self.edit(new=True,seed={**e,'id':uuid.uuid4().hex,'enabled':False})
 def toggle(self):
  e=self.selected()
  if e:self.guard(lambda:self.commit([{**x,'enabled':not x.get('enabled',True)} if x['id']==e['id'] else x for x in self.settings['events']]))
 def remove(self):
  e=self.selected()
  if e:self.guard(lambda:self.commit([x for x in self.settings['events'] if x['id']!=e['id']]))
 def from_cue(self):
  def do():
   import subtitle_layout
   i=self.cue_box.current()
   if i<0:raise ValueError('请先生成或导入字幕，再选择句子。')
   c=self.cues[i];a=subtitle_layout.time_seconds(c['start']);b=min(self.duration(),subtitle_layout.time_seconds(c['end']))
   self.edit(new=True,seed={'begin':a,'peak':(a+b)/2,'end':b,'label':c['text'].replace('\n',' ')})
  self.guard(do)
 def edit(self,new=False,seed=None):
  if not self.wb.idle():return
  try:
   duration=self.duration();source=seed if seed is not None else ({} if new else self.selected())
   if source is None:return
  except Exception as e:show_error('动作尚不能编辑',str(e));return
  dialog=tk.Toplevel(self.wb.root);dialog.title('动作时间设置');dialog.transient(self.wb.root);frame=ttk.Frame(dialog,padding=18);frame.pack()
  kind=tk.StringVar(value=cfg.action_name(source) if 'part' in source else '');on=tk.BooleanVar(value=source.get('enabled',True));fields={}
  field(frame,'动作类型',kind,list(cfg.ACTION_TYPES),width=25)
  for key,label in [('begin','开始（秒）'),('peak','峰值（秒）'),('end','结束（秒）'),('amplitude','幅度（度）'),('label','对应句子 / 备注')]:
   value=abs(source['degree']) if key=='amplitude' and 'degree' in source else source.get(key,'');fields[key]=tk.StringVar(value=f'{value:.6g}' if isinstance(value,(int,float)) else str(value));field(frame,label,fields[key],width=25)
  ttk.Checkbutton(frame,text='启用本动作',variable=on).pack(anchor='w',pady=7)
  ttk.Label(frame,text=f'配音{duration:.2f}秒；渐入与回落各至少0.25秒。\n头部建议1–2.5°、上限3°；前臂建议4–8°、上限9°。\n幅度越大越容易出现接缝，请先检查短样。',wraplength=470,style='Muted.TLabel').pack(anchor='w',pady=8)
  def save():
   try:
    if kind.get() not in cfg.ACTION_TYPES:raise ValueError('请选择动作类型。')
    if any(not fields[k].get().strip() for k in ('begin','peak','end','amplitude')):raise ValueError('请填写开始、峰值、结束时间和幅度。')
    part,sign=cfg.ACTION_TYPES[kind.get()]
    if part not in self.settings.get('regions',{}):raise ValueError('请先标注此类动作对应的区域与支点。')
    amplitude=float(fields['amplitude'].get())
    if not math.isfinite(amplitude) or amplitude<=0:raise ValueError('幅度请填写正数。')
    event={'id':source.get('id') or uuid.uuid4().hex,'part':part,'degree':sign*amplitude,'enabled':on.get(),'label':fields['label'].get(),**{k:float(fields[k].get()) for k in ('begin','peak','end')}}
    cfg.validate_events([{**event,'enabled':True}],duration);events=copy.deepcopy(self.settings['events'])
    events.append(event) if new else None
    if not new:events=[event if e['id']==source['id'] else e for e in events]
    self.commit(events);dialog.destroy()
   except Exception as e:show_error('动作未保存',str(e),parent=dialog)
  row=ttk.Frame(frame);row.pack();ttk.Button(row,text='保存动作',command=save).pack(side='left',padx=8);ttk.Button(row,text='取消',command=dialog.destroy).pack(side='left')
  dialog.update_idletasks();dialog.geometry(f'+{max(0,self.wb.root.winfo_rootx()+200)}+{max(0,self.wb.root.winfo_rooty()+100)}');dialog.grab_set()
 def logged(self,label,fn):
  path=project_logs(self.wb.project)/(stamp()+'-'+label+'.log')
  def work():
   with path.open('w',encoding='utf-8') as f:
    def log(line):f.write(str(line)+'\n');f.flush();self.wb.log(str(line))
    return fn(log)
  return work
 def render(self,preview):
  if not self.wb.idle():return
  try:
   audio=self.wb.require_audio()
   if self.mode.get()=='原图唇纹＋发声校准（推荐）':self.wb.commit_source(True)
   settings=self.serialize();cfg.signature(audio,settings);clip=None
   if preview:
    if not self.start.get().strip() or not self.length.get().strip():raise ValueError('请填写本次预览开始与长度，或主动应用推荐取景与短样参数。')
    start,length=float(self.start.get()),float(self.length.get());duration=self.duration()
    if not all(math.isfinite(v) for v in (start,length)) or not 0<=start<duration or not .5<=length<=30:raise ValueError('预览开始须在配音内，长度0.5–30秒。')
    clip={'start':start,'seconds':min(length,duration-start)}
   folder=self.wb.project/('05-人物口播/预览' if preview else '02-图片视频');folder.mkdir(parents=True,exist_ok=True);output=folder/(('人物短样-' if preview else '人物母版-')+stamp()+'.mp4');project=self.wb.project
  except Exception as e:show_error('人物视频尚不能制作',str(e));return
  def done(result):
   if preview:self.wb.data['portrait_preview']=str(output)
   else:self.wb.data.update(mother=str(output),mother_signature=result['signature'],final='');self.wb.reviewed.set(False)
   self.hint.set('短样已保存，请播放检查。' if preview else '人物母版已保存，请校对字幕并导出。');self.wb.status.set(self.hint.get())
  self.wb.job('制作人物'+('短样' if preview else '母版'),self.logged('人物制作',lambda log:service.build(project,audio,settings,output,log,clip)),done)
 def preview_selected(self):
  def do():
   event=self.selected()
   if not event:raise ValueError('请先选择动作。')
   start=max(0,float(event['begin'])-.5);self.start.set(f'{start:.3f}');self.length.set(f'{min(30,self.duration()-start,event["end"]-start+.5):.3f}');self.render(True)
  self.guard(do)
 def check(self):
  if not self.wb.idle():return
  try:
   audio=self.wb.require_audio();video=self.wb.data.get('final')
   if not video or not Path(video).is_file():raise ValueError('请先导出本次成片。')
   project=self.wb.project;subtitle=self.wb.data.get('subtitle');script=self.wb.data.get('script')
  except Exception as e:show_error('成片尚不能检查',str(e));return
  def done(result):self.wb.data['portrait_report']=str(cfg.inside(result['report']));self.wb.status.set('自动检查完成：'+('基础指标通过，请继续人工复核自然度。' if result['status']=='passed' else '存在需复核项目，请打开报告。'))
  self.wb.job('检查本次成片',self.logged('成片检查',lambda log:service.inspect(project,video,audio,subtitle,script,log)),done)
