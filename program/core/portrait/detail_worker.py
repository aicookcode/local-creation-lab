"""FP32 detail-preserving lips and local natural eyelids, independent of trials.

Only this job's photo, landmarks, audio and explicit eye settings are used.
Models run in separate serial stages. Lossless ROI caches avoid repeated models
and permit captions to be encoded directly from original frames.
"""
from pathlib import Path
import sys,os,json,time,math,subprocess,contextlib,argparse,gc,bisect,hashlib
PROGRAM=Path(__file__).resolve().parents[2];CODE=PROGRAM/'core'
sys.path[:0]=[str(CODE),str(CODE/'portrait_lab/Wav2Lip')]
from portable_bootstrap import configure,unpack_paths,pack_paths
configure()
from studio_paths import MODELS
from portrait.config import sha,inside,duration_wav
from task_progress import event,guard_space
import numpy as np,cv2,torch
from PIL import Image
from scipy.ndimage import gaussian_filter1d,maximum_filter1d
torch.set_num_threads(2);torch.set_num_interop_threads(1);cv2.setNumThreads(2)
FF=CODE/'video/ffmpeg.exe';WEIGHTS=MODELS/'portrait';RECIPE='original-lip-smooth-fp32-v1'
def save(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.pending');q.write_text(json.dumps(pack_paths(obj),ensure_ascii=False,indent=2),encoding='utf-8');q.replace(p)
def read(p):return unpack_paths(json.loads(Path(p).read_text(encoding='utf-8-sig')))
def smooth(x):
 x=np.clip(x,0,1);return x*x*x*(x*(x*6-15)+10)
def image_valid(path):
 if not path.is_file():return False
 try:
  with Image.open(path) as image:image.verify()
  return True
 except (OSError,ValueError):return False
def save_image(path,pixels):
 # Publish only a complete PNG; interrupted writes never become reusable frames.
 path.parent.mkdir(parents=True,exist_ok=True);pending=path.with_name(path.stem+'.pending.png')
 Image.fromarray(pixels).save(pending);pending.replace(path)
def save_array(path,value):
 pending=path.with_name(path.stem+'.pending.npy');np.save(pending,value);pending.replace(path)
class Engine:
 def __init__(self,path):
  self.path=inside(path);self.job=read(path);j=self.job;self.cache=inside(j['cache']);self.cache.mkdir(parents=True,exist_ok=True)
  self.manifest=read(inside(j['template']));self.assets=inside(j['template']).parent
  self.image=self.assets/'portrait.png';self.audio=inside(j['audio']);self.lmk=np.load(self.assets/'landmarks68.npy').astype(np.float32)
  self.src=np.asarray(Image.open(self.image).convert('RGB')).astype(np.float32);h,w=self.src.shape[:2]
  self.frames=round(duration_wav(self.audio)*30);self.begin=0;self.last=self.frames
  if j.get('preview'):
   self.begin=round(j['preview']['start']*30);self.last=min(self.frames,round((j['preview']['start']+j['preview']['seconds'])*30))
  if self.last<=self.begin:raise ValueError('预览区间不足一帧。')
  if j['framing']=='居中裁切':
   if self.manifest.get('crop'):cx,cy,cw,ch=map(float,self.manifest['crop'])
   else:
    cw=min(w,h*1080/1920);ch=min(h,w*1920/1080);cx=(w-cw)/2;cy=(h-ch)/2
   self.scale=1080/cw;self.ox=cx;self.oy=cy
  else:
   self.scale=min(1080/w,1920/h);self.ox=-(1080/self.scale-w)/2;self.oy=-(1920/self.scale-h)/2
  y,x=np.mgrid[:1920,:1080];sx=self.ox+x.astype(np.float32)/self.scale;sy=self.oy+y.astype(np.float32)/self.scale
  self.baseline=cv2.remap(self.src,sx,sy,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_CONSTANT,borderValue=(16,23,34))
  final=(self.lmk-[self.ox,self.oy])*self.scale;lo=final.min(0);hi=final.max(0);pad=max(hi-lo)*.45
  self.rect=[max(0,int(lo[0]-pad)),max(0,int(lo[1]-pad)),min(1080,int(hi[0]+pad)+1),min(1920,int(hi[1]+pad)+1)]
  x1,y1,x2,y2=self.rect
  if x2<=x1 or y2<=y1:raise ValueError('当前裁切没有包含完整人脸。')
  self.roi=(slice(y1,y2),slice(x1,x2));self.x=sx[self.roi];self.y=sy[self.roi]
  self.final_lmk=final;self.face_box=[int(lo[0]-.18*max(hi-lo)),int(lo[1]-.18*max(hi-lo)),int(hi[0]+.18*max(hi-lo)),int(hi[1]+.18*max(hi-lo))]
  self.face_box=[max(0,self.face_box[0]),max(0,self.face_box[1]),min(1080,self.face_box[2]),min(1920,self.face_box[3])]
  p=np.array([self.lmk[36:42].mean(0),self.lmk[42:48].mean(0),self.lmk[48:68].mean(0)],np.float32)
  self.A=cv2.getAffineTransform(p,np.array([[28,32],[68,32],[48,70]],np.float32));self.invA=cv2.invertAffineTransform(self.A)
  self.face=cv2.warpAffine(self.src[:,:,::-1].astype(np.uint8),self.A,(96,96),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT)
  mi,ma=self.lmk[48:68].min(0),self.lmk[48:68].max(0);self.center=(mi+ma)/2+[0,1];self.mouthw=float(ma[0]-mi[0])
  if self.mouthw<8:raise ValueError('嘴部过小，无法稳定保留纹理，请使用近景照片。')
  self.qx=self.A[0,0]*self.x+self.A[0,1]*self.y+self.A[0,2];self.qy=self.A[1,0]*self.x+self.A[1,1]*self.y+self.A[1,2]
  points=np.array([self.lmk[36:42].mean(0),self.lmk[42:48].mean(0),self.lmk[30],self.lmk[48],self.lmk[54]],np.float32)
  target=np.array([[192.98138,239.94708],[318.90277,240.1936],[256.63416,314.01935],[201.26117,371.41043],[313.08905,371.15118]],np.float32)
  self.B=cv2.estimateAffinePartial2D(points,target,method=cv2.LMEDS)[0].astype(np.float32)
  self.bx=self.B[0,0]*self.x+self.B[0,1]*self.y+self.B[0,2];self.by=self.B[1,0]*self.x+self.B[1,1]*self.y+self.B[1,2]
  self.pcm_path=self.cache/'analysis16k.f32'
  if not self.pcm_path.exists():
   tmp=self.pcm_path.with_suffix('.pending');subprocess.run([str(FF),'-y','-nostdin','-v','error','-i',str(self.audio),'-ac','1','-ar','16000','-f','f32le',str(tmp)],check=True);tmp.replace(self.pcm_path)
  self.pcm=np.memmap(self.pcm_path,dtype=np.float32,mode='r')
  rms=[]
  for i in range(self.frames):
   a=max(0,round(i/30*16000)-400);b=min(len(self.pcm),round(i/30*16000)+400);rms.append(float(np.sqrt(np.mean(self.pcm[a:b]**2))))
  self.gate=gaussian_filter1d(maximum_filter1d((np.array(rms)>.006).astype(np.float32),size=7,mode='nearest'),sigma=.75,mode='nearest')
  self.gate[self.gate<.003]=0;self.gate[self.gate>.997]=1
  from portrait.speech_constraints import activity
  self.speech_activity=activity(self.pcm,16000,self.frames,30)
  self.blink=j.get('blink',{'mode':'off','times':[]});self.started=time.monotonic()
 def progress(self,phase,n,total):print(event(phase,n,total,time.monotonic()-self.started),flush=True);guard_space(self.cache)
 def closure(self,i):
  if self.blink['mode']=='off':return 0.
  if self.blink['mode']=='custom':
   ordered=self.blink.get('times',[]);index=bisect.bisect_right(ordered,i/30)-1;times=ordered[index:index+1] if index>=0 else []
  else:
   # A 30fps schedule avoids both quadratic scans and long-run float drift.
   index=(i-42)//138;start=42+index*138
   times=[start/30] if index>=0 and start<self.frames-9 else []
  t=i/30;c=0.
  for start in times:
   if start<=t<=start+1/15:c=max(c,smooth((t-start)/(1/15)))
   elif start+1/15<t<=start+.1:c=1.
   elif start+.1<t<=start+.3:c=max(c,1-smooth((t-start-.1)/.2))
  return float(np.float32(c))
 def patch_path(self,c):return self.cache/'eyelids'/('closure-'+np.float32(c).tobytes().hex()+'.png')
 def blink_stage(self):
  states=sorted({self.closure(i) for i in range(self.frames)}-{0.})
  if not states:return
  needed=[c for c in states if not image_valid(self.patch_path(c))]
  if not needed:return
  repo=CODE/'portrait_lab/LivePortrait';sys.path.insert(0,str(repo))
  from src.config.inference_config import InferenceConfig
  from src.live_portrait_wrapper import LivePortraitWrapper
  from src.utils.crop import crop_image
  from src.utils.camera import get_rotation_matrix
  torch.manual_seed(20261019)
  cfg=InferenceConfig(flag_force_cpu=True,flag_use_half_precision=False);model=WEIGHTS/'LivePortrait/liveportrait'
  for field,name in [('checkpoint_F','appearance_feature_extractor.pth'),('checkpoint_M','motion_extractor.pth'),('checkpoint_G','spade_generator.pth'),('checkpoint_W','warping_module.pth')]:setattr(cfg,field,str(model/'base_models'/name))
  cfg.checkpoint_S=str(model/'retargeting_models/stitching_retargeting_module.pth')
  wrapper=LivePortraitWrapper(cfg);wrapper.inference_ctx=contextlib.nullcontext
  crop=crop_image(self.src.astype(np.uint8),self.lmk,dsize=512,scale=2.3,vx_ratio=0,vy_ratio=-.125,flag_do_rot=True)
  tensor=wrapper.prepare_source(crop['img_crop']);si=wrapper.get_kp_info(tensor);xs=wrapper.transform_keypoint(si);feature=wrapper.extract_feature_3d(tensor)
  ratios=[float(np.linalg.norm(self.lmk[a]-self.lmk[b])/(np.linalg.norm(self.lmk[c]-self.lmk[d])+1e-6)) for a,b,c,d in [(37,41,36,39),(43,47,42,45)]]
  delta=wrapper.retarget_eye(xs,torch.tensor([[*ratios,.01]],dtype=torch.float32))
  matrix=np.asarray(crop['M_o2c'],np.float32)[:2];save(self.cache/'eyelid-map.json',{'source_to_crop':matrix.tolist(),'ratios':ratios})
  for n,c in enumerate(needed):
   r=get_rotation_matrix(si['pitch'],si['yaw'],si['roll']);xd=si['scale'][...,None]*(si['kp']@r+si['exp']);xd[:,:,:2]+=si['t'][:,None,:2]
   xd=wrapper.stitching(xs,xd+delta*c);pixels=wrapper.parse_output(wrapper.warp_decode(feature,xs,xd)['out'])[0]
   path=self.patch_path(c);save_image(path,pixels);self.progress('准备自然眨眼',n+1,len(needed))
 def predict_stage(self):
  dest=self.cache/'predictions.npy'
  if dest.exists():return
  from models import Wav2Lip
  import audio as audio_utils
  model=Wav2Lip();ck=torch.load(WEIGHTS/'Wav2Lip-personal-research-only/wav2lip.pth',map_location='cpu',weights_only=True)
  model.load_state_dict({k.replace('module.',''):v for k,v in ck['state_dict'].items()},strict=True);del ck;model.eval()
  masked=self.face.copy();masked[48:]=0;image=torch.from_numpy(np.concatenate([masked,self.face],2).transpose(2,0,1)[None].astype(np.float32)/255)
  pending=dest.with_suffix('.pending.npy');out=np.lib.format.open_memmap(pending,mode='w+',dtype=np.float32,shape=(self.frames,96,96,3));core=-1
  for start in range(0,self.frames,4):
   batch=[]
   for i in range(start,min(start+4,self.frames)):
    if int(i/30//60)!=core:
     core=int(i/30//60);begin=max(0,core*60-.5);end=min(len(self.pcm)/16000,begin+61.5)
     mel=audio_utils.melspectrogram(np.asarray(self.pcm[round(begin*16000):round(end*16000)]).copy())
     if mel.shape[1]<16:mel=np.pad(mel,((0,0),(0,16-mel.shape[1])),mode='edge')
    pos=min(max(0,int((i/30-begin)*80)),mel.shape[1]-16);batch.append(mel[:,pos:pos+16])
   with torch.inference_mode():pred=model(torch.from_numpy(np.array(batch)[:,None].astype(np.float32)),image.repeat(len(batch),1,1,1)).permute(0,2,3,1).numpy()
   out[start:start+len(batch)]=pred
   if start%40==0:self.progress('生成同步嘴型',start+len(batch),self.frames)
  out.flush();del out;pending.replace(dest)
 def eye_patch(self,face,i):
  c=self.closure(i)
  if not c:return face
  M=np.asarray(read(self.cache/'eyelid-map.json')['source_to_crop'],np.float32);q=self.x*M[0,0]+self.y*M[0,1]+M[0,2];r=self.x*M[1,0]+self.y*M[1,1]+M[1,2]
  patch=np.asarray(Image.open(self.patch_path(c)).convert('RGB')).astype(np.float32);eye=cv2.remap(patch,q,r,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
  mask=np.zeros(self.src.shape[:2],np.float32)
  for e in [self.lmk[36:42],self.lmk[42:48]]:
   lo,hi=e.min(0),e.max(0);cv2.ellipse(mask,tuple(np.round((lo+hi)/2).astype(int)),(round((hi[0]-lo[0])*.70+2),round((hi[1]-lo[1])*.75+2)),0,0,360,1,-1)
  mask=cv2.GaussianBlur(mask,(7,7),1.1);weight=np.clip(cv2.remap(mask,self.x,self.y,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT),0,1)[...,None]*smooth(min(1,c/.3))
  return eye*weight+face*(1-weight)
 def cavity_stage(self):
  pred=np.load(self.cache/'predictions.npy',mmap_mode='r');folder=self.cache/'cavity';folder.mkdir(exist_ok=True)
  model=None
  inverse=cv2.invertAffineTransform(self.B);yy,xx=np.mgrid[:512,:512].astype(np.float32);sx=inverse[0,0]*xx+inverse[0,1]*yy+inverse[0,2];sy=inverse[1,0]*xx+inverse[1,1]*yy+inverse[1,2]
  aligned=cv2.remap(self.src,sx,sy,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
  ax=self.A[0,0]*sx+self.A[0,1]*sy+self.A[0,2];ay=self.A[1,0]*sx+self.A[1,1]*sy+self.A[1,2]
  for i in range(self.frames):
   dest=folder/f'{i:08d}.png'
   if image_valid(dest):continue
   face=self.eye_patch(self.baseline[self.roi].copy(),i)
   if self.gate[i]>0:
    raw=np.asarray(pred[i])*255;patch=np.clip(raw,0,255).astype(np.float32)
    patch=cv2.addWeighted(patch,1.6,cv2.GaussianBlur(patch,(0,0),1),-.6,0)[:,:,::-1].copy()
    generated=cv2.remap(patch,self.qx,self.qy,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
    flow=cv2.calcOpticalFlowFarneback(cv2.cvtColor(np.clip(patch,0,255).astype(np.uint8),cv2.COLOR_RGB2GRAY),cv2.cvtColor(self.face,cv2.COLOR_BGR2GRAY),None,.5,4,15,8,7,1.5,cv2.OPTFLOW_FARNEBACK_GAUSSIAN)
    fx=cv2.remap(flow[...,0],self.qx,self.qy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101);fy=cv2.remap(flow[...,1],self.qx,self.qy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT_101)
    qx=self.qx+fx;qy=self.qy+fy;ox=self.invA[0,0]*qx+self.invA[0,1]*qy+self.invA[0,2];oy=self.invA[1,0]*qx+self.invA[1,1]*qy+self.invA[1,2]
    rim=cv2.remap(self.src,ox,oy,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
    inner=np.sqrt(((self.x-self.center[0])/(self.mouthw*.49+1))**2+((self.y-self.center[1])/(self.mouthw*.22+1))**2);aperture=np.clip((1-inner)*4,0,1)[...,None]
    generated=generated*aperture+rim*(1-aperture)
    rx=self.mouthw*.58+2;ry=self.mouthw*.31+2;distance=np.sqrt(((self.x-self.center[0])/rx)**2+((self.y-self.center[1])/ry)**2)
    weight=np.clip((1-distance)*min(rx,ry)/1.5,0,1)[...,None]*self.gate[i]
    face=np.clip(generated*weight+face*(1-weight),0,255).astype(np.uint8).astype(np.float32)
    if model is None:
     from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
     model=GFPGANv1Clean(out_size=512,num_style_feat=512,channel_multiplier=2,decoder_load_path=None,fix_decoder=False,num_mlp=8,input_is_latent=True,different_w=True,narrow=1,sft_half=True)
     ck=torch.load(WEIGHTS/'facexlib/GFPGANv1.4.pth',map_location='cpu',weights_only=True);model.load_state_dict(ck.get('params_ema',ck.get('params')),strict=True);del ck;model.eval()
    neural=cv2.remap(raw[:,:,::-1],ax,ay,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
    dist=np.sqrt(((sx-self.center[0])/rx)**2+((sy-self.center[1])/ry)**2);alpha=np.clip((1-dist)*6,0,1)[...,None]*self.gate[i]
    context=aligned*(1-alpha)+neural*alpha;tensor=torch.from_numpy(np.ascontiguousarray(context.transpose(2,0,1)))[None]/127.5-1
    with torch.inference_mode():restored=model(tensor,return_rgb=False,randomize_noise=False)[0][0].permute(1,2,0).numpy()
    restored=np.clip((restored+1)*127.5,0,255).astype(np.float32);enhanced=cv2.remap(restored,self.bx,self.by,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
    rx2=self.mouthw*.40+.5;ry2=self.mouthw*.135+.5;cavity=np.sqrt(((self.x-self.center[0])/rx2)**2+((self.y-(self.center[1]-.5))/ry2)**2)
    alpha=np.clip((1-cavity)*min(rx2,ry2)*self.scale,0,1)[...,None]*self.gate[i]*.85;face=face*(1-alpha)+enhanced*alpha
   save_image(dest,np.clip(face,0,255).astype(np.uint8))
   if i%10==0:self.progress('保留口周与修复口腔',i+1,self.frames)
 def cached_frame(self,i):
  result=np.clip(self.baseline,0,255).astype(np.uint8);result[self.roi]=np.asarray(Image.open(self.cache/'cavity'/f'{i:08d}.png').convert('RGB'));return result
 def track_stage(self):
  from facexlib.alignment import FAN,landmark_98_to_68
  # Scoped to this dedicated tracking process. Other neural stages keep their
  # existing execution settings; all weights, frames and FP32 types are kept.
  flush_supported=bool(torch.set_flush_denormal(True))
  if not flush_supported:print('此设备不支持跟踪阶段的CPU极小数优化，保持FP32继续处理。',flush=True)
  file=self.cache/'track.npy';model=FAN(num_modules=4,num_landmarks=98,device='cpu')
  model.load_state_dict(torch.load(WEIGHTS/'facexlib/alignment_WFLW_4HG.pth',map_location='cpu',weights_only=True)['state_dict'],strict=True);model.eval()
  x1,y1,x2,y2=self.face_box
  def detect(frame):
   with torch.inference_mode():value=landmark_98_to_68(model.get_landmarks(frame[y1:y2,x1:x2,::-1].copy())).astype(np.float32)
   return value+[x1,y1]
  neutral=detect(np.clip(self.baseline,0,255).astype(np.uint8))
  context=hashlib.sha256(json.dumps({'key':self.job['key'],'frames':self.frames,'face_box':self.face_box,'algorithm':'fan98-to68-fp32-v1','flush_denormal':flush_supported},sort_keys=True).encode()).hexdigest()
  track=None
  if file.exists():
   try:
    candidate=np.load(file)
    raw=read(self.cache/'track-raw.json') if (self.cache/'track-raw.json').exists() else {}
    receipt=read(self.cache/'track-completed.json') if (self.cache/'track-completed.json').exists() else {}
    bound=(raw.get('context')==context and raw.get('sha256')==sha(file)) or receipt.get('files',{}).get('track.npy')==sha(file)
    if bound and candidate.shape==(self.frames,68,2) and candidate.dtype==np.float32 and np.isfinite(candidate).all():track=candidate
   except (OSError,ValueError,KeyError,EOFError):pass
  if track is None:
   chunks=self.cache/'track-chunks';chunks.mkdir(exist_ok=True);blocks=[]
   for first in range(0,self.frames,32):
    last=min(first+32,self.frames);part=chunks/f'{first:08d}.npy';meta=part.with_suffix('.json');block=None
    if part.exists() and meta.exists():
     try:
      record=read(meta);value=np.load(part)
      if record.get('context')==context and record.get('first')==first and record.get('last')==last and record.get('sha256')==sha(part) and value.dtype==np.float32 and value.shape==(last-first,68,2) and np.isfinite(value).all():block=value
     except (OSError,ValueError,KeyError,EOFError):pass
    if block is None:
     values=[]
     for i in range(first,last):
      values.append(detect(self.cached_frame(i)))
      if i%15==0:self.progress('跟踪下唇位置',i+1,self.frames)
     block=np.asarray(values,np.float32)
     if block.shape!=(last-first,68,2) or not np.isfinite(block).all():raise ValueError('下唇跟踪片段无效，已保留此前检查点，请检查本次人物照片与日志。')
     save_array(part,block)
     save(meta,{'context':context,'first':first,'last':last,'sha256':sha(part)})
    else:self.progress('复用已校验下唇跟踪片段',last,self.frames)
    blocks.append(block)
   track=np.concatenate(blocks,axis=0);save_array(file,track)
   save(self.cache/'track-raw.json',{'context':context,'sha256':sha(file),'frames':self.frames,'precision':'FP32','all_frames':True})
  save(self.cache/'track-runtime.json',{'flush_denormal':flush_supported,'precision':'FP32','threads':torch.get_num_threads(),'checkpoint_frames':32,'all_frames':True})
  tracked=gaussian_filter1d(track,sigma=.7,axis=0,mode='nearest');delta=tracked-neutral
  factor=self.mouthw*self.scale/(45.1328125*1.875)
  shifts=np.clip(delta[:,[55,56,57,58,59,65,66,67],1].mean(1),-2*factor,8*factor)*self.gate
  target=gaussian_filter1d(shifts,sigma=1,mode='nearest');target=np.clip(target,-2*factor*self.gate,8*factor*self.gate)
  for _ in range(2):
   for i in range(1,len(target)):target[i]=np.clip(target[i],target[i-1]-2*factor,target[i-1]+2*factor)
   for i in range(len(target)-2,-1,-1):target[i]=np.clip(target[i],target[i+1]-2*factor,target[i+1]+2*factor)
  target[self.gate==0]=0;save_array(self.cache/'shift.npy',target.astype(np.float32))
  save(self.cache/'geometry.json',{'neutral':neutral.tolist(),'rect':self.rect,'face_box':self.face_box,'mouth_scale_factor':factor,'max_step_final_px':float(np.abs(np.diff(target)).max()) if len(target)>1 else 0,'geometry_only':True,'pixels_blurred':False})
 def final_frame(self,i,shifts):
  if self.speech_activity['gate'][i]==0:
   frame=np.clip(self.baseline,0,255).astype(np.uint8);frame[self.roi]=np.clip(self.eye_patch(frame[self.roi].astype(np.float32),i),0,255).astype(np.uint8);return frame
  frame=self.cached_frame(i)
  if not self.gate[i]:return frame
  shift=float(shifts[i]);points=self.final_lmk[[48,59,58,57,56,55,54,64,65,66,67,60]].astype(np.float32)
  left,right=points[:,0].min(),points[:,0].max()
  def offset(x):return np.clip(np.sin(np.pi*np.clip((x-left)/(right-left),0,1)),0,1)**.8*shift
  points[:,1]+=offset(points[:,0]);x1=max(0,int(points[:,0].min())-3);x2=min(1080,int(points[:,0].max())+4);y1=max(0,int(points[:,1].min())-3);y2=min(1920,int(points[:,1].max())+4)
  y,x=np.mgrid[y1:y2,x1:x2].astype(np.float32);mx=x/self.scale+self.ox;my=(y-offset(x))/self.scale+self.oy
  mask=np.zeros((y2-y1,x2-x1),np.uint8);cv2.fillPoly(mask,[np.round(points-[x1,y1]).astype(np.int32)],1)
  texture=cv2.remap(self.src,mx,my,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101);a=np.clip(cv2.distanceTransform(mask,cv2.DIST_L2,3)/1.2,0,1)[...,None]*self.gate[i]
  frame[y1:y2,x1:x2]=np.clip(frame[y1:y2,x1:x2]*(1-a)+texture*a,0,255).astype(np.uint8);return frame
 def encode(self,output,ass=None):
  output=inside(output)
  if output.exists():raise FileExistsError('输出已存在，请另存。')
  output.parent.mkdir(parents=True,exist_ok=True);pending=output.with_name(output.stem+'.pending.mp4');shifts=np.load(self.cache/'shift.npy',mmap_mode='r')
  args=[str(FF),'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r','30','-i','pipe:0','-ss',str(self.begin/30),'-i',str(self.audio)]
  cwd=output.parent
  if ass:
   ass=inside(ass);cwd=ass.parent;name=ass.name.replace("'","'\\''");fonts=os.path.relpath(PROGRAM/'resources/fonts',cwd).replace('\\','/');args+=['-vf',f"ass=filename='{name}':fontsdir='{fonts}'"]
  # Bound the encoded video by its exact generated frame count.  A duration
  # rounded from the audio can otherwise make ffmpeg omit the final frame.
  args+=['-map','0:v','-map','1:a','-frames:v',str(self.last-self.begin),'-c:v','libx264','-threads','2','-preset','fast','-crf','14','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','24000','-movflags','+faststart',str(pending)]
  with (self.cache/'encode-error.log').open('w',encoding='utf-8') as error:
   proc=subprocess.Popen(args,cwd=cwd,stdin=subprocess.PIPE,stderr=error)
   try:
    for i in range(self.begin,self.last):
     proc.stdin.write(self.final_frame(i,shifts).tobytes())
     if (i-self.begin)%30==0:self.progress('编码字幕成片' if ass else '编码人物母版',i-self.begin+1,self.last-self.begin)
   finally:proc.stdin.close();code=proc.wait()
  if code:raise RuntimeError('编码失败，已保留原缓存与错误日志。')
  subprocess.run([str(FF),'-v','error','-xerror','-i',str(pending),'-f','null','-'],check=True);pending.replace(output)
  from portrait.speech_constraints import write_provenance
  track=np.load(self.cache/'track.npy',mmap_mode='r');op=np.maximum(track[:,66,1]-track[:,62,1],0)+np.maximum(shifts,0);op[self.speech_activity['gate']==0]=0
  clip=slice(self.begin,self.last)
  write_provenance(output,self.audio,self.speech_activity['rms'][clip],self.speech_activity['peak'][clip],op[clip],np.ones(self.last-self.begin),np.zeros(self.last-self.begin),fps=30,extra={'aperture_source':'screening estimate from cached inner-lip landmarks plus source-lip displacement; not a final-pixel phoneme classifier','audio_gate_enforced':True,'begin_frame':self.begin,'transcript_phoneme_constraints_applied':False})
  save(output.with_suffix('.detail.json'),{'job':str(self.path),'video_sha256':sha(output),'one_encode_per_output':True,'recipe':RECIPE})
  save(self.path.with_name('制作结果.json'),{'status':'completed','output':str(output),'frames':self.last-self.begin,'fps':30,'duration_s':(self.last-self.begin)/30,'signature':self.job['signature'],'method':RECIPE,'head_motion':False,'body_motion':False,'natural_blinks':self.blink,'human_review_required':True})
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);parser.add_argument('--stage',choices=['blink','predict','cavity','track','encode'],required=True);parser.add_argument('--output');parser.add_argument('--ass');args=parser.parse_args()
 engine=Engine(args.job)
 if args.stage=='encode':engine.encode(args.output,args.ass)
 else:getattr(engine,{'blink':'blink_stage','predict':'predict_stage','cavity':'cavity_stage','track':'track_stage'}[args.stage])()
 if args.stage!='encode':
  files={str(p.relative_to(engine.cache)):sha(p) for p in engine.cache.rglob('*') if p.is_file() and not p.name.endswith('-completed.json') and '.pending' not in p.name and p.suffix!='.log'}
  save(engine.cache/(args.stage+'-completed.json'),{'recipe':RECIPE,'files':files,'stage':args.stage,'status':'completed'})
