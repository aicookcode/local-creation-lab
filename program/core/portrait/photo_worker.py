"""Generic FP32 photo-lip worker. Every asset is supplied by a bound job/cache."""
from pathlib import Path
import sys,json,argparse,subprocess,time,gc,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from portrait.detail_worker import Engine,read,save,save_array,save_image,image_valid,FF,WEIGHTS,PROGRAM,RECIPE,sha,np,cv2,torch,Image,smooth
from portrait.config import inside
from portrait.blink_timing import starts,closure
from portrait.speech_constraints import activity,constrain,audit,write_provenance
from portrait.photo_texture import PhotoTexture

class Worker(Engine):
 def __init__(self,job):
  super().__init__(job);self.eye_cache=inside(self.job['eye_cache']);self.ref_cache=inside(self.job['ref_cache']);self.render_cache=inside(self.job['render_cache']);self.blink_starts=starts(self.blink,self.frames,self.job['audio_sha256']+self.job['source_sha256']);self._eye_data=None
 def closure(self,i):return closure(i,self.blink_starts)
 def patch_path(self,c):return self.eye_cache/'eyelids'/('closure-'+np.float32(c).tobytes().hex()+'.png')
 def blink_stage(self):
  old=self.cache;self.cache=self.eye_cache
  try:super().blink_stage()
  finally:self.cache=old
 def eye_patch(self,face,i):
  c=self.closure(i)
  if not c:return face
  if self._eye_data is None:
   M=np.asarray(read(self.eye_cache/'eyelid-map.json')['source_to_crop'],np.float32);q=self.x*M[0,0]+self.y*M[0,1]+M[0,2];r=self.x*M[1,0]+self.y*M[1,1]+M[1,2];mask=np.zeros(self.src.shape[:2],np.float32)
   for pts in (self.lmk[36:42],self.lmk[42:48]):
    lo,hi=pts.min(0),pts.max(0);cv2.ellipse(mask,tuple(np.round((lo+hi)/2).astype(int)),(round((hi[0]-lo[0])*.70+2),round((hi[1]-lo[1])*.75+2)),0,0,360,1,-1)
   mask=cv2.GaussianBlur(mask,(7,7),1.1);weight=np.clip(cv2.remap(mask,self.x,self.y,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT),0,1)[...,None];self._eye_data=(q,r,weight,{})
  q,r,weight,patches=self._eye_data
  if c not in patches:
   patch=np.asarray(Image.open(self.patch_path(c)).convert('RGB')).astype(np.float32);patches[c]=cv2.remap(patch,q,r,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
  a=weight*smooth(min(1,c/.3));return patches[c]*a+face*(1-a)
 def geometry_stage(self):
  from facexlib.alignment import FAN,landmark_98_to_68
  torch.set_flush_denormal(True);model=FAN(num_modules=4,num_landmarks=98,device='cpu');model.load_state_dict(torch.load(WEIGHTS/'facexlib/alignment_WFLW_4HG.pth',map_location='cpu',weights_only=True)['state_dict'],strict=True);model.eval();pred=np.load(self.cache/'predictions.npy',mmap_mode='r');blocks=self.cache/'geometry-chunks';blocks.mkdir(exist_ok=True);allpoints=[];binding=sha(self.cache/'predictions.npy')
  for a in range(0,self.frames,32):
   b=min(a+32,self.frames);p=blocks/f'{a:08d}.npy';meta=p.with_suffix('.json');part=None
   if p.exists() and meta.exists():
    m=read(meta)
    if m.get('input_sha256')==binding and m.get('sha256')==sha(p):
     value=np.load(p)
     if value.shape==(b-a,68,2) and np.isfinite(value).all():part=value
   if part is None:
    result=[]
    for i in range(a,b):
     with torch.inference_mode():result.append(landmark_98_to_68(model.get_landmarks(np.clip(pred[i]*255,0,255).astype(np.uint8))).astype(np.float32))
    part=np.asarray(result,np.float32);save_array(p,part);save(meta,{'input_sha256':binding,'sha256':sha(p),'first':a,'last':b,'FP32':True})
   allpoints.append(part);self.progress('提取发音口型几何',b,self.frames)
  save_array(self.cache/'raw-landmarks.npy',np.concatenate(allpoints))
 def reference_stage(self):
  self.ref_cache.mkdir(parents=True,exist_ok=True);frame_to_source=None
  if self.job.get('reference_template'):
   template=inside(self.job['reference_template']);lm=np.load(template.parent/'landmarks68.npy').astype(np.float32);ref=np.asarray(Image.open(template.parent/'portrait.png').convert('RGB')).astype(np.float32)
   ids=[36,39,42,45,30];M=cv2.estimateAffinePartial2D(lm[ids],self.lmk[ids],method=cv2.LMEDS)[0]
   if M is None:raise ValueError('张嘴参考无法对齐，请选择同人物、同角度的清晰照片。')
   mapped=cv2.transform(lm[None],M)[0];eye=np.linalg.norm(self.lmk[36]-self.lmk[45]);residual=float(np.median(np.linalg.norm(mapped[ids]-self.lmk[ids],axis=1)))/max(eye,1)
   if residual>.08:raise ValueError('参考图角度与人物照片差异过大，无法稳定对齐口腔。')
   info={'method':'user_reference','reference_sha256':sha(template.parent/'portrait.png'),'geometry_residual_fraction':residual,'identity_must_be_confirmed_by_user':True}
  else:
   pred=np.load(self.cache/'predictions.npy',mmap_mode='r');points=np.load(self.cache/'raw-landmarks.npy');gap=np.linalg.norm(points[:,62]-points[:,66],axis=1);good=(self.speech_activity['gate']>.9);scores=np.where(good,gap,-1);i=int(scores.argmax())
   if scores[i]<1.2:raise ValueError('本次配音没有可靠张嘴参考帧，请提供同人物的张嘴参考照片。')
   inv=cv2.invertAffineTransform(self.B);yy,xx=np.mgrid[:512,:512].astype(np.float32);sx=inv[0,0]*xx+inv[0,1]*yy+inv[0,2];sy=inv[1,0]*xx+inv[1,1]*yy+inv[1,2];aligned=cv2.remap(self.src,sx,sy,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101);ax=self.A[0,0]*sx+self.A[0,1]*sy+self.A[0,2];ay=self.A[1,0]*sx+self.A[1,1]*sy+self.A[1,2];neural=cv2.remap(np.asarray(pred[i])*255,ax,ay,cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)[:,:,::-1]
   radius=np.sqrt(((sx-self.center[0])/(self.mouthw*.58+2))**2+((sy-self.center[1])/(self.mouthw*.31+2))**2);a=np.clip((1-radius)*6,0,1)[...,None];context=aligned*(1-a)+neural*a
   from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
   model=GFPGANv1Clean(out_size=512,num_style_feat=512,channel_multiplier=2,decoder_load_path=None,fix_decoder=False,num_mlp=8,input_is_latent=True,different_w=True,narrow=1,sft_half=True);ck=torch.load(WEIGHTS/'facexlib/GFPGANv1.4.pth',map_location='cpu',weights_only=True);model.load_state_dict(ck.get('params_ema',ck.get('params')),strict=True);del ck;model.eval();tensor=torch.from_numpy(np.ascontiguousarray(context.transpose(2,0,1)))[None]/127.5-1
   with torch.inference_mode():restored=model(tensor,return_rgb=False,randomize_noise=False)[0][0].permute(1,2,0).numpy()
   ref=np.clip((restored+1)*127.5,0,255).astype(np.float32);del model,tensor;gc.collect()
   from facexlib.alignment import FAN,landmark_98_to_68
   torch.set_flush_denormal(True);fan=FAN(num_modules=4,num_landmarks=98,device='cpu');fan.load_state_dict(torch.load(WEIGHTS/'facexlib/alignment_WFLW_4HG.pth',map_location='cpu',weights_only=True)['state_dict'],strict=True);fan.eval()
   with torch.inference_mode():lm=landmark_98_to_68(fan.get_landmarks(ref[:,:,::-1].copy())).astype(np.float32)
   M=inv;info={'method':'photo_specific_model_reference','source_prediction_frame':i,'source_prediction_sha256':sha(self.cache/'predictions.npy'),'oral_detail_is_model_inferred':True,'FP32':True}
  canvas=np.array([[self.scale,0,-self.ox*self.scale],[0,self.scale,-self.oy*self.scale],[0,0,1]],np.float64);combined=canvas@np.vstack([M,[0,0,1]]);pixels=cv2.warpAffine(ref,combined[:2],(1080,1920),flags=cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101);landmarks=cv2.transform(lm[None],combined[:2])[0]
  opening=float(np.mean(np.linalg.norm(landmarks[[61,62,63]]-landmarks[[67,66,65]],axis=1)))
  if opening<1.8:raise ValueError('参考图口腔开合不足，无法提供稳定牙齿/口腔细节。请换一张张嘴参考。')
  save_image(self.ref_cache/'anchor.png',np.clip(pixels,0,255).astype(np.uint8));save_array(self.ref_cache/'landmarks.npy',landmarks.astype(np.float32));info.update(source_sha256=self.job['source_sha256'],opening_final_px=opening);save(self.ref_cache/'reference.json',info)
 def plan_stage(self):
  from portrait.speech_constraints import _smooth
  points=np.load(self.cache/'raw-landmarks.npy');w=np.linalg.norm(points[:,54]-points[:,48],axis=1);c=(points[:,48]+points[:,54])/2;u=(points[:,54]-points[:,48])/w[:,None];v=np.stack([-u[:,1],u[:,0]],1);normal=np.einsum('nij,nj->ni',points-c[:,None],v);gap=np.maximum(normal[:,[67,66,65]]-normal[:,[61,62,63]],0).mean(1);raw=np.maximum(gap-.3,0);active=self.speech_activity['gate']>.9;mouth_scale=self.mouthw*self.scale/90;peak=self.mouthw*self.scale*.155
  gain=peak/max(float(np.percentile(raw[active],97)) if active.any() else float(raw.max()),1);opening=raw*gain;quiet=self.speech_activity['gate']==0;rest=float(np.median(w[quiet])) if quiet.any() else float(np.median(w));ratio=np.clip(w/rest,.91,1.045);top=normal[:,[61,62,63]].mean(1);rest_top=float(np.median(top[quiet])) if quiet.any() else float(np.percentile(top,75));raise_px=np.clip((top-rest_top)*gain,-2.4*mouth_scale,0)
  # Broad envelope agreement suggests a timing correction; it is not a lip-sync score.
  level=np.log1p(self.speech_activity['rms']/.003);lag_scores=[]
  for lag in range(-6,7):
   idx=np.arange(max(lag,0),min(self.frames,self.frames+lag));score=float(np.corrcoef(level[idx],gap[idx-lag])[0,1]) if len(idx)>15 and np.std(gap[idx-lag])>.01 else 0;lag_scores.append((lag,score if np.isfinite(score) else 0))
  best=max(lag_scores,key=lambda p:p[1]);zero=dict(lag_scores)[0];delay=best[0] if best[1]-zero>.03 else 0
  alignment=read(inside(self.job.get('alignment_cache',str(self.render_cache)))/'alignment.json');plan=constrain(self.pcm,16000,opening,ratio,raise_px,chars=alignment['chars'],delay_frames=int(delay),peak_opening=peak,max_step=3.8*mouth_scale,mouth_scale=mouth_scale);check=audit(self.pcm,16000,plan['opening'],plan['width_ratio'],plan['upper_raise'],protected=plan['protected'],mouth_scale=mouth_scale)
  save(self.render_cache/'screening.json',{'audit':check,'delay_frames':int(delay),'envelope_diagnostic':lag_scores,'mouth_scale':mouth_scale,'human_review_required':True})
  if check['status']!='passed_constraints':raise ValueError('发声口型仍有可疑时间段，已保存筛查记录，停止编码；请检查原稿与配音。')
  dest=self.render_cache/'plan.npz';pending=dest.with_name('plan.pending.npz');np.savez(pending,**{k:v for k,v in plan.items() if isinstance(v,np.ndarray)});pending.replace(dest)
 def encode(self,output,ass=None):
  output=inside(output)
  if output.exists():raise FileExistsError('输出已存在，请另存。')
  plan=dict(np.load(self.render_cache/'plan.npz',allow_pickle=False));screen=read(self.render_cache/'screening.json');scale=screen['mouth_scale'];check=audit(self.pcm,16000,plan['opening'],plan['width_ratio'],plan['upper_raise'],protected=plan['protected'],mouth_scale=scale)
  if check['status']!='passed_constraints':raise ValueError('轨迹复核未通过，不能编码成片。')
  renderer=PhotoTexture(self,np.asarray(Image.open(self.ref_cache/'anchor.png').convert('RGB')),np.load(self.ref_cache/'landmarks.npy'),plan);output.parent.mkdir(parents=True,exist_ok=True);pending=output.with_name(output.stem+'.pending.mp4');cmd=[str(FF),'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1080x1920','-r','30','-i','pipe:0','-ss',str(self.begin/30),'-i',str(self.audio)]
  cwd=output.parent
  if ass:
   import os
   ass=inside(ass);cwd=ass.parent;name=ass.name.replace("'","'\\''");fonts=os.path.relpath(PROGRAM/'resources/fonts',cwd).replace('\\','/');vf=f"setpts=PTS+{self.begin/30}/TB,ass=filename='{name}':fontsdir='{fonts}',setpts=PTS-STARTPTS";cmd+=['-vf',vf]
  # Bound the encoded video by its exact generated frame count.  A duration
  # rounded from the audio can otherwise make ffmpeg omit the final frame.
  cmd+=['-map','0:v','-map','1:a','-frames:v',str(self.last-self.begin),'-c:v','libx264','-threads','2','-preset','fast','-crf','14','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-ar','24000','-movflags','+faststart',str(pending)]
  with (self.render_cache/'encode-error.log').open('w',encoding='utf-8') as error:
   proc=subprocess.Popen(cmd,cwd=cwd,stdin=subprocess.PIPE,stderr=error)
   try:
    for i in range(self.begin,self.last):
     proc.stdin.write(renderer.frame(i).tobytes())
     if (i-self.begin)%30==0:self.progress('编码原图唇纹与发声校准',i-self.begin+1,self.last-self.begin)
   finally:proc.stdin.close();code=proc.wait()
  if code:raise RuntimeError('编码失败，已保留输入和已验证缓存。')
  if not renderer.counters['quiet_mouth_all_original'] or renderer.counters['original_texture_interior_max_difference']!=0:raise ValueError('原图唇纹或静音回位检查未通过，停止发布输出。')
  subprocess.run([str(FF),'-v','error','-xerror','-i',str(pending),'-f','null','-'],check=True);pending.replace(output);sl=slice(self.begin,self.last)
  write_provenance(output,self.audio,self.speech_activity['rms'][sl],self.speech_activity['peak'][sl],plan['opening'][sl],plan['width_ratio'][sl],plan['upper_raise'][sl],plan['protected'][sl],fps=30,mouth_scale=scale,extra={'begin_frame':self.begin,'transcript_phoneme_constraints_applied':True,'alignment':'per-input transcript verified and attention-aligned in FP32','timing_delay_frames':screen['delay_frames'],'mouth_scale':scale,'recipe':'photo-lip-speech-v2'})
  save(output.with_suffix('.detail.json'),{'recipe':'photo-lip-speech-v2','job':str(self.path),'job_sha256':sha(self.path),'video_sha256':sha(output),'one_encode_per_output':True});save(output.with_suffix('.render.json'),{'frames':self.last-self.begin,'blink_start_seconds':[i/30 for i in self.blink_starts if self.begin<=i<self.last],'texture_check':renderer.counters,'screening':check,'reference':read(self.ref_cache/'reference.json'),'FP32':True,'head_motion':False,'body_motion':False,'human_review_required':True});save(self.path.with_name('制作结果.json'),{'status':'completed','output':str(output),'signature':self.job['signature'],'method':'photo-lip-speech-v2','human_review_required':True})

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--stage',required=True,choices=['predict','geometry','blink','reference','plan','encode']);p.add_argument('--output');p.add_argument('--ass');a=p.parse_args();worker=Worker(a.job)
 if a.stage=='encode':worker.encode(a.output,a.ass)
 else:getattr(worker,{'predict':'predict_stage','geometry':'geometry_stage','blink':'blink_stage','reference':'reference_stage','plan':'plan_stage'}[a.stage])()
