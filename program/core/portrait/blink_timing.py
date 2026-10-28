"""Frame-exact, reproducible blink timing; independent of photo identity."""
import bisect,hashlib,random
import numpy as np

def starts(blink,frames,seed='',fps=30):
 mode=blink.get('mode','off')
 if mode=='off':return []
 if mode=='custom':return [round(float(t)*fps) for t in blink.get('times',[]) if round(float(t)*fps)+9<frames]
 # 2.1–3.1s between blinks; audio/photo seed changes timing without global RNG state.
 rng=random.Random(int(hashlib.sha256(str(seed).encode()).hexdigest()[:16],16));at=round(rng.uniform(.9,1.3)*fps);out=[]
 while at+9<frames:
  out.append(at);at+=rng.randint(63,93)
 return out

def closure(frame,ordered):
 k=bisect.bisect_right(ordered,frame)-1
 if k<0:return 0.
 n=frame-ordered[k]
 def smooth(x):return x*x*x*(x*(x*6-15)+10)
 if n<0 or n>=9:return 0.
 if n<=2:c=smooth(n/2)
 elif n<=3:c=1.
 else:c=1-smooth((n-3)/6)
 return float(np.float32(c))
