from error_guidance import show_error
import os
"""Manual recording and adoption of a reference sample; never auto-starts capture."""
import datetime,json,os,queue,threading,uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
from tkinter.scrolledtext import ScrolledText
import microphone

class RecorderDialog:
 def __init__(self,owner,output_dir,on_use):
  self.output_dir=output_dir;self.on_use=on_use;self.events=queue.Queue();self.recording=False;self.closing=False;self.take=None;self.playing=False;self.play_id=None;self.preview_path=None;self.devices={};self.stop=threading.Event()
  self.window=tk.Toplevel(owner);self.window.title('录制参考样本');self.window.transient(owner)
  x=max(0,min(owner.winfo_screenwidth()-800,owner.winfo_rootx()+(owner.winfo_width()-780)//2));y=max(0,min(owner.winfo_screenheight()-740,owner.winfo_rooty()+(owner.winfo_height()-690)//2))
  self.window.geometry(f'780x690+{x}+{y}');self.window.minsize(700,640);self.window.protocol('WM_DELETE_WINDOW',self.close)
  box=ttk.Frame(self.window,padding=22);box.pack(fill='both',expand=True)
  ttk.Label(box,text='录制参考样本',style='Title.TLabel').pack(anchor='w')
  ttk.Label(box,text='选择麦克风后手动开始。建议录制5–10秒完整自然句，最长30秒自动停止。\n在安静环境中，用自然声音朗读；保持与麦克风距离稳定，避开音乐和其他人声。',style='Muted.TLabel',wraplength=700).pack(anchor='w',pady=(8,16))
  row=ttk.Frame(box);row.pack(fill='x');ttk.Label(row,text='输入设备').pack(side='left',padx=(0,12));self.device=tk.StringVar();self.device_box=ttk.Combobox(row,textvariable=self.device,state='readonly');self.device_box.pack(side='left',fill='x',expand=True);self.refresh_button=ttk.Button(row,text='刷新设备',command=self.refresh);self.refresh_button.pack(side='left',padx=(8,0))
  self.device_box.bind('<<ComboboxSelected>>',lambda event:self.update_controls())
  ttk.Label(box,text='本次朗读的文字（建议先填写，再照着读；采用录音时会一并填入参考文字）',wraplength=700).pack(anchor='w',pady=(18,6));self.text=ScrolledText(box,height=4,wrap='word',font=('Microsoft YaHei UI',10,'normal'),undo=True);self.text.pack(fill='x')
  self.state=tk.StringVar(value='尚未录音。选择设备后点击“开始录音”。');ttk.Label(box,textvariable=self.state,style='Good.TLabel',wraplength=700).pack(anchor='w',pady=(18,7))
  row=ttk.Frame(box);row.pack(fill='x');self.elapsed=tk.StringVar(value='0.0秒 / 最长30秒');ttk.Label(row,textvariable=self.elapsed).pack(side='left');self.level=ttk.Progressbar(row,maximum=100);self.level.pack(side='left',fill='x',expand=True,padx=12);self.db=tk.StringVar(value='输入音量');ttk.Label(row,textvariable=self.db,width=16).pack(side='left')
  row=ttk.Frame(box);row.pack(fill='x',pady=16);self.start_button=ttk.Button(row,text='开始录音',style='Primary.TButton',command=self.start);self.start_button.pack(side='left');self.stop_button=ttk.Button(row,text='停止录音',command=self.stop_recording);self.stop_button.pack(side='left',padx=8);self.play_button=ttk.Button(row,text='试听录音',command=self.play);self.play_button.pack(side='left')
  self.report=tk.StringVar(value='这里只检查长度与音量；是否有噪声、回声或口误，请试听确认。');ttk.Label(box,textvariable=self.report,wraplength=700,style='Muted.TLabel').pack(anchor='w',pady=(0,12))
  ttk.Label(box,text='录音只在本地处理。打开此窗口、刷新设备或启动工作台都不会自动录音。\n点击“用作参考录音”才把样本保存到当前任务；重录会替换本窗口尚未采用的样本。\n采用后仍需填写音色名称并“保存为新音色”，已有音色不会被覆盖。',wraplength=700,style='Muted.TLabel').pack(anchor='w',pady=8)
  row=ttk.Frame(box);row.pack(side='bottom',fill='x',pady=(8,0));self.use_button=ttk.Button(row,text='用作参考录音',style='Primary.TButton',command=self.use);self.use_button.pack(side='right');self.close_button=ttk.Button(row,text='关闭',command=self.close);self.close_button.pack(side='right',padx=8)
  self.refresh();self.update_controls();self.window.grab_set();self.poll_id=self.window.after(100,self.poll)
 def refresh(self):
  if self.recording:return
  previous=self.device.get()
  try:
   self.devices={f'{d["name"]}  [设备{d["id"]+1}]':d for d in microphone.list_devices()};self.device_box['values']=list(self.devices);self.device.set(previous if previous in self.devices else '')
   if not self.devices:self.state.set('未发现输入设备。请连接麦克风后刷新；也可以关闭窗口并导入现有录音。')
   else:self.state.set('请选择本次输入设备，然后点击“开始录音”。')
  except Exception as exc:self.devices={};self.device.set('');self.state.set('设备列表读取失败：'+str(exc))
  self.update_controls()
 def update_controls(self):
  self.start_button.configure(state='normal' if not self.recording and self.device.get() in self.devices and not self.closing else 'disabled',text='重新录制' if self.take else '开始录音')
  self.stop_button.configure(state='normal' if self.recording and not self.stop.is_set() else 'disabled')
  self.play_button.configure(state='normal' if self.take and not self.recording and not self.closing else 'disabled',text='停止试听' if self.playing else '试听录音')
  valid=self.take is not None and not microphone.quality(self.take)[0]
  self.use_button.configure(state='normal' if valid and not self.recording and not self.closing else 'disabled');self.refresh_button.configure(state='disabled' if self.recording else 'normal');self.device_box.configure(state='disabled' if self.recording else 'readonly');self.text.configure(state='disabled' if self.recording else 'normal')
 def start(self):
  if self.recording or self.closing:return
  chosen=self.devices.get(self.device.get())
  if not chosen:messagebox.showinfo('选择麦克风','请手动选择本次使用的输入设备。',parent=self.window);return
  self.stop_playback();self.recording=True;self.stop=threading.Event();self.state.set('正在打开所选麦克风……');self.elapsed.set('0.0秒 / 最长30秒');self.level['value']=0;self.report.set('录音时保持自然语气；说完完整句子后点击“停止录音”。');self.update_controls()
  def run():
   try:
    take=microphone.Recorder(dict(chosen)).capture(self.stop,lambda kind,data:self.events.put((kind,data)));self.events.put(('done',take))
   except Exception as exc:self.events.put(('error',str(exc)))
  threading.Thread(target=run,daemon=True).start()
 def stop_recording(self):
  if self.recording:self.stop.set();self.state.set('正在停止并释放麦克风……');self.update_controls()
 def poll(self):
  try:
   while True:
    kind,value=self.events.get_nowait()
    if kind=='started':self.state.set(f'● 正在录音 · {value["sample_rate"]}Hz · 请自然朗读')
    elif kind=='level':
     import math
     self.elapsed.set(f'{value["seconds"]:.1f}秒 / 最长30秒');self.level['value']=value['peak']*100;self.db.set(f'{20*math.log10(max(value["peak"],1e-9)):.0f} dBFS')
    elif kind in ('done','error'):
     self.recording=False;self.level['value']=0;self.db.set('麦克风已释放')
     if kind=='done':
      self.take=value;self.elapsed.set(f'{value.seconds:.2f}秒 / 最长30秒');problems,notes=microphone.quality(value);self.state.set('录音已停止。可以试听、重录或采用。' if not problems else '请重新录制合适的参考样本。');stats=value.stats
      self.report.set(f'单声道16位WAV / {value.rate}Hz / {value.seconds:.2f}秒 / 平均音量{stats["rms_dbfs"]:.1f}dBFS\n'+' '.join(problems+notes or ['长度与音量检查通过。请试听，确认人声清晰且没有口误、背景音乐。']))
     else:self.state.set('录音未完成：'+value);self.report.set('可以检查连接和权限后重试；原来已采用的参考录音不会被覆盖。')
     self.update_controls()
  except queue.Empty:pass
  if self.closing and not self.recording:self.destroy();return
  if self.window.winfo_exists():self.poll_id=self.window.after(100,self.poll)
 def stop_playback(self):
  if self.play_id:
   self.window.after_cancel(self.play_id);self.play_id=None
  if self.playing:
   import winsound
   winsound.PlaySound(None,0)
  self.playing=False
  if self.preview_path:
   try:self.preview_path.unlink(missing_ok=True)
   except OSError:pass
   self.preview_path=None
  self.update_controls()
 def play(self):
  if self.playing:self.stop_playback();return
  if not self.take or self.recording:return
  try:
   import winsound
   root=Path(__file__).resolve().parents[2];self.preview_path=Path(os.environ['ZMYG_CACHE_ROOT'])/'录音试听'/f'{uuid.uuid4().hex}.wav';self.take.save(self.preview_path)
   winsound.PlaySound(str(self.preview_path),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT);self.playing=True;self.play_id=self.window.after(round(self.take.seconds*1000)+300,self.stop_playback);self.update_controls()
  except Exception as exc:self.stop_playback();show_error('未能试听',str(exc),parent=self.window)
 def use(self):
  if not self.take or self.recording or microphone.quality(self.take)[0]:return
  self.stop_playback()
  try:
   directory=Path(self.output_dir())/'麦克风录音';path=directory/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.wav');self.take.save(path)
   meta={**self.take.stats,'device':self.take.device,'recorded_at':datetime.datetime.now().isoformat(),'mechanism':'manual_local_microphone'};path.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8');self.on_use(path,self.take.seconds,self.text.get('1.0','end').strip());self.destroy()
  except Exception as exc:show_error('参考录音未采用',str(exc),parent=self.window)
 def close(self):
  if self.recording:self.closing=True;self.stop_recording();self.state.set('正在停止录音并关闭……');return
  self.destroy()
 def destroy(self):
  if not self.window.winfo_exists():return
  if getattr(self,'poll_id',None):self.window.after_cancel(self.poll_id);self.poll_id=None
  self.stop_playback();self.take=None
  if self.window.winfo_exists():self.window.grab_release();self.window.destroy()
