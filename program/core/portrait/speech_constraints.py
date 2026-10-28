"""Audio-constrained geometric articulation. Does not certify phoneme accuracy.

No model pixels or original lip texture are filtered here. All smoothing is
on trajectories. Clear pauses override every mouth trajectory after shifting.
Short labial closures are not treated as errors. Transcript times are estimates.
"""
import numpy as np
POLICY='audio-articulation-constraints-v1'

def _smooth(x,sigma):
 x=np.asarray(x,dtype=np.float64);n=max(1,int(np.ceil(sigma*3)));k=np.arange(-n,n+1);w=np.exp(-.5*(k/sigma)**2);w/=w.sum()
 return np.convolve(np.pad(x,(n,n),mode='edge'),w,mode='valid')

def spans(mask,fps=30,min_frames=1):
 d=np.diff(np.r_[False,np.asarray(mask,bool),False].astype(np.int8));return [{'start':round(int(a)/fps,6),'end':round(int(b)/fps,6),'frames':int(b-a)} for a,b in zip(np.flatnonzero(d==1),np.flatnonzero(d==-1)) if b-a>=min_frames]

def activity(pcm,rate,frames,fps=30):
 x=np.asarray(pcm)
 if x.ndim!=1 or len(x)==0:raise ValueError('口型发声检查需要有效的单声道人声。')
 if rate<=0 or fps<=0 or frames<=0:raise ValueError('非法采样率、帧率或帧数。')
 if abs(len(x)/rate-frames/fps)>1/fps+.01:raise ValueError('口型轨迹和配音时长不一致，不能截断后继续。')
 rms=[];peak=[]
 for i in range(frames):
  a=min(len(x)-1,round(i*rate/fps));b=min(len(x),max(a+1,round((i+1)*rate/fps)));part=x[a:b].astype(np.float64)
  if not np.isfinite(part).all():raise ValueError('音频包含非法采样，停止口型制作。')
  rms.append(np.sqrt(np.mean(part*part)));peak.append(np.max(np.abs(part)))
 rms=np.asarray(rms);peak=np.asarray(peak)
 # Per-frame measured audio; no 7-frame dilation into future/past silence.
 gate=np.clip((np.log(np.maximum(rms,1e-12))-np.log(.00010))/(np.log(.0010)-np.log(.00010)),0,1);gate=gate*gate*(3-2*gate)
 quiet=(rms<.00010)&(peak<.0010);gate[quiet]=0
 return {'gate':gate.astype(np.float32),'rms':rms.astype(np.float32),'peak':peak.astype(np.float32),'clear_quiet':quiet}

def _final_floor(final):
 if not final:return 3.5
 if any(x in final for x in ('a','ang','ao','ai')):return 6.0
 if final in ('i','in','ing','iz','ir'):return 2.8
 if final in ('u','v','ou','uo','ui','iu','ong','iong','un','vn','ue','ve'):return 4.2
 return 4.4

def transcript_targets(chars,rms,frames,fps=30):
 t=(np.arange(frames)+.5)/fps;floor=np.zeros(frames);protected=np.zeros(frames,bool);owner=np.full(frames,-1,dtype=np.int32);confidence=np.zeros(frames)
 if not chars:return floor,protected,owner,confidence
 for i,w in enumerate(chars):
  a=float(w['start']);b=float(w['end'])
  if b<=a:continue
  selected=(t>=a)&(t<b);owner[selected]=i
 # Bounded nearest-char extension covers small attention-boundary errors and audible phrase tails.
 for j in np.flatnonzero(owner<0):
  distances=np.array([max(float(w['start'])-t[j],t[j]-float(w['end']),0) for w in chars]);i=int(distances.argmin())
  if distances[i]<=.22 and rms[j]>.001:owner[j]=i
 for j,i in enumerate(owner):
  if i<0:continue
  w=chars[i];a=float(w['start']);b=float(w['end']);dur=max(b-a,.04);initial=w.get('initial','');confidence[j]=float(w.get('probability',0))
  labial=initial in ('b','p','m')
  closure=min(.070,max(.025,dur*.27)) if labial else 0
  protected[j]=bool(closure and a-.015<=t[j]<=a+closure)
  # Whisper/DTW can assign a low-confidence boundary to a short b/p/m
  # syllable.  Keep its short closure protected, then still provide the
  # vowel floor after the closure so the raw geometry cannot leave the
  # whole syllable nearly closed.  Other low-confidence alignments remain
  # conservative and are left for the normal review gate.
  uncertain_short_labial=labial and confidence[j]<.15 and dur<=.30 and rms[j]>.008
  if protected[j] or (confidence[j]<.15 and not uncertain_short_labial):continue
  phase=np.clip((t[j]-a)/dur,0,1);onset=closure/dur if closure else .10;shape=np.clip((phase-onset)/.20,0,1);shape=shape*shape*(3-2*shape)
  # A weak but audible vowel tail still has an opening; measured silence overrides it later.
  shape=max(shape,.55 if phase>=.2 else 0)
  floor[j]=_final_floor(w.get('final',''))*shape
 return floor,protected,owner,confidence

def constrain(pcm,rate,opening,width_ratio,upper_raise,chars=None,fps=30,delay_frames=0,peak_opening=14.,max_step=3.8,mouth_scale=1.):
 op=np.asarray(opening,np.float64);wr=np.asarray(width_ratio,np.float64);up=np.asarray(upper_raise,np.float64);n=len(op)
 if any(v.shape!=(n,) or not np.isfinite(v).all() for v in (op,wr,up)):raise ValueError('口型轨迹形状或数值无效。')
 if not isinstance(delay_frames,int) or abs(delay_frames)>6:raise ValueError('口型偏移须在正负6帧内，不能掩盖错误时间轴。')
 a=activity(pcm,rate,n,fps);gate=a['gate'];index=np.clip(np.arange(n)-delay_frames,0,n-1);op=_smooth(np.maximum(op[index],0),.40);wr=_smooth(wr[index],.50);up=_smooth(up[index],.45)
 floor,protected,owner,confidence=transcript_targets(chars,a['rms'],n,fps);floor*=mouth_scale
 corrected=(op<floor)&(gate>.5)&(~protected)
 op=np.maximum(op,floor);op=np.clip(op,0,peak_opening)
 # Keep closure/speech boundaries anchored: fade with actual audio AFTER smoothing/offset.
 op*=gate;wr=1+(np.clip(wr,.9,1.05)-1)*gate;up=np.clip(up,-2.4*mouth_scale,0)*gate
 # Limit velocity without propagating motion through a measured pause.
 for i in range(1,n):
  if gate[i]>0:op[i]=min(op[i],op[i-1]+max_step)
 for i in range(n-2,-1,-1):
  if gate[i]>0:op[i]=min(op[i],op[i+1]+max_step)
 op[gate==0]=0;wr[gate==0]=1;up=np.maximum(up,-op*.30);up[gate==0]=0
 return {'opening':op.astype(np.float32),'width_ratio':wr.astype(np.float32),'upper_raise':up.astype(np.float32),'gate':gate,'rms':a['rms'],'protected':protected,'char_index':owner,'confidence':confidence,'corrected_frames':np.flatnonzero(corrected),'policy':POLICY,'delay_frames':delay_frames}

def audit(pcm,rate,opening,width_ratio,upper_raise,protected=None,chars=None,fps=30,mouth_scale=1.):
 op=np.asarray(opening)/mouth_scale;wr=np.asarray(width_ratio);up=np.asarray(upper_raise)/mouth_scale;a=activity(pcm,rate,len(op),fps)
 if any(v.shape!=op.shape or not np.isfinite(v).all() for v in (op,wr,up)):return {'status':'failed','reason':'nonfinite_or_shape_mismatch','policy':POLICY}
 if protected is None:_,protected,_,_=transcript_targets(chars,a['rms'],len(op),fps)
 changed=(op>.10)|(np.abs(wr-1)>.002)|(np.abs(up)>.10)
 leaks=spans(a['clear_quiet']&changed,fps,min_frames=1)
 # Screening only: physiological closures and alignment uncertainty require listening.
 closed=spans((a['rms']>.008)&(op<1.2)&(~protected),fps,min_frames=max(2,int(np.ceil(.10*fps))))
 return {'status':'passed_constraints' if not leaks and not closed else 'review_needed','policy':POLICY,'silence_motion':leaks,'sustained_voice_closed':closed,'frames':len(op),'speech_vs_mouth_is_screening_not_phoneme_accuracy':True,'human_review_required':True}

def audit_features(rms,peak,opening,width_ratio,upper_raise,protected=None,fps=30,mouth_scale=1.):
 op=np.asarray(opening)/mouth_scale;wr=np.asarray(width_ratio);up=np.asarray(upper_raise)/mouth_scale;rms=np.asarray(rms);peak=np.asarray(peak)
 if any(v.shape!=op.shape or not np.isfinite(v).all() for v in (op,wr,up,rms,peak)):return {'status':'failed','reason':'nonfinite_or_shape_mismatch','policy':POLICY}
 protected=np.zeros(len(op),bool) if protected is None else np.asarray(protected,bool)
 if protected.shape!=op.shape:raise ValueError('闭唇保护轨迹长度不一致。')
 leaks=spans((rms<.00010)&(peak<.0010)&((op>.1)|(np.abs(wr-1)>.002)|(np.abs(up)>.1)),fps)
 closed=spans((rms>.008)&(op<1.2)&(~protected),fps,min_frames=max(2,int(np.ceil(.1*fps))))
 return {'status':'passed_constraints' if not leaks and not closed else 'review_needed','policy':POLICY,'silence_motion':leaks,'sustained_voice_closed':closed,'frames':len(op),'speech_vs_mouth_is_screening_not_phoneme_accuracy':True,'human_review_required':True}

def file_sha(path):
 import hashlib
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def write_provenance(video,audio,rms,peak,opening,width_ratio,upper_raise,protected=None,fps=30,extra=None,mouth_scale=1.):
 from pathlib import Path
 import json
 video=Path(video);arrays=video.with_suffix('.speech.npz');meta=video.with_suffix('.speech.json')
 if arrays.exists() or meta.exists():raise FileExistsError('发声口型检查记录已存在，请另存视频。')
 op=np.asarray(opening,np.float32);prot=np.zeros(len(op),bool) if protected is None else np.asarray(protected,bool)
 report=audit_features(rms,peak,op,width_ratio,upper_raise,prot,fps,mouth_scale=mouth_scale)
 temp=arrays.with_name(arrays.stem+'.pending.npz');np.savez(temp,rms=np.asarray(rms,np.float32),peak=np.asarray(peak,np.float32),opening=op,width_ratio=np.asarray(width_ratio,np.float32),upper_raise=np.asarray(upper_raise,np.float32),protected=prot);temp.replace(arrays)
 data={'schema':1,'policy':POLICY,'video_sha256':file_sha(video),'audio_sha256':file_sha(audio),'arrays':arrays.name,'arrays_sha256':file_sha(arrays),'module_sha256':file_sha(__file__),'frames':len(op),'fps':fps,'mouth_scale':float(mouth_scale),'screening':report,'human_review_required':True,'extra':extra or {}}
 pending=meta.with_suffix('.json.pending');pending.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');pending.replace(meta);return data

def copy_provenance(source,target):
 from pathlib import Path
 import json,shutil
 source=Path(source);target=Path(target);meta=source.with_suffix('.speech.json')
 if not meta.is_file():return
 data=json.loads(meta.read_text(encoding='utf-8-sig'));arrays=source.with_suffix('.speech.npz')
 if data.get('arrays')!=arrays.name or not arrays.is_file() or data.get('arrays_sha256')!=file_sha(arrays) or data.get('video_sha256')!=file_sha(source):raise ValueError('输入视频的发声口型记录校验失败。')
 dest=target.with_suffix('.speech.npz');destmeta=target.with_suffix('.speech.json');data['arrays']=dest.name
 if dest.exists() and file_sha(dest)!=data['arrays_sha256']:raise ValueError('复制目标已有不同的发声口型记录。')
 if not dest.exists():shutil.copy2(arrays,dest)
 content=json.dumps(data,ensure_ascii=False,indent=2)
 if destmeta.exists() and json.loads(destmeta.read_text(encoding='utf-8-sig'))!=data:raise ValueError('复制目标已有不同的发声口型说明。')
 if not destmeta.exists():destmeta.write_text(content,encoding='utf-8')
