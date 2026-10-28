"""Bounded error summaries; full child output remains in its log."""
import re


def failure_message(lines,label,logfile):
    candidates=[]
    for line in lines:
        line=line.strip()
        if re.match(r'^(?:[\w.]+(?:Error|Exception|Rejected)|Exception):\s*',line):
            candidates.append(line)
    useful=[s for s in candidates if not any(x in s for x in ('本地程序未完成，查看日志','人物制作未完成，请查看'))]
    native=[str(s).strip() for s in lines if re.search(r'error|fatal|failed|assert|无法|缺少|不足',str(s),re.I) and 'Warning' not in str(s)]
    detail=(useful or candidates or native or ['子程序未正常结束'])[-1]
    return f'{label}：{detail[:1400]}\n日志：{logfile}'
