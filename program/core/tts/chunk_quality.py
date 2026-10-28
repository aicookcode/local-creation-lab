"""Fail-closed Qwen chunk acceptance before caching or concatenation.

Recognition is an approximate diagnostic, not a listening/naturalness guarantee.
Only sustained near-silence AFTER matched final speech can be shortened.
Unexplained audible tails are rejected, never denoised into an apparent pass.
"""
import json,os,re,subprocess,unicodedata,wave,difflib,math
from pathlib import Path
import numpy as np
from segment_cache import wav_data,sha
from opencc import OpenCC
from pypinyin import pinyin,Style

POLICY='qwen-chunk-acceptance-v3-tail-review'
_simplify=OpenCC('t2s')
def norm(s):return ''.join(c.lower() for c in _simplify.convert(unicodedata.normalize('NFKC',s)) if c.isalnum())

def exact_homophones(a,b):
 """Only equal-length Han substitutions with overlapping TONED syllables.

 No fuzzy pinyin, missing syllables, inserted words, or numeric substitutions.
 Multiple dictionary readings handle cases such as colloquial 踏(ta1)实.
 """
 if len(a)!=len(b):return False
 for x,y in zip(a,b):
  if x==y:continue
  if not ('\u4e00'<=x<='\u9fff' and '\u4e00'<=y<='\u9fff'):return False
  sx=set(pinyin(x,style=Style.TONE3,heteronym=True,neutral_tone_with_five=True)[0])
  sy=set(pinyin(y,style=Style.TONE3,heteronym=True,neutral_tone_with_five=True)[0])
  if not sx.intersection(sy):return False
 return bool(a)

def exact_untoned_homophones(a,b):
 """Allow a small same-syllable ASR drift when tones are uncertain.

 This remains an equal-length, Han-only comparison. It is intentionally
 separate from the strict toned check and is only used by the narrow
 high-match fallback in assess().
 """
 if len(a)!=len(b):return False
 for x,y in zip(a,b):
  if x==y:continue
  if not ('\u4e00'<=x<='\u9fff' and '\u4e00'<=y<='\u9fff'):return False
  sx=set(pinyin(x,style=Style.NORMAL,heteronym=True)[0]);sy=set(pinyin(y,style=Style.NORMAL,heteronym=True)[0])
  if not sx.intersection(sy):return False
 return bool(a)

def tail_review_candidate(report,text):
 """A lexical review cannot repair missing speech, noise or bad PCM."""
 target=norm(text);heard=norm(report.get('recognized',''))
 return (report.get('errors')==['末尾文字未可靠匹配，不能确认已经读完']
         and len(target)>=20 and len(target)==len(heard)
         and report.get('text_match',0)>=.82 and report.get('confidence',-99)>=-.8
         and exact_untoned_homophones(target[:-8],heard[:-8]))

def tail_sound_evidence(target,heard):
 """An independent unprompted crop must contain every final syllable.
 This is only eligibility evidence for a new full recognition, not acceptance.
 Use the existing pronunciation dictionary without fuzzy syllable substitution.
 """
 target=norm(target)[-8:];heard=norm(heard)[-8:]
 if len(target)!=8 or len(heard)!=8:return False
 return exact_untoned_homophones(target,heard)

def lexical_hints(text):
 # Short names/phrases, not the full expected transcript as a decoder prompt.
 books=re.findall(r'《([^》]{2,12})》',text)
 phrases=re.findall(r'[\u4e00-\u9fff]{2,12}',text)[-2:]
 return ' '.join(dict.fromkeys(books+phrases))[:64]

def exceeds_audio_time(later,earlier,limit,rate):
 """Compare elapsed time on the PCM sample grid, without float subtraction.

 This preserves the limit: exactly 0.90 seconds is not greater than 0.90.
 A real excess of one sample is still greater, without adding a tolerance.
 """
 return round(later*rate)-round(earlier*rate)>round(limit*rate)

class ChunkRejected(RuntimeError):
 def __init__(self,report,log_path):
  self.report=report;self.log_path=log_path
  super().__init__('本段未通过验收，未合并、未缓存：'+'；'.join(report['errors'])+'。检查记录：'+str(log_path))

def assess(samples,rate,text,recognized,words,confidence):
 if samples.ndim!=1 or not len(samples) or not np.isfinite(samples).all():raise ValueError('空音频或非法采样')
 duration=len(samples)/rate;target=norm(text);heard=norm(recognized)
 ratio=difflib.SequenceMatcher(None,target,heard,autojunk=False).ratio()
 different=sum(x!=y for x,y in zip(target,heard)) if len(target)==len(heard) else max(len(target),len(heard))
 phonetic_fallback=(different>0 and len(target)>=5 and ratio>=.60 and different<=max(2,int(len(target)*.20)) and exact_homophones(target,heard))
 short_toned=(different>0 and 3<=len(target)<=4 and confidence is not None and confidence>=-.8 and exact_homophones(target,heard))
 phonetic_fallback=phonetic_fallback or short_toned
 untoned_fallback=(different>0 and len(target)>=5 and ratio>=.86 and different<=max(2,int(len(target)*.15)) and exact_untoned_homophones(target,heard))
 step=max(1,round(rate*.01));rms=np.array([np.sqrt(np.mean(samples[i:i+step].astype(np.float64)**2)) for i in range(0,len(samples),step)])
 peak=float(np.max(np.abs(samples)));active=np.flatnonzero(rms>.001) # -60 dBFS; conservative silence boundary
 last_active=min(duration,(int(active[-1])+1)*step/rate) if len(active) else 0.
 last_word=max((float(w['end']) for w in words if norm(w['word'])),default=0.)
 tail=duration-last_active
 clipped=int(np.count_nonzero(np.abs(samples)>=32767/32768))
 report={'policy':POLICY,'seconds':duration,'recognized':recognized,'text_match':ratio,'last_word_end':last_word,
         'last_active':last_active,'trailing_near_silence':tail,'peak':peak,'clipped_samples':clipped,
         'confidence':confidence,'words':words,'errors':[],'warnings':[],'trim_end_seconds':None,'human_review_required':True,
         'simplified_recognition':heard,'exact_toned_homophone_fallback':phonetic_fallback,'exact_untoned_homophone_fallback':untoned_fallback}
 report['timing_comparison']={'method':'PCM sample indices','sample_rate':rate,'last_active_sample':round(last_active*rate),'last_word_end_sample':round(last_word*rate),'audible_tail_limit_samples':round(.9*rate)}
 errors=report['errors']
 if phonetic_fallback:report['warnings'].append('少量同音转写差异经逐音节声调字典复核；不等于人工读音验收')
 elif untoned_fallback:report['warnings'].append('少量同音转写差异经无声调拼音复核；仍需人工试听确认')
 if not target or not heard or not words or (ratio<.82 and not (phonetic_fallback or untoned_fallback)):errors.append('识别文字与正文差异较大，可能漏读、重复或识别失败')
 suffix=min(3,len(target))
 suffix_ok=heard.endswith(target[-suffix:]) or (phonetic_fallback and exact_homophones(target[-suffix:],heard[-suffix:])) or (untoned_fallback and exact_untoned_homophones(target[-suffix:],heard[-suffix:]))
 if not suffix_ok:errors.append('末尾文字未可靠匹配，不能确认已经读完')
 if confidence is None or confidence < -1.2:errors.append('语音识别置信度不足，需要试听核对')
 if not len(active) or peak<.003:errors.append('音频为空白或声音过弱')
 if clipped>max(3,len(samples)*.0001):errors.append('检测到明显满幅剪切采样')
 if duration>max(6,len(target)/1.3+3):errors.append('时长与正文严重不符，疑似失控尾音或重复')
 if exceeds_audio_time(last_active,last_word,.9,rate):errors.append('正文末尾之后仍有较长可闻信号，疑似杂音或多余内容；不自动裁切')
 if exceeds_audio_time(duration,last_active,.8,rate) and exceeds_audio_time(last_word,last_active,.4,rate):errors.append('末尾识别时间落在长静音内，无法可靠裁切，需试听核对')
 # Preserve all voiced samples plus 250ms room. Never trim based on ASR alone.
 if not errors and exceeds_audio_time(duration,last_active,.8,rate) and not exceeds_audio_time(last_word,last_active,.4,rate):
  report['trim_end_seconds']=min(duration,max(last_active,last_word)+.25)
 report['status']='rejected' if errors else 'accepted'
 return report

class ChunkInspector:
 def __init__(self,allow_fallback=True):self.model=None;self.allow_fallback=allow_fallback;self.active_model='base'
 def inspect(self,path,text,log_path=None):
  from faster_whisper import WhisperModel
  path=Path(path);log_path=Path(log_path) if log_path else path.with_suffix('.quality.json')
  info=wav_data(path)
  if info['channels']!=1 or info['width']!=2:raise ValueError('逐段检查需要单声道16位PCM')
  with wave.open(str(path),'rb') as f:raw=f.readframes(f.getnframes());rate=f.getframerate()
  samples=np.frombuffer(raw,'<i2').astype(np.float32)/32768
  if self.model is None:
   model=Path(os.environ['ZMYG_SUBTITLE_MODEL'])
   if not (model/'model.bin').is_file():raise FileNotFoundError('缺少本地字幕模型，无法验收Qwen片段；不会跳过检查合并')
   print('正在检查片段正文与结尾（本地识别，不上传）……',flush=True)
   self.model=WhisperModel(str(model),device='cpu',compute_type='float32',cpu_threads=4,num_workers=1,local_files_only=True)
  # FFmpeg resampling is recognition-only; accepted output retains original PCM/rate.
  ff=Path(os.environ['ZMYG_FFMPEG'])
  pcm=subprocess.run([str(ff),'-v','error','-nostdin','-i',str(path),'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
  audio16=np.frombuffer(pcm,np.float32).copy()
  def recognize(audio,offset=0.,**options):
   segs,_=self.model.transcribe(audio,language='zh',beam_size=5,word_timestamps=True,condition_on_previous_text=False,vad_filter=False,**options)
   texts=[];words=[];conf=[]
   for s in segs:
    texts.append(s.text);conf.append(float(s.avg_logprob))
    words.extend({'word':w.word,'start':float(w.start)+offset,'end':float(w.end)+offset,'probability':float(w.probability)} for w in s.words or [])
   return ''.join(texts),words,sum(conf)/len(conf) if conf else None
  recognized,words,confidence=recognize(audio16)
  report_model=self.active_model
  report=assess(samples,rate,text,recognized,words,confidence)
  if tail_review_candidate(report,text):
   print('尾部转写存在近音疑点，正在有限复核（不重生成、不修改正文）……',flush=True)
   start=max(0.,report['last_word_end']-5.)
   tail_text,tail_words,tail_conf=recognize(audio16[round(start*16000):].copy(),start)
   review={'unprompted_tail':tail_text,'tail_confidence':tail_conf,'tail_words':tail_words,'accepted':False}
   hints=lexical_hints(text)
   if hints and tail_conf is not None and tail_conf>=-.8 and tail_sound_evidence(text,tail_text):
    corrected,corrected_words,corrected_conf=recognize(audio16,hotwords=hints)
    candidate=assess(samples,rate,text,corrected,corrected_words,corrected_conf)
    review.update(hotwords=hints,recognized=corrected,confidence=corrected_conf,errors=candidate['errors'])
    if (candidate['status']=='accepted' and candidate['text_match']>=.95
        and exact_untoned_homophones(norm(text),norm(corrected))
        and corrected_conf is not None and corrected_conf>=max(-.8,confidence-.15)
        and abs(round(candidate['last_word_end']*rate)-round(report['last_word_end']*rate))<=round(.3*rate)):
     original=report;report=candidate;review['accepted']=True
     report['first_pass']={k:original[k] for k in ('recognized','text_match','confidence','errors','words')}
     report['warnings'].append('尾句经无提示近音复核和短热词转写复核通过；识别不等于读音或自然度已人工验收')
   report['tail_review']=review
  if report['status']!='accepted' and self.allow_fallback and self.active_model=='base':
   try:
    from recognition_support import review_model
    folder,model_receipt=review_model()
    print('轻量识别未通过，正在用本地备用模型独立复核；不修改正文或声音……',flush=True)
    self.model=None
    import gc
    gc.collect()
    self.model=WhisperModel(str(folder),device='cpu',compute_type='float32',cpu_threads=4,num_workers=1,local_files_only=True)
    self.active_model='small'
    checked,checked_words,checked_conf=recognize(audio16)
    candidate=assess(samples,rate,text,checked,checked_words,checked_conf)
    evidence={'accepted':candidate['status']=='accepted','model':model_receipt,'recognized':checked,'errors':candidate['errors'],'primary_recognized':report['recognized'],'primary_errors':report['errors']}
    if candidate['status']=='accepted':
     candidate['primary_pass']=report;report=candidate;report_model='small'
     report['warnings'].append('轻量模型转写未通过，本地独立备用模型复核通过；仍需人工试听')
    report['independent_review']=evidence
   except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
    report['warnings'].append('备用识别未完成：'+str(exc))
  report.update(raw_sha256=sha(path),sample_rate=rate,expected_text=text,recognizer='faster-whisper-'+report_model+' / CPU FP32 / '+('unprompted tail + bounded lexical hotwords' if report.get('tail_review',{}).get('accepted') else 'no text prompt'),trimmed=False)
  # Save rejection evidence as well. Failed raw WAV/log remain available to inspect.
  log_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  if report['status']!='accepted':raise ChunkRejected(report,log_path)
  if report['trim_end_seconds'] is not None:
   original=path.with_name(path.stem+'.raw.wav')
   if original.exists():raise FileExistsError('原始片段备份已存在，避免覆盖请使用新的输出任务')
   path.rename(original);pending=path.with_name(path.stem+'.checked.pending.wav')
   keep=min(len(samples),math.ceil(report['trim_end_seconds']*rate))
   with wave.open(str(pending),'wb') as f:f.setparams((1,2,rate,0,'NONE','not compressed'));f.writeframes(raw[:keep*2])
   os.rename(pending,path);report.update(trimmed=True,raw_file=original.name,kept_frames=keep)
  report.update(output_sha256=sha(path),accepted_seconds=wav_data(path)['seconds'])
  log_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  return report
