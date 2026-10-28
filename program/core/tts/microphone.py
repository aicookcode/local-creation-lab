"""Opt-in Windows microphone capture. No listening on import or device enumeration.

Uses the OS waveIn API, so the portable bundle needs no new audio driver/package.
API references: learn.microsoft.com/windows/win32/api/mmeapi/{waveinopen,wavehdr}.
"""
import array,collections,ctypes as C,math,os,time,wave
from dataclasses import dataclass
from pathlib import Path

WORD=C.c_uint16;DWORD=C.c_uint32;UINT=C.c_uint32;PTR=C.c_size_t
class WaveFormat(C.Structure):
 _pack_=1
 _fields_=[('tag',WORD),('channels',WORD),('rate',DWORD),('bytes_per_second',DWORD),('block_align',WORD),('bits',WORD),('extra',WORD)]
class WaveHeader(C.Structure):
 _fields_=[('data',C.c_void_p),('length',DWORD),('recorded',DWORD),('user',PTR),('flags',DWORD),('loops',DWORD),('next',C.c_void_p),('reserved',PTR)]
class InputCaps(C.Structure):
 _fields_=[('manufacturer',WORD),('product',WORD),('version',DWORD),('name',C.c_wchar*32),('formats',DWORD),('channels',WORD),('reserved',WORD)]

def native_api():
 if os.name!='nt':raise RuntimeError('当前录音入口支持Windows；其他系统请使用音频文件导入。')
 api=C.WinDLL('winmm.dll')
 definitions={
  'waveInGetNumDevs':([],UINT),'waveInGetDevCapsW':([PTR,C.POINTER(InputCaps),UINT],UINT),
  'waveInGetErrorTextW':([UINT,C.c_wchar_p,UINT],UINT),
  'waveInOpen':([C.POINTER(C.c_void_p),UINT,C.POINTER(WaveFormat),PTR,PTR,DWORD],UINT),
  'waveInPrepareHeader':([C.c_void_p,C.POINTER(WaveHeader),UINT],UINT),
  'waveInUnprepareHeader':([C.c_void_p,C.POINTER(WaveHeader),UINT],UINT),
  'waveInAddBuffer':([C.c_void_p,C.POINTER(WaveHeader),UINT],UINT),
  'waveInStart':([C.c_void_p],UINT),'waveInReset':([C.c_void_p],UINT),'waveInClose':([C.c_void_p],UINT)}
 for name,(args,result) in definitions.items():
  fn=getattr(api,name);fn.argtypes=args;fn.restype=result
 return api

def error(api,code,action):
 message=C.create_unicode_buffer(256);api.waveInGetErrorTextW(code,message,len(message))
 return RuntimeError(f'{action}失败：{message.value or "录音设备错误"}（{code}）。请检查设备连接、其他程序占用，以及Windows是否允许桌面应用访问麦克风。')
def check(api,code,action):
 if code:raise error(api,code,action)
def list_devices(api=None):
 api=api or native_api();result=[]
 for i in range(api.waveInGetNumDevs()):
  caps=InputCaps();code=api.waveInGetDevCapsW(i,C.byref(caps),C.sizeof(caps))
  if not code:result.append({'id':i,'name':caps.name,'channels':caps.channels})
 return result

@dataclass
class Take:
 pcm:bytes
 rate:int
 device:dict
 stats:dict
 @property
 def seconds(self):return len(self.pcm)/(self.rate*2)
 def save(self,path):
  path=Path(path)
  if path.exists():raise FileExistsError('录音文件已存在，请使用新的文件名。')
  path.parent.mkdir(parents=True,exist_ok=True)
  with wave.open(str(path),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(self.rate);f.writeframes(self.pcm)
  return path

class PCMCollector:
 def __init__(self,rate,channels,maximum=30):
  self.rate=rate;self.channels=channels;self.limit=int(rate*maximum);self.parts=[];self.frames=0;self.sum_squares=0.;self.peak=0;self.clipped=0
 def append(self,data):
  # Only whole frames are accepted. Stereo capture is downmixed without gain.
  data=data[:len(data)//(2*self.channels)*(2*self.channels)];source=array.array('h');source.frombytes(data)
  mono=source if self.channels==1 else array.array('h',(int(sum(source[i:i+self.channels])/self.channels) for i in range(0,len(source),self.channels)))
  mono=mono[:max(0,self.limit-self.frames)]
  if not mono:return 0.
  peak=max(abs(v) for v in mono);self.peak=max(self.peak,peak);self.clipped+=sum(abs(v)>=32760 for v in mono);self.sum_squares+=sum(float(v)*v for v in mono);self.frames+=len(mono);self.parts.append(mono.tobytes());return peak/32768
 def result(self,device):
  rms=math.sqrt(self.sum_squares/max(1,self.frames))/32768
  return Take(b''.join(self.parts),self.rate,dict(device),{'seconds':self.frames/self.rate,'sample_rate':self.rate,'channels':1,'peak':self.peak/32768,'rms_dbfs':20*math.log10(max(rms,1e-9)),'clipped_samples':self.clipped,'effects':'none'})

# Keep driver-owned buffers alive if an abnormal driver refuses to release them.
_quarantined=[]
class Recorder:
 def __init__(self,device,api=None,maximum=30):
  self.device=device;self.api=api or native_api();self.maximum=maximum
 def capture(self,stop,notify=lambda event,data:None):
  api=self.api;handle=C.c_void_p();held=[];pending=collections.deque();prepared=[];collector=None;reset_ok=False;closed=False
  try:
   for rate,channels in [(48000,1),(44100,1),(24000,1),(16000,1),(48000,2),(44100,2)]:
    fmt=WaveFormat(1,channels,rate,rate*channels*2,channels*2,16,0)
    if api.waveInOpen(None,self.device['id'],C.byref(fmt),0,0,1)==0:break
   else:raise RuntimeError('所选设备不支持常用16位PCM录音格式，请换一个麦克风。')
   if stop.is_set():raise RuntimeError('录音已取消，未开启麦克风。')
   check(api,api.waveInOpen(C.byref(handle),self.device['id'],C.byref(fmt),0,0,0),'打开麦克风')
   collector=PCMCollector(rate,channels,self.maximum);length=int(rate*.1)*channels*2
   for _ in range(8):
    buf=C.create_string_buffer(length);header=WaveHeader(data=C.addressof(buf),length=length);held.append((buf,header))
    check(api,api.waveInPrepareHeader(handle,C.byref(header),C.sizeof(header)),'准备录音缓冲');prepared.append(header)
    check(api,api.waveInAddBuffer(handle,C.byref(header),C.sizeof(header)),'提交录音缓冲');pending.append(header)
   check(api,api.waveInStart(handle),'开始录音');notify('started',{'sample_rate':rate,'channels':channels});last_data=time.monotonic();start=last_data
   while not stop.is_set() and collector.frames<collector.limit:
    while pending and pending[0].flags&1 and not stop.is_set() and collector.frames<collector.limit:
     header=pending.popleft();count=min(header.recorded,header.length)
     peak=collector.append(C.string_at(header.data,count));last_data=time.monotonic();notify('level',{'seconds':collector.frames/rate,'peak':peak})
     header.recorded=0;header.flags&=~1
     check(api,api.waveInAddBuffer(handle,C.byref(header),C.sizeof(header)),'继续录音');pending.append(header)
    if time.monotonic()-last_data>3:raise RuntimeError('麦克风超过3秒未返回音频，可能已断开。请检查设备后重新录制。')
    if time.monotonic()-start>self.maximum+2:raise RuntimeError('设备音频时间异常，录音已停止。请更换设备重试。')
    stop.wait(.015)
   check(api,api.waveInReset(handle),'停止录音');reset_ok=True
   # Reset returns even the last partly-filled buffer. Drain in queue order.
   for header in pending:
    if header.flags&1:collector.append(C.string_at(header.data,min(header.recorded,header.length)))
   if not collector.frames:raise RuntimeError('没有录到音频，请重新录制。')
   return collector.result(self.device)
  finally:
   if handle.value:
    if not reset_ok:reset_ok=api.waveInReset(handle)==0
    for header in prepared:
     # Buffers must be returned before Python can release their memory.
     code=api.waveInUnprepareHeader(handle,C.byref(header),C.sizeof(header))
     if code==33 and reset_ok:
      time.sleep(.03);code=api.waveInUnprepareHeader(handle,C.byref(header),C.sizeof(header))
    closed=api.waveInClose(handle)==0
    if not closed:
     _quarantined.append((api,handle,held));raise RuntimeError('录音设备未正常释放。请关闭工作台并检查设备驱动后重试。')

def quality(take):
 problems=[]
 if take.seconds<2:problems.append('录音少于2秒，请录制一句完整的话。')
 if take.stats['rms_dbfs']<-60:problems.append('录音接近静音，请检查麦克风与输入音量。')
 notes=[]
 if take.stats['clipped_samples']:notes.append('存在接近满幅的采样，请试听是否失真，建议降低输入音量后重录。')
 if take.stats['rms_dbfs']<-35 and not problems:notes.append('声音偏轻，建议靠近麦克风或适当提高输入音量。')
 return problems,notes
