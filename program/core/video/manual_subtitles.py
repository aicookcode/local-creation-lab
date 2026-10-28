from error_guidance import show_error
"""Local manual subtitle steps: audio + canonical text -> editable SRT -> burned video."""
import os,argparse,difflib,json,re,subprocess,sys,time,unicodedata
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
from portable_bootstrap import configure
from task_progress import event
configure()

ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('ZMYG_SOURCE_BASE',ROOT.parent.parent))

def norm(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC',text) if c.isalnum())

def stamp(seconds):
    n=round(max(0,seconds)*1000);s,ms=divmod(n,1000);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def phrases(text,limit=16):
    result=[]
    # Punctuation is retained; long unpunctuated clauses get a bounded fallback split.
    for clause in re.findall(r'[^，。！？；：、,!?;:\n]+[，。！？；：、,!?;:]*',text):
        clause=clause.strip()
        while len(norm(clause))>limit:
            count=0;at=0
            for at,c in enumerate(clause,1):
                count+=len(norm(c))
                if count>=limit:break
            result.append(clause[:at]);clause=clause[at:].strip()
        if norm(clause):result.append(clause)
    if norm(''.join(result))!=norm(text):raise ValueError('原稿包含不支持的分段字符，请使用普通中文标点。')
    return result

def align(audio,text_file,out_dir,use_prompt=True):
    import numpy as np
    from faster_whisper import WhisperModel
    audio,text_file,out_dir=Path(audio).resolve(),Path(text_file).resolve(),Path(out_dir).resolve()
    text=text_file.read_text(encoding='utf-8-sig').strip()
    if not text:raise ValueError('正文为空。')
    target=norm(text)
    if not target:raise ValueError('请提交有可朗读文字的正文。')
    out_dir.mkdir(parents=True,exist_ok=True)
    srt=out_dir/'字幕.srt';ass=out_dir/'安全字幕.ass';report=out_dir/'字幕校对记录.json'
    if any(p.exists() for p in [srt,ass,report]):raise FileExistsError('此文件夹已有字幕结果。请选择新的空文件夹，避免覆盖。')
    model_path=Path(os.environ['ZMYG_SUBTITLE_MODEL'])
    if not (model_path/'model.bin').is_file():model_path=None
    if model_path is None:raise FileNotFoundError('未找到本机已缓存的faster-whisper-base模型；本入口不会自动联网下载。')
    print('正在本地识别语音时间，首次加载请稍候……',flush=True);t=time.monotonic()
    probe=subprocess.run([str(ROOT/'ffmpeg.exe'),'-hide_banner','-i',str(audio)],capture_output=True,text=True,encoding='utf-8',errors='replace').stderr
    found=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe)
    if not found:raise ValueError('无法读取音频时长。')
    h,m,s=map(float,found.groups());duration=h*3600+m*60+s
    model=WhisperModel(str(model_path),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
    chars=[];times=[];records=[]
    # Bounded PCM windows with context on either side. Words belong to exactly
    # one core window by their midpoint; timestamps remain on the full timeline.
    for core in range(0,max(1,int(duration)+1),60):
        if core>=duration:break
        begin=max(0,core-1);end=min(duration,core+61)
        pcm=subprocess.run([str(ROOT/'ffmpeg.exe'),'-v','error','-nostdin','-ss',str(begin),'-i',str(audio),'-t',str(end-begin),'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
        samples=np.frombuffer(pcm,dtype=np.float32).copy()
        at=max(0,int(len(text)*core/duration)-80);prompt=text[at:at+300] if use_prompt else None
        segments,_=model.transcribe(samples,language='zh',beam_size=5,word_timestamps=True,condition_on_previous_text=False,initial_prompt=prompt)
        for seg in segments:
            owned=[]
            for word in seg.words or []:
                start,finish=word.start+begin,word.end+begin
                if not core<=(start+finish)*.5<min(duration,core+60):continue
                start=max(core,start,times[-1][1] if times else 0);finish=min(duration,core+60,max(start,finish));token=norm(word.word);owned.append(word.word)
                for i,c in enumerate(token):
                    chars.append(c);times.append((start+(finish-start)*i/len(token),start+(finish-start)*(i+1)/len(token)))
            if owned:records.append({'start':seg.start+begin,'end':seg.end+begin,'text':''.join(owned)})
        print(event('字幕语音识别',min(duration,core+60),duration,time.monotonic()-t),flush=True)
    source=''.join(chars);matcher=difflib.SequenceMatcher(None,source,target,autojunk=False)
    differences=[];aligned=[None]*len(target)
    for tag,a,b,c,d in matcher.get_opcodes():
        if tag=='equal':
            for j in range(d-c):aligned[c+j]=times[a+j]
        elif tag in ('replace','insert') and times:
            left=times[a][0] if a<len(times) else times[-1][1]
            right=times[b-1][1] if b>a else (times[a][0] if a<len(times) else duration)
            if tag=='insert':left=times[a-1][1] if a else 0
            right=max(left,right)
            for j in range(d-c):aligned[c+j]=(left+(right-left)*j/(d-c),left+(right-left)*(j+1)/(d-c))
        if tag!='equal':differences.append({'type':tag,'recognized':source[a:b],'script':target[c:d]})
    info={'audio':str(audio),'script':str(text_file),'duration':duration,'model':str(model_path),'match_ratio':matcher.ratio(),'differences':differences,'recognition':records,'use_initial_prompt':use_prompt,'method':'ASR word times + original-text sequence alignment; approximate timing, review required','elapsed_seconds':round(time.monotonic()-t,2)}
    report.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    if matcher.ratio()<.85 or not times or any(x is None for x in aligned):
        raise ValueError('语音与正文差异过大，已停止自动对时。请先确认选择了配套的正文和音频，再查看字幕校对记录.json。')
    cues=[];offset=0
    for phrase in phrases(text):
        n=len(norm(phrase));begin=aligned[offset][0];end=aligned[offset+n-1][1];offset+=n
        cues.append({'text':phrase,'start':max(0,begin-.025),'speech_end':min(duration,end)})
    for i,cue in enumerate(cues):
        next_start=cues[i+1]['start'] if i+1<len(cues) else duration
        cue['end']=min(next_start,cue['speech_end']+.12,duration)
        if cue['end']-cue['start']<.10:raise ValueError('发现过短或重叠的时间段，请查看校对记录并核对原稿/录音，不强行输出字幕。')
    srt.write_text('\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+'\n',encoding='utf-8-sig')
    info.update(cues=cues,status='draft_review_required');report.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'已保存 {len(cues)} 段字幕：{srt}\n文字匹配值 {matcher.ratio():.1%}，不是准确率评分。请边听音频边校对时间。',flush=True)
    return srt

def burn(video,subtitles,output):
    import subtitle_layout as layout
    video,subtitles,output=map(lambda p:Path(p).resolve(),[video,subtitles,output])
    if video==output:raise ValueError('成片不能覆盖无字幕母版。')
    if output.exists():raise FileExistsError('成片已存在，请换一个名称。')
    # Common subtitle layout is calibrated for 1080x1920.
    probe=subprocess.run([str(ROOT/'ffmpeg.exe'),'-hide_banner','-i',str(video)],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if not re.search(r'Video:.*\b1080x1920\b',probe.stderr):raise ValueError('请在视频制作器中选择1080×1920；本入口使用该画布的通用字幕样式。')
    cues=layout.read_cues(subtitles)
    if not cues:raise ValueError('字幕文件为空或格式错误。')
    last=0
    for cue in cues:
        start,end=layout.time_seconds(cue['start']),layout.time_seconds(cue['end'])
        if start<last-.005 or end<=start:raise ValueError('字幕时间倒序、重叠或结束时间不正确，请先校对SRT。')
        last=end
    # A unique sibling ASS preserves previous checked files and allows repeated burns.
    ass=output.with_suffix('.ass')
    print('正在应用固定84号粗体、安全宽度和1200px字幕中心……',flush=True)
    layout.prepare_ass(subtitles,ass)
    print('正在叠加字幕，保留母版音轨……',flush=True);layout.burn(video,ass,output)
    print('已完成：'+str(output),flush=True)
    return output

def interactive(mode):
    import tkinter as tk
    from tkinter import filedialog,messagebox
    root=tk.Tk();root.withdraw()
    try:
        if mode=='align':
            messagebox.showinfo('生成可校对字幕','依次选择：本次配音 → 与配音完全相同的正文TXT → 一个新建的空字幕文件夹。\n完成后请打开字幕.srt校对；此步骤不生成视频。')
            audio=filedialog.askopenfilename(title='1 选择本次配音',filetypes=[('语音','*.wav *.mp3 *.m4a *.flac *.ogg'),('所有文件','*.*')])
            if not audio:return
            script=filedialog.askopenfilename(title='2 选择本次配音的原正文TXT',filetypes=[('正文','*.txt')],initialdir=str(Path(audio).parent))
            if not script:return
            folder=filedialog.askdirectory(title='3 选择一个新建的空字幕文件夹')
            if not folder:return
            result=align(audio,script,folder)
            messagebox.showinfo('字幕初稿已生成','下一步：用记事本打开并校对\n'+str(result)+'\n\n然后运行“叠加字幕到视频.cmd”。')
        else:
            messagebox.showinfo('叠加字幕','依次选择：1080×1920无字幕母版 → 已校对字幕.srt → 成片保存位置。\n这一步会保留配音，添加固定样式字幕。')
            video=filedialog.askopenfilename(title='1 选择无字幕母版',filetypes=[('视频','*.mp4')])
            if not video:return
            subtitles=filedialog.askopenfilename(title='2 选择已校对的SRT或ASS',filetypes=[('字幕','*.srt *.ass')])
            if not subtitles:return
            output=filedialog.asksaveasfilename(title='3 保存带字幕成片',defaultextension='.mp4',initialfile=Path(video).stem.replace('无字幕母版','成片')+'-字幕版.mp4',filetypes=[('视频','*.mp4')])
            if not output:return
            result=burn(video,subtitles,output);messagebox.showinfo('视频已完成',str(result))
    except Exception as exc:
        show_error('未完成',str(exc));raise
    finally:root.destroy()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['align','burn'],required=True)
    ap.add_argument('--audio');ap.add_argument('--text');ap.add_argument('--out-dir');ap.add_argument('--video');ap.add_argument('--subtitles');ap.add_argument('--output');ap.add_argument('--interactive',action='store_true')
    ap.add_argument('--no-prompt',action='store_true',help='Disable the script hint if it biases recognition or word timings.')
    a=ap.parse_args()
    if a.interactive:return interactive(a.mode)
    if a.mode=='align':
        if not all([a.audio,a.text,a.out_dir]):ap.error('align需要--audio --text --out-dir')
        align(a.audio,a.text,a.out_dir,use_prompt=not a.no_prompt)
    else:
        if not all([a.video,a.subtitles,a.output]):ap.error('burn需要--video --subtitles --output')
        burn(a.video,a.subtitles,a.output)

if __name__=='__main__':main()
