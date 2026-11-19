"""Carry review notices with media and give the user a concrete next step."""

import json
import uuid
from pathlib import Path


def video_warnings(video):
    record = Path(video).with_suffix('.review.json')
    if not record.is_file():
        return []
    try:
        warnings = json.loads(record.read_text(encoding='utf-8-sig'))
        if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            raise ValueError('invalid warning list')
        return warnings
    except (OSError, ValueError):
        return ['成片附带的复核记录无法读取，请播放成片并重新运行检查。']


def save_video_warnings(video, warnings):
    items = list(dict.fromkeys(warnings))
    if items:
        record = Path(video).with_suffix('.review.json')
        pending = record.with_name(record.name + '.' + uuid.uuid4().hex + '.pending')
        try:
            pending.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
            pending.replace(record)
        finally:
            pending.unlink(missing_ok=True)


def copy_video_review(source, target, log=print):
    from portrait.speech_constraints import copy_provenance

    warnings = video_warnings(source) + video_warnings(target)
    try:
        copy_provenance(source, target)
    except (OSError, ValueError, TypeError) as exc:
        log('口型检查记录无法复制：' + str(exc))
        warnings.append('口型检查记录无法验证，请带声音检查嘴型。若不同步，请核对配音和正文后重做人物母版。')
    save_video_warnings(target, warnings)
    return warnings


def review_message(warnings, report=None):
    visible = list(dict.fromkeys(warnings))
    text = '\n'.join('• ' + item for item in visible[:3])
    if len(visible) > 3:
        text += '\n其余问题见检查报告。'
    text += '\n\n请先带声音播放成片。若不可用，请按提示修正素材，再重做对应步骤。'
    if report:
        text += '\n检查报告：' + str(report)
    return text
