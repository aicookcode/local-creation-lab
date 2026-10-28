"""Offline mapping to explicit speed/pitch, not model emotion instructions."""
import math
import re
import unicodedata


def parse_style_prompt(text, engine='qwen'):
    text = unicodedata.normalize('NFKC', text or '').strip()
    changes, applied, unsupported = {}, [], []
    if not text:
        return dict(text=text, changes=changes, applied=applied, unsupported=unsupported)
    active, locked = [], set()
    for clause in re.split(r'[，,。；;！!？?\n]+|但是|不过', text):
        if not clause.strip():
            continue
        if re.search(r'不要|不想|不需要|别|不用|保持|不变|不能|不希望', clause):
            unsupported.append('未自动映射含否定或保持要求的描述：' + clause.strip())
            if re.search(r'语速|快|慢',clause):locked.add('default_speed')
            if re.search(r'音高|音调|低沉|明亮|沧桑|苍桑',clause):locked.add('pitch_semitones')
        else:
            active.append(clause)
    request = '，'.join(active)
    speed = re.search(r'语速\s*(?:设为|设置为|调到|调整为|为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*([%倍xX]?)', request)
    if speed:
        value = float(speed[1]) / (100 if speed[2] == '%' else 1)
        changes['default_speed'] = min(1.15, max(.85, value))
        if value != changes['default_speed']:
            unsupported.append('语速已限制在0.85–1.15的安全范围。')
    else:
        slow = bool(re.search(r'慢(?:一?点|一?些|速)?|放慢|舒缓|从容', request))
        fast = bool(re.search(r'快(?:一?点|一?些|速)?|加快', request))
        if slow and fast:
            unsupported.append('同时出现加快和放慢要求，语速保持当前值。')
        elif slow or fast:
            changes['default_speed'] = .94 if slow else 1.06
    pitch = re.search(r'(?:音高|音调)\s*(?:设为|设置为|调到|调整为|为|是|[:：=])?\s*([+-]?\d+(?:\.\d+)?)\s*半音', request)
    relative = re.search(r'(降低|调低|升高|提高|调高)\s*(\d+(?:\.\d+)?)\s*半音', request)
    if pitch or relative:
        value = float(pitch[1]) if pitch else float(relative[2]) * (-1 if relative[1] in ('降低', '调低') else 1)
        changes['pitch_semitones'] = min(3., max(-3., value))
        if value != changes['pitch_semitones']:
            unsupported.append('音高偏移已限制在-3至+3半音。')
    else:
        low = bool(re.search(r'低沉|厚重|音[高调].{0,3}(?:低|降)|沧桑|苍桑', request))
        high = bool(re.search(r'明亮|清亮|音[高调].{0,3}(?:高一|升|提高)', request))
        if low and high:
            unsupported.append('同时出现调高和调低要求，音高保持当前值。')
        elif low or high:
            changes['pitch_semitones'] = -1. if low else 1.
    if re.search(r'沧桑|苍桑', request):
        if not speed and 'default_speed' not in changes and not (slow and fast):
            changes['default_speed'] = .94
        unsupported.append('“沧桑”仅提供音高、语速微调的试听起点；不能生成真实年龄感、沙哑或情绪表演。')
    if re.search(r'\d{1,3}\s*岁|[一二三四五六七八九十百]+岁|年龄|年轻|年长|少年|青年|中年|老年|老人|男声|女声|男性|女性', text):
        unsupported.append('年龄、性别无法直接控制；请采用更接近目标的参考录音。')
    if re.search(r'鼻音|沙哑|气声|童声|磁性|方言|口音|温柔|柔和|亲切|活泼|热情|悲伤|愤怒|平静|克制|稳重', text):
        unsupported.append('这些声音质感或情绪需要参考录音配合，当前模型没有对应的独立控制参数。')
    for key in locked:changes.pop(key,None)
    if 'default_speed' in changes:
        applied.append(f"语速 {changes['default_speed']:.2f}倍")
    if 'pitch_semitones' in changes:
        applied.append(f"音高偏移 {changes['pitch_semitones']:+.2f}半音（音频后处理）")
    return dict(text=text, changes=changes, applied=applied, unsupported=unsupported)


def style_summary(result):
    parts = list(result.get('applied', [])) + list(result.get('unsupported', []))
    return '；'.join(parts) if parts else '未识别到可调整项，保留当前参数；可输入“语速0.95，音高-1半音”。'


def pitch_filters(settings, tempo=1.):
    """Keep the original audio path untouched at zero pitch."""
    pitch = settings.get('pitch_semitones', 0.)
    if isinstance(pitch, bool) or not isinstance(pitch, (int, float)) or not math.isfinite(pitch) or not -3 <= pitch <= 3:
        raise ValueError('音高偏移必须在-3至+3半音之间。')
    if pitch:
        return [f'rubberband=tempo={tempo:.8f}:pitch={2 ** (pitch / 12):.10f}:pitchq=quality:formant=shifted','alimiter=limit=0.97:level=false:latency=true']
    return [f'atempo={tempo}'] if tempo != 1. else []
