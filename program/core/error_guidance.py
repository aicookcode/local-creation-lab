"""User-facing error explanations with ranked causes and next actions."""
import re
from tkinter import messagebox

def _text(value):
 return re.sub(r'\s+',' ',str(value or '').strip())

def _ranked(title,detail):
 text=(str(title or '')+' '+str(detail or '')).lower()
 if '发声口型' in text or 'sustained_voice_closed' in text or 'silence_motion' in text:
  return [
   ('配音正文与实际朗读不一致','先播放原始配音，对照正文检查漏字、尾音、停顿和多读。重新生成一份与正文逐字一致的配音。'),
   ('音频含背景声、底噪或音量过低','使用干净的单人人声；不要把音乐、音效或过长尾音作为口型输入。'),
   ('筛查记录发现静音嘴动或持续发声闭口','在“缓存/原图口播/画面”下找到对应 screening.json，查看具体时间段；先用 7 秒短样复验。'),
  ]
 if any(k in text for k in ('模型尚未','模型缺失','模型未安装','模型校验','模型下载','找不到.*python','ffmpeg')):
  return [
   ('对应模型或运行环境文件缺失','先打开“任务日志与组件检查”，确认模型组、Python、FFmpeg 和字体是否存在。'),
   ('模型版本、文件名或 SHA256 与清单不一致','不要混用不同版本模型；恢复同一发行版的模型，或按模型清单重新安装。'),
   ('模型下载中断或磁盘空间不足','检查网络和磁盘空间，删除对应 .partial 临时文件后重试。'),
  ]
 if any(k in text for k in ('人物照片','人脸','照片检测','张嘴参考','口腔参考','landmarks')):
  return [
   ('照片不是清晰的单人正脸或嘴部被遮挡','换一张单人、正脸、光线均匀、嘴部无遮挡的照片。'),
   ('人脸在画面中太小或背景边缘混乱','使用近景或半身照片，让脸部占画面更大比例。'),
   ('张嘴参考图与人物照片角度或人物不一致','提交同一个人、同一角度、嘴部清晰的张嘴参考图；否则选择自动参考。'),
  ]
 if any(k in text for k in ('字幕','srt','原稿','正文','对齐','识别')):
  return [
   ('字幕正文与配音正文不一致','确认字幕使用的 TXT 是本次音频实际朗读的原稿，不要把新正文套到旧配音。'),
   ('字幕时间倒序、重叠或超出音频','重新生成字幕初稿，再逐句校对时间；不要手动保留超出音频末尾的时间段。'),
   ('字幕模型或校准缓存不完整','检查字幕模型和对应缓存校验，必要时只重新生成字幕校准。'),
  ]
 if any(k in text for k in ('导出','输出','磁盘','同名','fileexists','空间不足','保存')):
  return [
   ('输出目录不可写、空间不足或文件被占用','换到有空间且可写的目录，关闭正在播放或占用成品的程序。'),
   ('目标文件已存在且内容不同','换一个新的文件名；工作台不会覆盖已有不同内容的文件。'),
   ('成片检查尚未通过','先打开检查报告，修复解码、音频、字幕或人工校对项，再重新导出。'),
  ]
 if any(k in text for k in ('sha','校验','cache','hash','signature')):
  return [
   ('输入素材、模型或程序版本发生变化','不要混用旧缓存；重新提交当前照片、配音和正文，让对应阶段重新计算。'),
   ('缓存文件损坏或未完整写入','删除对应损坏缓存或 .partial 文件，保留原始素材后重试。'),
   ('任务使用了另一台设备的路径或旧版本文件','在当前工作台内重新导入素材和项目，确认所有路径属于当前工作台。'),
  ]
 if any(k in text for k in ('权限','permission','access is denied','拒绝访问')):
  return [
   ('文件或目录没有写入权限','把项目和导出目录放到当前用户可写的本地目录，避免系统受保护目录。'),
   ('文件正在被其他程序占用','关闭播放器、同步工具和其他工作台窗口后重试。'),
  ]
 if any(k in text for k in ('timeout','超时','内存','memory','out of')):
  return [
   ('本阶段耗时较长或内存压力较大','先用短正文、7 秒短样测试，关闭其他模型和大型程序，等待后台阶段结束。'),
   ('输入素材过大或任务同时运行','减少本次输入长度，保持重模型任务串行，不要重复点击执行。'),
  ]
 return [
  ('输入素材、正文或当前步骤的前置条件未满足','回到当前功能页，确认音频、正文、照片和参数都已提交并保存。'),
  ('程序、模型或缓存版本不一致','查看任务日志中的第一处异常，不要只看最后一行；必要时从当前阶段重新生成。'),
  ('磁盘、权限或其他程序占用影响了本次写入','确认目录可写、磁盘有空间，并关闭占用素材的播放器或同步程序。'),
 ]

def explain(title,detail):
 raw=_text(detail)
 if len(raw)>1600:raw=raw[:1600]+'……'
 lines=[f'原始提示：{raw or "未提供具体错误文本。"}','', '最可能的原因（按相关性排序）：']
 for i,(cause,action) in enumerate(_ranked(title,raw),1):lines.append(f'{i}. {cause}\n   先做：{action}')
 lines += ['', '建议顺序：先处理第 1 项；如果仍失败，再按日志中的第一处异常检查后续项目。完整堆栈和任务日志仍保留在日志目录。']
 return '\n'.join(lines)

def show_error(title,detail,parent=None):
 return messagebox.showerror(title,explain(title,detail),parent=parent)

