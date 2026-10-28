"""Native DPI and size-specific application artwork; no system setting changes."""
from pathlib import Path
import ctypes
import os

ICON_SIZES=(16,20,24,28,32,40,48,56,64,72,80,96,128,144,192,256)

def enable_dpi_awareness():
 if os.name!='nt':return
 try:
  api=ctypes.windll.user32.SetProcessDpiAwarenessContext
  api.argtypes=[ctypes.c_void_p];api.restype=ctypes.c_bool
  if api(ctypes.c_void_p(-4)):return
 except (AttributeError,OSError):pass
 try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
 except (AttributeError,OSError):pass

def display_scale(window):
 # Tk points are converted using its current display DPI, not a device constant.
 return max(.75,float(window.winfo_fpixels('1i'))/96)

def sidebar_artwork(window,assets):
 from PIL import Image,ImageTk
 pixels=max(24,round(72*display_scale(window)))
 path=Path(assets)/f'sidebar-icon-{pixels}.png'
 if path.is_file():return ImageTk.PhotoImage(Image.open(path),master=window)
 # Unusual DPI: redraw geometry at the exact physical pixel size.
 image=Image.new('RGBA',(pixels,pixels),(0,0,0,0))
 from PIL import ImageDraw
 draw=ImageDraw.Draw(image);draw.rounded_rectangle((0,0,pixels-1,pixels-1),radius=max(2,round(pixels*.2)),fill='#063e35')
 draw.polygon([(round(pixels*.35),round(pixels*.23)),(round(pixels*.35),round(pixels*.77)),(round(pixels*.78),round(pixels*.5))],fill='white')
 return ImageTk.PhotoImage(image,master=window)

def set_app_icons(window,assets):
 from PIL import Image,ImageTk
 assets=Path(assets)
 window.iconbitmap(default=str(assets/'app.ico'))
 window._app_icon_photos=[ImageTk.PhotoImage(Image.open(assets/f'window-icon-{size}.png'),master=window) for size in ICON_SIZES]
 window.iconphoto(True,*window._app_icon_photos)
 if os.name!='nt':return
 from ctypes import wintypes
 user=ctypes.windll.user32
 user.GetParent.argtypes=[wintypes.HWND];user.GetParent.restype=wintypes.HWND
 user.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT];user.LoadImageW.restype=wintypes.HANDLE
 user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t];user.SendMessageW.restype=ctypes.c_ssize_t
 user.DestroyIcon.argtypes=[wintypes.HANDLE]
 window._native_app_icons=[];window._native_icon_dpi=None;window._native_icon_window=None
 def apply():
  if not window.winfo_exists() or not window.winfo_ismapped():return
  handle=user.GetParent(window.winfo_id()) or window.winfo_id()
  try:
   user.GetDpiForWindow.argtypes=[wintypes.HWND];dpi=user.GetDpiForWindow(handle) or 96
  except AttributeError:dpi=round(window.winfo_fpixels('1i'))
  if window._native_icon_dpi==dpi and window._native_icon_window==handle:return
  fresh=[]
  for kind,metric,base in ((0,49,16),(1,11,32)):
   try:size=user.GetSystemMetricsForDpi(metric,dpi)
   except AttributeError:size=round(base*dpi/96)
   icon=user.LoadImageW(None,str(assets/'app.ico'),1,size,size,0x10)
   if icon:
    user.SendMessageW(handle,0x80,kind,int(icon));fresh.append(icon)
  old=window._native_app_icons
  window._native_app_icons=fresh;window._native_icon_dpi=dpi;window._native_icon_window=handle
  for icon in old:user.DestroyIcon(icon)
 def release(event):
  if event.widget is window:
   for icon in window._native_app_icons:user.DestroyIcon(icon)
   window._native_app_icons=[]
 window.after_idle(apply)
 window.after(100,apply)
 window.bind('<Map>',lambda e:window.after_idle(apply) if e.widget is window else None,add='+')
 window.bind('<Configure>',lambda e:window.after_idle(apply) if e.widget is window else None,add='+')
 window.bind('<Destroy>',release,add='+')
