"""Atomic annotation edits. No implicit delete/merge/swap."""
from copy import deepcopy
from studio_core.models import normalize_boxes, iou

class FixConflict(ValueError):
    pass


def _safe(out):
    seen=set()
    for b in out:
        key=(b['frame_id'],b['track_id'])
        if key in seen:raise FixConflict(f'Xung đột: track {key[1]} có hai box tại frame {key[0]}')
        seen.add(key)


def apply_fix(rows,action,payload):
    src=normalize_boxes(rows);out=deepcopy(src)
    if action=='stitch_ids':
        source=int(payload['source']);target=int(payload['target']);frm=int(payload['from_frame'])
        if source==target:raise FixConflict('Nguồn và đích là cùng ID')
        selected=[b for b in out if b['track_id']==source and b['frame_id']>=frm]
        if not selected:raise FixConflict('Không tìm thấy đoạn track cần nối')
        if any(b['track_id']==target and b['frame_id']>=frm for b in out):
            # any concurrent target segment after start needs human review, even if no same frame
            raise FixConflict('ID đích vẫn còn sau điểm nối: cần kiểm tra/tách track thủ công')
        for b in selected:b['track_id']=target
    elif action=='swap_ids':
        a,b,frm=int(payload['a']),int(payload['b']),int(payload['from_frame'])
        if a==b:raise FixConflict('Hai ID giống nhau')
        if not any(v['track_id']==a and v['frame_id']>=frm for v in out) or not any(v['track_id']==b and v['frame_id']>=frm for v in out):
            raise FixConflict('Một trong hai ID không tồn tại tại đoạn muốn hoán đổi')
        for v in out:
            if v['frame_id']>=frm:
                if v['track_id']==a:v['track_id']=b
                elif v['track_id']==b:v['track_id']=a
    elif action=='set_class':
        tid=int(payload['track_id']);name=str(payload['class_name']).strip()
        if not name:raise FixConflict('Class không được trống')
        subset=[b for b in out if b['track_id']==tid]
        if not subset:raise FixConflict('Track không tồn tại')
        for b in subset:b['class_name']=name
    elif action=='remove_exact_duplicates':
        f=int(payload['frame_id']);tid=int(payload['track_id'])
        group=[b for b in out if (b['frame_id'],b['track_id'])==(f,tid)]
        if len(group)<2:raise FixConflict('Không có duplicate cần xóa')
        first=group[0]
        if not all(iou(first,b)>=0.9999 and b['class_name']==first['class_name'] for b in group[1:]):
            raise FixConflict('Các box trùng ID nhưng khác vị trí: không tự xóa')
        discard={b['uid'] for b in group[1:]}
        out=[b for b in out if b['uid'] not in discard]
    elif action=='interpolate_gap':
        tid=int(payload['track_id']);f0=int(payload['from_frame']);f1=int(payload['to_frame'])
        if f1-f0<2 or f1-f0>4:raise FixConflict('Chỉ nội suy gap 1–3 frame. Gap dài phải xem ảnh.')
        a=next((b for b in out if b['track_id']==tid and b['frame_id']==f0),None)
        b=next((b for b in out if b['track_id']==tid and b['frame_id']==f1),None)
        if a is None or b is None:raise FixConflict('Không có hai mốc để nội suy')
        for f in range(f0+1,f1):
            if any(v['track_id']==tid and v['frame_id']==f for v in out):raise FixConflict('Frame đã có box')
            t=(f-f0)/(f1-f0)
            box=deepcopy(a);box['uid']=f'interp_{tid}_{f}'
            box['frame_id']=f;box['keyframe']=False
            for k in ('x','y','w','h'):box[k]=round(a[k]*(1-t)+b[k]*t,3)
            out.append(box)
    elif action=='update_box':
        uid=str(payload['uid']);target=next((b for b in out if b['uid']==uid),None)
        if target is None:raise FixConflict('Không tìm thấy box cần sửa')
        for k in ('frame_id','track_id','class_name','x','y','w','h','occluded'):
            if k in payload:
                value=payload[k]
                if k in ('frame_id','track_id'):value=int(value)
                elif k in ('x','y','w','h'):value=float(value)
                elif k=='class_name':value=str(value).strip()
                elif k=='occluded':value=bool(value)
                target[k]=value
    elif action=='delete_box':
        uid=str(payload['uid']);out=[b for b in out if b['uid']!=uid]
        if len(out)==len(src):raise FixConflict('Không tìm thấy box để xóa')
    elif action=='create_box':
        vals={k:payload[k] for k in ('frame_id','track_id','x','y','w','h','class_name')}
        vals['uid']='user_'+str(max([len(out)]+[int(v['uid'].split('_')[-1]) for v in out if v['uid'].startswith('user_') and v['uid'].split('_')[-1].isdigit()])+1)
        out+=normalize_boxes([vals])
    else:raise FixConflict(f'Thao tác không được hỗ trợ: {action}')
    if any(b['w']<=0 or b['h']<=0 or b['x']<0 or b['y']<0 for b in out):
        # Allow existing bad boxes to be corrected one-by-one, don't block unrelated fixes
        oldbad={b['uid'] for b in src if b['w']<=0 or b['h']<=0 or b['x']<0 or b['y']<0}
        newbad={b['uid'] for b in out if b['w']<=0 or b['h']<=0 or b['x']<0 or b['y']<0}
        if newbad-oldbad:raise FixConflict('Tọa độ hoặc kích thước box sửa không hợp lệ')
    _safe(out)
    return sorted(out,key=lambda b:(b['frame_id'],b['track_id'],b['uid']))
