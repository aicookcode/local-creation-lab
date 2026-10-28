"""Snapshot inputs, assemble supported API steps, and publish only checked output."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'tts'))
import copy,datetime,json,shutil,subprocess,sys,uuid,re
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.pycache_prefix=str(Path(__file__).resolve().parents[2]/'cache/pycache')
from studio_paths import PACKAGE_ROOT,PROGRAM,PROJECTS
from portable_bootstrap import pack_paths,unpack_paths
import settings_library as library

def stamp():return datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
def prepare(text,images,voice_id,route,record,*,output_dir='',mouth_reference='',name=''):
    from PIL import Image
    import voice_store as voices
    if not isinstance(text,str) or not text.strip():raise ValueError('请填写需要朗读的正文。')
    if route not in ('photo','portrait'):raise ValueError('请选择图片轮转或人物口播。')
    if not images or (route=='portrait' and len(images)!=1):raise ValueError('人物口播请提交一张照片；图片轮转可提交一张或多张。')
    # A quick run is bound to one output route. Legacy 5.5 workflow records
    # are projected to that route for old scripts/tasks; the UI only exposes
    # route-scoped records.
    record=library.route_record(record,route);params=library.validate(route,record['payload'])
    if route=='portrait' and params['portrait']['engine']!='detail_v2':raise ValueError('快捷口播使用原图唇纹＋发声校准方案。')
    ref=Path(mouth_reference) if mouth_reference else None
    if route=='portrait' and params['portrait']['reference_mode']=='photo' and not ref:raise ValueError('本套参数使用张嘴参考，请先提交同人物参考图，或导入自动参考参数。')
    all_images=[Path(p).resolve(strict=True) for p in images]+([ref.resolve(strict=True)] if ref else [])
    for path in all_images:
        if not path.is_file():raise ValueError('图片路径不是文件。')
        with Image.open(path) as im:im.verify()
    info=library.voice_info(voice_id)
    export=Path(output_dir).expanduser().resolve() if output_dir else None
    if export:
        if export.exists() and not export.is_dir():raise ValueError('输出位置须为文件夹。')
        # Never treat the installed program/model library as an output destination.
        if export.is_relative_to(PROGRAM) or export.is_relative_to(PACKAGE_ROOT/'models'):raise ValueError('输出目录请放在项目或其他创作目录，不放进程序、模型文件夹。')
    area=PROJECTS/('快捷-'+stamp()+'-'+uuid.uuid4().hex[:6]);inputs=area/'输入';inputs.mkdir(parents=True)
    script=inputs/'正文.txt';script.write_text(text.strip(),encoding='utf-8');pictures=[]
    for i,path in enumerate(all_images[:len(images)],1):
        dest=inputs/(f'{i:03d}-'+path.name);shutil.copy2(path,dest);pictures.append(str(dest))
    reference=None
    if ref:
        reference=inputs/('张嘴参考'+ref.suffix);shutil.copy2(ref,reference)
    steps=[{'id':'speech','action':'speech','voice_id':voice_id,'text_file':str(script),'segmentation':params.get('speech',{}).get('segmentation','auto')},
           {'id':'captions','action':'align','audio':'@speech.audio','text_file':'@speech.text_file','method':'verified','route':route}]
    if route=='photo':steps.append({'id':'picture','action':'photo','audio':'@speech.audio','images':pictures,**params['photo'],'route':route});video='@picture.video'
    else:
        p=params['portrait'];steps.append({'id':'prepare','action':'portrait_prepare','image':pictures[0],'framing':p['framing'],'engine':p['engine'],'blink':p['blink']})
        step={'id':'portrait','action':'portrait','audio':'@speech.audio','text_file':'@speech.text_file','settings':'@prepare.settings','route':route}
        if reference:step['mouth_reference']=str(reference)
        steps.append(step);video='@portrait.video'
    steps.extend([{'id':'final','action':'export','video':video,'audio':'@speech.audio','subtitles':'@captions.subtitles','style':params['subtitles'],'review':'ai_checked','route':route},
                  {'id':'check','action':'check','video':'@final.video','audio':'@speech.audio','subtitles':'@captions.subtitles','text_file':'@speech.text_file','route':route}])
    task={'schema':1,'name':name.strip() or ('快捷图片视频' if route=='photo' else '快捷人物口播'),'steps':steps}
    from automation import validate
    validate(task)
    submission={'schema':1,'task':task,'record':record,'voice':info,'parameters':params,'route':route,'output_dir':str(export) if export else '',
                'quick':{'text':text.strip(),'images':pictures,'voice_id':voice_id,'route':route,'record_id':record['id'],'record':record,'output_dir':str(export) if export else '', 'mouth_reference':str(reference) if reference else '', 'last_result':None}}
    library.atomic(area/'提交记录.json',submission);library.atomic(area/'执行任务.json',task)
    return area/'提交记录.json'

def execute(submission_file,log):
    data=library.read(submission_file);area=Path(submission_file).parent;result_file=area/'完成结果.json'
    cmd=[PROGRAM/'runtime/python/python.exe','-X','utf8','-u',PROGRAM/'core/quick_service.py','--run',Path(submission_file)]
    with (area/'执行日志.log').open('w',encoding='utf-8') as stream:
        proc=subprocess.Popen(list(map(str,cmd)),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for line in proc.stdout:stream.write(line);stream.flush();log(line.rstrip())
        code=proc.wait()
    if not result_file.is_file():raise RuntimeError('快捷制作中断，已保留输入与日志：'+str(area))
    result=library.read(result_file)
    if code or result['status']!='completed':raise RuntimeError(result.get('error','快捷制作未完成')+'\n输入及日志：'+str(area))
    return result

def publish_outputs(results,area,data,log):
    from task_progress import export_copy
    final=Path(results['final']['video']);check=Path(results['check']['report'])
    tag='成品-'+stamp()+'-'+uuid.uuid4().hex[:6]
    def publish(root):
        destination=Path(root)/tag;destination.mkdir(parents=True)
        items=[(final,'视频.mp4'),(Path(results['speech']['audio']),'配音.wav'),(Path(results['speech']['text_file']),'正文.txt'),(Path(results['captions']['subtitles']),'字幕.srt')]
        for source,name in items:export_copy(source,destination/name,log,('.ass','.layout.json') if source==final else ())
        shutil.copytree(check.parent,destination/'检查报告')
        page=destination/'检查报告'/check.name
        html=page.read_text(encoding='utf-8')
        html=re.sub(r'(<video\b[^>]*\bsrc=)([\"\x27]).*?\2',lambda m:m[1]+m[2]+'../视频.mp4'+m[2],html,flags=re.I)
        page.write_text(html,encoding='utf-8')
        library.atomic(destination/'制作参数.json',data)
        return destination
    try:return publish(data.get('output_dir') or area),None
    except OSError as exc:
        if not data.get('output_dir'):raise
        warning='外部导出未完成，已改为保存在本次项目中：'+str(exc)
        log(warning)
        return publish(area),warning

def automatic_repair_notes(results):
    notes=[]
    try:
        speech=library.read(Path(results['speech']['audio']).with_suffix('.json'))
        groups={p['recovery']['rejected_files'] for p in speech.get('parts',[]) if p.get('recovery')}
        if groups:notes.append(f'已按原稿边界自动恢复{len(groups)}处配音，请试听停顿。')
        if any('-small /' in p.get('quality',{}).get('recognizer','') for p in speech.get('parts',[])):notes.append('已使用本地备用模型复核配音。')
    except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
    try:
        captions=library.read(Path(results['captions']['subtitles']).parent/'字幕校对记录.json')
        if captions.get('automatic_repairs'):notes.append(f"已自动合并{len(captions['automatic_repairs'])}处过短字幕。")
    except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
    return notes

def run_submission(path):
    import automation
    data=library.read(path);area=Path(path).parent;usage=library.begin_usage(data['task']['name'],data['route'],data['parameters'],source='quick',project=area,record=data['record'],voice=data['voice']);runner=None
    try:
        live=library.voice_info(data['voice']['id'])
        if live['profile_sha256']!=data['voice']['profile_sha256']:raise ValueError('提交后音色档案发生变化，停止使用不一致参数。')
        runner=automation.TaskRunner(data['task'],area/'执行任务.json');runner.gui['quick']=data['quick'];report=runner.run()
        if report['status']!='completed' or runner.results['check']['status']!='passed':raise ValueError('制作或技术检查未通过，未发布完成结果。')
        # All media remains in the project; a failed external copy never destroys it.
        check=Path(runner.results['check']['report'])
        publication={'version':'5.5','record':data['record'],'parameters':data['parameters'],'voice':data['voice'],'project':str(runner.project),'human_review_required':True,'output_dir':data.get('output_dir','')}
        destination,export_warning=publish_outputs(runner.results,area,publication,automation.log)
        result={'status':'completed','video':str(destination/'视频.mp4'),'audio':str(destination/'配音.wav'),'subtitles':str(destination/'字幕.srt'),'report':str(destination/'检查报告'/check.name),'directory':str(destination),'project':str(runner.project),'usage_record':str(usage),'human_review_required':True}
        if export_warning:result['export_warning']=export_warning
        result['automatic_repairs']=automatic_repair_notes(runner.results)
        runner.gui['quick']['last_result']=result;runner.persist();library.finish_usage(usage,'completed',result=result);library.atomic(area/'完成结果.json',result);automation.log('快捷制作完成，技术检查通过，等待人工验收。');return 0
    except Exception as exc:
        import traceback
        traceback.print_exc();result={'status':'failed','error':str(exc),'project':str(runner.project) if runner else None,'submission':str(path),'usage_record':str(usage),'human_review_required':True};library.finish_usage(usage,'failed',result=result,error=str(exc));library.atomic(area/'完成结果.json',result);return 1

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--run',required=True,type=Path);args=p.parse_args();raise SystemExit(run_submission(args.run))
