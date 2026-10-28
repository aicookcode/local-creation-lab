"""Personal/research-only Wav2Lip comparison. Excluded from production defaults."""
from pathlib import Path
import os,sys,argparse,json,time,subprocess,math,gc,wave,hashlib
ROOT=Path(__file__).resolve().parents[2];PROJECT=ROOT/'projects'/'portrait-local-test-20261018'
p=argparse.ArgumentParser();p.add_argument('--name',default='F-Wav2Lip-personal-only');p.add_argument('--bottom-pad',type=int,default=12);p.add_argument('--threads',type=int,default=2);p.add_argument('--aligned',action='store_true');p.add_argument('--sharpen',type=float,default=0.);p.add_argument('--audio');p.add_argument('--out-dir');p.add_argument('--fps',type=int,default=25,choices=[25,30])
p.add_argument('--image');p.add_argument('--landmarks');p.add_argument('--model-manifest')
p.add_argument('--reference-blend',action='store_true',help=argparse.SUPPRESS)
a=p.parse_args();os.environ.update(OMP_NUM_THREADS=str(a.threads),MKL_NUM_THREADS=str(a.threads),PYTHONNOUSERSITE='1',NUMBA_CACHE_DIR=str(Path(os.environ.get('ZMYG_CACHE_ROOT',str(ROOT.parent/'cache')))/'portrait-numba'))
repo=Path(__file__).resolve().parent/'Wav2Lip';sys.path[:0]=[str(Path(__file__).resolve().parent),str(repo)]
import numpy as np,cv2,torch
from PIL import Image
from models import Wav2Lip
import audio
torch.set_num_threads(a.threads);torch.set_num_interop_threads(1)
record=json.loads((Path(a.model_manifest) if a.model_manifest else PROJECT/'wav2lip-model-integrity-and-license.json').read_text(encoding='utf-8'))
if not record['verified']:raise RuntimeError('Unverified checkpoint')
out=Path(a.out_dir).resolve() if a.out_dir else PROJECT/a.name;out.mkdir(parents=True,exist_ok=True)
audio_path=Path(a.audio).resolve() if a.audio else PROJECT/'inputs'/'voice-7s.wav'
if (out/'result.mp4').exists():raise FileExistsError('Output exists; choose a new case directory.')
start=time.monotonic();model=Wav2Lip()
checkpoint=torch.load(ROOT.parent/'models'/'portrait'/'Wav2Lip-personal-research-only'/'wav2lip.pth',map_location='cpu')
model.load_state_dict({k.replace('module.',''):v for k,v in checkpoint['state_dict'].items()})
del checkpoint;gc.collect();model.eval()
src=np.asarray(Image.open(Path(a.image) if a.image else PROJECT/'inputs'/'portrait.png').convert('RGB'))[:,:,::-1].copy();h,w=src.shape[:2]
lmk=np.load(Path(a.landmarks) if a.landmarks else PROJECT/'motion-driver-7s'/'source-landmarks68.npy')
mi,ma=lmk.min(0),lmk.max(0);fh=ma[1]-mi[1]
x1=max(0,int(mi[0]-10));x2=min(w,int(ma[0]+10));y1=max(0,int(mi[1]-.30*fh));y2=min(h,int(ma[1]+a.bottom_pad))
face=cv2.resize(src[y1:y2,x1:x2],(96,96),interpolation=cv2.INTER_AREA)
if a.aligned:
 src3=np.array([lmk[36:42].mean(0),lmk[42:48].mean(0),lmk[48:68].mean(0)],dtype='float32')
 dst3=np.array([[28,32],[68,32],[48,70]],dtype='float32')
 align=cv2.getAffineTransform(src3,dst3);inverse=cv2.invertAffineTransform(align)
 face=cv2.warpAffine(src,align,(96,96),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REFLECT)
masked=face.copy();masked[48:]=0
image=np.concatenate([masked,face],axis=2).astype('float32')/255.
image=torch.from_numpy(image.transpose(2,0,1)[None])
with wave.open(str(audio_path)) as header:duration=header.getnframes()/header.getframerate()
fps=a.fps;frames=round(duration*fps);mel=None;mel_begin=0;mel_core=-1
mask=np.zeros((h,w),np.float32);lip=lmk[48:68];mi,ma=lip.min(0),lip.max(0);center=tuple(np.round((mi+ma)/2+[0,2]).astype(int))
axes=(round((ma[0]-mi[0])*.85+5),round(max((ma[0]-mi[0])*.5,(ma[1]-mi[1])*1.7)))
cv2.ellipse(mask,center,axes,0,0,360,1,-1);mask=cv2.GaussianBlur(mask,(15,15),3)[...,None]
if a.aligned:
 coverage=cv2.warpAffine(np.ones((96,96),np.float32),inverse,(w,h),flags=cv2.INTER_LINEAR)
 coverage=cv2.erode(coverage,np.ones((9,9),np.uint8))
 coverage=cv2.GaussianBlur(coverage,(9,9),2)
 mask*=coverage[...,None]
from mouth_blend import MouthBlend
blender=MouthBlend(src,mask)
ffmpeg=ROOT/'core'/'video'/'ffmpeg.exe'
pending=out/'result.pending.mp4'
proc=subprocess.Popen([str(ffmpeg),'-y','-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','bgr24','-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-i',str(audio_path),'-map','0:v','-map','1:a','-c:v','libx264','-threads',str(a.threads),'-crf','14','-preset','fast','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-shortest','-movflags','+faststart',str(pending)],stdin=subprocess.PIPE)
written=0
try:
 for first in range(0,frames,4):
  batch=[]
  for i in range(first,min(first+4,frames)):
   core=int(i/fps//60)
   if core!=mel_core:
    mel_core=core;mel_begin=max(0,core*60-.5);length=min(duration-mel_begin,61.5)
    pcm=subprocess.run([str(ffmpeg),'-v','error','-nostdin','-ss',str(mel_begin),'-i',str(audio_path),'-t',str(length),'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
    wav=np.frombuffer(pcm,dtype=np.float32).copy();mel=audio.melspectrogram(wav)
   index=min(max(0,int((i/fps-mel_begin)*80)),mel.shape[1]-16);batch.append(mel[:,index:index+16])
  m=torch.from_numpy(np.array(batch)[:,None].astype('float32'))
  with torch.no_grad():pred=model(m,image.repeat(len(batch),1,1,1)).permute(0,2,3,1).numpy()
  for j,patch in enumerate(pred):
   patch=np.clip(patch*255,0,255).astype('uint8')
   if a.sharpen:patch=cv2.addWeighted(patch,1+a.sharpen,cv2.GaussianBlur(patch,(0,0),1),-a.sharpen,0)
   if a.aligned:generated=cv2.warpAffine(patch,inverse,(w,h),flags=cv2.INTER_CUBIC)
   else:
    generated=src.copy();patch=cv2.resize(patch,(x2-x1,y2-y1),interpolation=cv2.INTER_CUBIC)
    generated[y1:y2,x1:x2]=patch
   result=np.clip(generated*mask+src*(1-mask),0,255).astype('uint8') if a.reference_blend else blender.apply(generated)
   proc.stdin.write(result.tobytes())
   written+=1
   i=first+j
   if i in (0,15,40,90):Image.fromarray(result[:,:,::-1]).save(out/f'frame-{i:03d}.png')
  print(f'{min(first+4,frames)}/{frames} {time.monotonic()-start:.1f}s',flush=True)
finally:
 proc.stdin.close();code=proc.wait()
if code or written!=frames:raise RuntimeError('Encoding failed or incomplete frame count; no completed cache published.')
os.rename(pending,out/'result.mp4')
with (out/'result.mp4').open('rb') as stream:output_sha256=hashlib.file_digest(stream,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else None
if output_sha256 is None:
 digest=hashlib.sha256()
 with (out/'result.mp4').open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 output_sha256=digest.hexdigest()
info={'model':'Wav2Lip (personal/research-only)','args':vars(a),'frames':written,'fps':fps,'elapsed_s':round(time.monotonic()-start,2),'status':'completed','output_sha256':output_sha256,'license':'Non-commercial. Not included in production recommendation.','fixed_box':[x1,y1,x2,y2],'original_preserving_mask':'mouth only','blend':'reference' if a.reference_blend else 'roi-exact'}
(out/'run.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
if code:raise RuntimeError('Encoding failed')
print(info,flush=True)
