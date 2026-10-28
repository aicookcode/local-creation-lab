from error_guidance import show_error
"""Blank-start quick creation and explicitly loaded reusable parameter records."""
from pathlib import Path
import copy,json,shutil,tkinter as tk
from tkinter import ttk,filedialog,messagebox,simpledialog
from tkinter.scrolledtext import ScrolledText
from ui_kit import ScrollPage,heading,section,field
from studio_paths import USER_RESOURCES
import settings_library as library
import quick_service

class QuickPanel:
    def __init__(self,manager,parent):
        self.manager=manager;self.root=manager.root;self.records={};self.usages={};self.images=[];self.selected=None;self.last_result=None
        library.initialize();self.tabs=ttk.Notebook(parent);self.tabs.pack(fill='both',expand=True)
        self.creation=ScrollPage(self.tabs);self.library_page=ScrollPage(self.tabs);self.tabs.add(self.creation,text='  快捷制作  ');self.tabs.add(self.library_page,text='  参数与使用记录  ')
        self.build_creation(self.creation.body);self.build_library(self.library_page.body);self.refresh()
    @property
    def project(self):return self.manager.project
    def button(self,parent,label,callback,**pack):return self.manager.button(parent,label,callback,**pack)
    def row(self,parent):
        row=ttk.Frame(parent);row.pack(fill='x',pady=6);return row
    def build_creation(self,p):
        heading(p,'快捷制作','提交文字和图片，选择固定音色、输出形式与该形式对应的参数。自动完成配音、视频、字幕和技术检查，结束后通知你验收。')
        box=section(p,'本次正文','只写实际要朗读的内容；不包含表演提示。首次建议约25–30字，先验证7秒左右。')
        self.text=ScrolledText(box,height=5,wrap='word',font=('Microsoft YaHei UI',11,'normal'),undo=True);self.text.pack(fill='x')
        self.button(box,'导入正文TXT',self.import_text,anchor='w',pady=6)
        box=section(p,'本次图片与声音','图片轮转按列表顺序循环；人物口播只用一张照片。选择保存过的音色档案，其中已包含参考样本与模型配置。')
        self.image_list=tk.Listbox(box,height=4,font=('Microsoft YaHei UI',10,'normal'),exportselection=False);self.image_list.pack(fill='x')
        row=self.row(box)
        for label,fn in [('添加图片',self.choose_images),('上移',lambda:self.move(-1)),('下移',lambda:self.move(1)),('移出',self.remove_image)]:self.button(row,label,fn,side='left',padx=(0,6))
        self.voice=tk.StringVar();self.voice_box=field(box,'声音样本 / 音色',self.voice,[],note='没有音色时，先在“声音克隆”中保存；此处不临时创建或改变已认可音色。')
        self.voice_box.bind('<<ComboboxSelected>>',lambda e:self.voice_note());row=self.row(box);self.button(row,'刷新音色',self.refresh,side='left');self.button(row,'听参考样本',self.listen,side='left',padx=6)
        self.voice_hint=tk.StringVar();ttk.Label(box,textvariable=self.voice_hint,wraplength=850,style='Good.TLabel').pack(anchor='w')
        self.route=tk.StringVar();self.route.trace_add('write',lambda *_:self.route_changed());field(box,'输出形式',self.route,['图片轮转','人物口播'],note='选择后只显示该形式的参数：图片轮转使用整图运动和转场；人物口播使用嘴型、发声校准与眨眼。')
        box=section(p,'导入本次参数','从参数库选择与输出形式对应的记录后点击载入，也可导入参数JSON。只加载该形式的制作参数，不带入旧文案、旧照片或输出目录。')
        self.record_choice=tk.StringVar();self.record_label=tk.StringVar(value='输出形式参数');record_row=ttk.Frame(box);record_row.pack(fill='x',pady=5);ttk.Label(record_row,textvariable=self.record_label,width=16).pack(side='left');self.record_box=ttk.Combobox(record_row,textvariable=self.record_choice,values=[],state='readonly',width=25);self.record_box.pack(side='left',padx=(0,12));ttk.Label(record_row,text='只显示当前输出形式的参数记录。',style='Muted.TLabel',wraplength=480).pack(side='left',fill='x',expand=True)
        row=self.row(box);self.button(row,'载入所选参数',self.load_selected,side='left');self.button(row,'导入参数文件',lambda:self.import_record(True),side='left',padx=6);self.button(row,'查看 / 管理参数记录',self.show_library,side='left')
        self.parameter_hint=tk.StringVar(value='尚未导入参数。');ttk.Label(box,textvariable=self.parameter_hint,wraplength=850,style='Good.TLabel').pack(anchor='w',pady=6)
        self.reference=tk.StringVar();row=self.row(box);ttk.Entry(row,textvariable=self.reference,state='readonly').pack(side='left',fill='x',expand=True);self.button(row,'可选：张嘴参考',self.choose_reference,side='left',padx=6);self.button(row,'清空参考',lambda:self.reference.set(''),side='left')
        ttk.Label(box,text='自动参考参数无需额外图片。只有选用“用户张嘴参考”的参数时，才必须另交同人物、同角度照片。',wraplength=850,style='Muted.TLabel').pack(anchor='w')
        box=section(p,'执行与结果','自动流程不会停下来要求逐步确认。检查发现异常时会停止并保留素材与日志；不会把异常当作完成。')
        self.output=tk.StringVar();row=self.row(box);ttk.Entry(row,textvariable=self.output,state='readonly').pack(side='left',fill='x',expand=True);self.button(row,'选择输出目录',self.choose_output,side='left',padx=6);self.button(row,'保存在本次项目',lambda:self.output.set(''),side='left')
        row=self.row(box);self.button(row,'一键制作',self.start,primary=True,side='left');self.button(row,'清空本页输入',self.clear_clicked,side='left',padx=6)
        self.result_hint=tk.StringVar(value='尚未执行。');ttk.Label(box,textvariable=self.result_hint,wraplength=850,style='Good.TLabel').pack(anchor='w',pady=8)
        row=self.row(box)
        for label,fn in [('播放成片',lambda:self.open_result('video')),('打开结果文件夹',lambda:self.open_result('directory')),('查看检查报告',lambda:self.open_result('report')),('转入分步界面继续编辑',self.open_advanced)]:self.button(row,label,fn,side='left',padx=(0,6))
    def build_library(self,p):
        heading(p,'参数与使用记录','命名参数可反复载入；每次执行另存实际参数快照和状态。修改另存新记录，不覆盖旧版本。音色参考仍由独立音色档案管理。')
        box=section(p,'可复用参数库','图片轮转和人物口播分别保存参数；载入只应用记录所属的输出形式，不会把两套视频配置混在一起。历史整套记录仅保留用于打开旧任务。')
        self.record_tree=ttk.Treeview(box,columns=('scope','date','source'),show='tree headings',height=6,selectmode='browse');self.record_tree.heading('#0',text='名称');self.record_tree.column('#0',width=320)
        for key,label,width in [('scope','适用范围',110),('date','保存时间',160),('source','来源',80)]:self.record_tree.heading(key,text=label);self.record_tree.column(key,width=width)
        self.record_tree.pack(fill='x');self.record_tree.bind('<<TreeviewSelect>>',lambda e:self.show_details())
        row=self.row(box)
        for label,fn in [('载入快捷制作',self.use_for_quick),('应用到分步页面',self.apply_selected),('导出JSON',self.export_record),('导入JSON',self.import_record),('刷新',self.refresh)]:self.button(row,label,fn,side='left',padx=(0,6))
        self.capture_scope=tk.StringVar();field(box,'保存当前页面参数',self.capture_scope,[library.SCOPES[k] for k in ('photo','portrait','speech','clone')],note='请选择已设置好的页面；视频参数只保存当前输出形式，不保存另一条路线或素材。')
        self.button(box,'将当前参数另存为记录',self.capture,anchor='w',pady=6)
        box=section(p,'执行使用记录','保存运行时的参数、音色配置及结果位置；失败也记录。可取回参数或打开原项目，不会自动恢复历史素材。')
        self.history_tree=ttk.Treeview(box,columns=('status','date'),show='tree headings',height=6,selectmode='browse');self.history_tree.heading('#0',text='执行任务');self.history_tree.column('#0',width=390)
        self.history_tree.heading('status',text='状态');self.history_tree.column('status',width=100);self.history_tree.heading('date',text='开始时间');self.history_tree.column('date',width=180);self.history_tree.pack(fill='x')
        self.history_tree.bind('<<TreeviewSelect>>',lambda e:self.show_history())
        row=self.row(box);self.button(row,'将这次参数另存入库',self.save_history,side='left');self.button(row,'打开关联项目',self.open_history,side='left',padx=6)
        self.details=ScrolledText(p,height=13,wrap='word',font=('Consolas',10,'bold'),state='disabled');self.details.pack(fill='x',pady=8)
    def guard(self,work):
        if not self.manager.idle():return
        try:return work()
        except Exception as exc:show_error('请检查输入',str(exc),parent=self.root)
    def refresh(self):
        import voice_store as voices
        current=self.voice.get();self.voices={f"{v['name']} · {v['engine']} · {v['id'][:6]}":v for v in voices.list_voices()};self.voice_box['values']=list(self.voices)
        if current not in self.voices:self.voice.set('')
        self.records={r['id']:r for r in library.records()};self.refresh_route_records()
        self.record_tree.delete(*self.record_tree.get_children())
        for r in self.records.values():self.record_tree.insert('','end',iid=r['id'],text=r['name'],values=(library.SCOPES.get(r['scope'],'不可用'),r.get('created_at','')[:19].replace('T',' '),{'approved':'已确认','user':'手动保存','import':'导入','history':'使用记录'}.get(r.get('source'),r.get('source',''))))
        self.usages={r['id']:r for r in library.history()};self.history_tree.delete(*self.history_tree.get_children())
        for r in self.usages.values():self.history_tree.insert('','end',iid=r['id'],text=r.get('label',''),values=({'running':'执行中 / 中断后待查','completed':'完成','failed':'失败'}.get(r['status'],r['status']),r['started_at'][:19].replace('T',' ')))
        self.voice_note()
    def show_library(self):self.manager.tab('quick');self.tabs.select(self.library_page);self.refresh()
    def display(self,data):self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('1.0',json.dumps(data,ensure_ascii=False,indent=2));self.details.configure(state='disabled')
    def selected_record(self):
        ids=self.record_tree.selection()
        if not ids:raise ValueError('请先选择一条参数记录。')
        return library.get(ids[0])
    def show_details(self):
        ids=self.record_tree.selection()
        if ids:self.display(self.records[ids[0]])
    def show_history(self):
        ids=self.history_tree.selection()
        if ids:self.display(self.usages[ids[0]])
    def route_key(self):
        return {'图片轮转':'photo','人物口播':'portrait'}.get(self.route.get(),'')
    def route_changed(self):
        if not hasattr(self,'record_box'):return
        self.selected=None;self.record_choice.set('');self.parameter_hint.set('尚未导入参数。')
        self.record_label.set({'photo':'图片轮转参数','portrait':'人物口播参数'}.get(self.route_key(),'输出形式参数'))
        self.refresh_route_records()
    def refresh_route_records(self):
        route=self.route_key()
        self.quick_records={r['name']+' · '+r['id'][:8]:r for r in self.records.values() if r.get('scope')==route} if hasattr(self,'records') else {}
        if hasattr(self,'record_box'):self.record_box['values']=list(self.quick_records)
    def load_record(self,r):
        route=self.route_key()
        if not route:raise ValueError('请先选择输出形式，再选择对应参数。')
        r=library.route_record(r,route)
        self.selected=r;p=r['payload'];self.parameter_hint.set(self.parameter_summary(r));self.record_choice.set(next((k for k,v in self.quick_records.items() if v['id']==r['id']),''))
    def parameter_summary(self,r):
        p=r['payload'];route=r['scope'];lines=[r['name']]
        if route=='photo':lines.append(f"图片轮转：每张{p['photo']['seconds']:g}秒 / {p['photo']['transition']} / {p['photo']['motion']} / {p['photo']['strength']}")
        else:lines.append('人物口播：原图唇纹＋发声校准 / '+f"{p['portrait']['framing']} / "+{'natural':'自然眨眼（约2.1–3.1秒）','off':'不添加眨眼','custom':'自定义眨眼'}.get(p['portrait']['blink']['mode'],'')+f" / {'自动口腔参考' if p['portrait']['reference_mode']=='auto' else '用户张嘴参考'}")
        if 'speech' in p:lines.append({'auto':'语音分段：自动','whole':'语音分段：整段','paragraph':'语音分段：按段落'}.get(p['speech']['segmentation'],'语音分段：自动'))
        if 'subtitles' in p:lines.append(f"字幕：固定{p['subtitles']['font_size']}号粗体 / 中心{p['subtitles']['center_y']}px / 宽{p['subtitles']['width']}px；音色按所选档案保留。")
        return '\n'.join(lines)
    def load_selected(self):
        def work():
            r=self.quick_records.get(self.record_choice.get())
            if not r:raise ValueError('请选择参数后点击载入。')
            self.load_record(r);self.manager.persist()
        self.guard(work)
    def use_for_quick(self):
        def work():
            r=self.selected_record()
            if r.get('scope')=='workflow':raise ValueError('历史整套记录只能用于打开旧任务；快捷制作请载入当前输出形式的参数。')
            self.load_record(r);self.tabs.select(self.creation);self.manager.persist()
        self.guard(work)
    def apply_selected(self):self.guard(lambda:self.apply_record(self.selected_record()))
    def apply_record(self,r):
        p=library.validate(r['scope'],r['payload']);wb=self.manager
        if 'clone' in p:
            c=p['clone'];v=wb.voice;v.engine_var.set('Qwen · 本地首选' if c['engine']=='qwen' else 'ZipVoice · 仅非商业测试');v.reset_recipe();v.recipe_base=copy.deepcopy(c['settings']);v.speed_var.set(str(c['settings'].get('default_speed',1.)));v.show_recipe(c['settings']);wb.tab('clone')
        if 'speech' in p:wb.voice.segmentation_var.set(next(k for k,v in library.SEGMENTS.items() if v==p['speech']['segmentation']));wb.tab('speech')
        for kind in ('photo','portrait'):
            if kind not in p:continue
            route=wb.routes[kind]
            if kind=='photo':
                for key,var in [('seconds',route.seconds),('transition',route.transition),('motion',route.motion),('strength',route.strength)]:var.set(str(p[kind][key]))
            else:
                panel=route.portrait;settings=panel.serialize();settings.update(copy.deepcopy(p[kind]));panel.load(settings)
            if 'subtitles' in p:
                for key,var in [('font_size',route.font_size),('center_y',route.caption_y),('width',route.caption_width)]:var.set(str(p['subtitles'][key]))
            wb.tab('video');wb.video_tabs.select(wb.route_containers[kind])
        wb.persist();wb.status.set('已导入参数：'+r['name']+'。本次文字、图片、音色和输出位置保留。')
    def capture_payload(self,scope):
        wb=self.manager
        if scope=='workflow':
            data={**self.capture_payload('speech'),**self.capture_payload('photo'),**self.capture_payload('portrait'),'quality':library.QUALITY}
            # Each route can have its own style; a reusable full recipe must state one unambiguous subtitle style.
            if self.style(wb.routes['photo'])!=self.style(wb.routes['portrait']):raise ValueError('两个视频页的字幕样式不同，请统一后保存整套参数；也可分别保存单页参数。')
        elif scope=='speech':
            seg=library.SEGMENTS.get(wb.voice.segmentation_var.get())
            if not seg:raise ValueError('请先在语音生成页选择生成方式。')
            data={'speech':{'segmentation':seg}}
        elif scope=='clone':
            v=wb.voice;engine='qwen' if v.engine_var.get().startswith('Qwen') else 'zipvoice';cfg=v.current_recipe()
            for key in ('reference','reference_text','role','label','selection','base_sample','note','target','accepted_at','acceptance'):cfg.pop(key,None)
            data={'clone':{'engine':engine,'settings':cfg}}
        elif scope=='photo':
            route=wb.routes['photo'];options=route.options();options.pop('size',None);data={'photo':options,'subtitles':self.style(route),'quality':library.QUALITY}
            seg=library.SEGMENTS.get(wb.voice.segmentation_var.get());data['speech']={'segmentation':seg or 'auto'}
        else:
            route=wb.routes['portrait'];s=route.portrait.serialize();data={'portrait':{k:s[k] for k in ('engine','framing','blink','reference_mode')},'subtitles':self.style(route),'quality':library.QUALITY}
            seg=library.SEGMENTS.get(wb.voice.segmentation_var.get());data['speech']={'segmentation':seg or 'auto'}
        return library.validate(scope,data)
    def execution_parameters(self,owner):
        kind=getattr(owner,'kind',None)
        if kind not in ('photo','portrait'):return None
        if kind=='photo':
            options=owner.options();options.pop('size',None);data={'photo':options,'quality':library.QUALITY}
        else:
            s=owner.portrait.serialize();data={'portrait':{k:s[k] for k in ('engine','framing','blink','reference_mode')},'quality':library.QUALITY}
        seg=library.SEGMENTS.get(self.manager.voice.segmentation_var.get())
        data['speech']={'segmentation':seg or 'auto'}
        try:data['subtitles']=self.style(owner)
        except ValueError:pass
        # Historical 2D actions remain project-specific; do not advertise them as a reusable photo-independent recipe.
        if kind=='portrait' and data['portrait']['engine']=='legacy_v4':return None
        library.validate(kind,data);return {'scope':kind,'payload':data}
    def style(self,route):
        cfg=route.export_style();return {'font_size':cfg['font_size'],'center_y':cfg['center_y_px'],'width':cfg['max_width_px']}
    def capture(self):
        def work():
            scope=next((k for k,v in library.SCOPES.items() if v==self.capture_scope.get()),None)
            if not scope or scope=='workflow':raise ValueError('请选择图片轮转、人物口播、语音生成或声音克隆页面。')
            data=self.capture_payload(scope);name=simpledialog.askstring('保存参数记录','输入便于识别的名称；会另存新版本：',parent=self.root)
            if name:library.save_record(name,scope,data);self.refresh();self.manager.status.set('参数已保存，可随时调取。')
        self.guard(work)
    def import_record(self,quick=False):
        def work():
            path=filedialog.askopenfilename(title='导入参数记录',filetypes=[('参数记录','*.json')])
            if not path:return
            r=library.checked(library.read(path))
            if quick and r.get('scope')!=self.route_key():raise ValueError('导入的参数与当前输出形式不匹配，请选择'+library.SCOPES.get(self.route_key(),'对应')+'参数。')
            saved=library.save_record(r['name'],r['scope'],r['payload'],source='import',note=r.get('note',''));self.refresh()
            if quick:self.load_record(saved)
        self.guard(work)
    def export_record(self):
        def work():
            r=self.selected_record();path=filedialog.asksaveasfilename(title='导出可复用参数',initialfile='制作参数记录.json',defaultextension='.json',filetypes=[('参数记录','*.json')])
            if path:library.atomic(Path(path),r)
        self.guard(work)
    def save_history(self):
        def work():
            ids=self.history_tree.selection()
            if not ids:raise ValueError('请选择一次使用记录。')
            r=self.usages[ids[0]];p=library.validate(r['scope'],r['parameters']);name=simpledialog.askstring('保存历史参数','为这次参数命名：',parent=self.root)
            if name:library.save_record(name,r['scope'],p,source='history',note='取自执行：'+r['label']);self.refresh()
        self.guard(work)
    def open_history(self):
        ids=self.history_tree.selection()
        if ids:
            r=self.usages[ids[0]];path=(r.get('result') or {}).get('project') if isinstance(r.get('result'),dict) else None;path=path or r.get('project')
            if path:self.manager.open_path(path)
    def voice_note(self):
        r=self.voices.get(self.voice.get())
        if not r:self.voice_hint.set('尚未选择声音样本。');return
        try:
            v=library.voice_info(r['id']);self.voice_hint.set(f"{v['name']} · 语速{v['settings'].get('default_speed',1):.2f} · 参考{v['reference_info']['seconds']:.2f}秒；按原档案生成。")
        except Exception as exc:self.voice_hint.set(str(exc))
    def listen(self):
        def work():
            import voice_store as voices
            r=self.voices.get(self.voice.get())
            if not r:raise ValueError('请先选择声音样本。')
            _,_,cfg=voices.get_voice(r['id'],verify_models=False);self.manager.open_path(cfg['reference'])
        self.guard(work)
    def import_text(self):
        def work():
            path=filedialog.askopenfilename(title='选择正文TXT',filetypes=[('正文','*.txt')])
            if path:self.text.delete('1.0','end');self.text.insert('1.0',self.manager.read_text(Path(path)));self.manager.persist()
        self.guard(work)
    def choose_images(self):
        def work():
            paths=filedialog.askopenfilenames(title='添加本次图片',filetypes=[('图片','*.png *.jpg *.jpeg *.webp')])
            if paths:
                import uuid
                area=self.project/'快捷素材'/uuid.uuid4().hex;area.mkdir(parents=True)
                for i,path in enumerate(paths):target=area/(str(i)+'-'+Path(path).name);shutil.copy2(path,target);self.images.append(str(target))
                self.refresh_images();self.manager.persist()
        self.guard(work)
    def refresh_images(self):self.image_list.delete(0,'end');[self.image_list.insert('end',f'{i+1}  {Path(p).name}') for i,p in enumerate(self.images)]
    def move(self,step):
        if not self.manager.idle():return
        ids=self.image_list.curselection()
        if ids and 0<=ids[0]+step<len(self.images):i=ids[0];j=i+step;self.images[i],self.images[j]=self.images[j],self.images[i];self.refresh_images();self.image_list.selection_set(j);self.manager.persist()
    def remove_image(self):
        if not self.manager.idle():return
        ids=self.image_list.curselection()
        if ids:self.images.pop(ids[0]);self.refresh_images();self.manager.persist()
    def choose_reference(self):
        def work():
            path=filedialog.askopenfilename(title='可选的同人物张嘴参考',filetypes=[('图片','*.png *.jpg *.jpeg *.webp')])
            if path:
                import uuid
                target=self.project/'快捷素材'/uuid.uuid4().hex/Path(path).name;target.parent.mkdir(parents=True);shutil.copy2(path,target);self.reference.set(str(target));self.manager.persist()
        self.guard(work)
    def choose_output(self):
        if not self.manager.idle():return
        path=filedialog.askdirectory(title='选择本次成品输出目录')
        if path:self.output.set(path);self.manager.persist()
    def snapshot(self):
        r=self.voices.get(self.voice.get());return {'text':self.text.get('1.0','end').strip(),'images':list(self.images),'voice_id':r['id'] if r else None,'route':{'图片轮转':'photo','人物口播':'portrait'}.get(self.route.get(),''),'record':copy.deepcopy(self.selected),'output_dir':self.output.get(),'mouth_reference':self.reference.get(),'last_result':self.last_result}
    def load(self,data):
        self.text.delete('1.0','end');self.text.insert('1.0',data.get('text',''));self.images=list(data.get('images',[]));self.refresh_images();self.voice.set(next((k for k,v in self.voices.items() if v['id']==data.get('voice_id')),''));self.route.set({'photo':'图片轮转','portrait':'人物口播'}.get(data.get('route'),''));self.reference.set(data.get('mouth_reference',''));self.output.set(data.get('output_dir',''));self.selected=None;self.record_choice.set('');self.parameter_hint.set('尚未导入参数。')
        if data.get('record'):self.load_record(data['record'])
        self.last_result=data.get('last_result');self.result_hint.set('已手动打开结果：'+self.last_result['directory'] if self.last_result else '尚未执行。');self.voice_note()
    def clear_clicked(self):self.guard(lambda:(self.load({}),self.manager.persist()))
    def start(self):
        if not self.manager.idle():return
        try:
            d=self.snapshot()
            if not self.selected:raise ValueError('请选择并载入本次参数。')
            r=quick_service.prepare(d['text'],d['images'],d['voice_id'],d['route'],self.selected,output_dir=d['output_dir'],mouth_reference=d['mouth_reference'],name=self.manager.name.get())
        except Exception as exc:show_error('尚不能开始',str(exc),parent=self.root);return
        self.result_hint.set('已提交，正在排队或执行。完成后通知；无需中间操作。')
        def done(result):
            note=('\n'+result['export_warning']) if result.get('export_warning') else ''
            if result.get('automatic_repairs'):note+='\n'+'\n'.join(result['automatic_repairs'])
            self.last_result=result;self.result_hint.set('制作完成，技术检查通过，请带声音验收。'+note+'\n'+result['directory']);self.manager.status.set('快捷制作完成，等待人工验收。'+note);self.root.bell();messagebox.showinfo('制作完成','配音、视频、字幕与检查已完成。'+note+'\n请带声音验收：\n'+result['directory'],parent=self.root)
        self.manager.run(self,'快捷制作',lambda:quick_service.execute(r,self.manager.log),done,serialized=False,parameters=None)
    def open_result(self,key):
        if not self.last_result:messagebox.showinfo('尚无结果','请先完成一次快捷制作。',parent=self.root);return
        self.manager.open_path(self.last_result[key])
    def open_advanced(self):
        if not self.manager.idle() or not self.last_result:return
        self.manager.persist();self.manager.load_project(Path(self.last_result['project'])/'项目.json');self.manager.tab('video');route=self.snapshot()['route'] or 'portrait';self.manager.video_tabs.select(self.manager.route_containers[route])
