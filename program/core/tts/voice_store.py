"""Local immutable reference-voice profiles; no training or cloud requests."""
from contextlib import contextmanager
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import uuid
import wave
import zipfile

ROOT = Path(os.environ.get('ZMYG_MODEL_HOME', Path(__file__).resolve().parent))
LIBRARY = Path(os.environ.get('ZMYG_LIBRARY', ROOT / 'voice_library'))
def reference_path(value):
    from studio_paths import USER_RESOURCES
    path=Path(value)
    if not path.is_absolute() and path.parts and path.parts[0]=='profiles':return USER_RESOURCES/'reference_samples'/Path(*path.parts[1:])
    return ROOT/path
BUILTINS = {'qwen': 'qwen-primary', 'zipvoice': 'zip-identity-distill'}


def ffmpeg_path():
    configured = os.environ.get('ZMYG_FFMPEG')
    if configured and Path(configured).is_file():
        return Path(configured)
    for path in [ROOT / 'bin/ffmpeg.exe', ROOT.parent / '照片循环视频制作器/ffmpeg.exe']:
        if path.is_file():
            return path
    raise FileNotFoundError('找不到 FFmpeg，请先安装配音工具的运行环境。')


def python_path(engine):
    import sys
    portable = os.environ.get('ZMYG_PYTHON')
    if portable and Path(portable).is_file():return Path(portable)
    candidates = [ROOT / '.venv/Scripts/python.exe']
    if engine == 'zipvoice':
        candidates.append(ROOT / 'candidates/zipvoice-int8/venv/Scripts/python.exe')
    else:
        candidates.append(Path(sys.executable).with_name('python.exe'))
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError('没有找到对应模型的 Python 环境，请先完成安装。')


def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


@contextmanager
def library_lock(library):
    import msvcrt
    library = Path(library)
    library.mkdir(parents=True, exist_ok=True)
    with (library / '.lock').open('a+b') as f:
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        deadline = time.monotonic() + 15
        while True:
            try:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('音色库正被另一窗口写入，请稍后重试。')
                time.sleep(.1)
        try:
            yield
        finally:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def index(library=LIBRARY):
    p = Path(library) / 'index.json'
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    return {'schema': 1, 'default_id': None, 'voices': []}


def list_voices(library=LIBRARY):
    return index(library)['voices']


def profile_path(voice_id, library=LIBRARY):
    if len(voice_id) != 32 or any(c not in '0123456789abcdef' for c in voice_id):
        raise ValueError('音色 ID 无效。')
    return Path(library) / 'voices' / voice_id / 'profile.json'


def list_deleted_voices(library=LIBRARY):
    active = {v['id'] for v in list_voices(library)}
    records = []
    for path in (Path(library) / '回收站').glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            entry = record['entry']; voice_id = entry['id']
            profile_path(voice_id, library)
            if not all(isinstance(entry.get(k),str) for k in ('name','engine','profile_sha256')) or not isinstance(record.get('deleted_at'),str):continue
            if path.stem == voice_id and voice_id not in active:
                records.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(records, key=lambda r:r.get('deleted_at', ''), reverse=True)


def delete_voice(voice_id, library=LIBRARY):
    """Remove from the active index; keep immutable assets and existing works."""
    profile_path(voice_id, library)
    with library_lock(library):
        data = index(library)
        entry = next((v for v in data['voices'] if v['id'] == voice_id), None)
        record_path = Path(library) / '回收站' / (voice_id + '.json')
        if entry is None:
            if record_path.is_file():
                return json.loads(record_path.read_text(encoding='utf-8'))
            raise ValueError('此音色已不在列表中，请刷新列表。')
        record = {'schema':1, 'entry':copy.deepcopy(entry), 'deleted_at':now()}
        # Write recovery metadata first. If the index write fails, the voice is
        # still active and list_deleted_voices hides this uncommitted record.
        atomic_json(record_path, record)
        data['voices'] = [v for v in data['voices'] if v['id'] != voice_id]
        if data.get('default_id') == voice_id:data['default_id'] = None
        atomic_json(Path(library) / 'index.json', data)
        return record


def restore_voice(voice_id, library=LIBRARY):
    path = profile_path(voice_id, library)
    with library_lock(library):
        data = index(library)
        existing = next((v for v in data['voices'] if v['id'] == voice_id), None)
        if existing:return existing
        record = json.loads((Path(library) / '回收站' / (voice_id + '.json')).read_text(encoding='utf-8'))
        entry = record['entry']
        if entry['id'] != voice_id:raise ValueError('恢复记录与音色不一致。')
        load_profile(path, verify_models=False, expected_profile_hash=entry['profile_sha256'])
        data['voices'].append(entry)
        atomic_json(Path(library) / 'index.json', data)
        return entry


def validate_settings(engine, settings):
    if engine not in BUILTINS:
        raise ValueError('不支持此模型。')
    cfg = copy.deepcopy(settings)
    for key in ['reference', 'reference_text']:
        cfg.pop(key, None)
    cfg['engine'] = engine
    ranges = {'default_speed': (.85, 1.15), 'pitch_semitones': (-3, 3), 'chunk_limit': (10, 160),
              'gap_seconds': (0, 1), 'temperature': (.01, 2), 'top_p': (.01, 1),
              'repeat_penalty': (.8, 2), 'seed': (0, 2**32 - 1), 'max_frames': (50, 1000),
              'steps': (1, 64), 'guidance_scale': (0, 10), 'threads': (1, 16),
              'minimum_chars': (1, 1000), 'maximum_chars': (1, 2000),
              'denoise_nr': (0, 30), 'denoise_highpass_hz': (0, 1000), 'denoise_lowpass_hz': (1000, 12000)}
    for key, (lo, hi) in ranges.items():
        if key in cfg:
            val = cfg[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not lo <= val <= hi:
                raise ValueError('参数范围不正确：' + key)
    for key in ['seed', 'steps', 'threads', 'chunk_limit', 'max_frames']:
        if key in cfg and int(cfg[key]) != cfg[key]:
            raise ValueError('此参数必须为整数：' + key)
    if 'light_denoise' in cfg and not isinstance(cfg['light_denoise'], bool):
        raise ValueError('light_denoise 必须为 true 或 false。')
    if engine == 'zipvoice':
        directory = Path(cfg.get('model_directory', ''))
        candidate_root = (ROOT / 'candidates').resolve()
        if directory.is_absolute() or not (ROOT / directory).resolve().is_relative_to(candidate_root):
            raise ValueError('模型目录必须在当前工具的 candidates 文件夹内。')
        for key in ['encoder_file', 'decoder_file']:
            if key in cfg and Path(cfg[key]).name != cfg[key]:
                raise ValueError('模型文件名不能包含目录。')
    return cfg


def model_files(engine, cfg, ensure_models=True):
    if ensure_models:
        from model_manager import ensure
        ensure('voice_qwen' if engine == 'qwen' else 'voice_zipvoice')
    if engine == 'qwen':
        base = ROOT / 'candidates/qwen06-llamacpp'
        runtime=Path(os.environ.get('ZMYG_QWEN_RUNTIME',base/'runtime'))
        paths = [base/'model.gguf',base/'mmproj.gguf']+[runtime/name for name in ['llama-tts.exe','llama.dll','mtmd.dll','ggml-base.dll','ggml.dll']]
    else:
        base = ROOT / cfg['model_directory']
        encoder = base / cfg['encoder_file'] if cfg.get('encoder_file') else next(base.glob('encoder*.onnx'), base / 'encoder.onnx')
        decoder = base / cfg['decoder_file'] if cfg.get('decoder_file') else next(base.glob('decoder*.onnx'), base / 'decoder.onnx')
        paths = [encoder, decoder, base / 'tokens.txt', base / 'lexicon.txt',
                 ROOT / 'candidates/zipvoice-int8/vocos_24khz.onnx']
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError('模型尚未安装完整：' + str(p))
    return paths


def fingerprint(engine, cfg):
    result={}
    for p in model_files(engine,cfg):
        try:key=p.relative_to(ROOT).as_posix()
        except ValueError:
            # Keep existing immutable profiles' logical component IDs while
            # hashing the same runtime bytes in the new program directory.
            runtime=Path(os.environ.get('ZMYG_QWEN_RUNTIME','')).resolve()
            if engine!='qwen' or p.resolve().parent!=runtime:raise
            key='candidates/qwen06-llamacpp/runtime/'+p.name
        result[key]=sha256(p)
    return result


def wav_info(path):
    with wave.open(str(path)) as f:
        if (f.getnchannels(), f.getframerate(), f.getsampwidth()) != (1, 24000, 2):
            raise ValueError('音色库参考必须是 24kHz 单声道 16 位 WAV。')
        frames = f.getnframes()
        values = struct.iter_unpack('<h', f.readframes(frames))
        total = 0; peak = 0; clipped = 0
        for (sample,) in values:
            total += sample * sample; peak = max(peak, abs(sample))
            clipped += sample in (-32768, 32767)
    seconds = frames / 24000
    if not 2 <= seconds <= 30:
        raise ValueError('参考片段须为 2–30 秒；建议先选一段 5–10 秒的完整自然句。')
    rms = math.sqrt(total / max(1, frames)) / 32768
    if rms < .001:
        raise ValueError('参考录音接近静音，请重新选择有清晰人声的片段。')
    return {'seconds': seconds, 'sample_rate': 24000, 'channels': 1, 'sample_width': 2,
            'rms_dbfs': round(20 * math.log10(rms), 2), 'peak': peak / 32768,
            'clipped_samples': clipped}


def create_voice(name, engine, reference, transcript, settings, *, start=0., duration=8.,
                 tags='', note='', copy_exact=False, accepted=False, library=LIBRARY, builtin_key=None):
    name = str(name).strip()
    if not name or len(name) > 80:
        raise ValueError('请填写 1–80 字的音色名称。')
    transcript = str(transcript).strip()
    if engine == 'zipvoice' and not transcript:
        raise ValueError('ZipVoice 必须填写与截取片段完全一致的参考文字。')
    if len(transcript) > 3000:
        raise ValueError('参考文字过长。')
    if not math.isfinite(start) or start < 0 or not math.isfinite(duration) or not 2 <= duration <= 30:
        raise ValueError('开始秒数须大于等于 0，片段长度须为 2–30 秒。')
    cfg = validate_settings(engine, settings)
    model_hashes = fingerprint(engine, cfg)
    voice_id = uuid.uuid4().hex
    dest = profile_path(voice_id, library).parent
    dest.mkdir(parents=True, exist_ok=False)
    try:
        source = Path(reference).resolve(strict=True)
        ref = dest / 'reference.wav'
        if copy_exact:
            shutil.copy2(source, ref)
        else:
            subprocess.run([str(ffmpeg_path()), '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-ss', str(start), '-i', str(source), '-t', str(duration), '-map', '0:a:0',
                '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', str(ref)], check=True,
                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = wav_info(ref)
        if not copy_exact and info['seconds'] < duration - .08:
            raise ValueError('录音剩余长度不足，无法截取所填时长；请检查开始秒数和长度。')
        (dest / 'reference.txt').write_text(transcript, encoding='utf-8')
        profile = {'schema': 1, 'id': voice_id, 'name': name, 'engine': engine, 'created_at': now(),
                   'tags': tags.strip(), 'note': note.strip(), 'settings': cfg,
                   'settings_sha256': json_hash(cfg), 'model_sha256': model_hashes,
                   'reference_sha256': sha256(ref), 'transcript_sha256': sha256(dest / 'reference.txt'),
                   'reference_info': info, 'import_selection': {'start': start, 'requested_seconds': duration,
                   'copy_exact': copy_exact}, 'fixed_reference': True,
                   'license_note': ('当前 Emilia 权重仅限非商业测试。' if engine == 'zipvoice' else
                                    '模型许可与参考声音的使用授权须分别确认。'),
                   'mechanism': '参考条件生成，不训练或修改基础模型；文件校验用于检测改动，不保证所有新文本的韵律相同。'}
        atomic_json(dest / 'profile.json', profile)
        entry = {'id': voice_id, 'name': name, 'engine': engine, 'accepted': accepted,
                 'builtin_key': builtin_key, 'profile_sha256': sha256(dest / 'profile.json')}
        with library_lock(library):
            data = index(library); data['voices'].append(entry)
            if data['default_id'] is None:
                data['default_id'] = voice_id
            atomic_json(Path(library) / 'index.json', data)
        return entry
    except Exception:
        # Failed drafts are retained for diagnosis and never registered as voices.
        (dest / 'FAILED.txt').write_text('未完成的音色草稿，未加入音色库。', encoding='utf-8')
        raise


def load_profile(path, *, verify_models=True, expected_profile_hash=None):
    path = Path(path).resolve(strict=True)
    if expected_profile_hash and sha256(path) != expected_profile_hash:
        raise ValueError('音色档案已被修改。请恢复备份，或另存为新版本。')
    profile = json.loads(path.read_text(encoding='utf-8'))
    cfg = validate_settings(profile['engine'], profile['settings'])
    if json_hash(cfg) != profile['settings_sha256']:
        raise ValueError('音色参数校验失败，已停止生成。')
    for filename, key in [('reference.wav', 'reference_sha256'), ('reference.txt', 'transcript_sha256')]:
        if sha256(path.parent / filename) != profile[key]:
            raise ValueError('参考录音或文字发生变化，已停止生成：' + filename)
    wav_info(path.parent / 'reference.wav')
    if profile['engine'] == 'zipvoice' and not (path.parent / 'reference.txt').read_text(encoding='utf-8').strip():
        raise ValueError('ZipVoice 的参考文字为空。')
    if verify_models and fingerprint(profile['engine'], cfg) != profile['model_sha256']:
        raise ValueError('关键模型或运行器文件与保存音色时不同。请恢复原版本或另建新音色档案。')
    cfg['reference'] = str(path.parent / 'reference.wav')
    cfg['reference_text'] = str(path.parent / 'reference.txt')
    return profile, cfg


def get_voice(voice_id, library=LIBRARY, verify_models=True):
    entry = next((v for v in list_voices(library) if v['id'] == voice_id), None)
    if not entry:
        raise ValueError('音色不在当前音色库中。')
    path = profile_path(voice_id, library)
    profile, cfg = load_profile(path, verify_models=verify_models, expected_profile_hash=entry['profile_sha256'])
    return entry, profile, cfg


def set_default(voice_id, library=LIBRARY):
    get_voice(voice_id, library, verify_models=False)
    with library_lock(library):
        data = index(library); data['default_id'] = voice_id
        atomic_json(Path(library) / 'index.json', data)


def mark_accepted(voice_id, audio, library=LIBRARY):
    manifest = json.loads(Path(audio).with_suffix('.json').read_text(encoding='utf-8'))
    if manifest.get('voice_profile', {}).get('id') != voice_id or sha256(audio) != manifest['output_sha256']:
        raise ValueError('只能认可由当前档案生成且校验通过的样音。')
    get_voice(voice_id, library, verify_models=False)
    with library_lock(library):
        data = index(library)
        for entry in data['voices']:
            if entry['id'] == voice_id:
                entry.update(accepted=True, accepted_at=now(), accepted_audio=str(Path(audio).resolve()),
                             accepted_audio_sha256=manifest['output_sha256'])
        atomic_json(Path(library) / 'index.json', data)


def bootstrap(library=LIBRARY):
    presets = json.loads(Path(__file__).with_name('tuning-presets.json').read_text(encoding='utf-8'))
    deleted_builtins = {r['entry'].get('builtin_key') for r in list_deleted_voices(library)}
    for engine, key in BUILTINS.items():
        if key not in presets or key in deleted_builtins or any(v.get('builtin_key') == key for v in list_voices(library)):
            continue
        cfg = presets[key]
        try:
            # Startup/bootstrap must stay offline and must not trigger a large
            # download. The actual generation path calls model_files() with
            # the default ensure_models=True.
            model_files(engine, cfg, ensure_models=False)
        except FileNotFoundError:
            continue
        txt = reference_path(cfg['reference_text']).read_text(encoding='utf-8') if cfg.get('reference_text') else ''
        create_voice('zhimingyouguang · ' + ('Qwen 首选' if engine == 'qwen' else 'ZipVoice 备选 A'),
                     engine, reference_path(cfg['reference']), txt, cfg, copy_exact=True, accepted=True,
                     tags='zhimingyouguang,已认可', note='从已有已认可预设原样导入，未重新截取或处理参考。',
                     library=library, builtin_key=key)
    return list_voices(library)


def export_voice(voice_id, destination, library=LIBRARY):
    entry, profile, _ = get_voice(voice_id, library, verify_models=False)
    folder = profile_path(voice_id, library).parent
    destination = Path(destination)
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as z:
        checks = {}
        for name in ['profile.json', 'reference.wav', 'reference.txt']:
            z.write(folder / name, name); checks[name] = sha256(folder / name)
        z.writestr('checksums.json', json.dumps(checks, ensure_ascii=False, indent=2))
        z.writestr('说明.txt', '固定音色备份：包含参考、文字、参数与关键文件校验，不包含基础模型或授权证明。\n导入时保留原配置，须安装匹配的模型。校验值用于检查文件变化，并非数字签名。\n')
    return destination


def import_voice(source, library=LIBRARY):
    allowed = {'profile.json', 'reference.wav', 'reference.txt', 'checksums.json', '说明.txt'}
    with zipfile.ZipFile(source) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or not set(names) <= allowed or sum(i.file_size for i in z.infolist()) > 20*1024*1024:
            raise ValueError('备份内容异常或过大，未导入。')
        required = {'profile.json', 'reference.wav', 'reference.txt', 'checksums.json'}
        if not required <= set(names):
            raise ValueError('备份缺少必要文件。')
        files = {n: z.read(n) for n in required}
    checks = json.loads(files.pop('checksums.json').decode('utf-8'))
    if any(hashlib.sha256(b).hexdigest() != checks.get(n) for n,b in files.items()):
        raise ValueError('备份文件校验失败。')
    data = json.loads(files['profile.json'].decode('utf-8'))
    voice_id = uuid.uuid4().hex; dest = profile_path(voice_id, library).parent
    dest.mkdir(parents=True, exist_ok=False)
    for n,b in files.items():
        (dest/n).write_bytes(b)
    # Check imported fixed files, settings and model fingerprints before registration.
    load_profile(dest/'profile.json')
    data.update(id=voice_id, name=data['name'] + '（导入副本）', imported_at=now())
    atomic_json(dest/'profile.json', data)
    entry = {'id':voice_id,'name':data['name'],'engine':data['engine'],'accepted':False,
             'builtin_key':None,'profile_sha256':sha256(dest/'profile.json')}
    with library_lock(library):
        ix=index(library);ix['voices'].append(entry)
        if ix['default_id'] is None:ix['default_id']=voice_id
        atomic_json(Path(library)/'index.json',ix)
    return entry
