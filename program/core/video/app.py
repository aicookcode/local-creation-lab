from error_guidance import show_error
"""Offline photo loop video maker. Standard-library Python + bundled FFmpeg."""
from pathlib import Path
import argparse
import json
import math
import queue
import random
import re
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT = Path(__file__).resolve().parent
FFMPEG = ROOT / 'ffmpeg.exe'
SETTINGS = ROOT.parents[2] / 'user_data/presets/photo_video.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
BASE = ROOT.parent.parent
DEFAULTS = {
    'audio': '',
    'pictures': '',
    'output': '',
    'seconds': 3.0,
    'size': '1080×1920',
    'transition': '智能匹配',
    'transition_seconds': 0.2,
    'motion': '连续呼吸运镜',
    'strength': '轻柔',
}

TRANSITIONS = {'智能匹配': 'auto_bd', '平滑推移': 'slideleft', '轻柔柔焦': 'microfocus'}
TRANSITION_ALIASES = {'自动选择（B/D）':'智能匹配', 'B｜平滑推移':'平滑推移', 'D｜极轻柔焦':'轻柔柔焦'}

def normalize_transition(value):
    """Read old task labels without changing their rendering behavior."""
    return TRANSITION_ALIASES.get(value,value)

def transition_label(mode):
    return {'B':'平滑推移','D':'轻柔柔焦'}.get(mode,mode)
MOTIONS = ['连续呼吸运镜', '随机搭配', '推近拉远交替', '缓慢推近', '缓慢拉远', '向左平移', '向右平移', '静止']


def image_paths(pictures):
    return sorted((p for p in Path(pictures).iterdir()
                   if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}),
                  key=natural_key)


def image_features(source):
    """Low-resolution, centered portrait crop. No identity or face recognition."""
    result = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-i', str(source),
                             '-vf', 'scale=72:128:force_original_aspect_ratio=increase,crop=72:128,format=gray',
                             '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True,
                            creationflags=NO_WINDOW)
    if result.returncode or len(result.stdout) != 72*128:
        raise ValueError(f'无法分析图片：{source.name}')
    return result.stdout


def choose_bd(first, second):
    """Deterministic visual heuristic, conservative about central subject changes."""
    mean_a, mean_b = sum(first)/len(first), sum(second)/len(second)
    brightness = abs(mean_a-mean_b)/255
    offset = mean_a-mean_b
    def difference(x0,y0,x1,y1):
        values = [abs((first[y*72+x]-second[y*72+x])-offset)
                  for y in range(y0,y1) for x in range(x0,x1)]
        return sum(values)/len(values)/255
    whole = difference(0,0,72,128)
    center = difference(17,8,55,119)
    upper = difference(19,8,53,44)
    metrics = {'whole_difference':round(whole,4),'subject_difference':round(center,4),
               'upper_subject_difference':round(upper,4),'brightness_difference':round(brightness,4)}
    # Upper-center changes often reflect a head turn or a change in portrait crop.
    use_b = brightness>.12 or whole>.075 or center>.050 or upper>.065
    if use_b:
        reason = '主体位置、姿态或场景变化较大，用推移分开两张画面'
        if brightness>.12:reason='整体明暗差异较大，用推移完成场景衔接'
        return 'B',reason,metrics
    return 'D','构图与主体变化较小，用0.1秒极轻柔焦衔接',metrics


def plan_transitions(images, selection='智能匹配', cancel=None):
    selection = normalize_transition(selection)
    if selection not in TRANSITIONS:
        raise ValueError('请选择智能匹配、平滑推移或轻柔柔焦。')
    if not images:
        raise ValueError('图片文件夹中没有可用图片。')
    override = {}
    if selection == '智能匹配':
        try:
            policy = json.loads((ROOT/'default_transition_modes.json').read_text(encoding='utf-8'))
            group = images[0].parent.name
            if all(p.parent == images[0].parent for p in images):
                override = policy.get('group_overrides', {}).get(group, {})
            if override.get('mode') not in ('B', 'D'):
                override = {}
        except (OSError, ValueError, AttributeError):
            override = {}
    features = []
    if selection == '智能匹配' and not override:
        for source in images:
            if cancel and cancel.is_set():raise Cancelled()
            features.append(image_features(source))
    plan = []
    for current in range(len(images)):
        previous = (current-1)%len(images)
        if override:
            mode, reason, metrics = override['mode'], override.get('reason', '用户指定此组转场'), {}
        elif features:
            mode,reason,metrics = choose_bd(features[previous],features[current])
        else:
            mode = 'B' if TRANSITIONS[selection]=='slideleft' else 'D'
            reason,metrics = '手动选择'+transition_label(mode),{}
        plan.append({'from':images[previous].name,'to':images[current].name,
                     'mode':transition_label(mode),'effect':'slideleft' if mode=='B' else 'microfocus',
                     'seconds':.2 if mode=='B' else .1,'reason':reason,'metrics':metrics})
    return plan


def motion_filter(mode, strength, frames, width, height, fps=30, phase_frames=0, cycle_frames=None, vertical_focus=.45):
    u = f'(on/{frames - 1})'
    ease = f'(3*{u}*{u}-2*{u}*{u}*{u})'
    span = 0.035 if strength == '轻柔' else 0.075
    zoom, x = '1.015', '(iw-iw/zoom)*0.5'
    if mode == '连续呼吸运镜':
        cycle_frames = cycle_frames or max(2, frames)
        phase = f'(2*PI*(on+{phase_frames})/{cycle_frames})'
        amplitude = .006 if strength == '轻柔' else .03
        base_zoom = 1.015 if strength == '轻柔' else 1.025
        zoom = f'{base_zoom}+{amplitude}*(1-cos({phase}))'
        x = f'(iw-iw/zoom)*(0.50+0.06*sin({phase}))'
    elif mode == '缓慢推近':
        zoom = f'1.015+{span}*{ease}'
    elif mode == '缓慢拉远':
        zoom = f'{1.015+span}-' + f'{span}*{ease}'
    elif mode in ('向左平移', '向右平移'):
        zoom = str(1.055 if strength == '轻柔' else 1.095)
        # Moving the crop right moves the visible picture left.
        fraction = f'(0.2+0.6*{ease})' if mode == '向左平移' else f'(0.8-0.6*{ease})'
        x = f'(iw-iw/zoom)*{fraction}'
    elif mode == '静止':
        zoom = '1'
    else:
        raise ValueError('不支持的照片运动。')
    return (f"scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop=2160:3840,zoompan=z='{zoom}':x='{x}':"
            f"y='(ih-ih/zoom)*{vertical_focus:.6f}':d={frames}:s={width}x{height}:fps={fps},"
            'setsar=1,format=yuv420p')


def transition_slot(previous, current, target, effect, seconds, transition, cancel):
    """One fixed-length slot. Overlap the prior tail with the current head.

    Pre-rendering two-input slots bounds memory regardless of audio duration and
    also transitions seamlessly from the last photo back to the first photo.
    """
    graph = (f'[0:v]trim=start={seconds:.6f}:duration={transition:.6f},'
             'setpts=PTS-STARTPTS,fps=30,settb=AVTB[a];'
             f'[1:v]trim=duration={seconds:.6f},setpts=PTS-STARTPTS,fps=30,settb=AVTB[b];')
    if effect == 'microfocus':
        # Defocus peaks at the midpoint, then returns to the original sharp image.
        # The blur layer never covers subtitles: captions are added after rendering.
        # Frame-level opacity commands keep blending fast on a CPU; no trigonometry per pixel.
        steps = round(transition*30)
        commands = ';'.join(
            f'{j/30:.6f} blend@focus all_opacity '
            f'{math.sin(math.pi*j/max(1,steps))**2:.6f}'
            for j in range(steps+1))
        graph += (f'[a][b]xfade=transition=fade:duration={transition:.6f}:offset=0,'
                  'split[sharp][base];[base]gblur=sigma=2.5:steps=2,'
                  f"sendcmd=c='{commands}'[soft];"
                  '[soft][sharp]blend@focus=all_mode=normal:all_opacity=0,'
                  'format=yuv420p[v]')
    else:
        graph += (f'[a][b]xfade=transition={effect}:duration={transition:.6f}:offset=0,'
                  'format=yuv420p[v]')
    execute(['-i', str(previous), '-i', str(current), '-filter_complex_threads', '1',
             '-filter_complex', graph, '-map', '[v]', '-an', '-t', f'{seconds:.6f}',
             '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '19',
             '-r', '30', str(target)], cancel)


def settings():
    try:
        saved = {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding='utf-8'))}
        saved['transition']=normalize_transition(saved['transition'])
        if saved['transition'] not in TRANSITIONS:saved['transition']='智能匹配'
        return saved
    except (OSError, ValueError):
        return DEFAULTS.copy()


def natural_key(path):
    return [int(x) if x.isdigit() else x.casefold()
            for x in re.split(r'(\d+)', path.name)]


class Cancelled(Exception):
    pass


def execute(args, cancel, progress=None, total=1):
    """Drain both output streams to disk; no pipe deadlock on long renders."""
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen([str(FFMPEG), '-hide_banner', '-nostdin',
                                 '-loglevel', 'error', '-y', *args],
                                stdout=subprocess.PIPE, stderr=errors,
                                text=True, encoding='utf-8', errors='replace',
                                creationflags=NO_WINDOW)
        lines = queue.Queue()

        def read():
            for line in proc.stdout:
                lines.put(line.strip())

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while proc.poll() is None:
                if cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise Cancelled()
                try:
                    line = lines.get(timeout=0.1)
                    if progress and line.startswith('out_time_us='):
                        progress(min(1, max(0, int(line.split('=')[1]) / 1e6 / total)))
                except (queue.Empty, ValueError):
                    pass
            if proc.returncode:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', 'replace')[-3000:])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reader.join(timeout=1)
            proc.stdout.close()


def audio_duration(audio):
    result = subprocess.run([str(FFMPEG), '-hide_banner', '-i', str(audio)],
                            capture_output=True, encoding='utf-8', errors='replace',
                            creationflags=NO_WINDOW)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr)
    if not match or 'Audio:' not in result.stderr:
        raise ValueError('无法读取音频，请换用正常的 WAV、MP3 或 M4A 文件。')
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s


def render(config, report, cancel):
    config = {**DEFAULTS, **config}
    config['transition'] = normalize_transition(config['transition'])
    audio, pictures, out = (Path(config[k]).expanduser().resolve()
                            for k in ('audio', 'pictures', 'output'))
    if not FFMPEG.is_file():
        raise ValueError('缺少 ffmpeg.exe，请保留程序文件夹的完整内容。')
    if not audio.is_file() or not pictures.is_dir():
        raise ValueError('请先选择有效的配音文件和图片文件夹。')
    seconds = float(config['seconds'])
    if not math.isfinite(seconds) or not 1 <= seconds <= 30:
        raise ValueError('每张图片停留时间请填写 1～30 秒。')
    if config['transition'] not in TRANSITIONS or config['motion'] not in MOTIONS:
        raise ValueError('请从列表选择转场方式和照片运动。')
    if config['strength'] not in ('轻柔', '明显'):
        raise ValueError('请选择有效的运动幅度。')
    vertical_focus = config.get('vertical_focus')
    if vertical_focus is None:
        try:
            vertical_focus = json.loads((pictures/'取景参数.json').read_text(encoding='utf-8'))['vertical_focus']
        except (OSError, ValueError, KeyError):
            vertical_focus = {}
    if not isinstance(vertical_focus, dict) or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
            for v in vertical_focus.values()):
        raise ValueError('图片取景参数必须是文件名到0～1数值的映射。')
    if out.suffix.lower() != '.mp4':
        raise ValueError('输出文件名请以 .mp4 结尾。')
    if out == audio:
        raise ValueError('输出路径不能与配音相同。')
    images = image_paths(pictures)
    if not images:
        raise ValueError('该文件夹里没有 PNG、JPG、WebP 或 BMP 图片。')
    duration = audio_duration(audio)
    if duration <= 0:
        raise ValueError('配音时长必须大于零。')
    report(0, '正在按相邻图片特征安排转场……')
    transition_plan = plan_transitions(images,config['transition'],cancel)
    transition = max(item['seconds'] for item in transition_plan)
    config['transition_seconds']=transition
    width, height = (720, 1280) if config['size'] == '720×1280' else (1080, 1920)
    fps = 30
    seconds = round(seconds * fps) / fps
    frames = max(2, round((seconds + transition) * fps))
    seed = config.get('random_seed', random.SystemRandom().randrange(2**32))
    rng = random.Random(seed)
    motions = []
    choices = ['缓慢推近', '缓慢拉远', '向左平移', '向右平移']
    rng.shuffle(choices)
    for i in range(len(images)):
        mode = config['motion']
        if mode == '随机搭配':
            mode = choices[i % len(choices)]
        elif mode == '推近拉远交替':
            mode = '缓慢推近' if i % 2 == 0 else '缓慢拉远'
        motions.append(mode)
    timeline = []
    out.parent.mkdir(parents=True, exist_ok=True)
    report(0, f'{len(images)} 张图，每张 {seconds:g} 秒，循环至配音结束（{duration:.2f} 秒）。')
    # Work only on a unique temporary directory; never modify source pictures/audio.
    with tempfile.TemporaryDirectory(prefix='photo-loop-', dir=out.parent) as temp:
        temp = Path(temp)
        for i, source in enumerate(images):
            if cancel.is_set():
                raise Cancelled()
            vf = motion_filter(motions[i], config['strength'], frames, width, height, fps,
                               phase_frames=round(i*seconds*fps), cycle_frames=round(len(images)*seconds*fps),
                               vertical_focus=vertical_focus.get(source.name,.45))
            execute(['-i', str(source), '-vf', vf, '-frames:v', str(frames), '-an',
                     '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '19',
                     '-progress', 'pipe:1', str(temp / f'{i:04d}.mp4')], cancel,
                    lambda p, i=i: report((i+p)/len(images)*45,
                                         f'正在处理图片 {i+1}/{len(images)}：{source.name}（{motions[i]}）'), frames/fps)
        # The first slot starts on the first image. No opening/closing black fade.
        execute(['-i', str(temp/'0000.mp4'), '-an', '-t', f'{seconds:.6f}',
                 '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '19',
                 str(temp/'first.mp4')], cancel)
        slot_files = ['first.mp4']
        timeline.append({'start': 0, 'image': images[0].name, 'transition': '无缝直切', 'motion': motions[0]})
        count = math.ceil(duration / seconds)
        for slot in range(1, count):
            previous, current = (slot-1) % len(images), slot % len(images)
            choice = transition_plan[current]
            chosen, transition_time = choice['effect'], choice['seconds']
            name = f'edge-{previous}-{current}-{chosen or "cut"}.mp4'
            target = temp/name
            if not target.exists():
                if len(images) == 1:
                    execute(['-i', str(temp/f'{current:04d}.mp4'), '-an', '-t', f'{seconds:.6f}',
                             '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '19', str(target)], cancel)
                else:
                    transition_slot(temp/f'{previous:04d}.mp4', temp/f'{current:04d}.mp4',
                                    target, chosen, seconds, transition_time, cancel)
            slot_files.append(name)
            timeline.append({'start': slot*seconds, 'image': images[current].name,
                             'transition': choice['mode'], 'transition_seconds':transition_time,
                             'transition_reason':choice['reason'], 'motion': motions[current]})
            report(45 + slot/max(1,count-1)*30, f'正在准备无黑屏转场 {slot}/{count-1}……')
        playlist = temp / 'playlist.txt'
        playlist.write_text(''.join(f"file '{name}'\n" for name in slot_files), encoding='utf-8')
        report(75, '正在循环图片并合入配音……')
        result = temp / 'result.mp4'
        execute(['-f', 'concat', '-safe', '0', '-i', str(playlist),
                 '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-t', f'{duration:.6f}',
                 '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '19',
                 '-pix_fmt', 'yuv420p', '-r', str(fps), '-c:a', 'aac', '-b:a', '192k',
                 '-ar', '48000', '-movflags', '+faststart', '-progress', 'pipe:1', str(result)],
                cancel, lambda p: report(75+p*20, '正在合成完整视频……'), duration)
        report(96, '正在检查完整视频……')
        execute(['-i', str(result), '-f', 'null', '-'], cancel)
        actual_duration = audio_duration(result)
        if abs(actual_duration - duration) > 1 / fps + .02:
            raise RuntimeError('视频时长校验失败，已有成片已保留。')
        if cancel.is_set():
            raise Cancelled()
        result.replace(out)
    record = {**config, 'output': str(out), 'audio_duration_seconds': duration,
              'actual_video_duration_seconds': actual_duration,
              'image_count': len(images), 'image_order': [p.name for p in images],
              'seconds_per_image': seconds, 'loop_count': math.ceil(duration/(seconds*len(images))),
              'width': width, 'height': height, 'fps': fps, 'watermark': False,
              'effective_transition_seconds': transition, 'random_seed': seed, 'timeline': timeline,
              'transition_plan':transition_plan,'transition_selection':'相邻图片的轻量画面相似度规则；不进行人脸身份识别',
              'effective_vertical_focus':vertical_focus,
              'motion_note': '照片镜头运动，非人物对口型；转场不插入黑帧',
              'full_decode_check': 'passed', 'created_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    out.with_suffix('.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    SETTINGS.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    report(100, f'已完成：{out}')
    return out


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('图片视频制作器')
        self.root.geometry('860x640')
        self.root.minsize(780, 600)
        self.root.option_add('*Font', ('Microsoft YaHei UI', 10))
        frame = ttk.Frame(self.root, padding=22)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text='配音 + 图片 → 本地动态视频', font=('Microsoft YaHei UI', 17, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 10))
        ttk.Label(frame, text='图片按文件名顺序循环，直到配音结束。无需联网、账号或积分。').grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 15))
        conf = settings()
        self.vars = {k: tk.StringVar(value=str(v)) for k, v in conf.items()}
        self.widgets = []
        for row, key, label in ((2, 'audio', '配音文件'), (3, 'pictures', '图片文件夹'), (4, 'output', '保存视频')):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=8)
            entry = ttk.Entry(frame, textvariable=self.vars[key])
            entry.grid(row=row, column=1, sticky='ew', pady=8)
            button = ttk.Button(frame, text='选择…', command=lambda k=key: self.choose(k))
            button.grid(row=row, column=2, padx=(8, 0))
            self.widgets.extend([entry, button])
        options = ttk.Frame(frame)
        options.grid(row=5, column=0, columnspan=3, sticky='w', pady=12)
        ttk.Label(options, text='每张停留（秒）').pack(side='left')
        spin = ttk.Spinbox(options, from_=1, to=30, increment=.5, textvariable=self.vars['seconds'], width=7)
        spin.pack(side='left', padx=(8, 28))
        ttk.Label(options, text='竖屏清晰度').pack(side='left')
        combo = ttk.Combobox(options, textvariable=self.vars['size'], values=['1080×1920', '720×1280'], state='readonly', width=14)
        combo.pack(side='left', padx=8)
        self.widgets.extend([spin, combo])
        effects = ttk.Frame(frame)
        effects.grid(row=6, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Label(effects, text='换图转场').pack(side='left')
        transitions = ttk.Combobox(effects, textvariable=self.vars['transition'], values=list(TRANSITIONS), state='readonly', width=19)
        transitions.pack(side='left', padx=(8, 22))
        ttk.Label(effects, text='B：0.20秒　D：0.10秒').pack(side='left')
        motions = ttk.Frame(frame)
        motions.grid(row=7, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Label(motions, text='照片运动').pack(side='left')
        motion = ttk.Combobox(motions, textvariable=self.vars['motion'], values=MOTIONS, state='readonly', width=14)
        motion.pack(side='left', padx=(8, 22))
        ttk.Label(motions, text='运动幅度').pack(side='left')
        strength = ttk.Combobox(motions, textvariable=self.vars['strength'], values=['轻柔', '明显'], state='readonly', width=10)
        strength.pack(side='left', padx=8)
        self.widgets.extend([transitions, motion, strength])
        hint = ('智能匹配：画面变化小优先轻柔柔焦，主体或场景变化大优先平滑推移，循环边界也单独判断。\n'
                '可先点“查看转场安排”检查结果，不生成视频。字幕在画面效果之后单独叠加。\n'
                '默认3秒换图，4张约12秒一轮。自动居中裁成9:16，建议使用主体居中的竖图。')
        ttk.Label(frame, text=hint, wraplength=800).grid(row=8, column=0, columnspan=3, sticky='w', pady=(8, 0))
        self.bar = ttk.Progressbar(frame, maximum=100)
        self.bar.grid(row=9, column=0, columnspan=3, sticky='ew', pady=(16, 8))
        self.status = tk.StringVar(value='准备就绪。使用已有配音，不会重新生成或改变音色。')
        ttk.Label(frame, textvariable=self.status, wraplength=800).grid(row=10, column=0, columnspan=3, sticky='w')
        actions = ttk.Frame(frame)
        actions.grid(row=11, column=0, columnspan=3, sticky='e', pady=18)
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.running = False
        self.plan_button = ttk.Button(actions, text='查看转场安排', command=self.preview_plan)
        self.plan_button.pack(side='left', padx=6)
        self.start_button = ttk.Button(actions, text='开始生成视频', command=self.start)
        self.start_button.pack(side='left', padx=6)
        self.stop_button = ttk.Button(actions, text='取消', command=self.cancel.set, state='disabled')
        self.stop_button.pack(side='left')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(150, self.poll)

    def choose(self, key):
        if key == 'pictures':
            value = filedialog.askdirectory(title='选择一组图片的文件夹', initialdir=self.vars[key].get())
        elif key == 'audio':
            value = filedialog.askopenfilename(title='选择已经配好的语音', filetypes=[('配音', '*.wav *.mp3 *.m4a *.flac *.aac *.ogg'), ('所有文件', '*.*')])
        else:
            value = filedialog.asksaveasfilename(title='保存视频', defaultextension='.mp4', initialfile=Path(self.vars[key].get()).name, filetypes=[('MP4视频', '*.mp4')])
        if value:
            self.vars[key].set(value)

    def start(self):
        config = {k: v.get() for k, v in self.vars.items()}
        if Path(config['output']).exists() and not messagebox.askyesno('同名视频已存在', '是否用本次生成的视频替换它？原始图片和配音不会更改。'):
            return
        self.set_busy(True)
        self.cancel.clear()
        self.bar['value'] = 0
        self.status.set('开始准备……')

        def work():
            try:
                out = render(config, lambda p, msg: self.events.put(('progress', p, msg)), self.cancel)
                self.events.put(('done', str(out)))
            except Cancelled:
                self.events.put(('cancelled',))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def set_busy(self, busy):
        self.running=busy
        self.start_button.configure(state='disabled' if busy else 'normal')
        self.plan_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        for widget in self.widgets:
            widget.configure(state='disabled' if busy else ('readonly' if isinstance(widget,ttk.Combobox) else 'normal'))

    def preview_plan(self):
        folder=Path(self.vars['pictures'].get())
        if not folder.is_dir():
            show_error('无法查看','请先选择有效的图片文件夹。')
            return
        selection=self.vars['transition'].get()
        self.cancel.clear()
        self.set_busy(True)
        self.status.set('正在分析相邻图片；不会生成视频……')
        def work():
            try:
                plan=plan_transitions(image_paths(folder),selection,self.cancel)
                (ROOT.parents[2]/'cache/transition_plan.json').write_text(json.dumps({'folder':str(folder),'selection':selection,'plan':plan},ensure_ascii=False,indent=2),encoding='utf-8')
                self.events.put(('plan',plan))
            except Cancelled:self.events.put(('cancelled',))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def show_plan(self,plan):
        window=tk.Toplevel(self.root)
        window.title('转场安排 · 未生成视频')
        box=ScrolledText(window,width=82,height=19,font=('Microsoft YaHei UI',11),wrap='word',padx=16,pady=14)
        box.pack(fill='both',expand=True)
        lines=['按相邻图片的画面相似度判断，可手动覆盖为统一B或D。\n']
        for item in plan[1:]+plan[:1]:
            lines.append(f"{item['from']} → {item['to']}　{item['mode']}（{item['seconds']:.2f}秒）\n{item['reason']}\n")
        box.insert('1.0','\n'.join(lines))
        box.configure(state='disabled')

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'progress':
                    self.bar['value'] = event[1]
                    self.status.set(event[2])
                else:
                    self.set_busy(False)
                    if event[0] == 'done':
                        messagebox.showinfo('视频已完成', '视频已保存：\n' + event[1])
                    elif event[0] == 'cancelled':
                        self.status.set('已取消，原始素材和已有成片均保留。')
                    elif event[0] == 'plan':
                        self.status.set('转场安排已就绪，未生成视频。')
                        self.show_plan(event[1])
                    else:
                        self.status.set('生成未完成，请检查提示。')
                        show_error('生成未完成', event[1])
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def close(self):
        if self.running:
            messagebox.showinfo('正在制作', '请先点击“取消”，等待停止后再关闭窗口。')
        else:
            self.root.destroy()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='本地照片循环视频制作器')
    parser.add_argument('--render', action='store_true', help='按已保存设置直接生成')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--plan', action='store_true', help='只分析相邻图片的转场安排，不生成视频')
    args = parser.parse_args()
    if args.plan:
        conf=json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else settings()
        print(json.dumps(plan_transitions(image_paths(Path(conf['pictures'])),conf.get('transition','智能匹配')),ensure_ascii=False,indent=2))
    elif args.render:
        conf = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else settings()
        last = [-1, '']
        def log(percent, message):
            mark = int(percent // 5) * 5
            if mark != last[0] or message != last[1]:
                print(f'{percent:.0f}% {message}', flush=True)
                last[:] = [mark, message]
        render(conf, log, threading.Event())
    else:
        App().root.mainloop()
