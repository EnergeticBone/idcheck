"""One-to-one matching for event-level QA benchmark; ground truth must be supplied."""

def evaluate_events(pred,gt,tolerance=2):
    tolerance=max(0,min(10,int(tolerance)))
    if not isinstance(pred,list) or not isinstance(gt,list):
        raise ValueError('predicted và ground_truth phải là hai mảng')
    used=set();tp=0
    for p in pred:
        if not isinstance(p,dict):raise ValueError('Mỗi predicted event phải là object')
        for ix,g in enumerate(gt):
            if not isinstance(g,dict):raise ValueError('Mỗi ground_truth event phải là object')
            if ix in used:continue
            if str(p.get('type'))!=str(g.get('type')):continue
            if int(p.get('track_id',-1))!=int(g.get('track_id',-1)):continue
            if abs(int(p.get('frame',-100))-int(g.get('frame',-200)))>tolerance:continue
            used.add(ix);tp+=1;break
    fp=len(pred)-tp;fn=len(gt)-tp
    prec=tp/(tp+fp) if tp+fp else 0
    rec=tp/(tp+fn) if tp+fn else 0
    return dict(tp=tp,fp=fp,fn=fn,precision=round(prec,4),recall=round(rec,4),
                f1=round(2*prec*rec/(prec+rec),4) if prec+rec else 0,
                target_met=prec>=.85 and rec>=.80,
                note='Chỉ đánh giá danh sách sự kiện người dùng cung cấp; không tự tạo ground truth.')
