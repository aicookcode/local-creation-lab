"""Original-photo lips driven by per-input geometry, never by project coordinates."""
import numpy as np,cv2
from scipy.interpolate import PchipInterpolator

def curve(points,ids):
 p=points[np.asarray(ids)];p=p[np.argsort(p[:,0])];x,k=np.unique(p[:,0],return_index=True)
 if len(x)<2:raise ValueError('嘴唇关键点无效，不能生成稳定口型。')
 return PchipInterpolator(x,p[k,1],extrapolate=True)

def smooth(x):return x*x*x*(x*(x*6-15)+10)

class PhotoTexture:
 def __init__(self,engine,anchor,anchor_lmk,plan):
  self.e=engine;self.anchor=anchor.astype(np.float32);self.plan=plan;lm=engine.lmk.astype(np.float32);self.c=(lm[48]+lm[54])/2;self.u=lm[54]-lm[48];self.u/=np.linalg.norm(self.u);self.v=np.array([-self.u[1],self.u[0]],np.float32)
  loc=np.column_stack(((lm-self.c)@self.u,(lm-self.c)@self.v));self.left,self.right=loc[[48,54],0];al=anchor_lmk.astype(np.float32);self.ac=(al[48]+al[54])/2;self.au=al[54]-al[48];self.au/=np.linalg.norm(self.au);self.av=np.array([-self.au[1],self.au[0]],np.float32);alc=np.column_stack(((al-self.ac)@self.au,(al-self.ac)@self.av));self.aleft,self.aright=alc[[48,54],0]
  self.lt=curve(loc,[48,60,67,66,65,64,54]);self.lb=curve(loc,[48,59,58,57,56,55,54]);self.ut=curve(loc,[48,60,61,62,63,64,54]);self.uo=curve(loc,[48,49,50,51,52,53,54]);self.at=curve(alc,[48,60,61,62,63,64,54]);self.ab=curve(alc,[48,60,67,66,65,64,54]);self.scale=engine.scale
  lo=lm[48:68].min(0);hi=lm[48:68].max(0);pad=max(10,engine.mouthw*.15);peak=float(plan['opening'].max())
  self.x1=max(0,int((lo[0]-pad-engine.ox)*engine.scale));self.x2=min(1080,int((hi[0]+pad-engine.ox)*engine.scale)+1);self.y1=max(0,int((lo[1]-pad-engine.oy)*engine.scale));self.y2=min(1920,int((hi[1]+pad+peak/engine.scale-engine.oy)*engine.scale)+1)
  yy,xx=np.mgrid[self.y1:self.y2,self.x1:self.x2].astype(np.float32);qx=engine.ox+xx/engine.scale;qy=engine.oy+yy/engine.scale;self.tu=(qx-self.c[0])*self.u[0]+(qy-self.c[1])*self.u[1];self.nv=(qx-self.c[0])*self.v[0]+(qy-self.c[1])*self.v[1]
  self.baseline=np.clip(engine.baseline,0,255).astype(np.uint8);self.roi=(slice(self.y1,self.y2),slice(self.x1,self.x2));self.counters={'quiet_frames':0,'quiet_mouth_all_original':True,'original_texture_interior_max_difference':0}
 def remap_source(self,t,n):
  sx=self.c[0]+t*self.u[0]+n*self.v[0];sy=self.c[1]+t*self.u[1]+n*self.v[1]
  return cv2.remap(self.e.src,sx.astype(np.float32),sy.astype(np.float32),cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
 def blend(self,frame,texture,alpha):
  old=frame[self.roi].astype(np.float32);pix=np.clip(old*(1-alpha[...,None])+texture*alpha[...,None],0,255).astype(np.uint8);frame[self.roi]=pix;return pix
 def frame(self,i):
  e=self.e;p=self.plan;gate=float(p['gate'][i]);frame=self.baseline.copy();frame[e.roi]=np.clip(e.eye_patch(frame[e.roi].astype(np.float32),i),0,255).astype(np.uint8)
  if gate==0:
   self.counters['quiet_frames']+=1;self.counters['quiet_mouth_all_original']&=bool(np.array_equal(frame[self.roi],self.baseline[self.roi]));return frame
  s=self.tu/float(p['width_ratio'][i]);t=np.clip(s,self.left,self.right);side=np.clip(np.minimum(s-self.left,self.right-s)*self.scale+.5,0,1);f=np.clip((s-self.left)/(self.right-self.left),0,1);shape=np.clip(np.sin(np.pi*f),0,1)**.8
  up=shape*p['upper_raise'][i]/self.scale;amount=np.maximum(shape*p['opening'][i]/self.scale+up,0);lt=self.lt(t);lb=self.lb(t);ut=self.ut(t);uo=self.uo(t);collar=max(10,(self.right-self.left)*.15)
  wx=smooth(np.clip((max(abs(self.left),abs(self.right))+collar-np.abs(self.tu))/collar,0,1));wy=smooth(np.clip((collar*2-np.abs(self.nv))/collar,0,1));inv=self.tu/(1+(float(p['width_ratio'][i])-1)*wx*wy);frame[self.roi]=np.clip(self.remap_source(inv,self.nv),0,255).astype(np.uint8)
  n=self.nv-up;tex=self.remap_source(s,n);alpha=np.clip(np.minimum((n-uo)*self.scale+1,(ut-n)*self.scale+.6),0,1)*side*gate;self.blend(frame,tex,alpha)
  top=ut+up;bottom=np.maximum(lt+amount,top+.01);ta=self.aleft+(t-self.left)/(self.right-self.left)*(self.aright-self.aleft);na=self.at(ta)+(self.nv-top)*self.scale;na=np.minimum(na,self.ab(ta)-.1);sx=self.ac[0]+ta*self.au[0]+na*self.av[0];sy=self.ac[1]+ta*self.au[1]+na*self.av[1];tex=cv2.remap(self.anchor,sx.astype(np.float32),sy.astype(np.float32),cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
  a=np.clip(np.minimum((self.nv-top)*self.scale+.4,(bottom-self.nv)*self.scale+.4),0,1)*side*gate*smooth(min(float(np.max(amount-up))*self.scale/2,1));self.blend(frame,tex,a)
  n=self.nv-amount;tex=self.remap_source(s,n);alpha=np.clip(np.minimum((n-lt)*self.scale+.7,(lb-n)*self.scale+3),0,1)*side*gate;pixels=self.blend(frame,tex,alpha);inside=alpha==1
  if inside.any():self.counters['original_texture_interior_max_difference']=max(self.counters['original_texture_interior_max_difference'],int(np.abs(pixels.astype(np.int16)-np.clip(tex,0,255).astype(np.int16))[inside].max()))
  return frame
