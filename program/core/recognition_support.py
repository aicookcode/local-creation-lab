"""Pinned, optional offline ASR review model with lazy installation."""
import json,hashlib
from studio_paths import MODELS


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(4*1024*1024):digest.update(block)
    return digest.hexdigest()


def review_model(install=True):
    if install:
        from model_manager import ensure
        ensure('subtitle_small')
    folder=MODELS/'subtitle/faster-whisper-small'
    manifest=folder/'integrity.json'
    record=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(record,dict) or not isinstance(record.get('files'),dict):raise ValueError('备用模型校验清单结构无效')
    required=('config.json','model.bin','tokenizer.json','vocabulary.txt')
    for name in required:
        if record.get('files',{}).get(name)!=sha(folder/name):raise ValueError('备用识别模型校验失败：'+name)
    return folder,{'manifest_sha256':sha(manifest),'files':{name:record['files'][name] for name in required}}


def review_descriptor():
    try:return review_model(install=False)[1]
    except (OSError,ValueError,KeyError,TypeError):return {'available':False}
