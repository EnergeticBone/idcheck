"""MOT CSV/TXT and CVAT XML parsers; strict about ambiguous image-level IDs."""
import csv
import json
import zipfile
from pathlib import PurePosixPath
import io
import re
import xml.etree.ElementTree as ET
from studio_core.models import normalize_boxes


def parse_mot(content, labels=None):
    rows, warnings = [], []
    labels = labels or []
    previous = None
    for n, raw in enumerate(content.lstrip('\ufeff').splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith('#'): continue
        parts = [p.strip() for p in next(csv.reader([s]))] if ',' in s else s.split()
        if len(parts) < 6:
            warnings.append(f'Dòng {n}: thiếu cột, đã bỏ qua'); continue
        try:
            frame = int(float(parts[0])); tid = int(float(parts[1]))
            x,y,w,h = [float(p) for p in parts[2:6]]
        except ValueError:
            if n == 1 or parts[0].lower() in ('frame','frame_id'):
                warnings.append(f'Dòng {n}: bỏ qua header'); continue
            warnings.append(f'Dòng {n}: giá trị không hợp lệ'); continue
        cid = int(float(parts[7])) if len(parts)>7 and isnum(parts[7]) else 1
        cls = labels[cid - 1] if cid>0 and cid<=len(labels) else ('Car' if cid == 1 else f'class_{cid}')
        if previous is not None and frame < previous:
            if 'MOT đầu vào không sắp xếp theo frame' not in warnings:
                warnings.append('MOT đầu vào không sắp xếp theo frame')
        previous = frame
        rows.append(dict(frame_id=frame, track_id=tid,x=x,y=y,w=w,h=h,class_name=cls))
    if not rows: raise ValueError('MOT không chứa bounding box hợp lệ.')
    boxes = normalize_boxes(rows)
    frame_base = 0 if min(b['frame_id'] for b in boxes) == 0 else 1
    return dict(boxes=boxes, format='mot', frame_base=frame_base, mode='video', labels=labels,
                width=None,height=None,total_frames=None,warnings=warnings)


def isnum(value):
    try: float(value); return True
    except (TypeError, ValueError): return False


def parse_cvat(content):
    try: root = ET.fromstring(content)
    except ET.ParseError as exc: raise ValueError(f'CVAT XML không hợp lệ: {exc}') from exc
    labels = [n.text or '' for n in root.findall('./meta/task/labels/label/name')]
    sz = root.find('./meta/task/original_size')
    def p_int(x):
        try:return int(x)
        except (ValueError, TypeError):return None
    width = p_int(sz.findtext('width')) if sz is not None else None
    height = p_int(sz.findtext('height')) if sz is not None else None
    total = p_int(root.findtext('./meta/task/size'))
    task_name = (root.findtext('./meta/task/name') or '').strip()
    rows=[]; warnings=[]
    if root.findall('./track'):
        for t in root.findall('./track'):
            tid = p_int(t.get('id'))
            if tid is None: continue
            lbl = t.get('label', 'Car')
            track_boxes = {}
            for b in t.findall('box'):
                try:
                    f = int(b.get('frame'))
                    track_boxes[f] = {
                        'x': float(b.get('xtl')),
                        'y': float(b.get('ytl')),
                        'w': float(b.get('xbr')) - float(b.get('xtl')),
                        'h': float(b.get('ybr')) - float(b.get('ytl')),
                        'outside': b.get('outside') == '1',
                        'occluded': b.get('occluded') == '1',
                        'keyframe': b.get('keyframe', '1') == '1',
                    }
                except (ValueError, TypeError): warnings.append(f'Track {tid}: box thiếu tọa độ')

            # Nội suy tuyến tính giữa các keyframe
            sorted_frames = sorted(track_boxes)
            for i in range(len(sorted_frames) - 1):
                f1, f2 = sorted_frames[i], sorted_frames[i + 1]
                b1, b2 = track_boxes[f1], track_boxes[f2]
                if not b1['outside'] and f2 > f1 + 1:
                    for f in range(f1 + 1, f2):
                        k = (f - f1) / (f2 - f1)
                        track_boxes[f] = {
                            'x': b1['x'] + k * (b2['x'] - b1['x']),
                            'y': b1['y'] + k * (b2['y'] - b1['y']),
                            'w': b1['w'] + k * (b2['w'] - b1['w']),
                            'h': b1['h'] + k * (b2['h'] - b1['h']),
                            'outside': False,
                            'occluded': b1['occluded'],
                            'keyframe': False,
                        }
            for f in sorted(track_boxes):
                b = track_boxes[f]
                if b['outside']: continue
                rows.append(dict(frame_id=f, track_id=tid, x=b['x'], y=b['y'], w=b['w'], h=b['h'],
                                 class_name=lbl, occluded=b['occluded'], keyframe=b['keyframe']))
        mode='video'
    else:
        mode='images'
        guessed = False
        for image in root.findall('./image'):
            iid = p_int(image.get('id')) or 0
            if width is None: width=p_int(image.get('width'))
            if height is None: height=p_int(image.get('height'))
            for idx,b in enumerate(image.findall('box')):
                tid = p_int(b.get('track_id'))
                if tid is None:
                    for a in b.findall('attribute'):
                        if a.get('name','').lower() in ('track_id','id','track'):
                            tid=p_int(a.text); break
                if tid is None:
                    guessed=True
                    tid = -(iid*100000+idx+1)   # unique per image: avoid inventing continuity
                try:
                    x,y = float(b.get('xtl')),float(b.get('ytl'))
                    x2,y2 = float(b.get('xbr')),float(b.get('ybr'))
                    rows.append(dict(frame_id=iid,track_id=tid,x=x,y=y,w=x2-x,h=y2-y,
                                     class_name=b.get('label','Car')))
                except (ValueError, TypeError): warnings.append(f'Image {iid}: box thiếu tọa độ')
        if guessed: warnings.append('CVAT dạng ảnh không có Track ID: tạo ID tạm riêng cho từng box. Không suy đoán ID Switch; hãy xuất CVAT for video nếu cần tracking.')
    if not rows: raise ValueError('Không tìm thấy box trong CVAT XML.')
    boxes=normalize_boxes(rows)
    return dict(boxes=boxes,format='cvat_xml',frame_base=0,mode=mode,
                width=width,height=height,total_frames=total,labels=labels,warnings=warnings,task_name=task_name)


def parse_file(filename, content, labels_text=''):
    labels=[v.strip() for v in (labels_text or '').splitlines() if v.strip()]
    if filename.lower().endswith('.json') or content.lstrip().startswith('{'):
        try: obj=json.loads(content.lstrip('\ufeff'))
        except json.JSONDecodeError as exc: raise ValueError(f'JSON không hợp lệ: {exc}') from exc
        if not isinstance(obj,dict):raise ValueError('JSON phải là một object.')
        if obj.get('project_type')=='t021_project':
            from backend.session_manager import parse_project
            return parse_project(content)
        if isinstance(obj.get('images'),list) and isinstance(obj.get('annotations'),list):
            return parse_coco(obj)
        if isinstance(obj.get('items'),list):return parse_datumaro(obj)
        raise ValueError('JSON chưa được hỗ trợ: chọn COCO instances_*.json, Datumaro *.json hoặc phiên T021. CVAT for video nên xuất XML.')
    if filename.lower().endswith('.xml') or content.lstrip().startswith('<?xml') or content.lstrip().startswith('<annotations'):
        return parse_cvat(content)
    if filename.lower().endswith(('.txt','.csv','.mot')):
        return parse_mot(content,labels)
    raise ValueError('Hỗ trợ CVAT XML, COCO JSON, Datumaro JSON, T021 JSON, MOT TXT/CSV và ZIP annotation CVAT.')


def _label_name(labels, label_id):
    try:return labels.get(int(label_id), f'class_{label_id}')
    except (ValueError,TypeError):return 'object'


def _track_number(annotation, fallback):
    """Never fabricate continuity for untracked image-level annotation."""
    attrs=annotation.get('attributes',{})
    attrs=attrs if isinstance(attrs,dict) else {str(a.get('name')):a.get('value') for a in attrs if isinstance(a,dict)} if isinstance(attrs,list) else {}
    for candidate in (annotation.get('track_id'), attrs.get('track_id'),attrs.get('trackId'),attrs.get('track')):
        if candidate is None or candidate=='':continue
        try:return int(candidate),True
        except (ValueError,TypeError):continue
    return -fallback,False


def parse_coco(obj):
    """Read COCO instances JSON (not necessarily tracking). Map image IDs to sequential UI frames."""
    images=obj['images'];annotations=obj['annotations']
    if not images:raise ValueError('COCO thiếu images.')
    image_map={}
    # Natural filename order is important when the original source is sampled frames.
    def sort_key(img):
        name=str(img.get('file_name') or img.get('id',''))
        return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)',name)]
    for idx,img in enumerate(sorted(images,key=sort_key)):
        if not isinstance(img,dict) or img.get('id') is None:raise ValueError('COCO image thiếu id.')
        if img['id'] in image_map:raise ValueError('COCO chứa image id trùng.')
        image_map[img['id']]=idx
    labels={}
    for c in obj.get('categories',[]):
        if isinstance(c,dict) and c.get('id') is not None:
            labels[c['id']]=str(c.get('name') or f'class_{c["id"]}')
    rows=[];warnings=[];untracked=False;unknown_images=0
    for idx,a in enumerate(annotations,1):
        if not isinstance(a,dict):continue
        frame=image_map.get(a.get('image_id'))
        if frame is None:unknown_images+=1;continue
        xywh=a.get('bbox')
        if not isinstance(xywh,list) or len(xywh)!=4:continue
        try:x,y,w,h=map(float,xywh)
        except (ValueError,TypeError):continue
        tid,tracked=_track_number(a,idx)
        untracked|=not tracked
        rows.append(dict(frame_id=frame,track_id=tid,x=x,y=y,w=w,h=h,
                         class_name=_label_name(labels,a.get('category_id')),
                         occluded=bool(a.get('occluded',False))))
    if not rows:raise ValueError('COCO không có bounding box hợp lệ.')
    if untracked:warnings.append('COCO không có track_id cho một số hoặc toàn bộ box: dùng ID riêng âm, không suy đoán tính liên tục.')
    if unknown_images:warnings.append(f'COCO bỏ qua {unknown_images} annotation có image_id không tồn tại.')
    widths={i.get('width') for i in images if isinstance(i,dict) and i.get('width')}
    heights={i.get('height') for i in images if isinstance(i,dict) and i.get('height')}
    return dict(boxes=normalize_boxes(rows),format='coco_json',frame_base=0,mode='images',
                width=widths.pop() if len(widths)==1 else None,
                height=heights.pop() if len(heights)==1 else None,
                total_frames=len(images),labels=list(labels.values()),warnings=warnings)


def parse_datumaro(obj):
    items=obj['items']
    if not items:raise ValueError('Datumaro thiếu items.')
    label_desc=(obj.get('categories') or {}).get('label',{})
    label_rows=label_desc.get('labels',[]) if isinstance(label_desc,dict) else label_desc
    labels={i:(x.get('name',f'class_{i}') if isinstance(x,dict) else str(x)) for i,x in enumerate(label_rows)}
    sorted_items=sorted(items,key=lambda it:[int(v) if v.isdigit() else v.lower() for v in re.split(r'(\d+)',str(it.get('id','')))])
    rows=[];untracked=False
    for frame,item in enumerate(sorted_items):
        for a in item.get('annotations',[]):
            if not isinstance(a,dict) or a.get('type') not in ('bbox', 1):continue
            bbox=a.get('bbox')
            if not isinstance(bbox,list) or len(bbox)!=4:continue
            try:x,y,w,h=map(float,bbox)
            except (ValueError,TypeError):continue
            tid,tracked=_track_number(a,len(rows)+1)
            untracked|=not tracked
            rows.append(dict(frame_id=frame,track_id=tid,x=x,y=y,w=w,h=h,
                             class_name=_label_name(labels,a.get('label_id'))))
    if not rows:raise ValueError('Datumaro JSON không chứa bbox hợp lệ.')
    warnings=[]
    if untracked:warnings.append('Datumaro không có track_id cho một số box: dùng ID âm tạm, không suy đoán ID Switch.')
    return dict(boxes=normalize_boxes(rows),format='datumaro_json',frame_base=0,mode='images',
                width=None,height=None,total_frames=len(items),labels=list(labels.values()),warnings=warnings)


def parse_cvat_archive(filename, payload, labels_text=''):
    """Import CVAT annotation ZIP without extracting paths or loading embedded images."""
    if not filename.lower().endswith('.zip'):raise ValueError('File phải có đuôi .zip')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            members=z.infolist()
            if len(members)>20000:raise ValueError('ZIP có quá nhiều file (>20.000).')
            candidates=[]
            for m in members:
                if m.is_dir():continue
                name=m.filename.lower()
                ext=PurePosixPath(name).suffix
                if ext not in ('.xml','.json','.txt','.csv','.mot'):continue
                base=PurePosixPath(name).name
                if base in ('dataset_meta.json','meta.json','README.txt'.lower(),'labels.txt'):continue
                priority= (0 if base=='annotations.xml' else
                           1 if ext=='.xml' else
                           2 if base.startswith('instances_') and ext=='.json' else
                           3 if ext=='.json' and ('annotations/' in name or base=='default.json') else
                           4 if ext=='.json' else
                           5 if name.endswith('gt/gt.txt') else 6)
                candidates.append((priority,name,m))
            if not candidates:raise ValueError('ZIP không có annotation .xml/.json/.txt. Nếu đây là frames.zip, hãy dùng nút Tải frames.zip.')
            candidates.sort(key=lambda t:(t[0],t[1]))
            errors=[]
            for _,name,member in candidates:
                if member.file_size>60_000_000:
                    errors.append(f'{name}: annotation quá lớn');continue
                try:
                    with z.open(member) as f:raw=f.read(60_000_001)
                    if len(raw)>60_000_000:raise ValueError('Annotation quá lớn')
                    txt=raw.decode('utf-8-sig')
                    result=parse_file(name,txt,labels_text)
                    result.setdefault('warnings',[]).append(f'Đã nhập annotation {name} từ ZIP {filename}; ảnh/video cần tải riêng.')
                    result['source_name']=name
                    return result
                except (ValueError,UnicodeError,ET.ParseError) as exc:
                    errors.append(f'{name}: {exc}')
            raise ValueError('Không tìm thấy annotation hợp lệ trong ZIP: '+'; '.join(errors[:3]))
    except (zipfile.BadZipFile,zipfile.LargeZipFile) as exc:
        raise ValueError(f'ZIP không hợp lệ: {exc}') from exc
