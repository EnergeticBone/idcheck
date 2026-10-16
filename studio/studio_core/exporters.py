"""Export checked annotation; no silent deduplication."""
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import defaultdict
from studio_core.models import normalize_boxes


def _check_unique(boxes):
    seen=set()
    for b in boxes:
        key=b['frame_id'],b['track_id']
        if key in seen:
            raise ValueError(f'Không thể export: trùng Track ID {key[1]} ở frame {key[0]}. Hãy sửa trước.')
        seen.add(key)


def export_mot(rows, frame_base=1, labels=None):
    boxes=normalize_boxes(rows);_check_unique(boxes)
    names = list(dict.fromkeys((labels or []) + [b['class_name'] for b in boxes]))
    lines=[]
    for b in sorted(boxes,key=lambda o:(o['frame_id'],o['track_id'])):
        cls=names.index(b['class_name'])+1
        lines.append(f"{b['frame_id']-frame_base+1},{b['track_id']},{b['x']:.2f},{b['y']:.2f},{b['w']:.2f},{b['h']:.2f},1,{cls},1")
    return '\n'.join(lines)+ ('\n' if lines else ''), '\n'.join(names)+'\n'


def export_cvat(rows, frame_base=0, width=1920, height=1080,total_frames=None,labels=None):
    boxes=normalize_boxes(rows);_check_unique(boxes)
    if not boxes: raise ValueError('Không có box để xuất.')
    width,height=int(width),int(height)
    if width <= 0 or height <=0: raise ValueError('Kích thước ảnh phải >0.')
    base=int(frame_base)
    minframe=min(b['frame_id'] for b in boxes)
    if minframe < base: raise ValueError('frame_base lớn hơn frame đầu tiên.')
    total=int(total_frames) if total_frames is not None else max(b['frame_id'] for b in boxes)-base+1
    if total < max(b['frame_id'] for b in boxes)-base+1:
        raise ValueError('total_frames nhỏ hơn frame lớn nhất.')
    classes=list(dict.fromkeys((labels or []) + [b['class_name'] for b in boxes]))
    root=ET.Element('annotations');ET.SubElement(root,'version').text='1.1'
    meta=ET.SubElement(root,'meta'); task=ET.SubElement(meta,'task')
    ET.SubElement(task,'name').text='T021 QA export'
    for k,v in [('size',total),('mode','interpolation'),('start_frame',0),('stop_frame',max(total-1,0))]:
        ET.SubElement(task,k).text=str(v)
    lm=ET.SubElement(task,'labels')
    for c in classes:
        lab=ET.SubElement(lm,'label'); ET.SubElement(lab,'name').text=c
        ET.SubElement(lab,'type').text='rectangle'; ET.SubElement(lab,'attributes')
    osize=ET.SubElement(task,'original_size')
    ET.SubElement(osize,'width').text=str(width);ET.SubElement(osize,'height').text=str(height)
    grouped=defaultdict(list)
    for b in boxes: grouped[b['track_id']].append(b)
    for tid, group in sorted(grouped.items()):
        group.sort(key=lambda b:b['frame_id']);cls=group[0]['class_name']
        if any(b['class_name']!=cls for b in group):
            raise ValueError(f'Track {tid} đổi class. Sửa Class Inconsistency trước khi xuất CVAT XML.')
        track=ET.SubElement(root,'track',id=str(tid),label=cls,source='manual')
        for i,b in enumerate(group):
            frm=b['frame_id']-base
            attrs={'frame':str(frm),'outside':'0','keyframe':'1' if b.get('keyframe',True) else '0',
                   'occluded':'1' if b.get('occluded') else '0',
                   'xtl':f"{b['x']:.2f}",'ytl':f"{b['y']:.2f}",
                   'xbr':f"{b['x']+b['w']:.2f}",'ybr':f"{b['y']+b['h']:.2f}",'z_order':'0'}
            ET.SubElement(track,'box',**attrs)
            nxt=group[i+1]['frame_id'] if i+1<len(group) else None
            if nxt is None or nxt > b['frame_id']+1:
                if frm+1<total:
                    ET.SubElement(track,'box',**dict(attrs,frame=str(frm+1),outside='1',keyframe='1'))
    raw=ET.tostring(root,encoding='utf-8')
    return minidom.parseString(raw).toprettyxml(indent='  ',encoding='utf-8').decode('utf-8')
