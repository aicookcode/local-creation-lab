from error_guidance import show_error
from studio_paths import PROJECTS,CACHE,LOGS,USER_RESOURCES,PACKAGE_ROOT,legacy_path,project_logs
"""Independent cloning, speech, and video workspaces with isolated video routes."""
from workflow import *
import copy,shutil,ctypes
from contextlib import nullcontext
from quick_panel import QuickPanel
import settings_library as parameter_library
from PIL import Image,ImageTk
from ui_kit import theme,ScrollPage,heading,section,field,COLORS
from ui_display import enable_dpi_awareness,display_scale,sidebar_artwork,set_app_icons
from video_workspace import VideoWorkspace
from task_progress import Tracker,status_from_log,phase_label
from resource_gate import production_slot

APP_NAME='本地影音创作工作台'

class Workbench:
 def __init__(self,root,restore=False):
  self.root=root;root.title(APP_NAME);theme(root);self.set_icon(root)
  scale=display_scale(root);width=min(round(1320*scale),root.winfo_screenwidth()-round(60*scale));height=min(round(960*scale),root.winfo_screenheight()-round(90*scale))
  root.geometry(f'{width}x{height}+20+20');root.minsize(min(round(1080*scale),width),min(round(760*scale),height))
  self.events=queue.Queue();self.busy=False;self.buttons=[];self.locked={};self.log_lines=[];self.status=tk.StringVar(value='选择一种功能开始。三类功能可以独立使用。');self.name=tk.StringVar();self.project=PROJECTS/(stamp()+'-新任务');self.data={};self.loading=False
  self.shell=ttk.Frame(root);self.shell.pack(fill='both',expand=True);side=tk.Frame(self.shell,bg=COLORS['surface'],width=round(222*scale),highlightthickness=1,highlightbackground=COLORS['line']);side.pack(side='left',fill='y');side.pack_propagate(False)
  self.logo=sidebar_artwork(root,ROOT/'resources/ui');tk.Label(side,image=self.logo,bg=COLORS['surface']).pack(anchor='w',padx=18,pady=(22,9))
  tk.Label(side,text='本地影音\n创作工作台',bg=COLORS['surface'],fg=COLORS['text'],font=('Microsoft YaHei UI',16,'bold'),justify='left').pack(anchor='w',padx=18)
  tk.Label(side,text='独立功能 · 本地处理',bg=COLORS['surface'],fg=COLORS['muted'],font=('Microsoft YaHei UI',9,'normal')).pack(anchor='w',padx=18,pady=(8,24))
  main=ttk.Frame(self.shell);main.pack(side='left',fill='both',expand=True)
  top=ttk.Frame(main,padding=(16,12));top.pack(fill='x');ttk.Label(top,text='当前任务').pack(side='left');self.name_entry=ttk.Entry(top,textvariable=self.name,width=25);self.name_entry.pack(side='left',padx=8)
  self.button(top,'新建',self.new_task,side='left',padx=3);self.button(top,'打开',self.choose_project,side='left',padx=3);self.button(top,'保存',self.save_clicked,side='left',padx=3);self.button(top,'任务文件夹',lambda:self.open_path(self.project),side='right')
  self.tabs=ttk.Notebook(main,style='Hidden.TNotebook');self.tabs.pack(fill='both',expand=True,padx=6,pady=(0,6));self.pages={};self.surfaces={};self.nav={}
  for key,label in [('quick','快捷制作'),('clone','声音克隆'),('speech','语音生成'),('video','视频生成')]:
   if key in ('quick','video'):page=ttk.Frame(self.tabs,padding=(12,8));body=page
   else:page=ScrollPage(self.tabs);body=page.body
   self.tabs.add(page,text=label);self.surfaces[key]=page;self.pages[key]=body
   nav=ttk.Button(side,text=label,style='Nav.TButton',command=lambda k=key:self.tab(k));nav.pack(fill='x',padx=9,pady=5);self.nav[key]=nav
  bottom=tk.Frame(side,bg=COLORS['surface']);bottom.pack(side='bottom',fill='x',pady=18)
  ttk.Button(bottom,text='操作指南',style='Nav.TButton',command=lambda:self.open_path(ROOT.parent/'USER_GUIDE.html')).pack(fill='x',padx=9)
  ttk.Button(bottom,text='项目与成品文件夹',style='Nav.TButton',command=lambda:self.open_path(PROJECTS)).pack(fill='x',padx=9,pady=4)
  ttk.Button(bottom,text='任务日志与组件检查',style='Nav.TButton',command=self.show_logs).pack(fill='x',padx=9,pady=4)
  tk.Label(bottom,text='启动为空白任务\n历史素材需手动选择',bg=COLORS['surface'],fg=COLORS['muted'],font=('Microsoft YaHei UI',9,'normal'),justify='left').pack(anchor='w',padx=18,pady=(15,0))
  video=self.pages['video'];heading(video,'视频生成','自主选择图片视频或人物口播。两个页签各自保存音频、字幕、母版、样式与检查结果。')
  self.video_tabs=ttk.Notebook(video);self.video_tabs.pack(fill='both',expand=True);self.routes={};self.route_containers={}
  for key,label in [('photo','图片视频'),('portrait','人物口播')]:
   frame=ttk.Frame(self.video_tabs);self.video_tabs.add(frame,text='  '+label+'  ');self.route_containers[key]=frame;self.routes[key]=VideoWorkspace(self,frame,key)
  self.voice=voice_gui.VoiceApp(root,bootstrap=False,clone_parent=self.pages['clone'],speech_parent=self.pages['speech'],output_getter=self.voice_output,on_generated=self.speech_generated,can_start=self.can_start_voice,on_mode=self.tab,on_transfer=self.transfer_audio,on_progress=self.log,on_library_changed=self.voice_library_changed)
  self.quick=QuickPanel(self,self.pages['quick'])
  self.button(top,'参数记录',self.quick.show_library,side='right',padx=7)
  self.tracker=Tracker();self.progress_mode='indeterminate';self.progress_text=tk.StringVar(value=self.tracker.text())
  footer=ttk.Frame(root,padding=(18,10),style='Footer.TFrame');footer.pack(fill='x');ttk.Separator(footer,orient='horizontal').pack(fill='x',pady=(0,8));ttk.Label(footer,textvariable=self.status,wraplength=1100,style='Muted.TLabel').pack(fill='x');row=ttk.Frame(footer,style='Footer.TFrame');row.pack(fill='x',pady=(5,0));self.progress=ttk.Progressbar(row,mode='indeterminate',length=180);self.progress.pack(side='left',padx=(0,12));ttk.Label(row,textvariable=self.progress_text,style='Muted.TLabel').pack(side='left',fill='x',expand=True)
  self.new_task(initial=True);root.protocol('WM_DELETE_WINDOW',self.close);root.after(120,self.poll);root.after(5000,self.autosave)

 def set_icon(self,window):
  try:
   set_app_icons(window,ROOT/'resources/ui');self.window_icons=window._app_icon_photos
  except Exception as error:
   LOGS.mkdir(parents=True,exist_ok=True)
   with (LOGS/'界面图标.log').open('a',encoding='utf-8') as log:log.write(str(error)+'\n')
 def button(self,parent,label,callback,primary=False,**pack):
  b=ttk.Button(parent,text=label,command=callback,style='Primary.TButton' if primary else 'TButton');b.pack(**pack);self.buttons.append(b);return b
 def bar(self,parent):
  row=ttk.Frame(parent);row.pack(fill='x',pady=7);return row
 def tab(self,key):
  self.tabs.select(self.surfaces[key])
  if hasattr(self,'voice') and key in ('clone','speech'):self.voice.active_mode=key
  for k,b in self.nav.items():b.state(['selected'] if k==key else ['!selected'])
 def idle(self):
  if self.busy or (hasattr(self,'voice') and self.voice.busy):messagebox.showinfo('任务进行中','当前有本地模型任务正在处理。你可以查看其他页面，完成后再提交新的制作任务。');return False
  return True
 def voice_output(self,preview=False):
  path=self.project/('声音克隆/试听' if preview else '语音生成');path.mkdir(parents=True,exist_ok=True);return path
 def can_start_voice(self):
  if self.busy:messagebox.showinfo('任务进行中','请等待当前视频任务完成，再运行声音模型。');return False
  self.persist();return True
 def speech_generated(self,path,voice_id):self.persist();self.status.set('语音已生成，可在语音生成中检查、导出或主动发送到某个视频页签。')
 def voice_library_changed(self):
  if hasattr(self,'quick'):self.quick.refresh()
 def transfer_audio(self,kind):
  if not self.idle():return
  path=self.voice.speech_output
  if not path or not Path(path).is_file():messagebox.showinfo('尚无生成音频','请先生成语音，或在对应视频页签选择音频文件。');return
  try:
   script=Path(path).with_suffix('.txt');text=self.read_text(script) if script.is_file() else ''
   self.routes[kind].set_source(Path(path),text);self.tab('video');self.video_tabs.select(self.route_containers[kind]);self.routes[kind].tab('video' if kind=='photo' else 'portrait')
  except Exception as e:show_error('配音未发送',str(e))
 @staticmethod
 def read_text(path):
  raw=Path(path).read_bytes()
  if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return raw.decode('utf-16')
  try:return raw.decode('utf-8-sig')
  except UnicodeDecodeError:return raw.decode('gb18030')
 def snapshot(self):
  voice=self.voice.snapshot();self.data.update(schema=2,id=self.data.get('id') or uuid.uuid4().hex,name=self.name.get().strip(),created_at=self.data.get('created_at') or now(),updated_at=now(),voice=voice,videos={k:r.snapshot() for k,r in self.routes.items()},quick=self.quick.snapshot());return copy.deepcopy(self.data)
 def meaningful(self,data):
  voice=data['voice'];clone=voice['clone'].get('fields',{});speech=voice['speech']
  return bool(data.get('quick',{}).get('text') or data.get('quick',{}).get('images') or data.get('quick',{}).get('record') or data.get('quick',{}).get('voice_id') or data['name'] or clone.get('reference') or clone.get('name') or clone.get('preview_text') or speech.get('text') or speech.get('voice_id') or any(r.get('audio') or r.get('pictures') or r.get('portrait',{}).get('image') or r.get('source_text') or r.get('mother') for r in data['videos'].values()))
 def persist(self,force=False):
  if self.loading or not hasattr(self,'voice'):return
  data=self.snapshot()
  if not force and not self.meaningful(data):return
  self.project.mkdir(parents=True,exist_ok=True);store.atomic_json(self.project/'项目.json',pack_paths(data))
 def save_clicked(self):
  if not self.idle():return
  try:self.persist(True);self.status.set('任务已保存。下次启动仍为空白，需手动打开项目.json恢复。')
  except Exception as e:show_error('保存未完成',str(e))
 def autosave(self):
  try:
   if not self.busy and not self.voice.busy:self.persist()
  except Exception as e:self.status.set('草稿保存未完成，请查看日志中的详细原因。')
  self.root.after(5000,self.autosave)
 def new_task(self,initial=False):
  if not initial and not self.idle():return
  if not initial:self.persist()
  self.loading=True;self.project=PROJECTS/(stamp()+'-新任务');self.data={'schema':2,'id':uuid.uuid4().hex,'created_at':now()};self.name.set('');self.voice.reset_session()
  for route in self.routes.values():route.load({})
  self.quick.load({})
  self.loading=False;self.tab('quick');self.status.set('可在快捷制作一键执行，或进入声音克隆、语音生成和视频生成分步操作。')
  if hasattr(self,'tracker'):self.tracker=Tracker();self.progress_text.set(self.tracker.text())
 def choose_project(self):
  if not self.idle():return
  path=filedialog.askopenfilename(title='手动打开任务项目.json',initialdir=PROJECTS,filetypes=[('任务文件','*.json')])
  if path:
   try:self.persist();self.load_project(Path(path))
   except Exception as e:show_error('任务未打开',str(e))
 def load_project(self,path):
  path=Path(path).resolve();raw=json.loads(path.read_text(encoding='utf-8-sig'));source=path.parent
  source_root=next((p/'program' if (p/'program/config/dependency_paths.json').is_file() else p for p in path.parents if (p/'program/config/dependency_paths.json').is_file() or (p/'config/dependency_paths.json').is_file()),ROOT);data=unpack_paths(raw,source_root)
  if data.get('schema') not in (1,2) or not data.get('id'):raise ValueError('这不是可识别的工作台任务文件。')
  candidates=[data] if data['schema']==1 else list(data.get('videos',{}).values())
  for route in candidates:
   for key in ('image','template'):
    value=route.get('portrait',{}).get(key)
    if value and not Path(value).is_absolute():route['portrait'][key]=str(legacy_path(value,source_root))
  if not source.is_relative_to(PACKAGE_ROOT):
   target=PROJECTS/('导入-'+stamp());shutil.copytree(source,target)
   def rebase(value):
    if isinstance(value,dict):return {k:rebase(v) for k,v in value.items()}
    if isinstance(value,list):return [rebase(v) for v in value]
    if isinstance(value,str) and Path(value).is_absolute():
     try:return str(target/Path(value).relative_to(source))
     except ValueError:pass
    return value
   data=rebase(data);source=target
  if data['schema']==1:
   kind='portrait' if (data.get('mother_signature') or {}).get('kind')=='portrait' else 'photo'
   old=copy.deepcopy(data);data={'schema':2,'id':data['id'],'name':data.get('name',''),'voice':{'speech':{'voice_id':data.get('voice_id'),'text':data.get('text',''),'audio':data.get('audio','')},'clone':{}},'videos':{kind:old}}
  self.loading=True
  try:
   self.project=source;self.data=data;self.name.set(data.get('name',''));self.voice.load(data.get('voice',{}))
   for key,route in self.routes.items():route.load(data.get('videos',{}).get(key,{}))
   self.quick.load(data.get('quick',{}))
  finally:self.loading=False
  self.status.set('已手动打开任务。三个功能及两个视频页签分别恢复输入与结果。')
 def run(self,owner,label,work,done,serialized=True,parameters='auto'):
  if not self.idle():return
  if parameters=='auto':
   try:parameters=self.quick.execution_parameters(owner)
   except Exception:parameters=None
  self.persist();self.busy=True;self.set_locked(True);self.tracker.start(label);self.status.set(phase_label(label)+'……')
  def execute():
   logfile=project_logs(owner.project)/(stamp()+'-任务摘要.log');logfile.parent.mkdir(parents=True,exist_ok=True)
   usage=None
   try:
    if parameters:usage=parameter_library.begin_usage(label,parameters['scope'],parameters['payload'],project=owner.project)
    from process_lifetime import protect_children
    protect_children(self.log)
    with logfile.open('w',encoding='utf-8') as f:
     f.write(label+'\n')
     with (production_slot(label,self.log) if serialized else nullcontext()):result=work()
     f.write('completed\n')
    if usage:parameter_library.finish_usage(usage,'completed',result={'project':str(owner.project)})
    self.events.put(('done',owner,done,result))
   except Exception as e:
    if usage:parameter_library.finish_usage(usage,'failed',error=str(e))
    trace=traceback.format_exc();logfile.write_text(label+'\n'+trace,encoding='utf-8');self.events.put(('error',owner,str(e),trace))
  threading.Thread(target=execute,daemon=True).start()
 def log(self,line):self.events.put(('log',str(line)))
 def set_locked(self,locked):
  if locked:
   if self.locked:return
   pending=[self.name_entry]+[page for page in self.pages.values()]
   while pending:
    widget=pending.pop();pending.extend(widget.winfo_children())
    if isinstance(widget,(tk.Text,tk.Listbox,ttk.Entry,ttk.Combobox,ttk.Spinbox,ttk.Checkbutton)):
     try:self.locked[widget]=widget.cget('state');widget.configure(state='disabled')
     except tk.TclError:pass
   for b in self.buttons+self.voice.action_buttons:
    try:b.configure(state='disabled')
    except tk.TclError:pass
  else:
   for widget,state in self.locked.items():
    try:widget.configure(state=state)
    except tk.TclError:pass
   self.locked={}
   for b in self.buttons+self.voice.action_buttons:
    try:b.configure(state='normal')
    except tk.TclError:pass
   self.voice.voice_box.configure(state='readonly');self.voice.engine_box.configure(state='readonly');self.voice.update_buttons()
   for route in self.routes.values():
    if route.portrait:route.portrait.mode_visibility()
 def poll(self):
  try:
   for _ in range(200):
    e=self.events.get_nowait()
    if e[0]=='log':
     self.tracker.feed(e[1]);self.log_lines.append(e[1]);self.log_lines=self.log_lines[-500:]
     self.status.set(status_from_log(e[1]))
     if hasattr(self,'log_view') and self.log_view.winfo_exists():
      self.log_view.insert('end',e[1]+'\n')
      excess=int(self.log_view.index('end-1c').split('.')[0])-1000
      if excess>0:self.log_view.delete('1.0',str(excess+1)+'.0')
      self.log_view.see('end')
     continue
    self.busy=False;self.set_locked(False);self.progress.stop()
    self.tracker.finish(e[0]=='done')
    if e[0]=='done':
     try:e[2](e[3]);e[1].refresh();self.persist()
     except Exception as exc:self.tracker.finish(False);show_error('结果处理未完成',str(exc));self.status.set('结果处理未完成，请查看日志中的详细原因。')
    else:self.log_lines.append(e[3]);self.status.set('任务未完成，请查看日志中的详细原因。');show_error('任务未完成',e[2])
  except queue.Empty:pass
  active=self.busy or self.voice.busy
  if active and not self.locked:self.set_locked(True)
  elif not active and self.locked:self.set_locked(False)
  if active and not self.tracker.active:self.tracker.start(self.voice.status.get() or '本地任务')
  elif not active and self.tracker.active:self.tracker.finish(self.voice.last_job_success)
  desired='determinate' if self.tracker.total or not self.tracker.active else 'indeterminate'
  if desired!=self.progress_mode:
   self.progress.stop();self.progress.configure(mode=desired);self.progress_mode=desired
   if desired=='indeterminate':self.progress.start(15)
  if desired=='determinate':self.progress['value']=min(100,100*self.tracker.done/self.tracker.total) if self.tracker.active and self.tracker.total else (100 if self.tracker.finished.startswith('完成') else 0)
  self.progress_text.set(self.tracker.text())
  self.root.after(120,self.poll)
 def show_logs(self):
  dialog=tk.Toplevel(self.root);dialog.title('任务日志与组件检查');dialog.geometry('900x600');dialog.transient(self.root)
  box=ttk.Frame(dialog,padding=14);box.pack(fill='both',expand=True);heading(box,'任务日志','此处显示运行细节。自然度和最终发布效果仍需人工确认。')
  row=self.bar(box);ttk.Button(row,text='检查组件',command=self.diagnostics).pack(side='left');ttk.Button(row,text='打开任务文件夹',command=lambda:self.open_path(self.project)).pack(side='left',padx=8)
  self.log_view=ScrolledText(box,font=('Consolas',11,'bold'),wrap='word');self.log_view.pack(fill='both',expand=True);self.log_view.insert('end','\n'.join(self.log_lines))
 def diagnostics(self):
  checks={'主运行环境':Path(CFG['main_python']),'语音模型':Path(CFG['model_home']),'字幕模型':Path(CFG['subtitle_models']),'fonts':Path(CFG['fonts']),'voice_library':USER_RESOURCES/'voice_library/index.json','应用图标':ROOT/'resources/ui/app.ico'};checks.update(portrait_service.prerequisites())
  for label,path in checks.items():self.log(('正常' if path.exists() else '缺失')+' | '+label+' | '+str(path))
 def open_path(self,path):
  try:
   path=Path(path)
   if not path.exists():
    if path.suffix:raise FileNotFoundError('文件尚未生成，请先完成对应操作。')
    path.mkdir(parents=True,exist_ok=True)
   os.startfile(path)
  except Exception as e:show_error('无法打开',str(e))
 def close(self):
  recorder=getattr(self.voice,'recorder_dialog',None)
  if recorder and recorder.window.winfo_exists():
   pending=recorder.recording;recorder.close()
   if pending:self.root.after(150,self.close);return
  if not self.idle():return
  try:self.persist()
  except Exception as e:show_error('草稿未保存',str(e));return
  for callback in self.root.tk.call('after','info'):self.root.after_cancel(callback)
  self.root.destroy()

def main():
 enable_dpi_awareness()
 try:ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LocalMediaStudio.Workbench')
 except Exception:pass
 root=tk.Tk()
 try:Workbench(root);root.mainloop()
 except Exception:
  (LOGS/'启动错误.log').write_text(traceback.format_exc(),encoding='utf-8');show_error('启动未完成','请查看根目录“日志”文件夹中的启动错误.log。');root.destroy();raise

if __name__=='__main__':main()

