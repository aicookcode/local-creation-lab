from pathlib import Path
import sys,queue,threading,traceback,ctypes
sys.path.insert(0,str(Path(__file__).resolve().parent/'core'))
sys.pycache_prefix=str(Path(__file__).resolve().parent.parent/'cache/pycache')

def prepare_models():
    """Show a small first-run window while every model group is downloaded and verified."""
    import tkinter as tk
    from tkinter import ttk,messagebox
    from model_manager import ensure_all
    events=queue.Queue();root=tk.Tk();root.title('Local Media Studio 7.0 · Startup preparation');root.geometry('620x190');root.resizable(False,False)
    root.protocol('WM_DELETE_WINDOW',lambda:root.destroy())
    frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='正在准备本地模型',font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w')
    ttk.Label(frame,text='首次启动会下载并校验全部模型。请保持网络连接和足够磁盘空间；完成后才进入工作台。',wraplength=560).pack(anchor='w',pady=(10,14))
    status=tk.StringVar(value='正在检查模型文件……');ttk.Label(frame,textvariable=status).pack(anchor='w')
    bar=ttk.Progressbar(frame,mode='indeterminate',length=560);bar.pack(anchor='w',pady=(10,0));bar.start(12)
    def worker():
        try:
            result=ensure_all(lambda message:events.put(('progress',str(message))))
            events.put(('done',result))
        except Exception as exc:
            events.put(('error',exc))
    threading.Thread(target=worker,daemon=True).start()
    result={'ok':False}
    def poll():
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='progress':status.set(value)
                elif kind=='done':
                    result['ok']=True;bar.stop();root.destroy();return
                else:
                    result['error']=value;bar.stop();root.destroy();return
        except queue.Empty:pass
        root.after(150,poll)
    root.after(150,poll);root.mainloop()
    if result.get('ok'):return True
    error=result.get('error',RuntimeError('启动准备被取消。'))
    log=Path(__file__).resolve().parent.parent/'logs/startup_preflight.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text(traceback.format_exc() if not isinstance(error,Exception) else ''.join(traceback.format_exception(error)),encoding='utf-8')
    messagebox.showerror('启动准备未完成','模型尚未全部准备好，工作台不会继续启动。\n\n请检查网络、磁盘空间和模型下载源配置，然后重新打开工作台。\n\n详细记录：'+str(log))
    return False

try:
    from portable_bootstrap import configure
    configure(gui=True)
    if not prepare_models():raise SystemExit(1)
    from workbench import main
    main()
except Exception:
    report=Path(__file__).resolve().parent.parent/'logs/startup_error.log'
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(traceback.format_exc(),encoding='utf-8')
    ctypes.windll.user32.MessageBoxW(0,'启动未完成，请查看 logs/startup_error.log。','Local Media Studio',0x10)
    raise
