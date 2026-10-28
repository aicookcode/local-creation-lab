"""Reflow timed captions with one fixed, measured, platform-safe style."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile,time
from task_progress import event

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent
STYLE_FILE = ROOT / 'default_subtitle_style.json'

# Prefer meaningful clause boundaries when earlier subtitles lack punctuation.
CLAUSE_BREAKS = {}


def load_style():
    cfg = json.loads(STYLE_FILE.read_text(encoding='utf-8'))
    bundle=Path(os.environ.get('ZMYG_BUNDLE_ROOT',ROOT.parents[1]))
    cfg['font_file']=str((bundle/cfg['font_file']).resolve())
    width, height = cfg['resolution']
    if cfg['auto_resize'] or cfg['font_size_mode'] != 'fixed':
        raise ValueError('通用字幕必须使用固定字号。')
    if cfg['max_width_px'] > width - cfg['safe_margin_left_px'] - cfg['safe_margin_right_px']:
        raise ValueError('字幕宽度超过左右安全边距。')
    if not (0 < cfg['center_y_px'] < height):
        raise ValueError('字幕中心必须在画布内。')
    return cfg


def ass_time(seconds):
    ticks = round(seconds * 100)
    h, ticks = divmod(ticks, 360000)
    m, ticks = divmod(ticks, 6000)
    s, ticks = divmod(ticks, 100)
    return f'{h}:{m:02d}:{s:02d}.{ticks:02d}'


def time_seconds(value):
    h, m, s = value.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def read_cues(path):
    path = Path(path)
    content = path.read_text(encoding='utf-8-sig')
    cues = []
    if path.suffix.lower() == '.ass':
        for line in content.splitlines():
            if not line.startswith('Dialogue: '):
                continue
            fields = line[len('Dialogue: '):].split(',', 9)
            text = re.sub(r'\{[^}]*\}', '', fields[9]).replace(r'\N', '\n').replace(r'\n', '\n')
            cues.append({'start': fields[1], 'end': fields[2], 'text': text})
    elif path.suffix.lower() == '.srt':
        for block in re.split(r'\n\s*\n', content.strip()):
            lines = block.strip().splitlines()
            at = next((i for i, line in enumerate(lines) if '-->' in line), None)
            if at is None:
                continue
            start, end = lines[at].split('-->')
            text = re.sub(r'<[^>]+>', '', '\n'.join(lines[at + 1:]))
            cues.append({'start': ass_time(time_seconds(start.strip())),
                         'end': ass_time(time_seconds(end.strip().split()[0])), 'text': text})
    else:
        raise ValueError('只接受已经对时的 ASS 或 SRT 字幕。')
    if not cues:
        raise ValueError('字幕没有可用时间段。')
    for cue in cues:
        if time_seconds(cue['end']) <= time_seconds(cue['start']):
            raise ValueError('字幕结束时间必须晚于开始时间。')
    return cues


def reflow(text, cfg, font):
    text = text.replace('\r', '').replace('{', '｛').replace('}', '｝').replace('\\', '＼').strip()
    # Measure conservative glyph advances with outline and shadow headroom.
    allowance = 2 * math.ceil(cfg['outline_px'] + cfg['shadow_px']) + 4
    limit = cfg['max_width_px'] - allowance
    # libass normalizes this Windows font's ASS size differently from Pillow's
    # em-size. This conservative advance factor was calibrated on actual libass
    # output; production also audits the raster bounds of every timed caption.
    advance_scale = cfg.get('pillow_to_ass_advance_scale', 1.0)

    def fits(line):
        return font.getlength(line) * advance_scale <= limit

    lines = text.splitlines()
    if len(lines) <= cfg['max_lines'] and all(fits(line) for line in lines):
        return lines
    flat = ''.join(lines)
    proposed = CLAUSE_BREAKS.get(flat, '').splitlines()
    if proposed and len(proposed) <= cfg['max_lines'] and all(fits(line) for line in proposed):
        return proposed
    candidates = []
    opening = '（(《「“‘'
    closing = '，。！？；：、,.!?;:）)》」”’'
    for i in range(1, len(flat)):
        a, b = flat[:i].rstrip(), flat[i:].lstrip()
        if not fits(a) or not fits(b) or a[-1] in opening or b[0] in closing:
            continue
        score = abs(font.getlength(a) - font.getlength(b))
        if a[-1] in '，。！？；：、,.!?;:':
            score -= cfg['font_size'] * 2
        candidates.append((score, a, b))
    if candidates and cfg['max_lines'] >= 2:
        _, a, b = min(candidates)
        return [a, b]
    raise ValueError('字幕超过两行，请先按语义细分时间段，固定字号不会自动缩小：' + flat)


def prepare_ass(source, target, cfg=None):
    cfg = cfg or load_style()
    source, target = Path(source), Path(target)
    if target.exists():
        raise FileExistsError('字幕输出已存在，请换一个名称：' + str(target))
    cues = read_cues(source)
    font = ImageFont.truetype(cfg['font_file'], cfg['font_size'])
    w, h = cfg['resolution']
    style = (f"Style: Default,{cfg['font']},{cfg['font_size']},&H00FFFFFF,&H00FFFFFF,"
             f"&H00141414,&H80000000,-1,0,0,0,100,100,0,0,1,{cfg['outline_px']},"
             f"{cfg['shadow_px']},5,{cfg['safe_margin_left_px']},{cfg['safe_margin_right_px']},0,1")
    header = (f'[Script Info]\nTitle: Local Media Studio Captions\nScriptType: v4.00+\n'
              f'PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
              'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, '
              'Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n' + style + '\n\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    events, record = [], []
    for cue in cues:
        lines = reflow(cue['text'], cfg, font)
        text = r'\N'.join(lines)
        position = '{' + f"\\an5\\pos({cfg['center_x_px']},{cfg['center_y_px']})\\q2" + '}'
        events.append(f"Dialogue: 0,{cue['start']},{cue['end']},Default,,0,0,0,,{position}{text}")
        record.append({**cue, 'lines': lines, 'estimated_ass_widths_px':
                       [font.getlength(t) * cfg.get('pillow_to_ass_advance_scale', 1.0) for t in lines]})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + '\n'.join(events) + '\n', encoding='utf-8-sig')
    manifest = {'source': str(source.resolve()), 'style_file': str(STYLE_FILE), 'style': cfg,
                'cues': record, 'timing_changed': False, 'font_size_fixed': True}
    target.with_suffix('.layout.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def burn(video, subtitles, output,progress=None):
    video, subtitles, output = map(lambda p: Path(p).resolve(), (video, subtitles, output))
    if output.exists():
        raise FileExistsError('视频输出已存在，请换一个名称：' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    if video.with_suffix('.detail.json').is_file():
        from portrait.detail_service import burn_cached
        burn_cached(video,subtitles,output,progress or (lambda s:print(s,flush=True)))
        return
    # Relative filename avoids FFmpeg's Windows drive-letter filter escaping.
    escaped = subtitles.name.replace('\\', '\\\\').replace("'", "'\\''").replace(':', r'\:')
    fonts=os.path.relpath(Path(os.environ['ZMYG_BUNDLE_ROOT'])/'resources/fonts',subtitles.parent).replace('\\','/').replace("'","'\\''")
    probe=subprocess.run([str(ROOT/'ffmpeg.exe'),'-hide_banner','-i',str(video)],capture_output=True,text=True,encoding='utf-8',errors='replace').stderr
    found=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe);duration=None
    if found:
        h,m,s=map(float,found.groups());duration=h*3600+m*60+s
    args=[str(ROOT / 'ffmpeg.exe'), '-hide_banner', '-nostdin', '-loglevel', 'error',
                    '-i', str(video), '-vf', f"ass=filename='{escaped}':fontsdir='{fonts}'", '-map', '0:v:0', '-map', '0:a:0',
                    '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '14',
                    '-pix_fmt', 'yuv420p', '-r', '30', '-c:a', 'copy', '-movflags', '+faststart',
                    '-progress','pipe:1','-nostats',str(output)]
    sink=progress or (lambda line:print(line,flush=True));started=time.monotonic()
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(args,cwd=subtitles.parent,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf-8',errors='replace')
        for line in process.stdout:
            if duration and line.startswith('out_time_us='):
                try:done=min(duration,max(0,int(line.split('=',1)[1])/1000000));sink(event('字幕与视频编码',done,duration,time.monotonic()-started))
                except ValueError:pass
        code=process.wait()
        if code:
            errors.seek(0);raise RuntimeError('字幕编码未完成：'+errors.read(5000).decode('utf-8',errors='replace'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--subtitles', required=True)
    ap.add_argument('--out-ass', required=True)
    ap.add_argument('--video')
    ap.add_argument('--output')
    args = ap.parse_args()
    if bool(args.video) != bool(args.output):
        ap.error('--video 和 --output 需要同时提供')
    manifest = prepare_ass(args.subtitles, args.out_ass)
    print(f"已按默认样式排版 {len(manifest['cues'])} 段字幕，固定 {manifest['style']['font_size']} 号字。", flush=True)
    if args.video:
        burn(args.video, args.out_ass, args.output)
        print('已保存：' + str(Path(args.output).resolve()), flush=True)
