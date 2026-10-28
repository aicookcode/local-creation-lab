"""Bounded resynthesis at existing text boundaries; never rewrite spoken words."""
from pathlib import Path
import re,uuid


def recovery_segments(text,report,depth):
    if depth>=2:return []
    errors=report.get('errors',[])
    if not errors or any(not any(k in e for k in ('识别文字与正文差异','末尾文字未可靠匹配','正文末尾之后仍有较长可闻信号')) for e in errors):return []
    # Adjacent repeated characters can coalesce in the acoustic model. Split
    # only near a failed tail, preserving both characters and every punctuation.
    if any('末尾文字' in e for e in errors):
        matches=list(re.finditer(r'([\u4e00-\u9fff])\1',text))
        for match in reversed(matches):
            if len(re.sub(r'\W','',text[match.start():]))<=16:
                parts=[text[:match.start()+1],text[match.start()+1:]]
                if all(sum(c.isalnum() for c in p)>=3 for p in parts):return parts
    parts=re.findall(r'.+?[。！？!?；;][”’」』]*|.+$',text,re.S)
    if len(parts)<2:parts=re.findall(r'.+?[，,：:][”’」』]*|.+$',text,re.S)
    parts=[p.strip() for p in parts if p.strip()]
    if not 2<=len(parts)<=6 or not all(sum(c.isalnum() for c in p)>=3 for p in parts):return []
    if ''.join(''.join(parts).split())!=''.join(text.split()):return []
    return parts


def archive_rejected(wav):
    wav=Path(wav);root=wav.parent.resolve()
    folder=wav.parent/'拒绝片段'/(wav.stem+'-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    if not folder.resolve().is_relative_to(root):raise ValueError('恢复记录超出本次任务目录')
    for suffix in ('.wav','.quality.json','.log','.raw.wav'):
        source=wav.with_suffix(suffix)
        if source.exists():source.rename(folder/source.name)
    return str(folder.relative_to(wav.parent))
