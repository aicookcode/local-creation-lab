"""ROI-based 2D motion rendering in the bundled avatar Python environment."""
from pathlib import Path
import sys,json,subprocess,time,os
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'core'))
from portrait.config import inside,validate_events,pulse,duration_wav
import numpy as np,cv2
from PIL import Image
job_path=inside(sys.argv[1]);job=json.loads(job_path.read_text(encoding='utf-8'));manifest_path=inside(job['template']);manifest=json.loads(manifest_path.read_text(encoding='utf-8'));assets=manifest_path.parent
source,audio,output=map(inside,[job['source'],job['audio'],job['output']])
if output.exists():raise FileExistsError('请另存新文件，不能覆盖旧成片。')
output.parent.mkdir(parents=True,exist_ok=True)
seconds=duration_wav(audio);events=validate_events(job['events'],seconds)
cv2.setNumThreads(2);cap=cv2.VideoCapture(str(source));fps=cap.get(5);w,h=int(cap.get(3)),int(cap.get(4));total=int(cap.get(7))
if (w,h)!=tuple(manifest['canvas']) or fps!=30:raise ValueError('口型缓存尺寸或帧率不匹配，请重新生成。')
preview=job.get('preview')
begin=0.;end=seconds
if preview:
 begin=float(preview['start']);end=min(seconds,begin+float(preview['seconds']))
 if not 0<=begin<end<=seconds:raise ValueError('预览时间须在本次配音范围内。')
first=round(begin*fps);last=min(total,round(end*fps));cap.set(cv2.CAP_PROP_POS_FRAMES,first)
layers=[]
for name in ['head','forearm']:
 if name not in manifest:continue
 roi=manifest[name]['roi'];x1,y1,x2,y2=roi;pivot=manifest[name]['pivot']
 alpha=np.asarray(Image.open(assets/(name+'-mask.png'))).astype(np.float32)/255
 alpha=cv2.GaussianBlur(alpha,(5,5),.8)
 if name=='head' and manifest.get('schema',1)==1:
  xx,yy=np.meshgrid(np.arange(w),np.arange(h));neck=(xx>380)&(xx<455)&(yy>403)
  alpha=np.where(neck,alpha*np.clip((435-yy)/32.,0,1),alpha)
 plate=np.asarray(Image.open(assets/(name+'-edge-fill.png')).convert('RGB'))[:,:,::-1].copy()
 layers.append((name,(pivot[0]-x1,pivot[1]-y1),roi,alpha[y1:y2,x1:x2].copy(),plate[y1:y2,x1:x2].astype(np.float32)))
duration=(last-first)/fps
if last<=first:raise ValueError('预览区间不足一帧，请扩大时间范围。')
if job.get('framing')=='完整显示':vf='scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x101722,setsar=1'
else:vf='scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1'
pending=output.with_name(output.stem+'.pending.mp4');ffmpeg=ROOT/'core/video/ffmpeg.exe'
proc=subprocess.Popen([str(ffmpeg),'-y','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-ss',str(first/fps),'-i',str(audio),'-vf',vf,'-map','0:v','-map','1:a','-t',str(duration),'-c:v','libx264','-threads','2','-preset','fast','-crf','14','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','24000','-movflags','+faststart',str(pending)],stdin=subprocess.PIPE)
start=time.monotonic();count=0
try:
 for i in range(first,last):
  ok,frame=cap.read()
  if not ok:raise RuntimeError('口型缓存未完整解码。')
  t=i/fps;angles={part:sum(e['degree']*pulse(t,e) for e in events if e['part']==part) for part in ('head','forearm')}
  for name,pivot,roi,alpha,plate in layers:
   if abs(angles[name])<1e-8:continue
   x1,y1,x2,y2=roi;rw,rh=x2-x1,y2-y1;patch=frame[y1:y2,x1:x2].astype(np.float32)
   matrix=cv2.getRotationMatrix2D(pivot,angles[name],1)
   moved=cv2.warpAffine(alpha,matrix,(rw,rh),flags=cv2.INTER_LINEAR)[...,None]
   color=cv2.warpAffine(patch*alpha[...,None],matrix,(rw,rh),flags=cv2.INTER_LINEAR)
   vacated=np.clip((alpha[...,None]-moved)*2,0,1)
   frame[y1:y2,x1:x2]=np.clip(color+(patch*(1-vacated)+plate*vacated)*(1-moved),0,255).astype(np.uint8)
  proc.stdin.write(frame.tobytes());count+=1
  if count%90==0:print(f'动作合成 {count}/{last-first} 帧',flush=True)
finally:
 cap.release();proc.stdin.close();code=proc.wait()
if code or count!=last-first:raise RuntimeError('人物视频未完整生成，旧结果仍保留。')
os.replace(pending,output)
result={'status':'completed','output':job['output'],'frames':count,'fps':fps,'duration_s':count/fps,'source_start_s':first/fps,'preview':bool(preview),'elapsed_s':round(time.monotonic()-start,2),'method':'2D rigid head and forearm layers; no true 3D gestures.','signature':job['signature']}
job_path.with_name('制作结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('人物视频已完成：'+str(output),flush=True)
