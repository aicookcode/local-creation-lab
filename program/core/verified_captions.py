"""Unattended captions from the same verified FP32 alignment as portrait speech."""
from pathlib import Path
import json
from studio_paths import PROGRAM,CACHE,MODELS
from portrait.config import sha
from portrait.photo_service import key,valid,receipt,read
from portrait.service import call

def repair_short_cues(cues,length):
    """Merge tiny adjacent cues, preserving text order and subtitle width."""
    import math
    from portrait.speech_alignment import normalize
    result=[dict(c) for c in cues];repairs=[]
    for i,c in enumerate(result):
        if not math.isfinite(c['start']+c['end']) or c['start']<-.05 or c['end']>length+.15:raise ValueError('字幕时间无效。')
        c['start']=max(0.,c['start']);c['end']=min(length,c['end'])
        if i and c['start']<result[i-1]['start']:raise ValueError('字幕起点倒序。')
        if i+1<len(result):c['end']=min(c['end'],result[i+1]['start'])
    i=0
    while i<len(result):
        c=result[i]
        if round(c['end']*1000)-round(c['start']*1000)>=100:i+=1;continue
        neighbor=None
        if i+1<len(result) and len(normalize(c['text']+result[i+1]['text']))<=16:neighbor=i+1
        elif i and len(normalize(result[i-1]['text']+c['text']))<=16:neighbor=i-1
        if neighbor is None:raise ValueError('字幕缺少足够的可靠时间，自动合并仍无法修复；请校对。')
        a,b=sorted((i,neighbor));left,right=result[a],result[b]
        result[a]={'text':left['text']+right['text'],'start':min(left['start'],right['start']),'end':max(left['end'],right['end'])}
        repairs.append({'action':'merge_short_cue','text':c['text']});result.pop(b);i=max(0,a-1)
    return result,repairs

def make(audio,script,folder,log,source_audio=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);audio=Path(audio);script=Path(script)
    from model_manager import ensure
    ensure('subtitle_base',log)
    descriptor={'audio':sha(audio),'script':sha(script),'model':sha(MODELS/'subtitle/faster-whisper-base/model.bin'),'code':sha(PROGRAM/'core/portrait/speech_alignment.py'),'precision':'FP32'}
    from recognition_support import review_descriptor
    descriptor['review_model']=review_descriptor()
    cache=CACHE/'portrait/transcript_timing'/key(descriptor);cache.mkdir(parents=True,exist_ok=True)
    if not valid(cache,'alignment',['alignment.json']):
        pcm=folder/'analysis16k.f32'
        call([PROGRAM/'core/video/ffmpeg.exe','-v','error','-nostdin','-y','-i',audio,'-ac','1','-ar','16000','-f','f32le',pcm],log)
        args=[PROGRAM/'runtime/python/python.exe','-X','utf8','-u',PROGRAM/'core/portrait/speech_alignment.py','--pcm',pcm,'--script',script,'--output',cache/'alignment.json']
        source=Path(source_audio) if source_audio else audio
        if source.is_file() and sha(source)==sha(audio):args+=['--source-audio',source]
        call(args,log)
        receipt(cache,'alignment',['alignment.json'])
    else:log('字幕复用已核对的FP32原稿时间；无需重复识别。')
    from video.manual_subtitles import phrases,stamp
    from portrait.speech_alignment import normalize
    from portrait.config import duration_wav
    from opencc import OpenCC
    text=script.read_text(encoding='utf-8-sig').strip();convert=OpenCC('t2s');data=read(cache/'alignment.json');chars=data['chars'];target=normalize(convert.convert(text))
    if ''.join(c['char'] for c in chars)!=target or data['script_sha256']!=sha(script):raise ValueError('字幕原稿与校准记录不符。')
    length=duration_wav(audio);cues=[];at=0
    for clause in phrases(text,16):
        count=len(normalize(convert.convert(clause)));items=chars[at:at+count];at+=count
        if not items:continue
        begin=max(0,float(items[0]['start'])-.025);end=min(length,float(items[-1]['end'])+.12);cues.append({'text':clause,'start':begin,'end':end})
    if at!=len(chars) or not cues:raise ValueError('字幕分句未完整覆盖原稿。')
    cues,repairs=repair_short_cues(cues,length)
    if repairs:log(f'已自动合并{len(repairs)}处过短字幕，保留全部正文。')
    srt=folder/'字幕.srt'
    if srt.exists():raise FileExistsError('字幕结果已存在，不能覆盖。')
    srt.write_text('\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+'\n',encoding='utf-8-sig')
    (folder/'字幕校对记录.json').write_text(json.dumps({'status':'automatically_checked_pending_human_review','method':'verified FP32 transcript alignment','alignment_record':str(cache/'alignment.json'),'match_ratio':data['match_ratio'],'cues':cues,'automatic_repairs':repairs,'human_review_required':True},ensure_ascii=False,indent=2),encoding='utf-8')
    return srt

