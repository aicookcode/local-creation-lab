from pathlib import Path
import sys,json,argparse,subprocess,html
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'core'))
from portrait.config import inside,relative,duration_wav
import cv2,numpy as np
from PIL import Image,ImageDraw
p=argparse.ArgumentParser();p.add_argument('--video',required=True);p.add_argument('--audio',required=True);p.add_argument('--out-dir',required=True);p.add_argument('--subtitles');p.add_argument('--script');a=p.parse_args()
video,audio,out=map(inside,(a.video,a.audio,a.out_dir));out.mkdir(parents=True,exist_ok=True)
ff=ROOT/'core/video/ffmpeg.exe';cv2.setNumThreads(2)
cap=cv2.VideoCapture(str(video));fps=cap.get(5);w,h=int(cap.get(3)),int(cap.get(4));total=int(cap.get(7));images=[];black=[];i=0
if not cap.isOpened() or fps<=0:raise ValueError('视频无法读取。')
times=[max(.1,total/fps*f) for f in [.03,.2,.4,.6,.8,.96]]
while True:
 ok,fr=cap.read()
 if not ok:break
 if cv2.cvtColor(fr,cv2.COLOR_BGR2GRAY).mean()<4:black.append(i)
 if len(images)<len(times) and i/fps>=times[len(images)]:images.append(fr.copy())
 i+=1
 if i%300==0:print(f'已检查 {i}/{total} 帧',flush=True)
cap.release()
decode=subprocess.run([str(ff),'-v','error','-i',str(video),'-f','null','-'],capture_output=True)
def pcm(path,start,seconds):return np.frombuffer(subprocess.run([str(ff),'-v','error','-i',str(path),'-ss',str(start),'-t',str(seconds),'-ar','24000','-ac','1','-f','s16le','pipe:1'],capture_output=True,check=True).stdout,dtype='<i2').astype(np.float32)
reference_seconds=duration_wav(audio);parts=[];window=min(5,reference_seconds/3)
for k in range(3):
 start=max(0,min(reference_seconds-window,reference_seconds*(k+.5)/3-window/2));actual=pcm(video,start,window);reference=pcm(audio,start,window);n=min(len(actual),len(reference))
 corr=float(np.corrcoef(actual[:n],reference[:n])[0,1]) if n>10 and np.std(reference[:n])>1 else None
 parts.append({'section':k+1,'start_s':start,'sample_seconds':window,'correlation':corr})
warnings=[];duration=i/fps;frame_adjustment=None
if decode.returncode or decode.stderr.strip():warnings.append('视频解码报告错误，请重新生成或查看日志。')
if i!=total:
 if total-i==1 and abs(duration-reference_seconds)<=1/fps+.02:frame_adjustment='trimmed_one_trailing_geometry_frame'
 else:warnings.append('实际可解码帧数与文件记录不一致。')
if black:warnings.append('检测到整幅黑帧，请播放对应位置检查。')
if abs(duration-reference_seconds)>.1:warnings.append('视频与配音时长相差超过0.1秒，请确认是否选错配音或母版。')
if any(r['correlation'] is not None and r['correlation']<.995 for r in parts):warnings.append('成片音轨与所选配音存在差异，请复听确认。')
caption={}
if a.subtitles:
 sys.path.insert(0,str(ROOT/'core/video'));import subtitle_layout as layout
 cues=layout.read_cues(inside(a.subtitles));last=0
 for cue in cues:
  b,e=layout.time_seconds(cue['start']),layout.time_seconds(cue['end'])
  if b<last-.005 or e>duration+.1:warnings.append('字幕时间有重叠或超出视频时长。');break
  last=e
 caption={'count':len(cues),'last_end':last}
 if a.script:
  norm=lambda t:''.join(c for c in t if c.isalnum())
  caption['text_matches_script']=norm(''.join(c['text'] for c in cues))==norm(inside(a.script).read_text(encoding='utf-8-sig'))
  if not caption['text_matches_script']:warnings.append('字幕文字与配音原稿有差异，请复听确认。')
 # Inspect actual glyph bounds when this final has its paired ASS.
 ass=video.with_suffix('.ass')
 if ass.exists():
  from portable_bootstrap import configure
  configure();cfg=layout.load_style()
  record=video.with_suffix('.layout.json')
  if record.exists():cfg=json.loads(record.read_text(encoding='utf-8'))['style']
  selected=[int((layout.time_seconds(c['start'])+layout.time_seconds(c['end']))*.5*fps) for c in cues]
  import os,math,tempfile
  fonts=os.path.relpath(ROOT/'resources/fonts',ass.parent).replace('\\','/')
  bounds=[];raster_count=0;frame_bytes=1920*1080*3
  # Bounded command lines and one raw frame in memory, even with many captions.
  for batch_start in range(0,len(selected),24):
   batch=selected[batch_start:batch_start+24];first=batch[0];length=(batch[-1]-first+2)/fps;offset=first/fps
   expression='+'.join(f'eq(n\\,{x-first})' for x in batch)
   vf=f"setpts=PTS+{offset:.9f}/TB,ass=filename='{ass.name}':fontsdir='{fonts}',select='{expression}'"
   with tempfile.TemporaryFile() as errors:
    proc=subprocess.Popen([str(ff),'-v','error','-f','lavfi','-i',f'color=black:s=1080x1920:r=30:d={length:.9f}','-vf',vf,'-vsync','0','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],cwd=ass.parent,stdout=subprocess.PIPE,stderr=errors)
    while True:
     raw=proc.stdout.read(frame_bytes)
     if not raw:break
     if len(raw)!=frame_bytes:raise RuntimeError('字幕抽样帧未完整输出。')
     frame=np.frombuffer(raw,np.uint8).reshape((1920,1080,3));raster_count+=1;ys,xs=np.where(frame.max(2)>160)
     if not len(xs):warnings.append('有一条字幕没有渲染出可见文字。');continue
     box=[int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1];bounds.append(box)
     if box[0]<cfg['safe_margin_left_px'] or box[2]>1080-cfg['safe_margin_right_px']:warnings.append('有字幕超出左右安全边距。')
     if box[2]-box[0]>cfg['max_width_px']+2:warnings.append('有字幕超出本次设定的最大宽度。')
    if proc.wait():errors.seek(0);raise RuntimeError('字幕边界检查未完成：'+errors.read(3000).decode('utf-8',errors='replace'))
  caption['raster_bounds']=bounds;caption['raster_count_matches']=raster_count==len(cues)
  if raster_count!=len(cues):warnings.append('字幕抽样数量与字幕条数不一致，请复核时间与显示。')
# File/track integrity is separate from speech/mouth screening.
articulation={'status':'not_checked','reason':'No SHA-bound speech geometry record; file checks do not establish lip sync.'}
meta=video.with_suffix('.speech.json')
if meta.exists():
 try:
  from portrait.speech_constraints import audit_features,file_sha
  record=json.loads(meta.read_text(encoding='utf-8-sig'));arr=video.with_suffix('.speech.npz')
  if record.get('arrays')!=arr.name or record.get('video_sha256')!=file_sha(video) or not arr.is_file() or record.get('arrays_sha256')!=file_sha(arr):raise ValueError('video/audio/trajectory SHA mismatch')
  record_frames=int(record.get('frames',-1));trim_geometry=bool(frame_adjustment and record_frames==i+1)
  if record_frames!=i and not trim_geometry:raise ValueError('geometry timebase mismatch')
  geom=np.load(arr,allow_pickle=False)
  if any(geom[k].shape!=(record_frames,) for k in ('opening','width_ratio','upper_raise','protected','rms','peak')):raise ValueError('geometry lengths mismatch')
  if trim_geometry:geom={k:geom[k][:i] for k in ('opening','width_ratio','upper_raise','protected','rms','peak')}
  same_audio=record.get('audio_sha256')==file_sha(audio)
  if not same_audio and abs(reference_seconds-duration)>.05:raise ValueError('audio SHA differs and reference is not the same-duration excerpt')
  offset=float(record.get('extra',{}).get('begin_frame',0))/fps if same_audio else 0.
  proc=subprocess.Popen([str(ff),'-v','error','-ss',str(offset),'-i',str(audio),'-ac','1','-ar','16000','-f','f32le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  rms=[];peaks=[]
  try:
   for k in range(i):
    count=round((k+1)*16000/fps)-round(k*16000/fps);raw=proc.stdout.read(count*4);samples=np.frombuffer(raw,dtype='<f4').astype(np.float64)
    rms.append(float(np.sqrt(np.mean(samples*samples))) if len(samples) else 0.);peaks.append(float(np.max(np.abs(samples))) if len(samples) else 0.)
  finally:
   proc.stdout.close();proc.stderr.read();code=proc.wait()
  # Preview checks may intentionally stop a longer reference pipe; binding and timebase stay explicit.
  if np.max(np.abs(np.asarray(rms)-geom['rms']))>1e-4 or np.max(np.abs(np.asarray(peaks)-geom['peak']))>1e-3:raise ValueError('geometry activity differs from actual selected reference audio')
  articulation=audit_features(rms,peaks,geom['opening'],geom['width_ratio'],geom['upper_raise'],geom['protected'],fps,mouth_scale=float(record.get('mouth_scale',1.)))
  articulation['method']=record.get('extra',{})
  articulation['audio_binding']='original audio SHA' if same_audio else 'same-duration excerpt verified against frame RMS/peak and sampled mux audio'
  if articulation['status']!='passed_constraints':warnings.append('发声与口型筛查发现静音嘴动或持续发声闭口，请查看时间段并复核；不能当作口型通过。')
 except (OSError,ValueError,KeyError,TypeError) as exc:
  articulation={'status':'failed','reason':str(exc)};warnings.append('发声口型记录与本次素材不一致，不能沿用检查结果。')
sheet=Image.new('RGB',(900,1120),'#18232d');draw=ImageDraw.Draw(sheet)
for index,fr in enumerate(images):
 im=Image.fromarray(cv2.cvtColor(fr,cv2.COLOR_BGR2RGB));im.thumbnail((300,530));x,y=index%3*300,index//3*560
 sheet.paste(im,(x,y));draw.text((x+6,y+534),f'{times[index]:.2f}s',fill='white')
sheet.save(out/'关键帧.jpg',quality=95)
result={'status':'passed' if not warnings and decode.returncode==0 and (i==total or frame_adjustment) else 'review_needed','video':relative(video),'report':relative(out/'检查结果.html'),'frames':i,'container_frames':total,'fps':fps,'size':[w,h],'duration_s':duration,'black_frames':black,'decode_ok':decode.returncode==0 and not decode.stderr.strip(),'audio_sections':parts,'articulation':articulation,'captions':caption,'adjustments':[frame_adjustment] if frame_adjustment else [],'warnings':list(dict.fromkeys(warnings)),'note':'文件、音轨、字幕检查与发声口型筛查分别记录。筛查通过仍不代表每个音素的口型准确；无口型记录时标为未检查。请带声音复核。'}
(out/'检查结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
import os
src=Path(os.path.relpath(video,out)).as_posix()
labels={'passed_constraints':'规则筛查通过，仍需带声音验收','review_needed':'有可疑时间段，需要复核','not_checked':'未执行（缺少绑定的口型轨迹记录）','failed':'记录无效，不能沿用'}
art_page='<h2>发声与口型筛查</h2><p>'+html.escape(labels.get(articulation.get('status'),str(articulation.get('status'))))+'</p>'
for field,title in [('silence_motion','静音时嘴部动作'),('sustained_voice_closed','持续发声时可疑闭口')]:
 for item in articulation.get(field,[]):
  art_page+='<p>'+html.escape(title)+': '+f"{item['start']:.3f}–{item['end']:.3f} 秒"+'</p>'
page='<meta charset="utf-8"><title>人物视频检查</title><style>body{max-width:900px;margin:30px auto;font:18px/1.8 system-ui}video{max-height:720px;max-width:100%}img{width:100%}</style><h1>人物视频检查</h1><p>'+html.escape('检查通过' if result['status']=='passed' else '有项目需要复核')+'</p><p>'+html.escape(result['note'])+'</p>'+art_page+''.join('<p>'+html.escape(t)+'</p>' for t in result['warnings'])+'<video controls src="'+html.escape(src)+'"></video><p>'+f'{duration:.2f}秒 · {i}帧 · 黑帧{len(black)}'+'</p><img src="关键帧.jpg">'
(out/'检查结果.html').write_text(page,encoding='utf-8')
print('检查完成：'+str(out/'检查结果.html'),flush=True)
