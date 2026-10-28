"""Detect a newly submitted face and prepare user-defined local motion layers."""
from pathlib import Path
import sys,json,argparse,uuid,os
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'core'))
from portrait.config import inside,sha,relative
import cv2,numpy as np,torch
from PIL import Image,ImageOps,ImageDraw
p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--out-dir',required=True);p.add_argument('--regions');a=p.parse_args()
source,out=inside(a.image),inside(a.out_dir)
if out.exists():raise FileExistsError('照片准备目录已存在，请另存版本。')
out.mkdir(parents=True);torch.set_num_threads(min(4,os.cpu_count() or 2));cv2.setNumThreads(2)
im=ImageOps.exif_transpose(Image.open(source)).convert('RGB')
w,h=im.size;im=im.crop((0,0,w-w%2,h-h%2));w,h=im.size;im.save(out/'portrait.png');rgb=np.asarray(im);bgr=rgb[:,:,::-1].copy()
weights=ROOT.parent/'models/portrait/facexlib'
for name in ('alignment_WFLW_4HG.pth','detection_Resnet50_Final.pth'):
 if not (weights/name).is_file():raise FileNotFoundError('人脸检测组件缺失：'+name)
print('正在检测本次照片中的人脸……',flush=True)
from facexlib.detection import init_detection_model
from facexlib.alignment import landmark_98_to_68,FAN
detector=init_detection_model('retinaface_resnet50',half=False,device='cpu',model_rootpath=str(weights))
with torch.no_grad():boxes=detector.detect_faces(bgr,.90)
def choose_face(detections,w,h):
 arr=np.asarray(detections,dtype=np.float64)
 if arr.ndim!=2 or arr.shape[1]<4:return None,{'raw_count':0,'reason':'invalid_detector_output'}
 candidates=[]
 for row in arr:
  x1,y1,x2,y2=map(float,row[:4]);bw=max(0,x2-x1);bh=max(0,y2-y1)
  if bw<16 or bh<16:continue
  candidates.append({'box':(x1,y1,x2,y2),'area':bw*bh,'score':float(row[4]) if arr.shape[1]>4 else 0.})
 if not candidates:return None,{'raw_count':len(arr),'reason':'no_usable_box'}
 candidates.sort(key=lambda x:(x['score'],x['area']),reverse=True)
 kept=[]
 for item in candidates:
  x1,y1,x2,y2=item['box'];overlap=False
  for prior in kept:
   a1,b1,a2,b2=prior['box'];ix=max(0,min(x2,a2)-max(x1,a1));iy=max(0,min(y2,b2)-max(y1,b1));inter=ix*iy
   union=item['area']+prior['area']-inter
   if union and inter/union>=.35:overlap=True;break
  if not overlap:kept.append(item)
 if len(kept)==1:return kept[0]['box'],{'raw_count':len(arr),'usable_count':len(candidates),'selected_count':1,'reason':'single_or_overlapping_duplicate'}
 top,second=kept[0],kept[1];cx=(top['box'][0]+top['box'][2])/(2*w);cy=(top['box'][1]+top['box'][3])/(2*h)
 # A tiny background false positive is recoverable when the dominant face is
 # clearly larger and near the image centre. Similar-sized independent faces
 # remain a hard stop for safety.
 if top['area']>=second['area']*3 and .18<=cx<=.82 and .12<=cy<=.78:
  return top['box'],{'raw_count':len(arr),'usable_count':len(candidates),'selected_count':len(kept),'reason':'dominant_central_face'}
 return None,{'raw_count':len(arr),'usable_count':len(candidates),'selected_count':len(kept),'reason':'multiple_independent_faces'}
face_box,face_detection=choose_face(boxes,w,h)
if face_box is None:raise ValueError(f'检测到{face_detection.get("selected_count",len(boxes))}张独立人脸。请提交清晰、无遮挡的单人正脸照片。')
x1,y1,x2,y2=map(int,face_box);x1,y1=max(0,x1),max(0,y1);x2,y2=min(w,x2),min(h,y2)
if min(x2-x1,y2-y1)<64:raise ValueError('人脸在照片中太小，请裁切为近景或使用更清晰的照片。')
align=FAN(num_modules=4,num_landmarks=98,device='cpu');align.load_state_dict(torch.load(weights/'alignment_WFLW_4HG.pth',map_location='cpu')['state_dict'],strict=True);align.eval()
with torch.no_grad():landmarks=landmark_98_to_68(align.get_landmarks(bgr[y1:y2,x1:x2].copy()))
landmarks[:,0]+=x1;landmarks[:,1]+=y1
if not np.isfinite(landmarks).all():raise ValueError('人脸关键点无效，请更换照片。')
np.save(out/'landmarks68.npy',landmarks)
manifest={'schema':2,'id':uuid.uuid4().hex,'name':source.stem,'canvas':[w,h],'fps':30,'limits':{'head':3.,'forearm':9.},'face_box':[x1,y1,x2,y2],'face_detection':face_detection,'method':'mouth preservation plus optional user-calibrated rigid motion','files':{}}
regions=json.loads(inside(a.regions).read_text(encoding='utf-8')) if a.regions else {}
for name,region in regions.items():
 if name not in ('head','forearm'):raise ValueError('不支持的动作区域。')
 points=np.asarray(region['polygon'],dtype=np.float64);pivot=list(map(float,region['pivot']))
 if points.ndim!=2 or points.shape[1]!=2 or len(points)<3 or not np.isfinite(points).all() or len(pivot)!=2 or not np.isfinite(pivot).all():raise ValueError('动作区域须至少三个有效边界点和一个支点。')
 if any(not 0<=x<w or not 0<=y<h for x,y in points) or not 0<=pivot[0]<w or not 0<=pivot[1]<h:raise ValueError('动作区域超出图片。')
 mask=np.zeros((h,w),np.uint8);cv2.fillPoly(mask,[np.rint(points).astype(np.int32)],255)
 if cv2.countNonZero(mask)<100:raise ValueError('动作区域太小，请重新标注。')
 band=cv2.subtract(mask,cv2.erode(mask,np.ones((21,21),np.uint8)))
 plate=cv2.inpaint(bgr,band,9,cv2.INPAINT_TELEA)
 Image.fromarray(mask).save(out/(name+'-mask.png'));Image.fromarray(plate[:,:,::-1]).save(out/(name+'-edge-fill.png'))
 lo=points.min(0);hi=points.max(0);pad=32
 manifest[name]={'pivot':pivot,'roi':[max(0,int(lo[0])-pad),max(0,int(lo[1])-pad),min(w,int(hi[0])+pad),min(h,int(hi[1])+pad)]}
 manifest[name]['polygon']=points.tolist()
for file in out.iterdir():
 if file.suffix in ('.png','.npy'):manifest['files'][file.name]=sha(file)
preview=im.copy();draw=ImageDraw.Draw(preview);draw.rectangle((x1,y1,x2,y2),outline='#28d99b',width=3)
for x,y in landmarks:draw.ellipse((x-1,y-1,x+1,y+1),fill='#28d99b')
preview.save(out/'检测预览.png');(out/'模板.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('照片已准备完成：'+str(out/'模板.json'),flush=True)
