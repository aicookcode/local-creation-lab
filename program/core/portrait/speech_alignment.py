"""Bounded local Mandarin transcript alignment. Never imports project assets."""
from pathlib import Path
import argparse,json,sys,difflib,re,time,wave
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from portable_bootstrap import configure,unpack_paths,pack_paths
from studio_paths import MODELS
from portrait.config import inside,sha
from task_progress import event

def normalize(s):return ''.join(c for c in s if c.isalnum())

def checked_chunk_times(audio,text,duration,simplify):
 """Use original verified chunks only when final audio AND every chunk match."""
 import math
 from tts.segment_cache import wav_data
 audio=Path(audio);record=json.loads(audio.with_suffix('.json').read_text(encoding='utf-8'))
 canonical=lambda s:normalize(simplify.convert(s))
 if (record.get('engine')!='qwen' or record.get('quality_policy')!='qwen-chunk-acceptance-v3-tail-review'
     or record.get('output_sha256')!=sha(audio) or canonical(record['text'])!=canonical(text)):
  raise ValueError('配音成品或正文与验收记录不一致')
 speed=float(record['speed']);gap=float(record['settings'].get('gap_seconds',.15))
 if not .85<=speed<=1.15 or not 0<=gap<=1:raise ValueError('配音语速或间隔无效')
 heard=[];times=[];offset=0.;sources=[];parts=record['parts']
 if not parts or canonical(''.join(p['text'] for p in parts))!=canonical(text):raise ValueError('分段正文未完整覆盖原稿')
 for i,part in enumerate(parts,1):
  quality=part['quality'];path=audio.parent/(audio.stem+'-parts')/f'{i:03d}.wav'
  info=wav_data(path);seconds=info['seconds']
  if (quality.get('status')!='accepted' or quality.get('policy')!=record['quality_policy']
      or quality.get('output_sha256')!=sha(path) or canonical(quality['expected_text'])!=canonical(part['text'])
      or abs(float(quality['accepted_seconds'])-seconds)>1/info['rate']):raise ValueError('分段音频或验收记录校验失败')
  before=len(heard);last=0.
  for word in quality['words']:
   token=canonical(word['word']);a=float(word['start']);b=float(word['end'])
   if not token:continue
   if not math.isfinite(a+b) or a<last-.03 or a<0 or b<=a or b>seconds+.05:raise ValueError('分段识别时间无效')
   for j,ch in enumerate(token):
    heard.append(ch);times.append(((offset+a+(b-a)*j/len(token))/speed,(offset+a+(b-a)*(j+1)/len(token))/speed))
   last=b
  if ''.join(heard[before:])!=canonical(quality['recognized']):raise ValueError('分段字词时间与转写不一致')
  sources.append({'chunk':i,'sha256':quality['output_sha256']})
  offset+=seconds+(gap if i<len(parts) else 0.)
 if abs(offset/speed-duration)>.12:raise ValueError('分段合并时长与成品不一致')
 return heard,times,{'method':'verified Qwen chunk recognition; every raw chunk and final output hashed','source_audio_sha256':record['output_sha256'],'chunks':sources}

def align(audio16k,script,model_path,output,log=print,source_audio=None):
 from faster_whisper import WhisperModel
 from faster_whisper.tokenizer import Tokenizer
 from faster_whisper.audio import pad_or_trim
 from pypinyin import lazy_pinyin,pinyin,Style
 from opencc import OpenCC
 text=Path(script).read_text(encoding='utf-8-sig').strip();target=normalize(text)
 if not target:raise ValueError('请提供与配音对应的正文，不能用字幕占位文字替代。')
 pcm=np.memmap(audio16k,dtype=np.float32,mode='r');duration=len(pcm)/16000;started=time.monotonic();simplify=OpenCC('t2s')
 model=WhisperModel(str(model_path),device='cpu',compute_type='float32',cpu_threads=2,num_workers=1,local_files_only=True);tokenizer=Tokenizer(model.hf_tokenizer,model.model.is_multilingual,task='transcribe',language='zh')
 def transcribe_windows(recognizer,label):
  heard=[];times=[]
  for core in np.arange(0,duration,24.):
   begin=max(0,core-.7);end=min(duration,core+24.7);sample=np.asarray(pcm[round(begin*16000):round(end*16000)]).copy()
   segs,_=recognizer.transcribe(sample,language='zh',beam_size=5,word_timestamps=True,condition_on_previous_text=False)
   for seg in segs:
    for word in seg.words or []:
     a,b=float(word.start)+begin,float(word.end)+begin
     if not core<=(a+b)/2<min(duration,core+24):continue
     token=normalize(simplify.convert(word.word))
     for j,ch in enumerate(token):heard.append(ch);times.append((a+(b-a)*j/len(token),a+(b-a)*(j+1)/len(token)))
   log(event(label,min(core+24,duration),duration,time.monotonic()-started))
  return heard,times
 heard,times=transcribe_windows(model,'核对配音与原稿')
 canonical=normalize(simplify.convert(text));heard_text=''.join(heard);matcher=difflib.SequenceMatcher(None,heard_text,canonical,autojunk=False);recognition_source=None
 if matcher.ratio()<.90 and source_audio:
  try:
   verified_heard,verified_times,evidence=checked_chunk_times(source_audio,text,duration,simplify)
   verified_match=difflib.SequenceMatcher(None,''.join(verified_heard),canonical,autojunk=False)
   if verified_match.ratio()>=.90:
    heard,times=verified_heard,verified_times;heard_text=''.join(heard);matcher=verified_match;recognition_source=evidence
    log('整段转写存在差异；已复用逐文件校验通过的配音分段记录，继续原稿时间校准。')
  except (OSError,ValueError,KeyError,TypeError,OverflowError,EOFError,wave.Error) as exc:log('逐段验收记录未复用，保留原稿匹配检查：'+str(exc))
 if matcher.ratio()<.90:
  try:
   from recognition_support import review_model
   folder,evidence=review_model();log('原稿整段识别存在差异，正在本地独立复核。')
   reviewer=WhisperModel(str(folder),device='cpu',compute_type='float32',cpu_threads=2,num_workers=1,local_files_only=True)
   checked_heard,checked_times=transcribe_windows(reviewer,'备用识别核对原稿')
   checked_match=difflib.SequenceMatcher(None,''.join(checked_heard),canonical,autojunk=False)
   if checked_match.ratio()>=.90:
    heard,times=checked_heard,checked_times;heard_text=''.join(heard);matcher=checked_match;recognition_source={'method':'independent offline review model','model':evidence}
   del reviewer
  except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:log('独立复核未完成，保留原有检查：'+str(exc))
 mapped=[None]*len(canonical);differences=[]
 def same_untoned_sounds(a,b):
  if len(a)!=len(b):return False
  for x,y in zip(a,b):
   if x==y:continue
   if not ('\u4e00'<=x<='\u9fff' and '\u4e00'<=y<='\u9fff'):return False
   sx=set(pinyin(x,style=Style.NORMAL,heteronym=True)[0]);sy=set(pinyin(y,style=Style.NORMAL,heteronym=True)[0])
   if not sx.intersection(sy):return False
  return bool(a)
 mismatch=sum(x!=y for x,y in zip(heard_text,canonical)) if len(heard_text)==len(canonical) else max(len(heard_text),len(canonical))
 untoned_fallback=(len(heard_text)==len(canonical) and matcher.ratio()>=.86 and mismatch<=max(2,int(len(canonical)*.15)) and same_untoned_sounds(heard_text,canonical))
 for tag,a,b,c,d in matcher.get_opcodes():
  if tag=='equal':
   for j in range(d-c):mapped[c+j]=times[a+j]
  else:
   differences.append({'type':tag,'heard':''.join(heard[a:b]),'script':canonical[c:d]})
   if d>c and times:
    left=times[a][0] if a<len(times) else times[-1][1];right=times[b-1][1] if b>a else left
    if tag=='insert':left=times[a-1][1] if a else 0
    for j in range(d-c):mapped[c+j]=(left+(right-left)*j/(d-c),left+(right-left)*(j+1)/(d-c))
 if (matcher.ratio()<.90 and not untoned_fallback) or any(t is None for t in mapped):raise ValueError('配音与原稿未可靠匹配（文字匹配值低于90%或缺少时间）。请核对输入，不生成猜测口型。')
 # Teacher-force the provided text in bounded windows, with local context.
 chars=[];at=0
 while at<len(canonical):
  last=at+1
  while last<len(canonical) and last-at<130 and mapped[last][1]-mapped[at][0]<22:last+=1
  begin=max(0,mapped[at][0]-.18);end=min(duration,mapped[last-1][1]+.24)
  if end-begin<.1:raise ValueError('字词对齐窗口过短，需要人工复核正文或录音。')
  sample=np.asarray(pcm[round(begin*16000):round(end*16000)]).copy();features=model.feature_extractor(sample);enc=model.encode(pad_or_trim(features));words=model.find_alignment(tokenizer,[tokenizer.encode(canonical[at:last])],enc,min(round(len(sample)/160),3000))[0];chunk=[]
  for word in words:
   token=normalize(word['word'])
   for j,ch in enumerate(token):chunk.append({'char':ch,'start':float(word['start'])+begin+(float(word['end'])-float(word['start']))*j/len(token),'end':float(word['start'])+begin+(float(word['end'])-float(word['start']))*(j+1)/len(token),'probability':float(word['probability']),'split_multichar_token':len(token)>1})
  if ''.join(w['char'] for w in chunk)!=canonical[at:last]:raise ValueError('原稿时间对齐未完整返回，不能猜测缺失音节。')
  chars.extend(chunk);at=last;log(event('原稿发音时间校准',at,len(canonical),time.monotonic()-started))
 initials=lazy_pinyin(canonical,style=Style.INITIALS,strict=False,errors=lambda s:list(s));finals=lazy_pinyin(canonical,style=Style.FINALS,strict=False,errors=lambda s:list(s))
 for w,i,f in zip(chars,initials,finals):w.update(initial=i,final=f)
 result={'schema':1,'method':'Whisper-base FP32 verified transcript + teacher-forced attention/DTW; approximate character boundaries, not phoneme ground truth','audio_pcm_sha256':sha(audio16k),'script_sha256':sha(script),'model_sha256':sha(Path(model_path)/'model.bin'),'match_ratio':matcher.ratio(),'differences':differences,'recognition_source':recognition_source,'homophone_fallback':'untoned_pinyin' if untoned_fallback else None,'chars':chars,'elapsed_seconds':time.monotonic()-started,'human_review_required':True}
 tmp=Path(output).with_suffix('.pending.json');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(output);return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--pcm',required=True);p.add_argument('--script',required=True);p.add_argument('--output',required=True);p.add_argument('--source-audio');a=p.parse_args();configure();align(inside(a.pcm),inside(a.script),MODELS/'subtitle/faster-whisper-base',inside(a.output),source_audio=inside(a.source_audio) if a.source_audio else None)
if __name__=='__main__':main()
