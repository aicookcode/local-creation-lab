"""Verified raw TTS chunks, before merge and unchanged voice postprocessing.

Only completed chunks are reusable. The caller supplies a key that includes
the full model fingerprint, voice, generation code and exact text/settings.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import uuid
import wave


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def key_for(recipe, text):
    data = json.dumps({'schema': 1, 'recipe': recipe, 'text': text},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def wav_data(path):
    with wave.open(str(path)) as audio:
        if audio.getcomptype() != 'NONE' or not audio.getnframes():
            raise ValueError('分段不是有效的PCM音频。')
        expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
        # A WAV header alone is insufficient: read bounded blocks to detect a
        # truncated file before accepting or caching it.
        read = 0
        while True:
            block = audio.readframes(65536)
            if not block:
                break
            read += len(block)
        if read != expected:
            raise ValueError('分段音频不完整。')
        return {'frames': audio.getnframes(), 'rate': audio.getframerate(),
                'channels': audio.getnchannels(), 'width': audio.getsampwidth(),
                'seconds': audio.getnframes() / audio.getframerate()}


class SegmentCache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def paths(self, key):
        folder = self.directory / key[:2]
        return folder / (key + '.wav'), folder / (key + '.json')

    def restore(self, key, target):
        audio, receipt = self.paths(key)
        try:
            info = json.loads(receipt.read_text(encoding='utf-8'))
            if not isinstance(info, dict) or not isinstance(info.get('record'), dict):
                return None
            if info.get('key') != key or info.get('sha256') != sha(audio):
                return None
            if info.get('wav') != wav_data(audio):
                return None
            target = Path(target)
            pending = target.with_name(target.name + '.' + uuid.uuid4().hex + '.partial')
            try:
                shutil.copyfile(audio, pending)
                os.replace(pending, target)
            finally:
                pending.unlink(missing_ok=True)
            return info['record']
        except (OSError, ValueError, KeyError, TypeError, wave.Error, EOFError):
            return None

    def store(self, key, source, record):
        audio, receipt = self.paths(key)
        audio.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(audio.parent).free < Path(source).stat().st_size + 256*1024*1024:
            raise OSError('缓存盘空间不足，本次不再复制语音缓存。')
        info = {'key': key, 'sha256': sha(source), 'wav': wav_data(source),
                'record': record}
        pending = audio.with_name(audio.name + '.' + uuid.uuid4().hex + '.partial')
        meta = receipt.with_name(receipt.name + '.' + uuid.uuid4().hex + '.partial')
        try:
            shutil.copyfile(source, pending)
            meta.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
            os.replace(pending, audio)
            os.replace(meta, receipt)
        finally:
            pending.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
