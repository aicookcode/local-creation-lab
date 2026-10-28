"""Shared native UI styles and responsive scroll surfaces.

The workbench deliberately uses a quiet, light visual language: white surfaces,
soft separators, one blue accent and readable secondary text. Keeping the theme
here means newly added pages inherit the same look automatically.
"""
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

COLORS={
 'canvas':'#F5F5F7','surface':'#FFFFFF','text':'#1D1D1F','muted':'#6E6E73',
 'line':'#E5E5EA','accent':'#007AFF','accent_active':'#0066D6',
 'accent_soft':'#E8F1FF','control':'#F2F2F7','disabled':'#D1D1D6',
}

def theme(root):
 root.configure(bg=COLORS['canvas'])
 body_font=('Microsoft YaHei UI',10,'normal')
 for name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont','TkCaptionFont','TkSmallCaptionFont','TkIconFont','TkTooltipFont'):
  try:tkfont.nametofont(name,root=root).configure(family=body_font[0],size=body_font[1],weight='normal')
  except tk.TclError:pass
 for pattern in ('*TEntry.font','*TCombobox.font','*Text.font','*Listbox.font','*TCombobox*Listbox.font'):root.option_add(pattern,body_font)
 for option,value in [('Text.foreground',COLORS['text']),('Text.background',COLORS['surface']),('Text.insertBackground',COLORS['text']),('Text.selectBackground',COLORS['accent']),('Text.selectForeground','white'),('Listbox.foreground',COLORS['text']),('Listbox.background',COLORS['surface'])]:root.option_add('*'+option,value)
 s=ttk.Style(root);s.theme_use('clam')
 s.configure('.',font=body_font,background=COLORS['canvas'],foreground=COLORS['text'])
 s.configure('TFrame',background=COLORS['canvas']);s.configure('Card.TFrame',background=COLORS['surface']);s.configure('Footer.TFrame',background=COLORS['surface'])
 s.configure('TLabel',background=COLORS['canvas'],foreground=COLORS['text']);s.configure('Card.TLabel',background=COLORS['surface'],foreground=COLORS['text'])
 s.configure('Title.TLabel',font=('Microsoft YaHei UI',22,'bold'),foreground=COLORS['text'])
 s.configure('Section.TLabel',font=('Microsoft YaHei UI',12,'bold'),foreground=COLORS['text'])
 s.configure('Muted.TLabel',foreground=COLORS['muted'],font=('Microsoft YaHei UI',10,'normal'))
 s.configure('Good.TLabel',foreground='#248A3D',font=('Microsoft YaHei UI',10,'normal'))
 s.configure('TButton',padding=(14,8),background=COLORS['control'],foreground=COLORS['text'],font=body_font,borderwidth=0,relief='flat')
 s.map('TButton',background=[('disabled','#ECECF0'),('pressed','#D1D1D6'),('active','#E5E5EA')],foreground=[('disabled',COLORS['muted']),('active',COLORS['text'])])
 s.configure('Primary.TButton',background=COLORS['accent'],foreground='white',padding=(16,8))
 s.map('Primary.TButton',background=[('disabled','#B9D7FF'),('pressed',COLORS['accent_active']),('active',COLORS['accent_active'])],foreground=[('disabled','#F7FBFF'),('!disabled','white')])
 s.configure('Nav.TButton',anchor='w',padding=(16,10),background=COLORS['surface'],foreground='#3A3A3C',font=('Microsoft YaHei UI',10,'normal'),borderwidth=0)
 s.map('Nav.TButton',background=[('selected',COLORS['accent_soft']),('active','#F2F2F7')],foreground=[('disabled',COLORS['muted']),('selected',COLORS['accent']),('!disabled','#3A3A3C')])
 s.configure('TEntry',padding=(9,7),fieldbackground=COLORS['surface'],foreground=COLORS['text'],font=body_font,borderwidth=1,lightcolor=COLORS['line'],darkcolor=COLORS['line'])
 s.configure('TCombobox',padding=(8,6),fieldbackground=COLORS['surface'],foreground=COLORS['text'],font=body_font,borderwidth=1,lightcolor=COLORS['line'],darkcolor=COLORS['line'])
 for widget in ('TEntry','TCombobox'):
  s.map(widget,foreground=[('disabled',COLORS['muted']),('!disabled',COLORS['text'])],fieldbackground=[('disabled','#F2F2F7'),('readonly',COLORS['surface'])],selectbackground=[('!disabled',COLORS['accent'])],selectforeground=[('!disabled','white')])
 root.option_add('*TCombobox*Listbox.foreground',COLORS['text']);root.option_add('*TCombobox*Listbox.background',COLORS['surface'])
 s.configure('TNotebook',background=COLORS['canvas'],borderwidth=0,tabmargins=(0,0,0,0))
 s.configure('TNotebook.Tab',foreground=COLORS['muted'],background=COLORS['canvas'],font=('Microsoft YaHei UI',10,'normal'),padding=(14,9),borderwidth=0)
 s.map('TNotebook.Tab',foreground=[('disabled',COLORS['muted']),('selected',COLORS['text']),('!selected',COLORS['muted'])],background=[('selected',COLORS['surface']),('active','#EEEEF0')])
 for widget in ('TCheckbutton','TRadiobutton'):
  s.configure(widget,foreground=COLORS['text'],font=body_font,background=COLORS['canvas']);s.map(widget,foreground=[('disabled',COLORS['muted']),('!disabled',COLORS['text'])])
 s.configure('Treeview',rowheight=30,fieldbackground=COLORS['surface'],background=COLORS['surface'],foreground=COLORS['text'],font=body_font,borderwidth=0)
 s.configure('Treeview.Heading',padding=7,background='#F2F2F7',font=('Microsoft YaHei UI',10,'bold'),foreground=COLORS['text'])
 s.map('Treeview',foreground=[('selected','white')],background=[('selected',COLORS['accent'])])
 s.configure('TLabelframe',background=COLORS['canvas'],bordercolor=COLORS['line'],lightcolor=COLORS['line'],darkcolor=COLORS['line'],borderwidth=1)
 s.configure('TLabelframe.Label',font=('Microsoft YaHei UI',10,'bold'),foreground=COLORS['text'],background=COLORS['canvas'])
 s.configure('TProgressbar',background=COLORS['accent'],troughcolor=COLORS['line'],borderwidth=0,thickness=6)
 s.configure('Hidden.TNotebook',tabmargins=0);s.layout('Hidden.TNotebook.Tab',[])

class ScrollPage(ttk.Frame):
 def __init__(self,parent):
  super().__init__(parent);self.canvas=tk.Canvas(self,bg=COLORS['canvas'],highlightthickness=0)
  bar=ttk.Scrollbar(self,command=self.canvas.yview);bar.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True);self.canvas.configure(yscrollcommand=bar.set)
  self.body=ttk.Frame(self.canvas,padding=22);self.item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
  self.body.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
  self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.item,width=e.width))
  self.canvas.bind_all('<MouseWheel>',self.wheel,add='+')
 def wheel(self,event):
  widget=event.widget
  # Text editors and lists keep their own scrolling; only scroll this visible page.
  if not self.winfo_viewable() or isinstance(widget,(tk.Text,tk.Listbox,ttk.Treeview,ttk.Combobox)):return
  if str(widget).startswith(str(self)):self.canvas.yview_scroll(-int(event.delta/120),'units')

def heading(parent,title,description):
 ttk.Label(parent,text=title,style='Title.TLabel').pack(anchor='w')
 ttk.Label(parent,text=description,style='Muted.TLabel',wraplength=870,justify='left').pack(anchor='w',pady=(7,18))

def section(parent,title,description=None):
 box=ttk.LabelFrame(parent,text=title,padding=14);box.pack(fill='x',pady=(0,14))
 if description:ttk.Label(box,text=description,wraplength=850,style='Muted.TLabel').pack(anchor='w',pady=(0,10))
 return box

def field(parent,label,var,values=None,width=25,note=None):
 row=ttk.Frame(parent);row.pack(fill='x',pady=5);ttk.Label(row,text=label,width=16).pack(side='left')
 item=ttk.Combobox(row,textvariable=var,values=values,state='readonly',width=width) if values is not None else ttk.Entry(row,textvariable=var,width=width)
 item.pack(side='left',padx=(0,12))
 if note:ttk.Label(row,text=note,style='Muted.TLabel',wraplength=480).pack(side='left',fill='x',expand=True)
 return item
