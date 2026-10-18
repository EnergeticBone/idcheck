'use strict';
const el=id=>document.getElementById(id);
const htmlEscape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={boxes:[],meta:{},session:null,issues:[],decisions:{},selectedUid:null,selectedTrackId:null,selectedIssue:null,
         frame:1,frameMap:new Map(),undo:[],redo:[],imageURLs:new Map(),images:new Map(),videoURL:null,
         mediaFiles:[],mediaNames:new Map(),mediaToken:null,mediaMode:'none',
         videoToken:null,videoMeta:null,videoError:'',projectId:null,projectName:null,
         user:null,tasks:{},cvatJob:null,
         zoom:1.0,panX:0,panY:0,panDrag:null,spaceDown:false,
         isCreate:false,drag:null,sourceName:'',lastScan:null,labelsText:'',page:'cvatJobs',report:null};
let busy=false,toastTimer;
async function api(path,body={}){
 const res=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 let data;try{data=await res.json();}catch(e){throw Error('Máy chủ không trả JSON hợp lệ')}
 if(!res.ok)throw Error(data.error||`Lỗi HTTP ${res.status}`);
 return data;
}
function toast(text,error=false){const v=el('toast');v.textContent=text;v.className='toast show'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>v.className='toast',4400)}
function setBusy(v){busy=v;if(el('btnScan'))el('btnScan').disabled=v||!S.boxes.length;if(el('btnExport'))el('btnExport').disabled=v||!S.boxes.length||!canEdit();if(el('btnDemo'))el('btnDemo').disabled=v||!canEdit();if(el('btnSaveSession'))el('btnSaveSession').disabled=v||!S.boxes.length||!canEdit()}
function deepBoxes(){return S.boxes.map(b=>({...b}))}
function undoButtons(){const edit=canEdit();if(el('btnUndo'))el('btnUndo').disabled=!edit||!S.undo.length;if(el('btnRedo'))el('btnRedo').disabled=!edit||!S.redo.length}
function updateZoomBadge(){const z=el('zoomPercent');if(z)z.textContent=`${Math.round(S.zoom*100)}%`}
function resetZoom(){S.zoom=1.0;S.panX=0;S.panY=0;updateZoomBadge();draw()}
function zoomAtCenter(factor){const c=el('viewer');if(!c)return;const cx=c.width/2,cy=c.height/2;const wx=(cx-S.panX)/S.zoom,wy=(cy-S.panY)/S.zoom;const newZoom=Math.min(25.0,Math.max(0.4,S.zoom*factor));S.panX=cx-wx*newZoom;S.panY=cy-wy*newZoom;S.zoom=newZoom;updateZoomBadge();draw()}
let zoomAnimId=null;
function zoomToBox(b, animate=true){
 if(!b)return;
 const {c,sx,sy}=canvasParams();
 if(!c||!c.width||!c.height)return;
 const bw=Math.max(14,(b.w||0)*sx);
 const bh=Math.max(14,(b.h||0)*sy);
 const centerX=(b.x+(b.w||0)/2)*sx;
 const centerY=(b.y+(b.h||0)/2)*sy;
 const targetW=c.width*0.38;
 const targetH=c.height*0.38;
 const scaleX=targetW/bw;
 const scaleY=targetH/bh;
 const targetZoom=Math.min(8.0,Math.max(1.8,Math.min(scaleX,scaleY)));
 const targetPanX=c.width/2-targetZoom*centerX;
 const targetPanY=c.height/2-targetZoom*centerY;
 if(!animate){
  S.zoom=targetZoom;S.panX=targetPanX;S.panY=targetPanY;updateZoomBadge();draw();return;
 }
 if(zoomAnimId)cancelAnimationFrame(zoomAnimId);
 const startZoom=S.zoom,startPanX=S.panX,startPanY=S.panY;
 const startTime=performance.now(),duration=240;
 function step(now){
  const elapsed=now-startTime;
  const progress=Math.min(1.0,elapsed/duration);
  const ease=1-Math.pow(1-progress,3);
  S.zoom=startZoom+(targetZoom-startZoom)*ease;
  S.panX=startPanX+(targetPanX-startPanX)*ease;
  S.panY=startPanY+(targetPanY-startPanY)*ease;
  updateZoomBadge();draw();
  if(progress<1.0){zoomAnimId=requestAnimationFrame(step)}else{zoomAnimId=null}
 }
 zoomAnimId=requestAnimationFrame(step);
}
function checkpoint(){if(!canEdit())return;S.undo.push(deepBoxes());if(S.undo.length>20)S.undo.shift();S.redo=[];undoButtons()}
function indexBoxes(){
 S.frameMap=new Map();
 S.trackMap=new Map();
 let counter=1;
 for(const b of S.boxes){
  if(b.outside) continue;
  if(!b.uid) b.uid=`b_${b.frame_id}_${b.track_id}_${counter++}`;
  if(!S.frameMap.has(b.frame_id))S.frameMap.set(b.frame_id,[]);
  S.frameMap.get(b.frame_id).push(b);
  if(!S.trackMap.has(b.track_id))S.trackMap.set(b.track_id,[]);
  S.trackMap.get(b.track_id).push(b);
 }
}
function resetDecisions(){S.decisions=Object.fromEntries(Object.entries(S.tasks).map(([id,t])=>[id,t.status]));S.selectedIssue=null}
function readMeta(){const width=Number(el('imageWidth').value),height=Number(el('imageHeight').value),fpsVal=Number(el('fps').value);
 const cfgVal = S.activeConfig || S.meta?.config || el('cvatConfigSelect')?.value || 'config_project_3.yaml';
 return {...S.meta,job_id:S.cvatJob?.id||S.meta?.job_id||null,video_token:S.videoToken||S.meta?.video_token||null,width:width>0?width:null,height:height>0?height:null,fps:fpsVal>0?fpsVal:(S.meta.fps||5.0),config:cfgVal}}
function setMetadata(data,name){
 clearMedia(); // Prevent previous dataset/demo media from leaking into a newly imported XML/MOT.
 S.boxes=(data.boxes||[]).filter(b=>!b.outside);S.meta={format:data.format,frame_base:data.frame_base??0,mode:data.mode||'video',
 width:data.width||null,height:data.height||null,total_frames:data.total_frames||null,labels:data.labels||[],task_name:data.task_name||''};
 S.session=(S.projectId && S.session && !restoringProject)?S.session:data.session;S.sourceName=data.source_name||name;S.undo=[];S.redo=[];S.selectedUid=null;S.selectedTrackId=null;S.issues=[];if(!S.projectId)S.tasks={};resetDecisions();S.lastScan=null;
 if(S.meta.width)el('imageWidth').value=S.meta.width;
 if(S.meta.height)el('imageHeight').value=S.meta.height;
 if(!S.meta.width)el('imageWidth').value='';if(!S.meta.height)el('imageHeight').value='';
 el('videoStart').value=0;el('videoStep').value=1;
 el('datasetName').textContent=S.sourceName; indexBoxes();undoButtons();
 S.frame=S.boxes.length?S.boxes.reduce((m,b)=>Math.min(m,b.frame_id),Infinity):Number(S.meta.frame_base||0);refreshAll();
 queueProjectSave();
 (data.warnings||[]).forEach(w=>toast(w));
}
function maxFrame(){const arr=S.boxes.map(b=>b.frame_id);return arr.length?Math.max(...arr):S.frame}
function minFrame(){const arr=S.boxes.map(b=>b.frame_id);return arr.length?Math.min(...arr):S.frame}
function timelineBounds(){const start=S.meta.frame_base??minFrame();return {start:Math.min(start,minFrame()),end:Math.max(maxFrame(),start+(S.meta.total_frames||0)-1)}}
function setFrame(f){const b=timelineBounds();S.frame=Math.max(b.start,Math.min(b.end,Number(f)));el('timeline').value=S.frame;
 el('frameLabel').textContent=`Frame ${S.frame}`;el('frameRange').textContent=`${b.start} – ${b.end}`;
 let targetUid=null;
 if(S.selectedTrackId!==null&&S.selectedTrackId!==undefined){
  const frameBoxes=S.frameMap?.get(S.frame)||[];
  const match=frameBoxes.find(x=>x.track_id===S.selectedTrackId);
  if(match)targetUid=match.uid;
 }
 selectBox(targetUid,{keepTrack:S.selectedTrackId!==null&&S.selectedTrackId!==undefined});
 seekVideo();updateMediaStatus();draw();renderFrameBoxes();queueProjectSave()}
let playInterval=null;
function togglePlayback(){
 if(playInterval){
  clearInterval(playInterval);
  playInterval=null;
  if(el('btnPlayPause'))el('btnPlayPause').textContent='▶';
 } else {
  const fps=Number(el('playbackFps')?.value||5);
  if(el('btnPlayPause'))el('btnPlayPause').textContent='⏸';
  playInterval=setInterval(()=>{
   const b=timelineBounds();
   if(S.frame>=b.end)setFrame(b.start);
   else setFrame(S.frame+1);
  }, 1000/fps);
 }
}
function refreshTimeline(){
 const b=timelineBounds();
 const span=Math.max(1, b.end-b.start);
 el('timeline').min=b.start;el('timeline').max=b.end;
 el('timeline').disabled=!S.boxes.length&&!S.videoToken&&!S.mediaFiles.length;
 el('frameCount').textContent=`${b.end-b.start+1} frames`;

 // 1. Render Coverage Bar (dải mật độ bounding box)
 const covEl=el('coverageBar');
 if(covEl){
  covEl.innerHTML='';
  if(S.boxes.length){
   const bucketsCount=Math.min(250, span+1);
   const filled=new Array(bucketsCount).fill(false);
   for(const box of S.boxes){
    const f=box.frame_id;
    if(f>=b.start && f<=b.end){
     const idx=Math.min(bucketsCount-1, Math.floor(((f-b.start)/(span+1))*bucketsCount));
     filled[idx]=true;
    }
   }
   for(let k=0; k<bucketsCount; k++){
    const bucketDiv=document.createElement('div');
    bucketDiv.className='cov-bucket'+(filled[k]?' filled':'');
    covEl.appendChild(bucketDiv);
   }
  }
 }

 // 2. Render Interactive Issue Markers
 const marksEl=el('timelineMarks');
 marksEl.innerHTML='';
 if(b.end<=b.start)return;
 
 const marksMap=new Map();
 const sevVal={high:3,medium:2,low:1};
 for(const i of S.issues){
  const existing=marksMap.get(i.frame);
  if(!existing || (sevVal[i.severity]||1) > (sevVal[existing.severity]||1)){
   marksMap.set(i.frame, i);
  }
 }

 [...marksMap.values()].slice(0, 500).forEach(i=>{
  const btn=document.createElement('button');
  btn.className=i.severity||'low';
  btn.style.left=`${(100*(i.frame-b.start)/span)}%`;
  btn.title=`${i.type} · Frame ${i.frame} · Track #${i.track_id}\n(Bấm để nhảy tới frame này)`;
  btn.onclick=(ev)=>{
   ev.stopPropagation();
   setFrame(i.frame);
   S.selectedIssue=i.id;
   renderIssues();
  };
  marksEl.appendChild(btn);
 });
}
function metrics(){const tracks=new Set(S.boxes.map(b=>b.track_id));el('metricBoxes').textContent=S.boxes.length.toLocaleString('vi-VN');
 el('metricTracks').textContent=tracks.size.toLocaleString('vi-VN');el('metricIssues').textContent=S.issues.length.toLocaleString('vi-VN');
 el('metricHigh').textContent=S.issues.filter(i=>i.severity==='high').length;
 el('issueSubtitle').textContent=S.lastScan?'Cần xác nhận từng issue':'Chưa quét';
 const reviewed=S.issues.filter(i=>S.decisions[i.id]).length;
 el('metricProgress').textContent=S.issues.length?Math.round(100*reviewed/S.issues.length)+'%':'—';
 el('issueCount').textContent=S.issues.length;if(el('issueCountSub'))el('issueCountSub').textContent=S.issues.length;if(el('frameBoxesBadge'))el('frameBoxesBadge').textContent=(S.frameMap.get(S.frame)||[]).length;}
function refreshAll(){metrics();refreshTimeline();setFrame(S.frame);renderIssues();setBusy(false)}
async function loadFile(file){try{
 setBusy(true);
 if(canReview()&&S.projectId){
   throw Error('Reviewer không thể thay annotation trong Project đang mở. Hãy mở Project để QC hoặc vào workspace không gắn Project để xem file tạm.');
 }
 let data;
 if(file.name.toLowerCase().endsWith('.zip')){
  const res=await fetch('/api/annotation/import',{method:'POST',headers:{'Content-Type':'application/zip',
    'X-T021-Filename':encodeURIComponent(file.name)},body:file});
  data=await res.json();if(!res.ok)throw Error(data.error||`Import ZIP thất bại: HTTP ${res.status}`);
 } else {
  const content=await file.text();data=await api('parse',{filename:file.name,content,labels_text:S.labelsText});
 }
 // Video can be uploaded before XML; keep it when the user imports annotation.
 const oldVideoToken=S.videoToken,oldVideoMeta=S.videoMeta;
 const start=el('videoStart').value,step=el('videoStep').value;
 setMetadata(data,file.name);
 if(oldVideoToken){
  S.videoToken=oldVideoToken;S.videoMeta=oldVideoMeta;S.mediaMode='server-video';
  el('btnRetryVideo').disabled=false;
  el('videoStart').value=start;el('videoStep').value=step;
  mapVideoFrames();queueProjectSave();
  toast('Đã ghép XML với video đang mở; kiểm tra Video start / Step trước khi sửa.');
 }else toast(`Đã đọc ${S.boxes.length} box; đang quét...`);
 if(canReview()&&!canEdit())toast('Reviewer đã nhập dữ liệu ở chế độ chỉ xem/QC. Annotator cần đưa dataset vào Project trước khi QC được lưu và chuyển sửa.');
 await scan();
 }catch(e){toast(e.message,true)}finally{setBusy(false)}}
function manualStorageKey(){
 if(S.cvatJob && S.cvatJob.id) return 'idcheck_manual_cvat_' + S.cvatJob.id;
 if(S.projectId) return 'idcheck_manual_proj_' + S.projectId;
 return null;
}
function saveManualIssues(){
 const key = manualStorageKey();
 if(!key) return;
 const manuals = (S.issues || []).filter(i => i.manual);
 try{ localStorage.setItem(key, JSON.stringify(manuals)); }catch(e){}
}
function loadSavedManualIssues(){
 const key = manualStorageKey();
 if(!key) return [];
 try{
  const raw = localStorage.getItem(key);
  return raw ? JSON.parse(raw) : [];
 }catch(e){ return []; }
}
async function scan(){
 if(!S.boxes.length)return;
 if(S.cvatJob && S.cvatJobEffectiveRole === 'annotator' && S.user?.role !== 'admin'){
  toast('Chế độ Annotator: Bạn đang xử lý các Issue đã được Reviewer chốt trên CVAT.', true);
  return;
 }
 try{
  setBusy(true);
  const meta=readMeta();
  const r=await api('validate',{boxes:S.boxes,...meta});
  const existingManual = (S.issues || []).filter(i => i.manual);
  const existingCvat = (S.issues || []).filter(i => i.is_cvat_issue);
  const serverIds = new Set((r.issues || []).map(i => i.id));
  const preservedManual = existingManual.filter(i => !serverIds.has(i.id));
  const preservedCvat = existingCvat.filter(i => !serverIds.has(i.id));
  S.issues = [...preservedManual, ...preservedCvat, ...(r.issues || [])];
  S.lastScan=r;
  saveManualIssues();
  if(el('cvatBannerSubtitle')&&S.cvatJob){
   el('cvatBannerSubtitle').textContent=`Stage: ${(S.cvatJob.stage||'').toUpperCase()} (${S.cvatJob.state||''}) · Assignee: ${S.cvatJob.assignee||'Chưa gán'} · ${S.issues.length} issues · ⚙ ${meta.config}`;
  }
  renderIssues();metrics();refreshTimeline();draw();
  toast(`Hoàn tất: ${r.count} cảnh báo (${meta.config}), ${r.stats.tracks} track`);
 }catch(e){toast(e.message,true)}finally{setBusy(false)}
}
async function loadDemo(){try{setBusy(true);const data=await api('demo');setMetadata(data,'Demo (dữ liệu giả lập)');await scan()}catch(e){toast(e.message,true)}finally{setBusy(false)}}
function localVideoFrame(){return Math.max(0,S.frame-(S.meta.frame_base??0))}
function seekVideo(){/* v2.2: decoded source-index frames fetched via FFmpeg; no inaccurate browser seek */}
// Image/annotation alignment. For CVAT video XML with N frames and N images,
// images sorted by filename MUST map to XML frames 0..N-1; suffix digits
// may be original sampled video frame numbers (1,4,7,...,201).
function clearMedia(){
 for(const u of S.imageURLs.values())if(u.startsWith('blob:'))URL.revokeObjectURL(u);
 S.imageURLs.clear();S.images.clear();S.mediaFiles=[];S.mediaNames.clear();
 S.mediaMode='none';S.mediaToken=null;S.videoToken=null;S.videoMeta=null;S.videoError='';
 el('btnRetryVideo').disabled=true;
 if(S.videoURL)URL.revokeObjectURL(S.videoURL);S.videoURL=null;
 const v=el('videoElement');v.pause();v.removeAttribute('src');v.load();
 el('fileImages').value='';el('fileVideo').value='';
 el('imageMapping').value='auto';updateMediaStatus();
 S.zoom=1.0;S.panX=0;S.panY=0;updateZoomBadge();
}
async function chooseVideo(file){
 // Video can be loaded alone for a new project; annotation is optional at import time.
 try{
  setBusy(true);toast('Đang upload và kiểm tra codec video. Video dài có thể mất một chút thời gian...');
  const res=await fetch('/api/video/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream',
    'X-T021-Filename':encodeURIComponent(file.name)},body:file});
  let info;try{info=await res.json()}catch(_){throw Error('Server không trả JSON')}
  if(!res.ok)throw Error(info.error||'Lỗi tải video');
  clearMedia();
  if(!S.boxes.length){
    S.meta={...S.meta,format:'video',mode:'video',frame_base:0,total_frames:info.frame_count||1,labels:S.meta.labels||[]};
    S.frame=0;S.sourceName=file.name;el('datasetName').textContent=file.name;
  }
  S.videoToken=info.token;S.videoMeta=info;S.mediaMode='server-video';
  el('btnRetryVideo').disabled=false;
  el('imageWidth').value=info.width;el('imageHeight').value=info.height;
  el('fps').value=info.fps||30;
  mapVideoFrames();refreshAll();queueProjectSave();
  if(S.boxes.length&&info.frame_count&&Number(S.meta.total_frames)>0&&info.frame_count!==Number(S.meta.total_frames)){
    toast(`Video có ${info.frame_count} frame, XML có ${S.meta.total_frames}. Nếu XML được lấy mẫu, chỉnh Video start / Step.`,true);
  }else toast(`Đã tải ${file.name} (${info.codec}, ${info.width}×${info.height}). Đang giải mã frame đầu...`);
 }catch(e){toast('Không mở được video: '+e.message,true)}finally{setBusy(false)}
}
function mapVideoFrames(){
 if(!S.videoToken)return;
 for(const url of S.imageURLs.values())if(url.startsWith('blob:'))URL.revokeObjectURL(url);
 S.images.clear();S.imageURLs.clear();S.mediaNames.clear();S.videoError='';
 const start=Math.max(0,Math.trunc(Number(el('videoStart').value)||0));
 const step=Math.max(1,Math.trunc(Number(el('videoStep').value)||1));
 const bounds=timelineBounds(),base=Number(S.meta.frame_base||0);
 const cap=Math.min(25000,bounds.end);
 for(let frame=bounds.start;frame<=cap;frame++){
  const sourceFrame=start+(frame-base)*step;
  if(sourceFrame<0||S.videoMeta?.frame_count&&sourceFrame>=S.videoMeta.frame_count)continue;
  S.imageURLs.set(frame,`/api/video/${encodeURIComponent(S.videoToken)}/frame/${sourceFrame}`);
  S.mediaNames.set(frame,`${S.videoMeta?.original_name||'video'} · source frame ${sourceFrame}`);
 }
 updateMediaStatus();draw();
}
function parseImageFrame(s){const name=s.replace(/\.[^.]+$/,'');const parts=name.match(/\d+/g);return parts?Number(parts[parts.length-1]):null}
function currentMediaMapping(){
 const requested=el('imageMapping').value;
 if(requested!=='auto')return requested;
 const expected=Number(S.meta.total_frames)||0;
 if(expected>0&&expected===S.mediaFiles.length)return 'sequence';
 const lo=timelineBounds().start,hi=timelineBounds().end;
 const ids=S.mediaFiles.map(f=>parseImageFrame(f.name));
 const valid=ids.filter(n=>n!==null&&n>=lo&&n<=hi);
 return valid.length>=0.7*ids.length&&new Set(valid).size===valid.length?'filename':'sequence';
}
function mapImages(){
 for(const u of S.imageURLs.values())if(u.startsWith('blob:'))URL.revokeObjectURL(u);
 S.imageURLs.clear();S.mediaNames.clear();S.images.clear();
 if(!S.mediaFiles.length){updateMediaStatus();draw();return}
 const mode=currentMediaMapping();S.mediaMode=mode;
 const frameBase=Number(S.meta.frame_base??0);
 for(let i=0;i<S.mediaFiles.length;i++){
   const file=S.mediaFiles[i];const frame=mode==='filename'?parseImageFrame(file.name):frameBase+i;
   if(frame===null||S.imageURLs.has(frame))continue;
   S.imageURLs.set(frame,file.url||URL.createObjectURL(file.file));
   S.mediaNames.set(frame,file.name);
 }
 updateMediaStatus();draw();
 const expected=Number(S.meta.total_frames)||0;
 if(expected&&expected!==S.mediaFiles.length)toast(`Cảnh báo: XML có ${expected} frame nhưng chỉ tải ${S.mediaFiles.length} ảnh. Kiểm tra cách ghép frame.`,true);
 else toast(`Đã ghép ${S.imageURLs.size} ảnh theo ${mode==='sequence'?'thứ tự danh sách (đúng XML)':'số trong tên file'}.`);
}
function chooseImages(files){
 if(!S.boxes.length)return toast('Hãy tải XML/MOT trước khi chọn ảnh.',true);
 if(S.videoURL){URL.revokeObjectURL(S.videoURL);S.videoURL=null;el('videoElement').removeAttribute('src');el('videoElement').load()}
 // Revoke the previous browser-local image resources before loading fresh media.
 for(const u of S.imageURLs.values())if(u.startsWith('blob:'))URL.revokeObjectURL(u);
 S.imageURLs.clear();S.images.clear();S.mediaNames.clear();S.mediaToken=null;S.videoToken=null;S.videoMeta=null;
 const items=[...files].filter(f=>f.type.startsWith('image/')||/\.(jpe?g|png|webp|gif)$/i.test(f.name));
 items.sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));
 if(!items.length)return toast('Không có ảnh hợp lệ. Nếu ảnh ở trong ZIP, dùng nút Tải frames.zip.',true);
 S.mediaFiles=items.map(file=>({name:file.name,file}));
 S.mediaMode='images';mapImages();queueProjectSave();
}
async function chooseZip(file){
 if(!S.boxes.length)return toast('Hãy tải annotation XML/MOT trước khi tải ZIP.',true);
 try{
   setBusy(true);toast('Đang đọc ZIP ảnh và kiểm tra mapping...');
   const res=await fetch('/api/media/upload-zip',{method:'POST',headers:{'Content-Type':'application/zip',
        'X-T021-Task':(S.meta.task_name||'').replace(/[^a-zA-Z0-9._ -]/g,'').slice(0,100),
        'X-T021-Frame-Count':String(S.meta.total_frames||'')},body:file});
   let data;try{data=await res.json()}catch(e){throw Error('Server không phản hồi JSON')}
   if(!res.ok)throw Error(data.error||'Không tải được ZIP');
   for(const u of S.imageURLs.values())if(u.startsWith('blob:'))URL.revokeObjectURL(u);
   S.imageURLs.clear();S.images.clear();S.mediaNames.clear();
   if(S.videoURL){URL.revokeObjectURL(S.videoURL);S.videoURL=null;el('videoElement').removeAttribute('src');el('videoElement').load()}
   S.videoToken=null;S.videoMeta=null;S.mediaToken=data.token;S.mediaFiles=data.files.map((name,index)=>({name,url:`/api/media/${encodeURIComponent(data.token)}/${index}`}));
   mapImages();queueProjectSave();
   if(data.warnings?.length)openDialog('Cảnh báo ghép dữ liệu',`<p>${data.warnings.map(htmlEscape).join('</p><p>')}</p>`,[{label:'Đã hiểu',onClick:closeDialog}]);
 }catch(e){toast('Không thể tải frames.zip: '+e.message,true)}finally{setBusy(false)}
}
function updateMediaStatus(){
 const info=el('mediaInfo');if(!info)return;
 if(S.videoToken){const sourceFrame=Math.max(0,Math.trunc(Number(el('videoStart').value)||0))+(S.frame-Number(S.meta.frame_base||0))*Math.max(1,Math.trunc(Number(el('videoStep').value)||1));
 info.textContent=S.videoError?`⚠ Video: ${S.videoError}`:`Video: ${S.videoMeta?.original_name||'video'} · Frame annotation ${S.frame} ↔ video frame ${sourceFrame} · ${S.videoMeta?.codec||''}`;info.title=info.textContent;return}
 if(!S.mediaFiles.length){info.textContent=S.boxes.length?'Chưa tải ảnh/video; không dùng ảnh demo cũ':'Chưa có ảnh';info.title=info.textContent;return}
 const name=S.mediaNames.get(S.frame);
 info.textContent=name?`Frame ${S.frame} ↔ ${name} (${S.mediaMode==='sequence'?'theo thứ tự':'theo filename'})`:`⚠ Frame ${S.frame}: không có ảnh tương ứng`;
 info.title=info.textContent;
}
function canvasParams(){const m=readMeta(),c=el('viewer');let w=m.width||960,h=m.height||540;
 if(w>0&&h>0){const outW=Math.min(1280,Math.max(300,w));const outH=Math.round(outW*h/w);
 if(c.width!==outW||c.height!==outH){c.width=outW;c.height=outH}}
 return {ctx:c.getContext('2d'),c,w,h,sx:c.width/w,sy:c.height/h};}
async function describeVideoFrameFailure(url,frame,img){
 let message=`Frame ${frame} không hiển thị được`;
 try{
  const res=await fetch(url,{cache:'no-store'});
  if(!res.ok){
   const result=await res.json().catch(()=>({}));
   message=`Frame ${frame}: HTTP ${res.status} · ${result.error||'lỗi API video'}`;
  }else message=`Frame ${frame}: server trả ảnh nhưng trình duyệt không đọc được JPEG`;
 }catch(e){message=`Frame ${frame}: lỗi kết nối backend (${e.message})`}
 if(S.images.get(frame)===img&&S.videoToken&&S.frame===frame){S.videoError=message;updateMediaStatus();toast(message,true);draw()}
}
function imageAtFrame(){if(S.images.has(S.frame))return S.images.get(S.frame);
 const url=S.imageURLs.get(S.frame);if(!url)return null;
 const img=new Image();const requestFrame=S.frame;
 img.onload=()=>{if(S.images.get(requestFrame)!==img)return;if(S.mediaMode==='server-video'&&S.frame===requestFrame){S.videoError='';updateMediaStatus()}draw()};
 img.onerror=()=>{if(S.images.get(requestFrame)!==img)return;if(S.videoToken){
  if(S.frame===requestFrame)S.videoError=`Đang kiểm tra lỗi giải mã frame ${requestFrame}...`;updateMediaStatus();
  describeVideoFrameFailure(url,requestFrame,img)
 }else draw()};
 S.images.set(S.frame,img);img.src=url;
 if(S.images.size>72)S.images.delete(S.images.keys().next().value);return img}
function colors(t){const arr=['#51a4fe','#27d1ae','#ffc46a','#e98fff','#7bda65','#f88868','#9eadfa'];let k=(Math.abs(t)*37)%arr.length;return arr[k]}
function draw(){const {ctx,c,w,h,sx,sy}=canvasParams();ctx.fillStyle='#152139';ctx.fillRect(0,0,c.width,c.height);
 ctx.save();ctx.translate(S.panX,S.panY);ctx.scale(S.zoom,S.zoom);
 const img=imageAtFrame(),v=el('videoElement');
 if(img&&img.complete&&img.naturalWidth){ctx.drawImage(img,0,0,c.width,c.height)}
 else if(S.videoURL&&v.readyState>=2){try{ctx.drawImage(v,0,0,c.width,c.height)}catch(e){}}
 else {ctx.strokeStyle='#ffffff10';ctx.lineWidth=1/S.zoom;for(let x=0;x<c.width;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,c.height);ctx.stroke()}for(let y=0;y<c.height;y+=40){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(c.width,y);ctx.stroke()}
   ctx.font='bold 19px system-ui';ctx.fillStyle='#fff';ctx.textAlign='center';
   ctx.fillText(S.videoError?'Không đọc được frame video':S.videoToken?'Đang tải frame video...':S.mediaFiles.length?`Chưa có ảnh cho Frame ${S.frame}`:'Chưa tải ảnh/video của dataset này',c.width/2,c.height/2);ctx.textAlign='left';}
 const rows=S.frameMap.get(S.frame)||[];
 const labelScale=Math.min(1.4,Math.max(0.6,1/S.zoom));
 const fontSize=Math.round(12*labelScale);
 ctx.textBaseline='top';ctx.font=`bold ${fontSize}px system-ui`;
 if(S.selectedTrackId!==null&&S.selectedTrackId!==undefined&&S.trackMap?.has(S.selectedTrackId)){
  const trackBoxes=S.trackMap.get(S.selectedTrackId)||[];
  if(trackBoxes.length>1){
   const nearby=trackBoxes.filter(tb=>Math.abs(tb.frame_id-S.frame)<=25).sort((a,b)=>a.frame_id-b.frame_id);
   if(nearby.length>1){
    ctx.save();
    ctx.lineWidth=Math.max(1, 1.8/S.zoom);
    ctx.setLineDash([4/S.zoom, 3/S.zoom]);
    ctx.strokeStyle='rgba(56, 189, 248, 0.45)';
    ctx.beginPath();
    let started=false;
    for(const tb of nearby){
     const cx=(tb.x+tb.w/2)*sx, cy=(tb.y+tb.h/2)*sy;
     if(!started){ ctx.moveTo(cx,cy); started=true; }
     else { ctx.lineTo(cx,cy); }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    for(const tb of nearby){
     if(tb.frame_id===S.frame)continue;
     const cx=(tb.x+tb.w/2)*sx, cy=(tb.y+tb.h/2)*sy;
     const r=Math.max(2, 3/S.zoom);
     ctx.beginPath();
     ctx.arc(cx,cy,r,0,Math.PI*2);
     ctx.fillStyle=tb.frame_id<S.frame?'rgba(56, 189, 248, 0.65)':'rgba(192, 132, 252, 0.65)';
     ctx.fill();
    }
    ctx.restore();
   }
  }
 }
 const unselected=[], selectedArr=[];
 for(const b of rows){
  if((S.selectedUid && b.uid===S.selectedUid) || (S.selectedTrackId!==null && b.track_id===S.selectedTrackId)) selectedArr.push(b);
  else unselected.push(b);
 }
 const drawList=[...unselected, ...selectedArr];
 for(const b of drawList){
    const selected=Boolean((S.selectedUid && b.uid && b.uid===S.selectedUid) || (S.selectedTrackId!==null && b.track_id===S.selectedTrackId));
    const hovered=Boolean(!selected && S.hoveredUid && b.uid && b.uid===S.hoveredUid);
    const col=selected?'#fff000':colors(b.track_id);
    const x=b.x*sx,y=b.y*sy,bw=b.w*sx,bh=b.h*sy;
    const strokeW=Math.max(1,(selected?3:hovered?2.6:1.8)/S.zoom);
    ctx.strokeStyle=hovered?'#ffffff':col;ctx.lineWidth=strokeW;ctx.strokeRect(x,y,bw,bh);
    const text=`#${b.track_id} · ${b.class_name}`;const tw=ctx.measureText(text).width+(8*labelScale);
    const tagH=18*labelScale;
    if(selected){ctx.shadowColor='rgba(0,0,0,0.55)';ctx.shadowBlur=6/S.zoom;ctx.shadowOffsetY=2/S.zoom}
    ctx.fillStyle=selected?'#edce00':hovered?'#243756':'#152139db';ctx.fillRect(x,Math.max(0,y-tagH-1),tw,tagH);
    if(selected){ctx.shadowColor='transparent';ctx.shadowBlur=0;ctx.shadowOffsetY=0}
    ctx.fillStyle=selected?'#182033':col;ctx.fillText(text,x+(4*labelScale),Math.max(0,y-tagH));
    if(selected){const handleSize=Math.max(4,8/S.zoom);ctx.fillStyle='#fff000';ctx.fillRect(x+bw-handleSize/2,y+bh-handleSize/2,handleSize,handleSize)}
  }
 if(S.drag?.type==='create'&&S.drag.preview){const [x,y,bw,bh]=S.drag.preview;ctx.setLineDash([7/S.zoom,5/S.zoom]);ctx.strokeStyle='#66ffcb';ctx.lineWidth=2/S.zoom;ctx.strokeRect(x*sx,y*sy,bw*sx,bh*sy);ctx.setLineDash([])}
 ctx.restore();
 el('canvasMessage').hidden=!!S.boxes.length||!!S.videoToken||!!S.mediaFiles.length;
 updateZoomBadge();
}
function toWorld(evt){const c=el('viewer'),r=c.getBoundingClientRect(),m=canvasParams();
 const sx=(evt.clientX-r.left)*(c.width/r.width);
 const sy=(evt.clientY-r.top)*(c.height/r.height);
 const cx=(sx-S.panX)/S.zoom;
 const cy=(sy-S.panY)/S.zoom;
 return {x:cx*(m.w/c.width),y:cy*(m.h/c.height)};
}
function hit(p){const rawRows=S.frameMap.get(S.frame)||[];const m=canvasParams();
 const corner=Math.max(5,10/(m.sx*S.zoom));
 const selBox=S.selectedUid?rawRows.find(b=>b.uid===S.selectedUid):null;
 const otherRows=selBox?rawRows.filter(b=>b.uid!==S.selectedUid).reverse():[...rawRows].reverse();
 const rows=selBox?[selBox,...otherRows]:otherRows;
 for(const b of rows){if(Math.abs(p.x-(b.x+b.w))<corner&&Math.abs(p.y-(b.y+b.h))<corner)return {box:b,type:'resize'};
 if(p.x>=b.x&&p.x<=b.x+b.w&&p.y>=b.y&&p.y<=b.y+b.h)return {box:b,type:'move'}}return null}
function selectBox(uid, opts={}){
 const keepTrack=opts.keepTrack??false;
 S.selectedUid=uid||null;
 const b=S.selectedUid?S.boxes.find(x=>x.uid===S.selectedUid):null;
 if(b){
  S.selectedTrackId=b.track_id;
 } else if(!keepTrack){
  S.selectedTrackId=null;
 }
 const edit=canEdit();
 for(const [id,k] of [['fieldUid','uid'],['fieldTrack','track_id'],['fieldClass','class_name'],['fieldX','x'],['fieldY','y'],['fieldW','w'],['fieldH','h']]){
   if(el(id)){
     el(id).value=(b&&b[k]!==undefined)?b[k]:'';
     el(id).disabled=!edit||id==='fieldUid'||!b;
   }
 }
 for(const id of ['btnSaveBox','btnDeleteBox','btnStitch','btnSwap']){
   if(el(id)){
     el(id).disabled=!b||!edit;
     el(id).hidden=!edit;
   }
 }
 const hasTrack=(b!==null)||(S.selectedTrackId!==null&&S.selectedTrackId!==undefined);
 if(el('btnDeselectBox')){
   el('btnDeselectBox').hidden=!hasTrack;
 }
 if(el('boxBarTag')){
   if(b){
     const trackBoxes=S.trackMap?.get(b.track_id)||[];
     const fList=trackBoxes.map(x=>x.frame_id);
     const rangeText=fList.length?` · F${Math.min(...fList)}–${Math.max(...fList)}`:'';
     el('boxBarTag').textContent=`Track #${b.track_id} · ${b.class_name||'Object'}${rangeText}`;
     el('boxBarTag').className='box-bar-tag active';
     const col=colors(b.track_id);
     el('boxBarTag').style.borderColor=col;
     el('boxBarTag').style.color='#fff';
   } else if(S.selectedTrackId!==null&&S.selectedTrackId!==undefined){
     const trackBoxes=S.trackMap?.get(S.selectedTrackId)||[];
     const fList=trackBoxes.map(x=>x.frame_id);
     const rangeText=fList.length?` (Tồn tại ở F${Math.min(...fList)}–${Math.max(...fList)})`:'';
     el('boxBarTag').textContent=`Track #${S.selectedTrackId} · Không có ở Frame ${S.frame}${rangeText}`;
     el('boxBarTag').className='box-bar-tag';
     const col=colors(S.selectedTrackId);
     el('boxBarTag').style.borderColor=col;
     el('boxBarTag').style.color='#cbd5e1';
   } else {
     el('boxBarTag').textContent='Chưa chọn đối tượng';
     el('boxBarTag').className='box-bar-tag';
     el('boxBarTag').style.borderColor='';
     el('boxBarTag').style.color='';
   }
 }
 if(el('boxInspectorHint')){
   if(b){
     el('boxInspectorHint').textContent=edit?'Thay đổi được kiểm tra xung đột trước khi lưu':'Chế độ Reviewer: Chỉ xem';
   } else if(S.selectedTrackId!==null&&S.selectedTrackId!==undefined){
     el('boxInspectorHint').textContent=`Đang theo dõi Track #${S.selectedTrackId} (Chuyển frame sẽ tự bắt box)`;
   } else {
     el('boxInspectorHint').textContent='Nhấp box trên frame để kiểm tra';
   }
 }
 draw();renderFrameBoxes()}
async function doFix(action,data,success='Đã cập nhật annotation'){if(!canEdit())return toast('Chỉ Annotator được sửa annotation.',true);try{setBusy(true);const r=await api('fix',{boxes:S.boxes,action,data,session:S.session});checkpoint();S.boxes=r.boxes;
 indexBoxes();resetDecisions();await scan();selectBox(action==='update_box'?data.uid:null);refreshAll();queueProjectSave();toast(success);
 }catch(e){toast(e.message,true)}finally{setBusy(false)}}
function openDialog(title,body,buttons=[]){el('dialogTitle').textContent=title;el('dialogContent').innerHTML=body;
 const dst=el('dialogButtons');dst.replaceChildren();for(const b of buttons){const btn=document.createElement('button');btn.textContent=b.label;btn.className=b.className||'ghost';btn.onclick=b.onClick;dst.appendChild(btn)};
 el('dialog').hidden=false}
function closeDialog(){el('dialog').hidden=true}
function addTextDialog(title,text,cb){openDialog(title,`<p>${htmlEscape(text)}</p>`,[{label:'Hủy',onClick:closeDialog},
 {label:'Xác nhận',className:'primary',onClick:()=>{closeDialog();cb()}}])}
const safeAction={stitch_ids:'Nối ID',swap_ids:'Hoán đổi ID',interpolate_gap:'Nội suy gap',remove_exact_duplicates:'Xóa box trùng tuyệt đối',set_class:'Đồng bộ class'};
function issueFix(i){const action=i.suggestion,p=i.fix_data||{};
 if(action==='review')return;
 let data={...p};
 if(action==='remove_exact_duplicates')data={frame_id:i.frame,track_id:i.track_id};
 if(action==='stitch_ids')data={source:p.source,target:p.target,from_frame:p.from_frame};
 if(action==='set_class')data={track_id:p.track_id,class_name:p.class_name};
 if(action==='interpolate_gap')data={track_id:p.track_id,from_frame:p.from_frame,to_frame:p.to_frame};
 addTextDialog('Xác nhận sửa nhãn',`Thao tác: ${safeAction[action]||action}.\n${i.message}\nHệ thống kiểm tra xung đột trước khi áp dụng.`,()=>doFix(action,data))}
const statusText={needs_fix:'Cần Annotator sửa',fixed:'Đã sửa · Chờ Reviewer QC',
 verified:'Reviewer đã xác nhận',false_positive:'Đã đánh dấu: Không phải lỗi'};
function handoffDialog(i,status){
 if(!S.projectId)return toast('Hãy tạo hoặc mở Project trước khi gửi yêu cầu QC.',true);
 const isReviewer=canReview(), allowed=(isReviewer&&status!=='fixed')||(canEdit()&&status==='fixed');
 if(!allowed)return toast('Vai trò hiện tại không có quyền đổi trạng thái này.',true);
 const heading={needs_fix:'Gửi lỗi về Annotator',fixed:'Annotator báo đã sửa',
 verified:'Reviewer xác nhận đã sửa',false_positive:'Xác nhận không phải lỗi'}[status];
 const requirement=status==='needs_fix'||status==='fixed';
 openDialog(heading,`<p>${htmlEscape(i.type)} · Frame ${i.frame} · Track ${i.track_id}</p>
 <p>${htmlEscape(i.message||'')}</p><label>Ghi chú ${requirement?'(bắt buộc)':'(tùy chọn)'}
 <textarea id="handoffNote" rows="4" placeholder="${requirement?'Mô tả cần sửa / đã sửa gì':'Ghi nhận kết quả kiểm tra'}"></textarea></label>`,[
 {label:'Hủy',onClick:closeDialog},{label:heading,className:'primary',onClick:async()=>{
 const note=el('handoffNote').value.trim();if(requirement&&!note)return toast('Vui lòng ghi chú nội dung.',true);
 closeDialog();try{
 if(status==='fixed')await saveProject(true);
 const result=await api('workflow/transition',{project_id:S.projectId,issue_id:i.id,
 status,note,issue:{type:i.type,frame:i.frame,track_id:i.track_id,message:i.message}});
 S.tasks=result.tasks;S.decisions=Object.fromEntries(Object.entries(S.tasks).map(([id,t])=>[id,t.status]));
 renderIssues();metrics();toast('Đã lưu vào Project: '+statusText[status]);
 }catch(e){toast(e.message,true)}
 }}]);
}

function switchSideTab(tab){
 const isIssues=tab==='issues';
 el('sideTabIssues')?.classList.toggle('active',isIssues);
 el('sideTabBoxes')?.classList.toggle('active',!isIssues);
 if(el('sidePaneIssues'))el('sidePaneIssues').hidden=!isIssues;
 if(el('sidePaneBoxes'))el('sidePaneBoxes').hidden=isIssues;
 if(!isIssues)renderFrameBoxes();
}
function renderFrameBoxes(){
 const dst=el('frameBoxList');if(!dst)return;
 const rows=S.frameMap.get(S.frame)||[];
 const query=(el('frameBoxSearch')?.value||'').trim().toLowerCase();
 if(el('frameBoxesBadge'))el('frameBoxesBadge').textContent=rows.length;
 if(el('frameBoxesSubtitle'))el('frameBoxesSubtitle').textContent=`Frame ${S.frame} · ${rows.length} đối tượng (click đúp để zoom)`;

 const filtered=rows.filter(b=>{
  if(!query)return true;
  return String(b.track_id).includes(query)||(b.class_name||'').toLowerCase().includes(query);
 });

 dst.replaceChildren();
 if(!filtered.length){
  const empty=document.createElement('div');empty.className='side-empty';
  empty.textContent=rows.length?'Không có box nào khớp với tìm kiếm.':`Frame ${S.frame} chưa có bounding box nào.`;
  dst.appendChild(empty);return;
 }

 for(const b of filtered){
  const isSelected=Boolean(S.selectedUid&&b.uid===S.selectedUid);
  const card=document.createElement('div');
  card.className=`frame-box-card${isSelected?' selected':''}`;
  card.id=`boxCard_${b.uid}`;
  const col=colors(b.track_id);
  const tagsHtml=[
   b.keyframe?'<span class="box-card-tag">Keyframe</span>':'',
   b.occluded?'<span class="box-card-tag">Occluded</span>':'',
   b.outside?'<span class="box-card-tag">Outside</span>':''
  ].filter(Boolean).join('');

  card.innerHTML=`
   <div class="box-card-header">
    <div class="box-card-badge" style="background:${col.stroke}">#${b.track_id}</div>
    <div class="box-card-class" title="${htmlEscape(b.class_name||'unclassified')}">${htmlEscape(b.class_name||'unclassified')}</div>
    <div class="box-card-meta">${Math.round(b.w)}×${Math.round(b.h)}</div>
   </div>
   <div class="box-card-coords">Pos: (${Math.round(b.x)}, ${Math.round(b.y)}) · UID: ${htmlEscape(String(b.uid).slice(0,8))}</div>
   ${tagsHtml?`<div class="box-card-tags">${tagsHtml}</div>`:''}
  `;

  card.title="Click để chọn · Click đúp để zoom tới box";
  card.onclick=(e)=>{
   e.stopPropagation();
   if(e.detail>1)return;
   if(S.selectedUid===b.uid){
    selectBox(null);
   } else {
    selectBox(b.uid);
   }
  };
  card.ondblclick=(e)=>{
   e.stopPropagation();
   selectBox(b.uid);
   zoomToBox(b, true);
  };
  dst.appendChild(card);
 }

 if(S.selectedUid){
  const selEl=dst.querySelector('.frame-box-card.selected');
  if(selEl&&dst.scrollHeight>dst.clientHeight){
   selEl.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
 }
}

function findIssueBox(i){
 const tid=(i.track_id!==undefined&&i.track_id!==null)?Number(i.track_id):(i.message?Number(i.message.match(/Track\s*#?(\d+)/i)?.[1]):null);
 let matchBox=(S.frameMap&&S.frameMap.get(i.frame)||[]).find(b=>b.track_id===tid);
 if(!matchBox&&tid!=null&&S.trackMap&&S.trackMap.has(tid)){
  const trBoxes=S.trackMap.get(tid);
  if(trBoxes&&trBoxes.length>0){
   matchBox=trBoxes.reduce((p,c)=>Math.abs(c.frame_id-i.frame)<Math.abs(p.frame_id-i.frame)?c:p);
  }
 }
 return {tid,matchBox};
}

function renderIssues(){
 const dst=el('issueList'),filter=el('issueFilter').value;
 const savedScrollTop = dst ? dst.scrollTop : 0;
 const merged=[...S.issues],seen=new Set(merged.map(i=>i.id));
 for(const [id,t] of Object.entries(S.tasks))if(!seen.has(id))merged.push({id,type:t.type,frame:t.frame,
 track_id:t.track_id,message:t.message||'Issue đã biến mất sau khi sửa – chờ Reviewer xác nhận',severity:'low',archived:true});
 const list=merged.filter(i=>{const status=S.tasks[i.id]?.status||i.status||S.decisions[i.id]||null;
  return filter==='all'||(filter==='manual'&&i.manual)||(filter==='cvat'&&i.is_cvat_issue)||(filter==='open'&&(!status||status==='needs_fix'||status==='fixed'||(i.is_cvat_issue&&!i.resolved)))||
  (filter==='handoff'&&status==='needs_fix')||(filter==='fixed'&&status==='fixed')||
  (filter==='high'&&i.severity==='high')||i.type===filter;
 });
 dst.replaceChildren();if(!list.length){dst.innerHTML='<div class="side-empty">Không có cảnh báo phù hợp. Reviewer và Annotator làm việc qua các yêu cầu đã lưu trong Project.</div>';return}
 if(list.length>250){const note=document.createElement('p');note.className='side-empty';note.textContent=`Hiển thị 250/${list.length} issue`;dst.appendChild(note)}
 for(const i of list.slice(0,250)){
  const t=S.tasks[i.id],status=t?.status||i.status||S.decisions[i.id]||null;
  const e=document.createElement('article');e.className=`issue-card ${i.severity||'low'}${S.selectedIssue===i.id?' selected':''}${status?' reviewed':''}`;
  const trackLabel = (i.track_id !== undefined && i.track_id !== null) ? `Track #${i.track_id}` : '';
  e.innerHTML=`<div class="issue-headline">
   <div class="issue-title-group">
    <b>${htmlEscape(i.type)}</b>
    <span class="issue-pill-badge frame-pill">Frame ${i.frame}</span>
    ${trackLabel ? `<span class="issue-pill-badge track-pill">${htmlEscape(trackLabel)}</span>` : ''}
    ${i.is_cvat_issue ? `<span class="issue-pill-badge" style="background:rgba(59,130,246,0.2);color:#60a5fa;border:1px solid rgba(59,130,246,0.4);" title="Issue liên thông trực tiếp từ CVAT">🏷️ CVAT #${i.cvat_issue_id}</span>` : ''}
    ${i.is_cvat_issue ? (i.resolved ? `<span class="issue-pill-badge" style="background:rgba(16,185,129,0.2);color:#34d399;border:1px solid rgba(16,185,129,0.4);">✓ Resolved</span>` : `<span class="issue-pill-badge" style="background:rgba(239,68,68,0.2);color:#f87171;border:1px solid rgba(239,68,68,0.4);">⚡ Open</span>`) : ''}
    ${i.manual ? `<span class="issue-pill-badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;border:1px solid rgba(245,158,11,0.4);" title="Issue do Reviewer tự tạo thủ công">✍️ Reviewer Manual</span>` : ''}
    ${i.pushed_to_cvat ? `<span class="issue-pill-badge" style="background:rgba(16,185,129,0.2);color:#34d399;border:1px solid rgba(16,185,129,0.4);" title="Đã raise trực tiếp lên CVAT">⚡ CVAT Synced</span>` : ''}
    ${(i.fix_data?.reid_swap_score||i.data?.reid_swap_score)?`<span class="issue-pill-badge" style="background:rgba(99,102,241,0.2);color:#818cf8;border:1px solid rgba(99,102,241,0.4);" title="Xác thực ngoại quan bởi Visual Re-ID">👁 Re-ID ${Math.round((i.fix_data?.reid_swap_score||i.data?.reid_swap_score)*100)}%</span>`:(i.fix_data?.reid_visual_sim||i.data?.reid_visual_sim)?`<span class="issue-pill-badge" style="background:rgba(16,185,129,0.2);color:#34d399;border:1px solid rgba(16,185,129,0.4);" title="Độ tương đồng ngoại quan Re-ID">👁 Re-ID ${Math.round((i.fix_data?.reid_visual_sim||i.data?.reid_visual_sim)*100)}%</span>`:''}
   </div>
   <span class="sev ${i.severity||'low'}">${htmlEscape(i.severity||'low')}</span>
  </div>
 ${status?`<span class="issue-status ${status==='false_positive'?'false-positive':''}">${htmlEscape(statusText[status]||status)}</span>`:''}
 <p class="issue-message">${htmlEscape(i.message)}</p>
 ${t?.note?`<div class="issue-note">${htmlEscape(t.updated_by)}: ${htmlEscape(t.note)}</div>`:''}
 <div class="issue-actions"></div>`;
  const actions=e.querySelector('.issue-actions');
  const make=(label,cls,fn)=>{const b=document.createElement('button');b.textContent=label;b.className=cls;
   b.onclick=ev=>{ev.stopPropagation();fn()};actions.appendChild(b)};
  make('◎ Xem frame','',()=>{
   showPage('workspace');S.selectedIssue=i.id;
   const {tid,matchBox}=findIssueBox(i);
   if(matchBox){
    if(matchBox.frame_id!==S.frame)setFrame(matchBox.frame_id);
    else setFrame(i.frame);
    selectBox(matchBox.uid);
    zoomToBox(matchBox,true);
   }else{
    setFrame(i.frame);
    selectBox(null);
   }
   renderIssues();
  });

  // 1-Click Auto-Fixing Buttons (Chỉ dành cho Annotator và Admin - Reviewer không trực tiếp sửa box)
  if(canEdit()){
   const p=i.fix_data||{};
   if(i.type==='ID Switch Candidate'||i.suggestion==='swap_ids'){
    let a=p.a||i.track_id;
    let b=p.b||p.target;
    if(!b&&p.pair){
     const parts=String(p.pair).split('_');
     if(parts.length>=2)b=Number(parts[1]===String(a)?parts[0]:parts[1]);
    }
    if(!b&&i.message){
     const matches=[...i.message.matchAll(/track\s*#?(\d+)/gi)];
     if(matches.length>=2)b=Number(matches[1][1]);
     else if(matches.length===1&&Number(matches[0][1])!==a)b=Number(matches[0][1]);
    }
    if(a&&b&&Number(a)!==Number(b)){
     make('⇄ Hoán đổi ID','autofix-swap',()=>{
      addTextDialog('Hoán đổi Track ID',`Hoán đổi Track #${a} và #${b} từ frame ${i.frame} trở đi?`,()=>
       doFix('swap_ids',{a,b,from_frame:i.frame},`Đã hoán đổi Track #${a} và #${b}`));
     });
     make('⚯ Nối ID','autofix',()=>{
      addTextDialog('Nối Track ID',`Nối đoạn sau frame ${i.frame} của Track #${b} vào Track #${a}?`,()=>
       doFix('stitch_ids',{source:b,target:a,from_frame:i.frame},`Đã nối Track #${b} vào #${a}`));
     });
    }
   }else if(i.type==='Gap Frame'||i.suggestion==='interpolate_gap'){
    let gapLen=(p.to_frame&&p.from_frame)?(p.to_frame-p.from_frame):(p.gap||0);
    if(!gapLen&&i.message){
     const m=i.message.match(/gap\s*(\d+)/i);
     if(m)gapLen=Number(m[1]);
    }
    const fromF=p.from_frame!==undefined?Number(p.from_frame):Number(i.frame);
    const toF=p.to_frame!==undefined?Number(p.to_frame):(fromF+gapLen);
    if(gapLen>=2&&gapLen<=4){
     make('⤸ Nội suy gap','autofix',()=>{
      doFix('interpolate_gap',{track_id:i.track_id,from_frame:fromF,to_frame:toF},`Đã nội suy ${gapLen-1} frame thiếu cho Track #${i.track_id}`);
     });
    }
   }else if(i.type==='Duplicate Track/Frame'||i.type==='Duplicate Object Candidate'||i.suggestion==='remove_exact_duplicates'){
    make('✕ Xóa box trùng','autofix',()=>{
     doFix('remove_exact_duplicates',{frame_id:i.frame,track_id:i.track_id},`Đã xóa box trùng tại frame ${i.frame}`);
    });
   }else if(i.type==='Class Change'||i.suggestion==='set_class'){
    const cls=p.class_name||(i.message.match(/expected\s*(\w+)/i)?i.message.match(/expected\s*(\w+)/i)[1]:null);
    if(cls){
     make(`✎ Thống nhất class (${cls})`,'autofix',()=>{
      doFix('set_class',{track_id:i.track_id,class_name:cls},`Đã thống nhất class cho Track #${i.track_id}`);
     });
    }
   }
  }

  if(canReview()){
   if(S.cvatJob){
    if(status==='false_positive'){
     make('↩ Khôi phục Lỗi thật','fix',()=>{
       delete S.decisions[i.id];
       delete i.status;
       if(S.tasks[i.id]) delete S.tasks[i.id].status;
       renderIssues();
       metrics();
       toast('Đã khôi phục thành Lỗi thật.');
     });
    }else{
     make('✓ Không phải lỗi','reject',()=>{
       S.decisions[i.id]='false_positive';
       i.status='false_positive';
       if(!S.tasks[i.id]) S.tasks[i.id]={};
       S.tasks[i.id].status='false_positive';
       renderIssues();
       metrics();
       toast('Đã đánh dấu: Không phải lỗi (sẽ bỏ qua khi raise lên CVAT).');
     });
    }
    make('✎ Ghi chú issue','outline',()=>{
     openDialog('Ghi chú issue cho Annotator',`
      <p style="margin-bottom:8px"><b>Frame ${i.frame} · Track #${i.track_id} (${htmlEscape(i.type)})</b></p>
      <label>Nội dung mô tả lỗi / Yêu cầu sửa:
        <textarea id="editIssueComment" rows="3" style="width:100%;margin-top:8px;padding:8px;background:var(--card,#1a2035);color:var(--text,#e5e7eb);border:1px solid #374151;border-radius:6px;resize:vertical;">${htmlEscape(i.message||'')}</textarea>
      </label>`,[
       {label:'Hủy',onClick:closeDialog},
       {label:'Lưu ghi chú',className:'primary',onClick:()=>{
         const val=el('editIssueComment')?.value?.trim();
         if(val){
           i.message=val;
           if(!S.tasks[i.id])S.tasks[i.id]={};
           S.tasks[i.id].note=val;
           S.tasks[i.id].updated_by=S.user?.username||'Reviewer';
           renderIssues();
           toast('Đã cập nhật mô tả issue.');
         }
         closeDialog();
       }}
     ]);
    });
   }else{
    if(!status||status==='needs_fix'){
     make('↪ Gửi Annotator','fix',()=>handoffDialog(i,'needs_fix'));
     if(!status)make('✓ Không phải lỗi','reject',()=>{
       if(!S.projectId){
         S.decisions[i.id]='false_positive';
         i.status='false_positive';
         if(!S.tasks[i.id]) S.tasks[i.id]={};
         S.tasks[i.id].status='false_positive';
         renderIssues();
         metrics();
         toast('Đã đánh dấu: Không phải lỗi.');
       }else{
         handoffDialog(i,'false_positive');
       }
     });
    }
    if(status==='false_positive'){
     make('↩ Khôi phục Lỗi thật','fix',()=>{
       delete S.decisions[i.id];
       delete i.status;
       if(S.tasks[i.id]) delete S.tasks[i.id].status;
       renderIssues();
       metrics();
       toast('Đã khôi phục thành Lỗi thật.');
     });
    }
    if(status==='fixed'){
     make('✓ Xác nhận đã sửa','reject',()=>handoffDialog(i,'verified'));
     make('↩ Yêu cầu sửa tiếp','fix',()=>handoffDialog(i,'needs_fix'));
    }
   }
  }else if(canEdit()&&status==='needs_fix')make('✓ Đã sửa · Gửi lại QC','fix',()=>handoffDialog(i,'fixed'));

  if(i.is_cvat_issue && S.cvatJob){
   if(!i.resolved){
    make('✓ Resolved','fix',async ()=>{
     try{
      setBusy(true);
      await api('cvat/issue/status',{job_id:S.cvatJob.id,issue_id:i.cvat_issue_id,resolved:true});
      i.resolved=true;i.severity='low';renderIssues();metrics();toast(`Đã đánh dấu Resolved cho Issue #${i.cvat_issue_id} trên CVAT.`);
     }catch(e){toast(e.message,true)}finally{setBusy(false)}
    });
   }else{
    make('↩ Reopen','fix',async ()=>{
     try{
      setBusy(true);
      await api('cvat/issue/status',{job_id:S.cvatJob.id,issue_id:i.cvat_issue_id,resolved:false});
      i.resolved=false;i.severity='high';renderIssues();metrics();toast(`Đã mở lại Issue #${i.cvat_issue_id} trên CVAT.`);
     }catch(e){toast(e.message,true)}finally{setBusy(false)}
    });
   }
   make('💬 Bình luận','outline',()=>{
    const commentsHtml = (i.comments||[]).map(c=>`<div style="margin-bottom:6px;padding:6px;background:#0f172a;border-radius:4px;font-size:12px;"><b>${htmlEscape(c.author)}:</b> ${htmlEscape(c.message)} <span style="color:#64748b;font-size:11px;">(${c.created_date||''})</span></div>`).join('');
    openDialog(`Hội thoại CVAT Issue #${i.cvat_issue_id}`,`
      <p style="margin-bottom:6px"><b>Frame ${i.frame} · Trạng thái: ${i.resolved?'Đã giải quyết (Resolved)':'Đang mở (Open)'}</b></p>
      <div style="max-height:160px;overflow-y:auto;margin-bottom:8px;">${commentsHtml||'<p style="color:#94a3b8;font-size:12px;">Chưa có bình luận trao đổi nào.</p>'}</div>
      <label>Thêm phản hồi tới Annotator / Reviewer trên CVAT:
        <textarea id="cvatIssueNewComment" rows="2" style="width:100%;margin-top:6px;padding:8px;background:#1e293b;color:#f8fafc;border:1px solid #374151;border-radius:6px;"></textarea>
      </label>
    `,[
      {label:'Đóng',onClick:closeDialog},
      {label:'Gửi phản hồi lên CVAT',className:'primary',onClick:async()=>{
        const txt=el('cvatIssueNewComment')?.value?.trim();
        if(!txt)return toast('Vui lòng nhập nội dung bình luận.',true);
        closeDialog();
        try{
          setBusy(true);
          const res=await api('cvat/issue/status',{job_id:S.cvatJob.id,issue_id:i.cvat_issue_id,comment:txt});
          if(res.comment) (i.comments=i.comments||[]).push(res.comment);
          renderIssues();
          toast('Đã gửi phản hồi lên CVAT.');
        }catch(e){toast(e.message,true)}finally{setBusy(false)}
      }}
    ]);
   });
  }

  if(i.manual){
   if(canReview()&&S.cvatJob&&!i.pushed_to_cvat){
    make('⚡ Đẩy lên CVAT','fix',async ()=>{
     try{
      setBusy(true);
      toast('Đang đẩy issue lên CVAT...');
      const res=await api('cvat/job/raise-issue',{
       job_id:S.cvatJob.id,
       issue:i
      });
      i.pushed_to_cvat=true;
      saveManualIssues();
      renderIssues();
      toast(res.message||`Đã đẩy Issue Frame ${i.frame} lên CVAT thành công!`);
     }catch(err){
      toast('Lỗi khi đẩy lên CVAT: '+(err.message||err),true);
     }finally{
      setBusy(false);
     }
    });
   }
   if(canReview()||canEdit()){
    make('✕ Xóa Issue','reject',()=>{
     S.issues=S.issues.filter(x=>x.id!==i.id);
     delete S.decisions[i.id];
     delete S.tasks[i.id];
     if(S.selectedIssue===i.id)S.selectedIssue=null;
     saveManualIssues();
     renderIssues();
     metrics();
     draw();
     toast('Đã xóa issue thủ công.');
    });
   }
  }
  e.title="Click để xem · Click đúp để zoom tới đối tượng";
  e.onclick=()=>{
   S.selectedIssue=i.id;
   const {tid,matchBox}=findIssueBox(i);
   if(matchBox){
    if(matchBox.frame_id!==S.frame)setFrame(matchBox.frame_id);
    else setFrame(i.frame);
    selectBox(matchBox.uid);
   }else{
    setFrame(i.frame);
    selectBox(null);
   }
   renderIssues();
  };
  e.ondblclick=(evt)=>{
   evt.stopPropagation();
   S.selectedIssue=i.id;
   const {tid,matchBox}=findIssueBox(i);
   if(matchBox){
    if(matchBox.frame_id!==S.frame)setFrame(matchBox.frame_id);
    else setFrame(i.frame);
    selectBox(matchBox.uid);
    zoomToBox(matchBox,true);
   }else{
    setFrame(i.frame);
    selectBox(null);
    if(i.position && i.position.length >= 4){
     const isDummy = (i.position[0] === 0 && i.position[1] === 0 && i.position[2] <= 100 && i.position[3] <= 100);
     if (!isDummy) {
      const posBox = {
        x: i.position[0],
        y: i.position[1],
        w: Math.max(10, i.position[2] - i.position[0]),
        h: Math.max(10, i.position[3] - i.position[1])
      };
      zoomToBox(posBox, true);
     }
    }
   }
   renderIssues();
  };dst.appendChild(e);
 }
 if(savedScrollTop > 0 && dst) {
  dst.scrollTop = savedScrollTop;
 }
}
function showPage(page){
 S.page=page;
 const isWs=page==='workspace';
 if(el('workspacePage'))el('workspacePage').hidden=!isWs;
 if(el('workspace'))el('workspace').hidden=!isWs;
 if(el('cvatJobsPage'))el('cvatJobsPage').hidden=page!=='cvatJobs';
 if(el('projectsPage'))el('projectsPage').hidden=true;
 if(el('reportPage'))el('reportPage').hidden=page!=='report';
 if(el('auditPage'))el('auditPage').hidden=page!=='audit';
 if(el('importBar'))el('importBar').hidden=true;
 const hasJob=Boolean(S.cvatJob);
 if(el('cvatJobBanner'))el('cvatJobBanner').hidden=!isWs||!hasJob;
 if(el('cvatNoJobNotice'))el('cvatNoJobNotice').hidden=!isWs||hasJob;
 for(const [id,name] of [['navCvatJobs','cvatJobs'],['navWorkspace','workspace'],['navReport','report'],['navAudit','audit']]){
  const b=el(id);if(b)b.classList.toggle('active',page===name);
 }
 if(page==='cvatJobs')renderCvatJobs();
 if(page==='report')renderReport();
 if(page==='audit')renderAudit();
}
function reportObj(){return {project:'T021 Track ID QA Studio',source:S.sourceName,time:new Date().toISOString(),statistics:S.lastScan?.stats||{},issue_count:S.issues.length,
 issue_counts:S.lastScan?.counts||{},issues:S.issues,decisions:Object.fromEntries(Object.entries(S.tasks).map(([id,t])=>[id,t.status])),tasks:S.tasks,notes:S.lastScan?.notes||[],
 warning:'Các issue là nghi vấn tự động; chỉ dữ liệu có ground truth mới đo được Precision/Recall.'}}
function renderReport(){const r=reportObj();const list=Object.entries(r.issue_counts).sort((a,b)=>b[1]-a[1]);
 el('reportContent').innerHTML=`<h3>Tổng quan: ${htmlEscape(S.sourceName||'Chưa có dataset')}</h3>
 <p>Boxes: <b>${r.statistics.boxes||0}</b> · Tracks: <b>${r.statistics.tracks||0}</b> · Frames có nhãn: <b>${r.statistics.annotated_frames||0}</b> · Issues: <b>${r.issue_count}</b></p>
 <h3>Thống kê lỗi</h3><table><thead><tr><th>Loại lỗi</th><th>Số lượng</th></tr></thead><tbody>${list.map(([k,v])=>`<tr><td>${htmlEscape(k)}</td><td>${v}</td></tr>`).join('')||'<tr><td colspan="2">Chưa có dữ liệu</td></tr>'}</tbody></table>
 <h3>Kết quả review</h3><p>Đã kiểm tra: ${Object.keys(S.tasks).length} / ${S.issues.length}. Bản ghi Audit Log được lưu tại server.</p>
 <div class="note">Chưa đo Precision/Recall nếu không có ground truth sự kiện. Smooth ID Switch, Box Drift và Occlusion khó cần ảnh/video để xác nhận.</div>`}
async function renderAudit(){const box=el('auditContent');if(!S.session){box.textContent='Chưa có phiên làm việc.';return}try{const r=await api('audit',{session:S.session});
 box.innerHTML=`<p>${r.events.length} hoạt động được ghi trong phiên làm việc.</p><table><thead><tr><th>Thời gian</th><th>Hoạt động</th><th>Chi tiết</th></tr></thead><tbody>${r.events.slice().reverse().map(e=>`<tr><td>${htmlEscape(new Date(e.at).toLocaleString('vi-VN'))}</td><td>${htmlEscape(e.action)}</td><td><code>${htmlEscape(JSON.stringify(e.detail))}</code></td></tr>`).join('')}</tbody></table>`;
 }catch(e){box.textContent=e.message}}
function download(name,text,mime='text/plain;charset=utf-8'){const blob=new Blob([text],{type:mime});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function exportDialog(){if(!canEdit()||!S.boxes.length)return;openDialog('Xuất dữ liệu đã kiểm duyệt',
 '<p>Chọn định dạng. Xuất MOT/XML sẽ chặn track trùng ID trong cùng frame; CVAT XML còn chặn thay đổi class không nhất quán.</p><label>Định dạng<select id="exportFormat"><option value="mot">MOT TXT + labels.txt</option><option value="cvat">CVAT for video XML</option><option value="json">JSON chuẩn hóa</option></select></label>',
 [{label:'Hủy',onClick:closeDialog},{label:'Xuất file',className:'primary',onClick:async()=>{const fmt=el('exportFormat').value;closeDialog();try{const r=await api('export',{boxes:S.boxes,...readMeta(),format:fmt});
 if(fmt==='json')download(r.filename,JSON.stringify(r.data,null,2),'application/json');
 else {download(r.filename,r.content,fmt==='cvat'?'application/xml':'text/plain');if(fmt==='mot')download('labels.txt',r.labels)}toast('Đã xuất dữ liệu. Vui lòng kiểm tra lại khi import CVAT.')
 }catch(e){toast(e.message,true)}}}])}
function help(){
 openDialog('Hướng dẫn sử dụng CVAT QA Studio',`
  <div style="font-size:13px;line-height:1.6;color:#334155;">
    <p><b>1. Nhận việc & Xem Job từ CVAT:</b><br>
    Truy cập trang <b>CVAT Jobs &amp; QC</b>. Danh sách các Job được phân công hoặc cần review chéo sẽ hiển thị theo tổ chức của bạn. Bấm <b>"Mở xem / QC"</b> hoặc <b>"Bắt đầu làm"</b> để mở Job.</p>
    
    <p><b>2. Tự động đồng bộ Media & Annotation:</b><br>
    Hệ thống tự động nạp frame ảnh chất lượng cao và toàn bộ bounding boxes trực tiếp từ CVAT API. Bấm phím <code>P</code> để phát/dừng video, phím <code>←</code>/<code>→</code> để tua frame, chuột lăn để phóng to/thu nhỏ.</p>
    
    <p><b>3. Tự động kiểm tra lỗi (idcheck Core Rules):</b><br>
    Các lỗi sai lệch nghiêm trọng được tự động phát hiện và đánh dấu trên Timeline:
    <ul style="padding-left:18px;margin:4px 0;">
      <li><b>ID Jump (Vận tốc giật):</b> Bounding box di chuyển bất thường giữa 2 frame liên tiếp.</li>
      <li><b>ID Switch (Tráo đổi ID):</b> Hai đối tượng đi gần hoặc giao cắt nhau bị hoán đổi ID.</li>
      <li><b>Fragmentation / Gap (Đứt quãng):</b> Đối tượng biến mất đột ngột không rõ lý do.</li>
      <li><b>Duplicate Box:</b> Hai box trùng lặp trên cùng 1 đối tượng trong frame.</li>
    </ul>
    </p>

    <p><b>4. Quy trình Phê duyệt & Đẩy về CVAT:</b><br>
    <ul style="padding-left:18px;margin:4px 0;">
      <li><b>Reviewer / Supervisor:</b> Kiểm tra danh sách lỗi. Bấm <b>"✓ Duyệt hoàn thành"</b> để nghiệm thu Job, hoặc bấm <b>"✕ Trả lại Annotator"</b> để hệ thống tự động cắm các điểm Issues lên CVAT cho Annotator sửa.</li>
      <li><b>Annotator:</b> Xem các issue được chỉ định, nắn chỉnh lại box hoặc nối ID (Stitch ID), sau đó bấm <b>"☁️ Đẩy nhãn về CVAT"</b> (hệ thống tự động sao lưu và nén keyframe RDP &lt;0.5px).</li>
      <li><b>Supervisor / Admin:</b> Bấm <b>"⚙️ Tune ngưỡng"</b> để tự động học các quy luật chuyển động từ các Job đã nghiệm thu và tối ưu hóa bộ lọc.</li>
    </ul>
    </p>
  </div>
 `, [{label:'Đã hiểu', className:'primary', onClick:closeDialog}]);
}
// ---------- Local persistent project workspace ----------
let saveTimer=null,saveQueue=Promise.resolve(),restoringProject=false;
function queueProjectSave(){
 if(!S.projectId||restoringProject||!canEdit())return;
 clearTimeout(saveTimer);
 saveTimer=setTimeout(()=>saveProject(true),1250);
}
function projectState(){
 return {source_name:S.sourceName,boxes:S.boxes,meta:readMeta(),decisions:S.decisions,
         frame:S.frame,fps:Number(el('fps').value)||30,video_start:Number(el('videoStart').value)||0,
         video_step:Math.max(1,Number(el('videoStep').value)||1),image_mapping:el('imageMapping').value,
         media:S.videoToken?{kind:'video',token:S.videoToken}:S.mediaToken?{kind:'zip',token:S.mediaToken}:
               S.mediaFiles.length?{kind:'images'}:{kind:'none'}};
}
function updateProjectTitle(){
 if(el('currentProject')) el('currentProject').textContent=S.projectName||'Chưa chọn project';
 if(el('btnSaveProject')) el('btnSaveProject').disabled=!S.projectId||!canEdit();
}
function saveProject(silent=false){
 if(!S.projectId||!canEdit())return Promise.resolve();
 const projectId=S.projectId;
 const snapshot=projectState();
 saveQueue=saveQueue.catch(()=>{}).then(async()=>{
   if(projectId!==S.projectId)return;
   const result=await api('projects/save',{id:projectId,state:snapshot});
   if(!silent)toast(`Đã lưu project ${S.projectName} (${result.project.box_count} box)`);
 }).catch(e=>toast('Lưu project lỗi: '+e.message,true));
 return saveQueue;
}
function askCreateProject(){
 if(!canEdit())return toast('Chỉ Annotator được tạo Project.',true);
 openDialog('Tạo project mới',`<p>Đặt tên project để lưu annotation, kết quả review và media (video/ZIP) trên máy local.</p>
   <label>Tên project<input id="newProjectName" maxlength="100" placeholder="Ví dụ: T021 - Sequence 01"></label>
   <p class="hint">Dữ liệu đang hiển thị sẽ được lưu vào project mới. Ảnh rời phải tải lại; video và frames.zip sẽ được giữ trên server.</p>`,
  [{label:'Hủy',onClick:closeDialog},{label:'Tạo project',className:'primary',onClick:async()=>{
   const name=el('newProjectName').value.trim();if(!name)return toast('Nhập tên project',true);
   closeDialog();try{
    if(S.projectId)await saveProject(true);
    const r=await api('projects/create',{name});S.projectId=r.project.id;S.projectName=r.project.name;
    S.session=r.project.session;S.tasks={};resetDecisions();
    updateProjectTitle();await saveProject(true);await renderProjects();showPage('workspace');toast('Đã tạo project: '+name);
   }catch(e){toast(e.message,true)}
  }}]);
}
async function openProject(id){
 try{
  await saveProject(true);restoringProject=true;
  const r=await api('projects/open',{id}),p=r.project,state=p.state||{};
  S.projectId=p.id;S.projectName=p.name;
  S.session=p.session;
  const meta=state.meta||{};
  setMetadata({...meta,boxes:state.boxes||[],decisions:state.decisions||{},
       session:p.session,source_name:state.source_name||p.name},state.source_name||p.name);
  el('fps').value=state.fps||30;el('videoStart').value=state.video_start||0;
  el('videoStep').value=state.video_step||1;
  el('imageMapping').value=state.image_mapping||'auto';
  if(state.media?.kind==='video'){
    const info=await api('video/info',{token:state.media.token});
    S.videoToken=info.token;S.videoMeta=info;S.mediaMode='server-video';
  el('btnRetryVideo').disabled=false;mapVideoFrames();
  }else if(state.media?.kind==='zip'){
    const info=await api('media/manifest',{token:state.media.token});
    S.mediaToken=info.token;
    S.mediaFiles=info.files.map((name,index)=>({name,url:`/api/media/${encodeURIComponent(info.token)}/${index}`}));
    mapImages();
  }
  S.frame=Number.isFinite(Number(state.frame))?Number(state.frame):S.frame;
  const workflowResult=await api('workflow/list',{project_id:p.id});S.tasks=workflowResult.tasks||{};resetDecisions();
  updateProjectTitle();refreshAll();
  if(S.boxes.length)await scan();
  showPage('workspace');toast(`Đã mở project ${p.name}`);
 }catch(e){toast('Mở project lỗi: '+e.message,true)}finally{restoringProject=false}
}
async function renameProject(id,name){
 const result=await api('projects/rename',{id,name});
 if(S.projectId===id){S.projectName=result.project.name;updateProjectTitle()}
 await renderProjects();
}
async function renderProjects(){
 const dst=el('projectsContent');dst.replaceChildren();
 try{
  const r=await api('projects/list');
  if(!r.projects.length){dst.innerHTML='<div class="side-empty">Chưa có project. Bấm “Tạo project” để bắt đầu.</div>';return}
  for(const p of r.projects){
   const div=document.createElement('article');div.className='project-card'+(S.projectId===p.id?' active':'');
   const title=document.createElement('h3');title.textContent=p.name;div.appendChild(title);
   const info=document.createElement('div');info.className='project-meta';info.textContent=`${p.box_count||0} boxes · ${p.frame_count||0} frames · ${p.media_kind||'none'}`;div.appendChild(info);
   const time=document.createElement('div');time.className='project-time';
   time.textContent=`Cập nhật: ${p.updated_at?new Date(p.updated_at).toLocaleString('vi-VN'):'—'} · ${p.source_name||'Chưa có dataset'}`;div.appendChild(time);
   const actions=document.createElement('div');actions.className='project-actions';
   const add=(name,cl,fn)=>{const b=document.createElement('button');b.textContent=name;b.className=cl;b.onclick=fn;actions.appendChild(b)};
   add('Mở project','primary',()=>openProject(p.id));
   if(canEdit()||role()==='admin')add('Đổi tên','outline',()=>openDialog('Đổi tên project',`<label>Tên mới<input id="renameProjectName" maxlength="100" value="${htmlEscape(p.name)}"></label>`,[
     {label:'Hủy',onClick:closeDialog},{label:'Lưu tên',className:'primary',onClick:async()=>{
      const name=el('renameProjectName').value.trim();closeDialog();try{await renameProject(p.id,name)}catch(e){toast(e.message,true)}}}]));
   if(canEdit()||role()==='admin')add('Xóa','danger',()=>addTextDialog('Xóa project',`Xóa project “${p.name}” và snapshot annotation? Thao tác này không thể hoàn tác.`,async()=>{
     try{await api('projects/delete',{id:p.id});if(S.projectId===p.id){S.projectId=null;S.projectName=null;updateProjectTitle()}
     await renderProjects();toast('Đã xóa project')}catch(e){toast(e.message,true)}}));
   div.appendChild(actions);dst.appendChild(div);
  }
 }catch(e){dst.textContent='Không tải được projects: '+e.message}
}
async function renderCvatJobs(){
 const dst=el('cvatJobsGrid');if(!dst)return;
 dst.innerHTML='<div class="side-empty">Đang tải danh sách jobs từ CVAT...</div>';
 try{
  const res=await api('cvat/jobs');
  if(el('cvatHostTag'))el('cvatHostTag').textContent='⚡ '+(res.cvat_host?res.cvat_host.replace(/^https?:\/\//,''):'CVAT Connected');
  if(el('cvatStatusSummary'))el('cvatStatusSummary').innerHTML=`Tổ chức: <b>${htmlEscape(res.active_org||'Cá nhân')}</b> · Tài khoản: <b>${htmlEscape(res.username)}</b> · Vai trò: <b>${htmlEscape(res.role.toUpperCase())}</b> (${res.filtered_count}/${res.all_count} jobs)`;
  
  loadCvatConfigs();
  const userRole = role();
  const allJobsList = res.all_jobs || res.jobs || [];
  S.lastCvatJobs = allJobsList;

  const projSel = el('cvatProjectFilter');
  if (projSel) {
    const projects = Array.from(new Set(allJobsList.map(j => j.project_name).filter(Boolean))).sort();
    const curProj = projSel.value;
    projSel.innerHTML = '<option value="">-- Tất cả dự án --</option>' + projects.map(p =>
      `<option value="${htmlEscape(p)}" ${p === curProj ? 'selected' : ''}>${htmlEscape(p)}</option>`
    ).join('');
    if (curProj && projects.includes(curProj)) {
      projSel.value = curProj;
    }
    if (!projSel.dataset.bound) {
      projSel.dataset.bound = 'true';
      projSel.onchange = () => renderCvatJobs();
    }
  }

  const selectedProject = projSel?.value || '';
  const filteredJobsByProj = selectedProject ? (res.jobs || []).filter(j => j.project_name === selectedProject) : (res.jobs || []);
  const myUsername = (S.user?.username || '').toLowerCase();
  const admin = isAdmin();
  const superv = isSupervisor();

  let tabsConfig = [];
  if (admin) {
   tabsConfig = [
    { key: 'all', label: '📋 Tất cả jobs', filter: () => true, empty: 'Không có job nào trên CVAT.' },
    { key: 'validation', label: '🔍 Cần QC (Validation)', filter: j => j.stage === 'validation', empty: 'Không có job nào đang ở Validation.' },
    { key: 'annotation', label: '✏️ Đang làm nhãn', filter: j => j.stage === 'annotation', empty: 'Không có job nào ở Annotation.' },
    { key: 'acceptance', label: '✓ Đã nghiệm thu', filter: j => j.stage === 'acceptance', empty: 'Chưa có job nào nghiệm thu.' },
   ];
  } else if (superv) {
   tabsConfig = [
    { key: 'validation', label: '🔍 Cần Review QC', filter: j => j.stage === 'validation', empty: 'Hiện không có job nào đang ở Validation cần QC.' },
    { key: 'team_annotation', label: '👥 Team đang làm nhãn', filter: j => j.stage === 'annotation' && !j.is_my_annotation, empty: 'Team hiện không có job nào ở Annotation.' },
    { key: 'my_annotation', label: '✏️ Việc tôi làm nhãn', filter: j => j.is_my_annotation, empty: 'Bạn không phụ trách làm nhãn job nào (các job do đồng nghiệp đảm nhận).' },
    { key: 'acceptance', label: '✓ Đã nghiệm thu', filter: j => j.stage === 'acceptance', empty: 'Chưa có job nào nghiệm thu.' },
    { key: 'all', label: '📋 Tất cả jobs', filter: () => true, empty: 'Không có job nào trên CVAT.' },
   ];
  } else {
   tabsConfig = [
    { key: 'my_annotation', label: '✏️ Việc tôi làm nhãn', filter: j => j.is_my_annotation, empty: 'Bạn chưa có job làm nhãn nào.' },
    { key: 'my_review', label: '🔍 Việc tôi cần Review QC', filter: j => j.stage === 'validation' && (j.role_for_job === 'reviewer' || !j.is_my_annotation), empty: 'Hiện không có job nào của đồng nghiệp cần bạn Review QC.' },
    { key: 'my_rejected', label: '⚠️ Bị trả lại cần sửa', filter: j => j.state === 'rejected' && j.is_my_annotation, empty: 'Tuyệt vời! Không có job nào của bạn bị trả lại.' },
    { key: 'acceptance', label: '✓ Đã nghiệm thu', filter: j => j.stage === 'acceptance', empty: 'Chưa có job nào nghiệm thu.' },
    { key: 'all', label: '📋 Tất cả jobs', filter: () => true, empty: 'Không có job nào trên CVAT.' },
   ];
  }

  if (!S.cvatActiveTab || !tabsConfig.some(t => t.key === S.cvatActiveTab)) {
   S.cvatActiveTab = tabsConfig[0].key;
  }

  const tabsEl = el('cvatRoleTabs');
  if (tabsEl) {
   tabsEl.innerHTML = '';
   for (const t of tabsConfig) {
    const count = (filteredJobsByProj || []).filter(t.filter).length;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'cvat-tab-btn' + (S.cvatActiveTab === t.key ? ' active' : '');
    btn.innerHTML = `${t.label} <span class="tab-count">${count}</span>`;
    btn.onclick = () => {
     S.cvatActiveTab = t.key;
     renderCvatJobs();
    };
    tabsEl.appendChild(btn);
   }
  }

  const activeTabConfig = tabsConfig.find(t => t.key === S.cvatActiveTab) || tabsConfig[0];
  let list = (filteredJobsByProj || []).filter(activeTabConfig.filter);

  if(!list.length){
   dst.innerHTML=`<div class="side-empty">${activeTabConfig.empty}<br><small>Nhấn "Đồng bộ từ CVAT" để cập nhật dữ liệu mới nhất.</small></div>`;
   return;
  }

  dst.innerHTML='';
  for(const j of list){
   const card=document.createElement('div');card.className='cvat-card';
   const stageClass=j.stage||'annotation';
   const isMyJob = Boolean(j.is_my_annotation);
   const jobRole = j.role_for_job || (isMyJob ? 'annotator' : 'viewer');
   let actionsHtml=`<a href="${j.cvat_url}" target="_blank" class="outline small" title="Mở trực tiếp trên giao diện CVAT">🔗 CVAT</a>`;

   if(j.stage==='annotation'){
    if(j.state==='completed' || admin || jobRole==='reviewer'){
     actionsHtml+=`<button class="primary small btn-pull-qc" data-id="${j.id}" title="Chuyển Job này sang giai đoạn Validation để tiến hành QC">⚡ Chuyển sang QC</button>`;
    }
    if(isMyJob){
     actionsHtml+=`<button class="primary small btn-submit-validation" data-id="${j.id}" title="Hoàn tất và gửi Job cho Reviewer QC">🚀 Gửi Reviewer (Nộp QC)</button>`;
     actionsHtml+=`<button class="outline small btn-open-qc" data-id="${j.id}">✏️ Mở làm nhãn</button>`;
    }else{
     actionsHtml+=`<button class="outline small btn-open-qc" data-id="${j.id}">${admin ? '⌕ Xem giám sát' : (jobRole==='reviewer' ? '🔍 Xem trước nhãn' : 'Xem chi tiết')}</button>`;
     if(!admin && j.state!=='completed'){
      actionsHtml+=`<span class="pill" style="margin-left:auto;color:#64748b;font-size:11px;">✏️ ${htmlEscape(j.assignee || 'Đồng nghiệp')} đang làm</span>`;
     }
    }
    if(admin){
     actionsHtml+=`<button class="outline small btn-assign-job" data-id="${j.id}">👤 Phân công</button>`;
    }
   }else if(j.stage==='validation'){
    if(jobRole==='reviewer' || admin){
     actionsHtml+=`<button class="primary small btn-open-qc" data-id="${j.id}">🔍 Quét QC & Review</button>`;
    }else if(isMyJob){
     actionsHtml+=`<button class="outline small btn-open-qc" data-id="${j.id}">Xem tiến độ QC</button>`;
    }else{
     actionsHtml+=`<button class="outline small btn-open-qc" data-id="${j.id}">Xem chi tiết</button>`;
    }
    if(admin){
     actionsHtml+=`<button class="outline small btn-assign-job" data-id="${j.id}">👤 Phân công</button>`;
    }
   }else if(j.stage==='acceptance'){
    actionsHtml+=`<button class="outline small btn-open-qc" data-id="${j.id}">Xem nghiệm thu</button>`;
    if(admin){
     actionsHtml+=`<button class="outline small btn-assign-job" data-id="${j.id}">👤 Phân công</button>`;
    }
   }

   let statusBadge = `${j.stage} · ${j.state}`;
   const annoText = j.annotator_name ? `Annotator: <b>${htmlEscape(j.annotator_name)}</b>` : 'Annotator: <span style="color:#94a3b8;">Chưa gán</span>';
   const revText = j.reviewer_name ? `Reviewer: <b>${htmlEscape(j.reviewer_name)}</b>` : 'Reviewer: <span style="color:#94a3b8;">Chưa gán</span>';
   let assigneeDisplay = `${annoText} · ${revText}`;
   if (j.state === 'rejected' && j.stage === 'annotation') {
    statusBadge = `⚠️ ĐÃ TRẢ LẠI ANNOTATOR`;
   } else if (j.stage === 'validation') {
    statusBadge = `🔍 CẦN REVIEW QC`;
   } else if (j.stage === 'acceptance') {
    statusBadge = `✓ ĐÃ NGHIỆM THU`;
   }

   let rolePill = '';
   if(admin) rolePill = `<span class="job-role-pill admin">Lead/Admin</span>`;
   else if(isMyJob) rolePill = `<span class="job-role-pill annotator">Annotator: Việc của bạn</span>`;
   else if(jobRole==='reviewer') rolePill = `<span class="job-role-pill reviewer">Vai trò: Reviewer QC</span>`;
   else rolePill = `<span class="job-role-pill" style="background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0;">Chỉ xem</span>`;

   card.innerHTML=`<div class="cvat-card-head"><div><div class="cvat-card-title">Job #${j.id} <span style="font-size:12px;color:#8592a6;font-weight:normal;">(Task #${j.task_id})</span> ${rolePill}</div><div class="cvat-card-sub"><b>${htmlEscape(j.task_name)}</b> · <i>${htmlEscape(j.project_name)}</i></div></div><span class="stage-badge ${stageClass}">${statusBadge}</span></div><div class="cvat-card-body"><div class="cvat-card-meta">${assigneeDisplay}</div><div class="cvat-card-meta">Frames: <b>${j.frame_count}</b> (${j.start_frame}–${j.stop_frame})</div></div><div class="cvat-card-actions">${actionsHtml}</div>`;
   const btnSubmit=card.querySelector('.btn-submit-validation');if(btnSubmit)btnSubmit.onclick=()=>submitCvatJobForReview(j.id, j);
   const btnPull=card.querySelector('.btn-pull-qc');if(btnPull)btnPull.onclick=()=>pullCvatJobToValidation(j.id, j);
   const btnOpen=card.querySelector('.btn-open-qc');if(btnOpen)btnOpen.onclick=()=>openCvatJobInStudio(j.id,j);
   const btnAssign=card.querySelector('.btn-assign-job');if(btnAssign)btnAssign.onclick=()=>assignCvatJobDialog(j.id,j);
   dst.appendChild(card);
  }
 }catch(e){dst.innerHTML=`<div class="side-empty" style="color:#d93442;">Lỗi kết nối CVAT: ${htmlEscape(e.message)}<br><small>Kiểm tra cấu hình .env (CVAT_HOST, CVAT_USER, CVAT_PASS)</small></div>`;}
}
async function pullCvatJobToValidation(job_id, jobMeta){
 try{
  setBusy(true);
  const usersRes = await api('cvat/users').catch(() => ({users:[]}));
  const myId = S.user?.id;
  const users = usersRes.users || [];

  let defaultRevId = jobMeta.reviewer_id || (role() === 'reviewer' || role() === 'supervisor' ? myId : null);
  if (!defaultRevId && users.length) {
   const candidate = users.find(u => u.role === 'reviewer') || users[0];
   defaultRevId = candidate ? candidate.id : null;
  }

  const optionsHtml = users.length 
    ? users.map(u => `<option value="${u.id}" ${defaultRevId === u.id ? 'selected' : ''}>${htmlEscape(u.username)} (${u.role === 'reviewer' ? 'Reviewer' : u.is_staff ? 'Lead/Admin' : 'Annotator'})</option>`).join('')
    : '<option value="">-- Chọn Reviewer --</option>';

  const annoName = jobMeta.annotator_name || jobMeta.assignee || 'Annotator';

  openDialog(`Chuyển Job #${job_id} sang QC (Validation)`,
   `<p style="font-size:13px;line-height:1.5;">Job <b>#${job_id}</b> (Task #${jobMeta.task_id}: ${htmlEscape(jobMeta.task_name)}) đang ở stage <b>Annotation</b> với trạng thái <code>${jobMeta.state}</code>.</p>
    <p style="font-size:12px;color:#475569;margin-top:6px;">Chuyển sang stage <b>Validation</b> trên CVAT để tiến hành kiểm định và bắt lỗi (QC).</p>
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin:12px 0;display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:12px;">Annotator làm nhãn: <b>${htmlEscape(annoName)}</b></div>
      <label style="font-size:12px;font-weight:700;display:block;">Chỉ định Reviewer QC:
        <select id="selectPullReviewer" style="width:100%;margin-top:4px;padding:7px;border-radius:6px;border:1px solid #d3daea;font-size:13px;background:#fff;">
          ${optionsHtml}
        </select>
      </label>
    </div>
    <p style="font-size:11px;color:#64748b;line-height:1.4;">
      • Khi xác nhận, Job trên CVAT sẽ tự động chuyển sang <b>stage=validation, state=completed</b> và quyền phụ trách (assignee) sẽ gán cho Reviewer được chọn.
    </p>`,
   [
     {label: 'Hủy', onClick: closeDialog},
     {
       label: 'Xác nhận chuyển sang QC',
       className: 'primary',
       onClick: async () => {
         const revVal = el('selectPullReviewer')?.value;
         const reviewer_id = revVal ? Number(revVal) : (defaultRevId || myId);
         closeDialog();
         try {
           setBusy(true);
           const res = await api('cvat/job/submit-validation', { job_id, reviewer_id });
           toast(res.message || `Đã chuyển Job #${job_id} sang giai đoạn Validation.`);
           if (role() === 'reviewer' || role() === 'supervisor') {
             S.cvatActiveTab = 'validation';
           }
           await renderCvatJobs();
         } catch (err) {
           toast(err.message, true);
         } finally {
           setBusy(false);
         }
       }
     }
   ]
  );
 }catch(e){
  toast(`Lỗi: ${e.message}`, true);
 }finally{
  setBusy(false);
 }
}
async function submitCvatJobForReview(job_id, jobMeta){
 try{
  setBusy(true);
  const usersRes = await api('cvat/users').catch(() => ({users:[]}));
  const myName = (S.user?.username || '').toLowerCase();
  const colleagues = (usersRes.users || []).filter(u => u.username.toLowerCase() !== myName);
  const candidateList = colleagues.length ? colleagues : (usersRes.users || []);
  const defaultCandidate = candidateList.find(u => u.role === 'reviewer') || candidateList[0];
  const optionsHtml = candidateList.length 
    ? candidateList.map(u => `<option value="${u.id}" ${defaultCandidate && defaultCandidate.id === u.id ? 'selected' : ''}>${htmlEscape(u.username)} (${u.role === 'reviewer' ? 'Reviewer' : u.is_staff ? 'Lead/Admin' : 'Đồng nghiệp QC'})</option>`).join('')
    : '<option value="">-- Chọn Reviewer --</option>';

  openDialog('Gửi Job cho Reviewer QC',
   `<p>Bạn chuẩn bị hoàn tất và nộp <b>Job #${job_id}</b> để chuyển sang giai đoạn <b>Validation</b>.</p>
    <label style="margin-top:12px;display:block;"><b>Chỉ định Reviewer kiểm định:</b>
      <select id="selectSubmitReviewer" style="width:100%;margin-top:6px;padding:8px;border-radius:6px;border:1px solid #d3daea;background:#fff;font-size:13px;">
        ${optionsHtml}
      </select>
    </label>
    <p style="font-size:12px;color:#6b7c96;margin-top:8px;">Sau khi gửi, quyền phụ trách trên CVAT sẽ chuyển giao cho Reviewer được chọn. Nếu có lỗi phát hiện trong quá trình QC, Reviewer sẽ tự động trả lại job cho bạn kèm issues.</p>`,
   [{label:'Hủy',onClick:closeDialog},{label:'🚀 Xác nhận gửi Reviewer',className:'primary',onClick:async()=>{
     const revVal = el('selectSubmitReviewer')?.value;
     const reviewer_id = revVal ? Number(revVal) : null;
     closeDialog();
     try{
       setBusy(true);
       const res = await api('cvat/job/submit-validation',{job_id, reviewer_id});
       toast(res.message || `Đã chuyển Job #${job_id} sang Validation.`);
       await renderCvatJobs();
     }catch(e){
       toast(e.message, true);
     }finally{
       setBusy(false);
     }
   }}]
  );
 }catch(e){
   toast(`Lỗi chuẩn bị nộp job: ${e.message}`, true);
 }finally{
   setBusy(false);
 }
}
async function openCvatJobInStudio(job_id,jobMeta){
 try{
  setBusy(true);toast(`Đang tải annotation Job #${job_id} từ CVAT...`);
  const fps=Number(el('cvatFpsInput')?.value||5.0),config=el('cvatConfigSelect')?.value||'config_project_3.yaml';
  S.activeConfig=config;
  const r=await api('cvat/job/open',{job_id,fps,config});
  clearMedia();S.projectId=null;S.projectName=null;
  S.activeConfig=r.config||config;
  S.cvatJob={id:r.job_id,task_id:r.task_id,stage:r.stage,state:r.state,assignee:r.assignee,cvat_url:r.cvat_url,total_frames:r.total_frames,frame_base:r.frame_base,width:r.width,height:r.height,fps:r.fps,config:S.activeConfig};
  S.boxes=(r.boxes||[]).filter(b=>!b.outside);S.meta={format:'cvat',frame_base:r.frame_base??0,mode:'video',width:r.width,height:r.height,total_frames:r.total_frames,fps:r.fps,config:S.activeConfig,labels:r.labels||['Car'],task_name:`CVAT Job #${r.job_id}`};
  S.session=null;S.sourceName=`CVAT Job #${r.job_id} (Task #${r.task_id})`;S.undo=[];S.redo=[];S.selectedUid=null;S.tasks={};resetDecisions();
  const isAnnotator = (r.effective_role === 'annotator' && S.user?.role !== 'admin');
  const cvatIssues = (r.cvat_issues || []);
  const savedManuals = loadSavedManualIssues();

  if (isAnnotator) {
    // Với vai trò Annotator: Chỉ nạp các issue đã được Reviewer chốt tạo và đẩy lên CVAT
    S.issues = [...cvatIssues];
    S.lastScan = {
      count: cvatIssues.length,
      counts: r.counts || { cvat: cvatIssues.length },
      stats: r.stats,
      notes: [
        `Nạp trực tiếp từ CVAT API (Job #${r.job_id}).`,
        `Chế độ Annotator: Hiển thị ${cvatIssues.length} issue đã được Reviewer chốt tạo trên CVAT để bạn khắc phục.`
      ]
    };
  } else {
    // Với vai trò Reviewer / Admin: Nạp toàn bộ issue quét từ thuật toán + issue CVAT để rà soát và thẩm định
    const serverIssueIds = new Set((r.issues || []).map(i => i.id));
    const filteredManuals = savedManuals.filter(i => !serverIssueIds.has(i.id));
    S.issues = [...filteredManuals, ...cvatIssues, ...(r.issues || [])];
    S.lastScan = {
      count: S.issues.length,
      counts: r.counts,
      stats: r.stats,
      notes: [
        `Nạp trực tiếp từ CVAT API (Job #${r.job_id}).`,
        `Đã quét ${r.count} issues bằng idcheck core rules (${r.fps} FPS, ${S.activeConfig}).`,
        `Liên thông ${cvatIssues.length} issues trực tiếp từ CVAT.`
      ]
    };
  }
  if(r.width)el('imageWidth').value=r.width;if(r.height)el('imageHeight').value=r.height;el('fps').value=r.fps;el('datasetName').textContent=S.sourceName;
  indexBoxes();undoButtons();
  const startF=r.frame_base||0,totalF=r.total_frames||(r.boxes.length?Math.max(...r.boxes.map(b=>b.frame_id))-startF+1:1);
  S.imageURLs=new Map();S.images=new Map();
  for(let f=startF;f<startF+totalF;f++)S.imageURLs.set(f,`/api/cvat/frame/${r.job_id}/${f}`);
  S.mediaMode='cvat-stream';el('mediaInfo').textContent=`⚡ CVAT Live Stream · Job #${r.job_id} · ${totalF} frames trực tiếp từ server`;
  el('cvatJobBanner').hidden=false;
  el('cvatBannerTitle').textContent=`CVAT Job #${r.job_id} · Task #${r.task_id}`;
  const issueText = isAnnotator ? `${cvatIssues.length} CVAT issues` : `${S.issues.length} issues`;
  el('cvatBannerSubtitle').textContent=`Stage: ${(r.stage||'').toUpperCase()} (${r.state}) · Assignee: ${r.assignee||'Chưa gán'} · ${issueText} · ⚙ ${S.activeConfig}`;
  S.canReview = r.can_review;
  S.canEditBoxes = r.can_edit_boxes;
  S.cvatJobEffectiveRole = r.effective_role;
  el('btnCvatExternal').href=r.cvat_url;
  if(el('currentProject')) el('currentProject').textContent=`CVAT Job #${r.job_id} (Task #${r.task_id})`;
  if(el('datasetName')) el('datasetName').textContent=`⚡ Task #${r.task_id} · Job #${r.job_id}`;
  if(el('cvatJobRoleBadge')){
    const eff = r.effective_role || 'viewer';
    el('cvatJobRoleBadge').textContent = (eff === 'admin' ? 'Lead / Giám sát' : (eff === 'reviewer' ? 'Reviewer QC' : (eff === 'annotator' ? 'Annotator' : 'Chỉ xem')));
    el('cvatJobRoleBadge').className = 'cvat-badge role-tag ' + eff;
  }
  if(el('cvatRoleHint')){
    el('cvatRoleHint').hidden = false;
    el('cvatRoleHint').textContent = r.can_review ? '✓ Bạn có quyền Review QC Job này' : (r.can_edit_boxes ? '✏️ Bạn có quyền Sửa nhãn Job này' : 'Chế độ chỉ xem (Viewer)');
  }
  if(el('btnCvatPush')) el('btnCvatPush').hidden = !r.can_edit_boxes;
  if(el('btnCvatAccept')) el('btnCvatAccept').hidden = !r.can_review;
  if(el('btnCvatReject')) el('btnCvatReject').hidden = !r.can_review;
  if(el('btnCvatSubmitValidation')) el('btnCvatSubmitValidation').hidden = (r.stage !== 'annotation');
  if(el('btnCvatRescan')) el('btnCvatRescan').hidden = isAnnotator;
  if(el('btnCreateIssue')) el('btnCreateIssue').hidden = isAnnotator;
  if(el('btnBoxRaiseIssue')) el('btnBoxRaiseIssue').hidden = isAnnotator;
  if(el('cvatWorkspaceConfigSelect')) {
    el('cvatWorkspaceConfigSelect').disabled = isAnnotator;
    el('cvatWorkspaceConfigSelect').value = S.activeConfig;
  }
  const issueHeadP = el('sidePaneIssues')?.querySelector('.issues-head p');
  if(issueHeadP){
    issueHeadP.textContent = isAnnotator
      ? 'Danh sách Issue do Reviewer yêu cầu từ CVAT cần Annotator sửa'
      : 'Reviewer kiểm định chất lượng → Báo lỗi (Raise) về Annotator → Nghiệm thu';
  }
  updateRoleUI();
  S.frame=startF;refreshAll();showPage('workspace');
  if (isAnnotator) {
    toast(`Đã nạp Job #${r.job_id}: ${r.boxes.length} boxes, ${cvatIssues.length} issues từ CVAT.`);
  } else {
    toast(`Đã nạp Job #${r.job_id}: ${r.boxes.length} boxes, ${r.count} issues phát hiện.`);
  }
 }catch(e){toast(`Lỗi khi mở CVAT Job #${job_id}: ${e.message}`,true)}finally{setBusy(false)}
}
function rejectCvatJobDialog(){
 if(!S.cvatJob)return toast('Không có CVAT Job nào đang mở.',true);
 const activeIssues=S.issues.filter(i=>S.decisions[i.id]!=='false_positive'),falsePositives=S.issues.filter(i=>S.decisions[i.id]==='false_positive').length;
 openDialog('Trả lại Annotator & Raise Issues lên CVAT',
  `<p>Bạn chuẩn bị trả lại <b>CVAT Job #${S.cvatJob.id}</b> cho Annotator với các thông số sau:</p>
   <ul><li>Số issues sẽ tự động tạo trên CVAT: <b>${activeIssues.length}</b></li><li>Số cảnh báo đã loại bỏ (False Positive): <b>${falsePositives}</b></li><li>Trạng thái mới trên CVAT: <b>stage=annotation, state=rejected</b></li></ul>
   <p>CVAT sẽ ghim vị trí issue vào đúng frame và bounding box tương ứng để Annotator sửa lỗi.</p>`,
  [{label:'Hủy',onClick:closeDialog},{label:'Xác nhận tạo Issues & Trả lại',className:'primary',onClick:async()=>{
    closeDialog();try{setBusy(true);toast(`Đang đẩy ${activeIssues.length} issues lên CVAT...`);
    const res=await api('cvat/job/reject-and-raise',{job_id:S.cvatJob.id,issues:activeIssues});
    toast(res.message||`Đã chuyển Job #${S.cvatJob.id} về Annotation và tạo issues.`);
    el('cvatJobBanner').hidden=true;S.cvatJob=null;showPage('cvatJobs');await renderCvatJobs()}
    catch(e){toast(e.message,true)}finally{setBusy(false)}}}]
 );
}
function acceptCvatJobDialog(){
 if(!S.cvatJob)return toast('Không có CVAT Job nào đang mở.',true);
 const blockers = S.issues.filter(i => {
   const st = S.decisions[i.id] || i.status;
   if(st === 'false_positive' || st === 'verified' || st === 'fixed') return false;
   return (i.severity === 'high' || (i.is_cvat_issue && !i.resolved));
 });
 const isAdmin = S.user?.role === 'admin' || S.cvatJobEffectiveRole === 'admin';

 if (blockers.length > 0) {
   if (!isAdmin) {
     return openDialog('⚠️ Chốt chặn Quality Gate: Không thể nghiệm thu', `
       <p style="color:#ef4444;font-weight:600;">Job #${S.cvatJob.id} vẫn còn ${blockers.length} lỗi nghiêm trọng / Issue CVAT chưa giải quyết.</p>
       <ul style="padding-left:18px;margin:8px 0;font-size:12px;color:#94a3b8;max-height:140px;overflow-y:auto;">
         ${blockers.slice(0, 10).map(b => `<li>Frame ${b.frame}: [${htmlEscape(b.type)}] ${htmlEscape(b.message || '')}</li>`).join('')}
         ${blockers.length > 10 ? `<li>... và ${blockers.length - 10} lỗi khác</li>` : ''}
       </ul>
       <p style="font-size:13px;color:#cbd5e1;">Theo quy chuẩn QA, bạn không thể duyệt hoàn tất khi Quality Gate chưa đạt. Hãy yêu cầu Annotator khắc phục các lỗi này hoặc liên hệ Lead / Admin để xin phê duyệt ngoại lệ (Override).</p>
     `, [{label: 'Đóng', onClick: closeDialog}]);
   } else {
     return openDialog('🛡️ Quality Gate Cảnh báo: Phê duyệt Ngoại lệ (Admin Override)', `
       <p style="color:#f59e0b;font-weight:600;">Job #${S.cvatJob.id} còn ${blockers.length} lỗi nghiêm trọng / Issue CVAT chưa giải quyết.</p>
       <p style="font-size:13px;color:#cbd5e1;">Với vai trò <b>Lead / Admin</b>, bạn có thể thực hiện <b>Admin Override</b> để nghiệm thu có điều kiện.</p>
       <label style="display:block;margin-top:10px;">
         <b>Lý do phê duyệt ngoại lệ (Bắt buộc):</b>
         <textarea id="overrideAcceptReason" rows="3" placeholder="Ví dụ: Khách hàng chấp thuận ngoại lệ; các issue nhỏ được chuyển theo dõi riêng..." style="width:100%;margin-top:6px;padding:8px;background:#1e293b;color:#f8fafc;border:1px solid #374151;border-radius:6px;resize:vertical;"></textarea>
       </label>
     `, [
       {label: 'Hủy', onClick: closeDialog},
       {label: '⚡ Xác nhận Override & Duyệt', className: 'primary', onClick: async () => {
         const reason = el('overrideAcceptReason')?.value?.trim();
         if (!reason) return toast('Vui lòng nhập lý do phê duyệt ngoại lệ.', true);
         closeDialog();
         try {
           setBusy(true);
           const res = await api('cvat/job/accept', {
             job_id: S.cvatJob.id,
             issues: S.issues,
             override: true,
             override_reason: reason
           });
           toast(res.message || `Job #${S.cvatJob.id} đã được nghiệm thu (Override).`);
           el('cvatJobBanner').hidden = true;
           S.cvatJob = null;
           showPage('cvatJobs');
           await renderCvatJobs();
         } catch(e) {
           toast(e.message, true);
         } finally {
           setBusy(false);
         }
       }}
     ]);
   }
 }

 openDialog('Nghiệm thu CVAT Job',
  `<p>Quality Gate: <b>✓ ĐẠT CHUẨN</b> (Không còn lỗi nghiêm trọng chưa xử lý).</p>
   <p>Bạn có chắc chắn muốn nghiệm thu <b>CVAT Job #${S.cvatJob.id}</b>?</p>
   <p>Trạng thái trên CVAT sẽ chuyển thành <b>stage=acceptance, state=completed</b>.</p>`,
  [{label:'Hủy',onClick:closeDialog},{label:'✓ Nghiệm thu hoàn tất',className:'primary',onClick:async()=>{
    closeDialog();try{setBusy(true);const res=await api('cvat/job/accept',{job_id:S.cvatJob.id,issues:S.issues});
    toast(res.message||`Job #${S.cvatJob.id} đã được nghiệm thu.`);el('cvatJobBanner').hidden=true;S.cvatJob=null;showPage('cvatJobs');await renderCvatJobs()}
    catch(e){toast(e.message,true)}finally{setBusy(false)}}}]
 );
}
async function assignCvatJobDialog(job_id,job){
 try{
  const res=await api('cvat/users'),users=res.users||[];
  const optionsHtml=users.map(u=>`<option value="${u.id}" ${job.assignee_id===u.id?'selected':''}>${htmlEscape(u.username)} (ID: ${u.id}${u.is_staff?', Staff':''})</option>`).join('');
  openDialog(`Phân công Job #${job_id}`,
   `<p>Chọn Annotator đảm nhận Job #${job_id} trên CVAT:</p><label>Người thực hiện<select id="selectAssigneeUser"><option value="">-- Chưa gán (Bỏ gán) --</option>${optionsHtml}</select></label>`,
   [{label:'Hủy',onClick:closeDialog},{label:'Lưu phân công',className:'primary',onClick:async()=>{
     const val=el('selectAssigneeUser').value,assignee_id=val?Number(val):null;closeDialog();
     try{setBusy(true);await api('cvat/job/assign',{job_id,assignee:assignee_id});toast(`Đã cập nhật phân công cho Job #${job_id}`);await renderCvatJobs()}
     catch(e){toast(e.message,true)}finally{setBusy(false)}}}]
   );
  }catch(e){toast(`Lỗi khi lấy danh sách user: ${e.message}`,true)}
 }

async function loadCvatConfigs(selectedConfig){
 const sel = el('cvatConfigSelect');
 const wsSel = el('cvatWorkspaceConfigSelect');
 if(!sel && !wsSel) return;
 try {
  const res = await api('cvat/configs');
  if (res.configs && res.configs.length) {
   const curVal = selectedConfig || S.activeConfig || sel?.value || wsSel?.value || 'config_project_3.yaml';
   const formatLabel = cfg => {
    let label = cfg;
    if (cfg === 'config_project_3.yaml') label += ' (BDD100K 5 FPS)';
    else if (cfg === 'config_project_3_optimized.yaml') label += ' (BDD100K Tối ưu)';
    else if (cfg === 'config_project_3_test.yaml') label += ' (BDD100K Test)';
    else if (cfg === 'config_project_3_test2.yaml') label += ' (BDD100K Test 2)';
    else if (cfg === 'config_mot17.yaml') label += ' (MOT17 Benchmark)';
    else if (cfg === 'config_base.yaml') label += ' (Mặc định)';
    return label;
   };
   const optionsHtml = res.configs.map(cfg => {
    return `<option value="${htmlEscape(cfg)}" ${cfg === curVal ? 'selected' : ''}>${htmlEscape(formatLabel(cfg))}</option>`;
   }).join('');
   if (sel) {
    sel.innerHTML = optionsHtml;
    sel.value = curVal;
   }
   if (wsSel) {
    wsSel.innerHTML = optionsHtml;
    wsSel.value = curVal;
   }
   S.activeConfig = curVal;
  }
 } catch(e) {
  console.warn('Could not load configs list:', e);
 }
}

async function openCreateIssueDialog(opt={}){
  const frame = Number(opt.frame !== undefined ? opt.frame : S.frame);
  let defaultTrack = '';
  if (opt.track_id !== undefined && opt.track_id !== null && opt.track_id !== '') {
    defaultTrack = opt.track_id;
  } else if (S.selectedUid) {
    const curBox = S.boxes.find(b => b.uid === S.selectedUid);
    if (curBox && curBox.track_id !== undefined && curBox.track_id !== null) {
      defaultTrack = curBox.track_id;
    }
  }

  const defaultType = opt.type || (defaultTrack !== '' ? 'ID Switch Candidate' : 'Box Jump');
  const isCvat = !!S.cvatJob;

  const html = `
  <div style="display:flex;flex-direction:column;gap:12px;text-align:left;">
    <p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.4;">
      Reviewer tự tạo issue cảnh báo cho lỗi mà rule/mô hình chưa bắt được. Issue sẽ được đưa vào danh sách kiểm định để giao việc cho Annotator hoặc raise trực tiếp lên CVAT.
    </p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <label style="font-size:12px;font-weight:600;">Frame số:
        <input type="number" id="manualIssueFrame" value="${frame}" min="0" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid #374151;background:#1e293b;color:#f8fafc;">
      </label>
      <label style="font-size:12px;font-weight:600;">Mức độ nghiêm trọng:
        <select id="manualIssueSeverity" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid #374151;background:#1e293b;color:#f8fafc;">
          <option value="high" selected>High (Bắt buộc sửa)</option>
          <option value="medium">Medium (Trung bình)</option>
          <option value="low">Low (Lưu ý / Nhắc nhở)</option>
        </select>
      </label>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <label style="font-size:12px;font-weight:600;">Track ID:
        <input type="number" id="manualIssueTrack" value="${defaultTrack}" placeholder="Ví dụ: 142" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid #374151;background:#1e293b;color:#f8fafc;">
      </label>
      <label style="font-size:12px;font-weight:600;">Track liên quan (hoán đổi ID):
        <input type="number" id="manualIssueOtherTrack" placeholder="Ví dụ: 144" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid #374151;background:#1e293b;color:#f8fafc;">
      </label>
    </div>
    <label style="font-size:12px;font-weight:600;">Loại lỗi (Error Type):
      <select id="manualIssueType" style="width:100%;margin-top:4px;padding:6px;border-radius:6px;border:1px solid #374151;background:#1e293b;color:#f8fafc;">
        <option value="ID Switch Candidate" ${defaultType==='ID Switch Candidate'?'selected':''}>ID Switch Candidate (Hoán đổi ID / Cướp track)</option>
        <option value="Box Jump" ${defaultType==='Box Jump'?'selected':''}>Box Jump (Box nhảy / Lệch quỹ đạo)</option>
        <option value="Gap Frame" ${defaultType==='Gap Frame'?'selected':''}>Gap Frame (Thiếu frame / Đứt đoạn track)</option>
        <option value="Class Change">Class Change (Sai nhãn phân loại)</option>
        <option value="Duplicate Track/Frame">Duplicate Track/Frame (Trùng lặp box)</option>
        <option value="Invalid Box">Invalid Box (Vẽ lỗi / Sai kích thước)</option>
        <option value="Custom Reviewer Note">Ghi chú kiểm định khác (Custom Note)</option>
      </select>
    </label>
    <label style="font-size:12px;font-weight:600;">Ghi chú mô tả lỗi / Hướng dẫn Annotator sửa:
      <textarea id="manualIssueMessage" rows="3" placeholder="Ví dụ: Xe 142 bị tráo ID với xe 144 tại frame này, annotator đổi lại ID giúp mình..." style="width:100%;margin-top:4px;padding:8px;background:#1e293b;color:#f8fafc;border:1px solid #374151;border-radius:6px;resize:vertical;"></textarea>
    </label>
  </div>
  `;

  const buttons = [
    { label: 'Hủy', onClick: closeDialog },
    {
      label: '＋ Thêm vào danh sách Issue',
      className: 'primary',
      onClick: () => {
        const fVal = Number(el('manualIssueFrame')?.value || frame);
        const tVal = el('manualIssueTrack')?.value?.trim();
        const otVal = el('manualIssueOtherTrack')?.value?.trim();
        const typeVal = el('manualIssueType')?.value || 'Custom Reviewer Note';
        const sevVal = el('manualIssueSeverity')?.value || 'high';
        const msgVal = el('manualIssueMessage')?.value?.trim() || `[Reviewer] Yêu cầu kiểm tra Track #${tVal || '—'} tại Frame ${fVal}`;

        const newIss = {
          id: `manual:${Date.now()}:${tVal || 'all'}:${fVal}`,
          type: typeVal,
          frame: fVal,
          track_id: tVal !== '' ? Number(tVal) : null,
          severity: sevVal,
          message: msgVal,
          manual: true,
          created_by: S.user?.username || 'Reviewer',
          created_at: new Date().toISOString(),
          fix_data: otVal !== '' ? { pair: `${tVal}_${otVal}`, target: Number(otVal) } : {},
          data: otVal !== '' ? { pair: `${tVal}_${otVal}`, target: Number(otVal) } : {}
        };

        S.issues.unshift(newIss);
        saveManualIssues();
        closeDialog();
        renderIssues();
        metrics();
        draw();
        toast(`Đã thêm Issue: Frame ${newIss.frame}${newIss.track_id !== null ? ' Track #' + newIss.track_id : ''}`);
      }
    }
  ];

  if (isCvat) {
    buttons.push({
      label: '⚡ Đẩy ngay lên CVAT',
      className: 'primary',
      onClick: async () => {
        const fVal = Number(el('manualIssueFrame')?.value || frame);
        const tVal = el('manualIssueTrack')?.value?.trim();
        const otVal = el('manualIssueOtherTrack')?.value?.trim();
        const typeVal = el('manualIssueType')?.value || 'Custom Reviewer Note';
        const sevVal = el('manualIssueSeverity')?.value || 'high';
        const msgVal = el('manualIssueMessage')?.value?.trim() || `[Reviewer] Yêu cầu kiểm tra Track #${tVal || '—'} tại Frame ${fVal}`;

        const newIss = {
          id: `manual:${Date.now()}:${tVal || 'all'}:${fVal}`,
          type: typeVal,
          frame: fVal,
          track_id: tVal !== '' ? Number(tVal) : null,
          severity: sevVal,
          message: msgVal,
          manual: true,
          pushed_to_cvat: true,
          created_by: S.user?.username || 'Reviewer',
          created_at: new Date().toISOString(),
          fix_data: otVal !== '' ? { pair: `${tVal}_${otVal}`, target: Number(otVal) } : {},
          data: otVal !== '' ? { pair: `${tVal}_${otVal}`, target: Number(otVal) } : {}
        };

        closeDialog();
        try {
          setBusy(true);
          const res = await api('cvat/job/raise-issue', {
            job_id: S.cvatJob.id,
            type: typeVal,
            frame: fVal,
            track_id: tVal !== '' ? Number(tVal) : null,
            other_id: otVal !== '' ? Number(otVal) : null,
            message: msgVal
          });
          S.issues.unshift(newIss);
          saveManualIssues();
          renderIssues();
          metrics();
          draw();
          toast(res.message || `Đã tạo Issue thành công trên CVAT tại Frame ${fVal}!`);
        } catch(e) {
          toast(`Lỗi khi tạo issue lên CVAT: ${e.message}`, true);
        } finally {
          setBusy(false);
        }
      }
    });
  }

  openDialog('Tạo Issue kiểm định (Reviewer Raise Issue)', html, buttons);
}

async function openTuneThresholdsDialog(){
 if (!S.lastCvatJobs || !S.lastCvatJobs.length) {
  try {
   const res = await api('cvat/jobs');
   S.lastCvatJobs = res.all_jobs || res.jobs || [];
  } catch(e) {}
 }
 const completedJobs = (S.lastCvatJobs || []).filter(j => j.stage === 'acceptance');
 if (!completedJobs.length) {
  openDialog('⚙️ Tune ngưỡng từ Job đã nghiệm thu',
   `<p style="font-size:13px;color:#1e293b;">Chưa có Job nào ở trạng thái <b>✓ Đã nghiệm thu (Acceptance)</b> trong tổ chức hiện tại.</p>
    <p style="font-size:12px;color:#64748b;margin-top:8px;">Quy trình Auto-Calibrate cần ít nhất 1 Job đã kiểm duyệt hoàn tất làm <b>dữ liệu chuẩn (Ground Truth)</b> để học các quy luật chuyển động và tối ưu hóa bộ lọc. Khi bạn hoặc Reviewer nghiệm thu (Accept) một Job trên CVAT, quay lại đây để tune ngưỡng.</p>`,
   [{label:'Đã hiểu', className:'primary', onClick:closeDialog}]
  );
  return;
 }

 const firstJob = completedJobs[0];
 const projId = firstJob.project_id || 3;
 const projName = firstJob.project_name || 'BDD100k_MOT';
 const defaultOut = `config_project_${projId}.yaml`;

 const jobsHtml = completedJobs.map(j => `
  <div class="calibrate-job-row">
   <label class="calibrate-job-label">
    <input type="checkbox" class="cb-calib-job" value="${j.id}" checked>
    <span><b>Job #${j.id}</b> <span style="color:#64748b;">(Task #${j.task_id}: ${htmlEscape(j.task_name)})</span></span>
   </label>
   <span style="color:#475569;font-size:11px;">Assignee: <b>${htmlEscape(j.assignee || 'Chưa gán')}</b> · <b>${j.frame_count}</b> frames</span>
  </div>
 `).join('');

 openDialog('⚙️ Tune ngưỡng tự động từ Job đã nghiệm thu',
  `<p style="font-size:13px;line-height:1.5;">Bạn có muốn <b>tự động hiệu chuẩn (tune) ngưỡng</b> phát hiện lỗi cho Project <b>${htmlEscape(projName)}</b> dựa trên <b>${completedJobs.length} Job mẫu đã hoàn thành</b> dưới đây không?</p>
   <div style="max-height:180px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:8px;margin:12px 0;">
     ${jobsHtml}
   </div>
   <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0;">
     <label style="font-size:12px;font-weight:700;">FPS video:
       <input id="tuneFps" type="number" step="0.5" value="${el('cvatFpsInput')?.value || 5.0}" style="margin-top:4px;padding:6px;width:100%;border-radius:6px;border:1px solid #d3daea;">
     </label>
     <label style="font-size:12px;font-weight:700;">Tên file cấu hình xuất:
       <input id="tuneOutName" type="text" value="${htmlEscape(defaultOut)}" style="margin-top:4px;padding:6px;width:100%;border-radius:6px;border:1px solid #d3daea;">
     </label>
   </div>
   <p style="font-size:11px;color:#475569;background:#eff6ff;padding:8px 10px;border-radius:6px;border:1px solid #dbeafe;line-height:1.4;">
     💡 <b>Cơ chế hoạt động:</b> Hệ thống sẽ tải track sạch từ các Job đã chọn, sinh lỗi giả lập có kiểm soát (nhảy box, swap ID, đứt quãng) và chạy thuật toán Grid-Search F1-score để chọn ngưỡng nhạy nhất nhưng triệt tiêu tối đa báo oan (False Positives).
   </p>`,
  [
   {label: 'Hủy', onClick: closeDialog},
   {
    label: '🚀 Bắt đầu Tune ngưỡng',
    className: 'primary',
    onClick: async () => {
     const selectedCbs = Array.from(document.querySelectorAll('.cb-calib-job:checked'));
     const selectedJobIds = selectedCbs.map(cb => Number(cb.value));
     if (!selectedJobIds.length) {
      return toast('Vui lòng chọn ít nhất 1 Job mẫu để tune ngưỡng.', true);
     }
     const fps = Number(el('tuneFps')?.value || 5.0);
     const outName = el('tuneOutName')?.value.trim() || defaultOut;
     closeDialog();
     try {
      setBusy(true);
      toast(`Đang phân tích ${selectedJobIds.length} Job mẫu và tối ưu ngưỡng (vui lòng đợi vài giây)...`);
      const res = await api('cvat/calibrate', {
       job_ids: selectedJobIds,
       project_id: projId,
       project_name: projName,
       fps: fps,
       out_name: outName,
      });
      toast(res.message || 'Tối ưu ngưỡng thành công!');
      await loadCvatConfigs(res.config_file);

      const p = res.parameters || {};
      openDialog('🎉 Kết quả Tune ngưỡng tối ưu',
       `<p style="color:#15803d;font-weight:700;font-size:14px;">✓ Đã hiệu chuẩn thành công cấu hình: <code>${htmlEscape(res.config_file)}</code></p>
        <p style="font-size:12px;color:#475569;">Dựa trên <b>${res.jobs_used?.length || 1} Job mẫu</b> (${res.total_tracks} tracks chuẩn) ở FPS = <b>${res.fps}</b>.</p>
        <div class="calibrate-result-grid">
          <div><b>Tốc độ nhảy box (max_speed):</b><br><span style="color:#2563eb;font-weight:700;">${p.max_speed} px/s</span></div>
          <div><b>Khoảng cách swap (swap_min_sep):</b><br><span style="color:#2563eb;font-weight:700;">${p.swap_min_sep}</span></div>
          <div><b>Tỷ lệ cắt ngang (swap_cross_ratio):</b><br><span style="color:#2563eb;font-weight:700;">${p.swap_cross_ratio}</span></div>
          <div><b>Chi phí ghép track (merge_max_cost):</b><br><span style="color:#2563eb;font-weight:700;">${p.merge_max_cost}</span></div>
          <div><b>Khoảng cách đứt tối đa (max_merge_gap_s):</b><br><span style="color:#2563eb;font-weight:700;">${p.max_merge_gap_s}s</span></div>
        </div>
        <p style="margin-top:12px;font-size:12px;color:#1e293b;">File cấu hình mới đã được tự động áp dụng vào ô <b>Cấu hình</b> để bạn quét và kiểm định các Job tiếp theo.</p>`,
       [{label: 'Hoàn tất & Đóng', className: 'primary', onClick: closeDialog}]
      );
     } catch(e) {
      toast(`Lỗi khi tune ngưỡng: ${e.message}`, true);
     } finally {
      setBusy(false);
     }
    }
   }
  ]
 );
}

function showBenchmarkResults(r){
 const targetMet = r.target_met;
 const precPct = (r.precision * 100).toFixed(1);
 const recPct = (r.recall * 100).toFixed(1);
 const f1Pct = (r.f1 * 100).toFixed(1);

 let typeRows = '';
 for(const [tName, m] of Object.entries(r.by_type || {})){
  const pStr = (m.precision * 100).toFixed(1) + '%';
  const rStr = (m.recall * 100).toFixed(1) + '%';
  const fStr = (m.f1 * 100).toFixed(1) + '%';
  typeRows += `<tr>
   <td><b>${htmlEscape(tName)}</b></td>
   <td>${m.tp}</td>
   <td>${m.fp}</td>
   <td>${m.fn}</td>
   <td>${pStr}</td>
   <td>${rStr}</td>
   <td><b>${fStr}</b></td>
  </tr>`;
 }

 let missedHtml = '';
 if(r.missed && r.missed.length){
  const missedItems = r.missed.slice(0, 50).map(m =>
   `<li><span class="bm-link" onclick="closeDialog();setFrame(${m.frame});">${htmlEscape(m.type)} (Frame ${m.frame}, Track #${m.track_id})</span>${m.planted ? ` - <i>${htmlEscape(m.planted)}</i>` : ''}</li>`
  ).join('');
  missedHtml = `
   <details style="margin-top:12px;background:#fff1f2;padding:8px 12px;border-radius:6px;border:1px solid #fecdd3">
    <summary style="font-weight:700;color:#9f1239;cursor:pointer">Bỏ sót (Missed - ${r.missed.length} lỗi) · Bấm xem danh sách</summary>
    <ul style="margin:8px 0 0;padding-left:20px;font-size:12px;max-height:160px;overflow-y:auto">${missedItems}</ul>
   </details>
  `;
 }

 let falseAlarmHtml = '';
 if(r.false_alarms && r.false_alarms.length){
  const faItems = r.false_alarms.slice(0, 50).map(fa =>
   `<li><span class="bm-link" onclick="closeDialog();setFrame(${fa.frame});">${htmlEscape(fa.type)} (Frame ${fa.frame}, Track #${fa.track_id})</span> - ${htmlEscape(fa.message || '')}</li>`
  ).join('');
  falseAlarmHtml = `
   <details style="margin-top:8px;background:#fefce8;padding:8px 12px;border-radius:6px;border:1px solid #fef08a">
    <summary style="font-weight:700;color:#854d0e;cursor:pointer">Bắt nhầm (False Alarms - ${r.false_alarms.length} cảnh báo) · Bấm xem danh sách</summary>
    <ul style="margin:8px 0 0;padding-left:20px;font-size:12px;max-height:160px;overflow-y:auto">${faItems}</ul>
   </details>
  `;
 }

 const content = `
  <div class="bm-target-banner ${targetMet ? 'pass' : 'fail'}">
   <span>${targetMet ? '✓ ĐẠT CHUẨN ĐỘ CHÍNH XÁC (Target Met)' : '✕ CHƯA ĐẠT CHUẨN (Below Target)'}</span>
   <small>Mục tiêu: Precision ≥ 85%, Recall ≥ 80%</small>
  </div>
  <div class="bm-summary-grid">
   <div class="bm-metric-box">
    <span>Precision (Độ chuẩn)</span>
    <strong style="color:${r.precision >= 0.85 ? '#15803d' : '#b91c1c'}">${precPct}%</strong>
    <small style="color:#64748b">${r.tp} / ${r.tp + r.fp}</small>
   </div>
   <div class="bm-metric-box">
    <span>Recall (Độ bao phủ)</span>
    <strong style="color:${r.recall >= 0.80 ? '#15803d' : '#b91c1c'}">${recPct}%</strong>
    <small style="color:#64748b">${r.tp} / ${r.tp + r.fn}</small>
   </div>
   <div class="bm-metric-box">
    <span>F1 Score</span>
    <strong>${f1Pct}%</strong>
    <small style="color:#64748b">Tol: ±${r.tolerance}f</small>
   </div>
  </div>
  <p style="font-size:12px;color:#475569;margin-bottom:6px">
   Tổng GT: <b>${r.ground_truth_count}</b> · Dự đoán: <b>${r.predicted_count}</b> ${r.baseline_ignored ? `· Bỏ qua baseline: <b>${r.baseline_ignored}</b>` : ''} ${r.absent_ignored ? `· Bỏ qua class_flip: <b>${r.absent_ignored}</b>` : ''} · Cấu hình: <b>${htmlEscape(r.config_used || '')}</b>
  </p>
  ${r.note ? `<p style="font-size:12px;color:#0369a1;background:#f0f9ff;border:1px solid #bae6fd;padding:6px 10px;border-radius:6px;margin:8px 0;">ℹ️ ${htmlEscape(r.note)}</p>` : ''}
  <table class="bm-table">
   <thead>
    <tr>
     <th>Loại lỗi</th>
     <th>TP</th>
     <th>FP</th>
     <th>FN</th>
     <th>Precision</th>
     <th>Recall</th>
     <th>F1</th>
    </tr>
   </thead>
   <tbody>
    ${typeRows || '<tr><td colspan="7">Không có dữ liệu phân loại</td></tr>'}
   </tbody>
  </table>
  ${missedHtml}
  ${falseAlarmHtml}
 `;

 openDialog('Kết quả Benchmark Ground Truth', content, [{label:'Đóng', onClick:closeDialog}]);
}
function wire(){
 el('navCvatJobs').onclick=()=>{showPage('cvatJobs');renderCvatJobs()};
 el('btnRefreshCvat').onclick=renderCvatJobs;
 if(el('btnTuneThresholds')) el('btnTuneThresholds').onclick=openTuneThresholdsDialog;
 if(el('cvatStageFilter')) el('cvatStageFilter').onchange=renderCvatJobs;
 if(el('btnCvatRescan')) el('btnCvatRescan').onclick=async()=>{
  if(!S.cvatJob)return toast('Không có CVAT Job nào đang mở.',true);
  try{
   setBusy(true);
   await scan();
   toast(`Đã quét lại lỗi theo cấu hình ${S.activeConfig || 'config_project_3.yaml'} và dữ liệu hiện tại.`);
  }catch(e){toast(e.message,true)}finally{setBusy(false)}
 };
 if(el('cvatConfigSelect')) {
  el('cvatConfigSelect').onchange = () => {
   S.activeConfig = el('cvatConfigSelect').value;
   if(el('cvatWorkspaceConfigSelect')) el('cvatWorkspaceConfigSelect').value = S.activeConfig;
  };
 }
 if(el('cvatWorkspaceConfigSelect')) {
  el('cvatWorkspaceConfigSelect').onchange = async () => {
   const newCfg = el('cvatWorkspaceConfigSelect').value;
   S.activeConfig = newCfg;
   if(el('cvatConfigSelect')) el('cvatConfigSelect').value = newCfg;
   if(S.cvatJob && S.boxes.length) {
    toast(`Đang quét lại lỗi theo cấu hình: ${newCfg}...`);
    try {
     setBusy(true);
     await scan();
     toast(`Đã quét lại lỗi theo cấu hình ${newCfg}`);
    } catch(e) {
     toast(`Lỗi khi quét: ${e.message}`, true);
    } finally {
     setBusy(false);
    }
   }
  };
 }
 if(el('btnGoToCvatJobs')) el('btnGoToCvatJobs').onclick=()=>showPage('cvatJobs');
 if(el('btnCanvasGoToJobs')) el('btnCanvasGoToJobs').onclick=()=>showPage('cvatJobs');
 el('btnCvatReject').onclick=rejectCvatJobDialog;
 el('btnCvatAccept').onclick=acceptCvatJobDialog;
 if(el('btnCvatSubmitValidation')) el('btnCvatSubmitValidation').onclick=()=>{
  if(S.cvatJob) pullCvatJobToValidation(S.cvatJob.id, S.cvatJob);
 };
 if(el('btnCvatPush')){
  el('btnCvatPush').onclick=()=>{
   if(!S.cvatJob)return toast('Không có CVAT Job nào đang mở.',true);
   if(!canEdit())return toast('Chỉ Annotator hoặc Admin được đẩy nhãn về CVAT.',true);
   openDialog('☁️ Đẩy nhãn sạch về CVAT',`
    <p>Đẩy toàn bộ <b>${S.boxes.length} bounding boxes</b> hiện tại về <b>CVAT Job #${S.cvatJob.id}</b>?</p>
    <ul style="padding-left:20px;margin:8px 0;font-size:12px;color:#475569">
      <li>Nén keyframe với thuật toán <b>Ramer–Douglas–Peucker (&lt; 0.5px)</b> để giữ đúng các mốc chuyển động mà không làm phình dung lượng.</li>
      <li>Tự động đánh dấu <code>outside=True</code> sau frame cuối của mỗi run.</li>
      <li><b>Tự động sao lưu</b> nhãn cũ trên CVAT vào <code>storage/cvat_backups/</code> trước khi ghi đè để bảo đảm an toàn.</li>
    </ul>
   `,[
    {label:'Hủy',onClick:closeDialog},
    {label:'Xác nhận đẩy lên CVAT',className:'primary',onClick:async()=>{
      closeDialog();
      try{
        setBusy(true);
        const r=await api('cvat/job/push',{
          job_id:S.cvatJob.id,
          boxes:S.boxes,
          stop_frame:S.cvatJob.stop_frame
        });
        toast(r.message||'Đã đẩy thành công về CVAT!');
      }catch(e){toast(e.message,true)}finally{setBusy(false)}
    }}
   ]);
  };
 }
 if(el('btnBenchmarkGT')){
  el('btnBenchmarkGT').onclick=()=>{
   if(!S.boxes.length)return toast('Chưa có annotation nào trong workspace để benchmark.',true);
   openDialog('◎ Benchmark với Ground Truth',`
    <p style="margin-bottom:10px">So khớp các cảnh báo phát hiện của rule engine với file Ground Truth (chuẩn T021: <b>Precision ≥ 85%, Recall ≥ 80%</b>).</p>
    <label style="display:block;margin-bottom:8px">
      <b>File Ground Truth (<code>&lt;video&gt;.truth.json</code> hoặc JSON GT):</b>
      <input type="file" id="bmGtFile" accept=".json" style="margin-top:4px">
    </label>
    <label style="display:block;margin-bottom:8px">
      <b>File Baseline sạch (Tùy chọn: <code>&lt;video&gt;.zip</code>, <code>.xml</code> hoặc <code>.json</code>):</b>
      <input type="file" id="bmBaseFile" accept=".zip,.xml,.json" style="margin-top:4px">
    </label>
    <div style="display:flex;gap:15px;align-items:center;margin-top:10px;flex-wrap:wrap">
      <label style="font-size:12px">Tolerance frame: <input id="bmTol" type="number" min="0" max="10" value="2" style="width:50px"></label>
      <label style="font-size:12px"><input id="bmOnlyGt" type="checkbox" checked> Chỉ tính loại lỗi có trong GT</label>
      <label style="font-size:12px"><input id="bmIgnoreClassFlip" type="checkbox" checked> Bỏ qua class_flip (không có trong CVAT XML)</label>
    </div>
   `,[
    {label:'Hủy',onClick:closeDialog},
    {label:'Chạy Benchmark',className:'primary',onClick:async()=>{
      const gtInput=el('bmGtFile');
      if(!gtInput.files.length)return toast('Vui lòng chọn file Ground Truth JSON.',true);
      try{
        setBusy(true);
        const gtText=await gtInput.files[0].text();
        const gtJson=JSON.parse(gtText);
        const gtList=Array.isArray(gtJson)?gtJson:(gtJson.ground_truth||[]);
        
        let baseList=[];
        let baseBoxes=null;
        const baseInput=el('bmBaseFile');
        if(baseInput&&baseInput.files.length){
          const bFile=baseInput.files[0];
          const bName=bFile.name.toLowerCase();
          toast('Đang đọc và phân tích file baseline sạch...');
          if(bName.endsWith('.zip')){
            const bRes=await fetch('/api/annotation/import',{
              method:'POST',
              headers:{'Content-Type':'application/zip','X-T021-Filename':encodeURIComponent(bFile.name)},
              body:bFile
            });
            const bData=await bRes.json();
            if(!bRes.ok) throw Error(bData.error||'Không đọc được file baseline ZIP');
            baseBoxes=(bData.boxes||[]).filter(b=>!b.outside);
          } else if(bName.endsWith('.xml')){
            const bText=await bFile.text();
            const bData=await api('parse',{filename:bFile.name,content:bText,labels_text:S.labelsText});
            baseBoxes=(bData.boxes||[]).filter(b=>!b.outside);
          } else {
            const bText=await bFile.text();
            const bJson=JSON.parse(bText);
            baseList=Array.isArray(bJson)?bJson:(bJson.ground_truth||bJson.baseline||[]);
            if(bJson.boxes) baseBoxes=bJson.boxes.filter(b=>!b.outside);
          }
        }
        
        const tol=Number(el('bmTol').value||2);
        const onlyGt=el('bmOnlyGt')?.checked ?? true;
        const ignoreClassFlip=el('bmIgnoreClassFlip')?.checked ?? true;
        const cfgVal = S.activeConfig || S.meta?.config || el('cvatConfigSelect')?.value || 'config_project_3.yaml';
        const res=await api('benchmark/evaluate',{
          boxes:S.boxes,
          ground_truth:gtList,
          baseline:baseList,
          baseline_boxes:baseBoxes,
          tolerance:tol,
          only_gt_types:onlyGt,
          ignore_class_flip:ignoreClassFlip,
          config:cfgVal,
          meta:readMeta()
        });
        
        showBenchmarkResults(res);
      }catch(e){toast('Lỗi đọc file hoặc benchmark: '+e.message,true)}finally{setBusy(false)}
    }}
   ]);
  };
 }
 if(el('btnPlayPause')) el('btnPlayPause').onclick=togglePlayback;
 if(el('btnFrameFirst')) el('btnFrameFirst').onclick=()=>setFrame(timelineBounds().start);
 if(el('btnFrameStepBack')) el('btnFrameStepBack').onclick=()=>setFrame(S.frame-10);
 if(el('btnFrameStepFwd')) el('btnFrameStepFwd').onclick=()=>setFrame(S.frame+10);
 if(el('btnFrameLast')) el('btnFrameLast').onclick=()=>setFrame(timelineBounds().end);
 if(el('playbackFps')) el('playbackFps').onchange=()=>{if(playInterval){togglePlayback();togglePlayback()}};

 el('btnAdminUsers').onclick=openUserDialog;el('btnLogout').onclick=async()=>{
 await api('auth/logout').catch(()=>{});location.reload()};
 el('btnSaveSession').onclick=()=>{const data={project_type:'t021_project',version:2,source_name:S.sourceName,meta:readMeta(),boxes:S.boxes,decisions:S.decisions,saved_at:new Date().toISOString()};download('track_qa_session.t021.json',JSON.stringify(data,null,2),'application/json')};
 if(el('btnCreateProject')) el('btnCreateProject').onclick=askCreateProject;if(el('btnSaveProject')) el('btnSaveProject').onclick=()=>saveProject(false);
 if(el('btnProjectPageCreate')) el('btnProjectPageCreate').onclick=askCreateProject;if(el('navProjects')) el('navProjects').onclick=async()=>{showPage('projects');await renderProjects()};
 el('btnDemo').onclick=loadDemo;el('btnHelp').onclick=help;el('btnScan').onclick=scan;el('sideTabIssues')?.addEventListener('click',()=>switchSideTab('issues'));
el('sideTabBoxes')?.addEventListener('click',()=>switchSideTab('boxes'));
el('frameBoxSearch')?.addEventListener('input',()=>renderFrameBoxes());
el('btnExport').onclick=()=>{if(canEdit())exportDialog()};
 el('fileAnnotation').onchange=e=>{const f=e.target.files[0];e.target.value='';if(f)loadFile(f)};
 el('fileLabels').onchange=async e=>{const f=e.target.files[0];if(f){S.labelsText=await f.text();toast('Đã tải labels.txt. Tải lại MOT để áp dụng class.')}};
 el('fileImages').onchange=e=>{if(e.target.files.length)chooseImages(e.target.files)};
 el('fileZip').onchange=e=>{if(e.target.files[0])chooseZip(e.target.files[0])};
 el('imageMapping').onchange=()=>{if(S.mediaFiles.length)mapImages();queueProjectSave()};
 el('fileVideo').onchange=e=>{if(e.target.files[0])chooseVideo(e.target.files[0])};
 el('imageWidth').onchange=()=>{scan();draw();queueProjectSave()};el('imageHeight').onchange=()=>{scan();draw();queueProjectSave()};
 el('videoStart').onchange=()=>{mapVideoFrames();queueProjectSave()};
 el('videoStep').onchange=()=>{mapVideoFrames();queueProjectSave()};
 el('fps').onchange=()=>{scan();seekVideo();queueProjectSave()};el('timeline').oninput=e=>setFrame(e.target.value);
 el('btnRetryVideo').onclick=()=>{if(!S.videoToken)return;mapVideoFrames();toast('Đang thử tải lại frame '+S.frame)};
 el('btnPrev').onclick=()=>setFrame(S.frame-1);el('btnNext').onclick=()=>setFrame(S.frame+1);
 el('btnUndo').onclick=async()=>{if(!canEdit()||!S.undo.length)return;S.redo.push(deepBoxes());S.boxes=S.undo.pop();indexBoxes();resetDecisions();undoButtons();await scan();refreshAll();queueProjectSave();toast('Đã hoàn tác (chỉ trong phiên trình duyệt)')};
 el('btnRedo').onclick=async()=>{if(!canEdit()||!S.redo.length)return;S.undo.push(deepBoxes());S.boxes=S.redo.pop();indexBoxes();resetDecisions();undoButtons();await scan();refreshAll();queueProjectSave();toast('Đã làm lại')};
 el('btnSaveBox').onclick=()=>{if(!canEdit())return;const uid=S.selectedUid;if(!uid)return;
 const data={uid,track_id:Number(el('fieldTrack').value),class_name:el('fieldClass').value,x:Number(el('fieldX').value),y:Number(el('fieldY').value),w:Number(el('fieldW').value),h:Number(el('fieldH').value)};
 doFix('update_box',data,'Đã sửa box; đã quét lỗi lại')};
 el('btnDeleteBox').onclick=()=>{if(!canEdit())return;if(S.selectedUid)addTextDialog('Xóa bounding box','Bạn có chắc chắn muốn xóa box đang chọn?',()=>doFix('delete_box',{uid:S.selectedUid}))};
 el('btnStitch').onclick=()=>{if(!canEdit())return;const src=S.boxes.find(b=>b.uid===S.selectedUid);if(!src)return;
 openDialog('Nối track ID',`<p>Nối đoạn của ID ${src.track_id} từ frame đã chọn sang ID đích (sau khi xác nhận hai đoạn là cùng object).</p><label>ID đích<input type="number" id="stitchTarget" value="${src.track_id}"></label><label>Frame bắt đầu<input type="number" id="stitchStart" value="${S.frame}"></label>`,
 [{label:'Hủy',onClick:closeDialog},{label:'Nối track',className:'primary',onClick:()=>{const d={source:src.track_id,target:Number(el('stitchTarget').value),from_frame:Number(el('stitchStart').value)};closeDialog();doFix('stitch_ids',d)}}])};
 el('btnSwap').onclick=()=>{if(!canEdit())return;const src=S.boxes.find(b=>b.uid===S.selectedUid);if(!src)return;
 openDialog('Hoán đổi 2 Track ID',`<p>Hoán đổi ID ${src.track_id} và ID khác kể từ frame được chọn. Không tự suy luận người/xe.</p><label>ID còn lại<input type="number" id="swapTarget" value="${src.track_id+1}"></label><label>Frame bắt đầu<input type="number" id="swapStart" value="${S.frame}"></label>`,
 [{label:'Hủy',onClick:closeDialog},{label:'Swap ID',className:'primary',onClick:()=>{const d={a:src.track_id,b:Number(el('swapTarget').value),from_frame:Number(el('swapStart').value)};closeDialog();doFix('swap_ids',d)}}])};

 el('btnCreateMode').onclick=()=>{if(!canEdit())return toast('Reviewer không được vẽ bounding box.',true);S.isCreate=!S.isCreate;el('btnCreateMode').classList.toggle('active',S.isCreate);toast(S.isCreate?'Kéo chuột để tạo bounding box.':'Đã tắt chế độ vẽ box')};
 if(el('btnCreateIssue')) el('btnCreateIssue').onclick=()=>openCreateIssueDialog();
 if(el('btnToolbarRaiseIssue')) el('btnToolbarRaiseIssue').onclick=()=>openCreateIssueDialog({frame:S.frame});
 if(el('btnBoxRaiseIssue')) el('btnBoxRaiseIssue').onclick=()=>{
   const curBox = S.boxes.find(b => b.uid === S.selectedUid);
   openCreateIssueDialog({track_id: curBox?.track_id, frame: S.frame});
 };
 el('issueFilter').onchange=renderIssues;el('btnQuality').onclick=async()=>{if(!S.boxes.length)return;try{const r=await api('quality',{boxes:S.boxes,...readMeta(),project_id:S.projectId});openDialog('Quality Gate',
 `<p><b>${r.pass_gate?'PASS':'CHƯA PASS'}</b></p><p>${r.structural_blockers.length} lỗi cấu trúc chặn xuất sạch. ${r.unresolved_high.length} cảnh báo HIGH chưa xử lý; ${(r.pending_handoffs||[]).length} yêu cầu chưa kết thúc QC.</p><p>${htmlEscape(r.disclaimer)}</p>`,[{label:'Đóng',onClick:closeDialog}])}catch(e){toast(e.message,true)}};
 el('navWorkspace').onclick=()=>showPage('workspace');el('navReport').onclick=()=>showPage('report');el('navAudit').onclick=()=>showPage('audit');
 el('btnSaveReport').onclick=()=>download('t021_qa_report.json',JSON.stringify(reportObj(),null,2),'application/json');
 el('btnRefreshAudit').onclick=renderAudit;
 el('btnBenchmark').onclick=()=>openDialog('Đánh giá với Ground Truth',`<p>Tải JSON gồm hai mảng <code>predicted</code> và <code>ground_truth</code>, mỗi sự kiện có type, frame, track_id. Matching 1–1, tolerance ±2 frame.</p><label>JSON Ground Truth & Predictions<input id="benchmarkFile" type="file" accept=".json"></label>`,
 [{label:'Hủy',onClick:closeDialog},{label:'Đánh giá',className:'primary',onClick:async()=>{const f=el('benchmarkFile').files[0];if(!f)return toast('Chọn file JSON benchmark',true);
 try{const data=JSON.parse(await f.text()),r=await api('evaluate',data);closeDialog();openDialog('Kết quả Benchmark',`<p>TP: ${r.tp} · FP: ${r.fp} · FN: ${r.fn}</p><p>Precision: <b>${(100*r.precision).toFixed(1)}%</b> · Recall: <b>${(100*r.recall).toFixed(1)}%</b> · F1: <b>${(100*r.f1).toFixed(1)}%</b></p><p>Mục tiêu: ${r.target_met?'Đạt':'Chưa đạt'}. ${htmlEscape(r.note)}</p>`,[{label:'Đóng',onClick:closeDialog}])}catch(e){toast(e.message,true)}}}]);
 el('dialogClose').onclick=closeDialog;el('dialog').onclick=e=>{if(e.target===el('dialog'))closeDialog()};
  window.addEventListener('keydown',e=>{
   if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||!el('dialog').hidden)return;
   if(e.code==='Space'&&!e.repeat){S.spaceDown=true;el('viewer')?.classList.add('pan-ready');e.preventDefault();return}
   if(e.key==='Escape'){if(S.selectedUid||S.selectedTrackId!==null){selectBox(null);return}}
   if(e.key==='0'||e.code==='Numpad0'||e.key==='r'){resetZoom();return}
   if(e.key==='+'||e.key==='='||e.code==='NumpadAdd'){zoomAtCenter(1.25);return}
   if(e.key==='-'||e.key==='_'||e.code==='NumpadSubtract'){zoomAtCenter(1/1.25);return}
   if(e.key==='ArrowRight')setFrame(S.frame+(e.shiftKey?10:1));
   else if(e.key==='ArrowLeft')setFrame(S.frame-(e.shiftKey?10:1));
   else if(e.key==='Home')setFrame(timelineBounds().start);
   else if(e.key==='End')setFrame(timelineBounds().end);
   else if(e.key.toLowerCase()==='p')togglePlayback();
   else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();el('btnUndo').click()}
  });
  window.addEventListener('keyup',e=>{
   if(e.code==='Space'){S.spaceDown=false;el('viewer')?.classList.remove('pan-ready')}
  });
  const c=el('viewer');
  c.addEventListener('contextmenu',e=>e.preventDefault());
  c.addEventListener('dblclick',e=>{
    const p=toWorld(e);
    const v=hit(p);
    if(v?.box){
     selectBox(v.box.uid);
     zoomToBox(v.box, true);
    } else {
     resetZoom();
    }
   });
  c.addEventListener('wheel',e=>{
   e.preventDefault();
   const r=c.getBoundingClientRect();
   const clientX=(e.clientX-r.left)*(c.width/r.width);
   const clientY=(e.clientY-r.top)*(c.height/r.height);
   const wx=(clientX-S.panX)/S.zoom;
   const wy=(clientY-S.panY)/S.zoom;
   const factor=e.deltaY<0?1.18:(1/1.18);
   const newZoom=Math.min(25.0,Math.max(0.4,S.zoom*factor));
   S.panX=clientX-wx*newZoom;
   S.panY=clientY-wy*newZoom;
   S.zoom=newZoom;
   draw();
  },{passive:false});

  if(el('btnZoomIn')) el('btnZoomIn').onclick=()=>zoomAtCenter(1.25);
  if(el('btnZoomOut')) el('btnZoomOut').onclick=()=>zoomAtCenter(1/1.25);
  if(el('btnZoomReset')) el('btnZoomReset').onclick=()=>resetZoom();
  if(el('zoomPercent')) el('zoomPercent').onclick=()=>resetZoom();
  if(el('btnDeselectBox')) el('btnDeselectBox').onclick=()=>selectBox(null);

  c.addEventListener('pointerdown',e=>{
   if(!S.boxes.length&&!S.isCreate&&!S.videoToken&&!S.mediaFiles.length)return;
   const isPanBtn=e.button===1||e.button===2||e.altKey||S.spaceDown;
   const p=toWorld(e);
   const v=hit(p);
   if(isPanBtn){
    S.panDrag={startX:e.clientX,startY:e.clientY,origPanX:S.panX,origPanY:S.panY,moved:false,clickEmpty:false};
    c.classList.add('panning');
    c.setPointerCapture(e.pointerId);
    return;
   }
   if(S.isCreate){
    c.setPointerCapture(e.pointerId);
    S.drag={type:'create',start:p,preview:[p.x,p.y,0,0]};
    return;
   }
   if(v){
    selectBox(v.box.uid);
    c.setPointerCapture(e.pointerId);
    if(canEdit()){
     S.drag={type:v.type,start:p,orig:{...v.box},preview:null};
    }
    return;
   }
   // Click on empty space: prepare pan drag if moved > 6px
   S.panDrag={startX:e.clientX,startY:e.clientY,origPanX:S.panX,origPanY:S.panY,moved:false,clickEmpty:true};
   c.setPointerCapture(e.pointerId);
  });
  c.addEventListener('pointermove',e=>{
   if(S.panDrag){
    const r=c.getBoundingClientRect();
    const dx=(e.clientX-S.panDrag.startX)*(c.width/r.width);
    const dy=(e.clientY-S.panDrag.startY)*(c.height/r.height);
    if(Math.hypot(dx,dy)>6){
     S.panDrag.moved=true;
     c.classList.add('panning');
    }
    if(S.panDrag.moved){
     S.panX=S.panDrag.origPanX+dx;
     S.panY=S.panDrag.origPanY+dy;
     draw();
    }
    return;
   }
   if(S.drag){
    const p=toWorld(e),d=S.drag;
    if(d.type==='create'){const x=Math.min(d.start.x,p.x),y=Math.min(d.start.y,p.y);d.preview=[x,y,Math.abs(p.x-d.start.x),Math.abs(p.y-d.start.y)];draw();return}
    const b=S.boxes.find(v=>v.uid===d.orig.uid);if(!b)return;
    if(d.type==='move'){b.x=Math.max(0,d.orig.x+p.x-d.start.x);b.y=Math.max(0,d.orig.y+p.y-d.start.y)}
    else if(d.type==='resize'){b.w=Math.max(2,d.orig.w+p.x-d.start.x);b.h=Math.max(2,d.orig.h+p.y-d.start.y)}
    draw();
    return;
   }
   if(!S.spaceDown){
    const p=toWorld(e);
    const v=hit(p);
    const prevHover=S.hoveredUid;
    S.hoveredUid=v?.box?.uid||null;
    c.style.cursor=v?'pointer':'crosshair';
    if(prevHover!==S.hoveredUid)draw();
   }
  });
  c.addEventListener('pointerleave',()=>{
   if(S.hoveredUid){
    S.hoveredUid=null;
    draw();
   }
  });
  c.addEventListener('pointerup',async e=>{
   if(S.panDrag){
    const moved=S.panDrag.moved;
    const clickEmpty=S.panDrag.clickEmpty;
    S.panDrag=null;
    c.classList.remove('panning');
    if(!moved&&clickEmpty){
     selectBox(null);
    }
    return;
   }
   if(!S.drag)return;
   const d=S.drag;S.drag=null;
   if(d.type==='create'){draw();const [x,y,w,h]=d.preview;if(w<4||h<4)return;
   openDialog('Tạo Bounding Box',`<label>Track ID<input id="newTrack" type="number" value="${S.boxes.reduce((m,b)=>Math.max(m,b.track_id),0)+1}"></label><label>Class<input id="newClass" value="Car"></label>`,
   [{label:'Hủy',onClick:closeDialog},{label:'Tạo box',className:'primary',onClick:()=>{const tid=Number(el('newTrack').value),cls=el('newClass').value;closeDialog();doFix('create_box',{frame_id:S.frame,track_id:tid,x,y,w,h,class_name:cls})}}]);return}
   const box=S.boxes.find(v=>v.uid===d.orig.uid);if(!box)return;
   const moved=JSON.stringify([box.x,box.y,box.w,box.h])!==JSON.stringify([d.orig.x,d.orig.y,d.orig.w,d.orig.h]);
   if(!moved)return;
   const updated={uid:d.orig.uid,x:Math.round(box.x*100)/100,y:Math.round(box.y*100)/100,w:Math.round(box.w*100)/100,h:Math.round(box.h*100)/100};
   Object.assign(box,{x:d.orig.x,y:d.orig.y,w:d.orig.h});
   await doFix('update_box',updated,'Đã cập nhật box sau khi kéo');
  });
  c.addEventListener('pointercancel',()=>{
   if(S.panDrag){S.panDrag=null;c.classList.remove('panning')}
   if(S.drag?.orig){const b=S.boxes.find(x=>x.uid===S.drag.orig.uid);if(b)Object.assign(b,S.drag.orig)}
   S.drag=null;draw();
  });
}
function role(){return S.user?.role||''}
function isAdmin(){return role()==='admin'}
function isSupervisor(){return role()==='supervisor'||role()==='reviewer'||isAdmin()}
function isWorker(){return role()==='worker'||role()==='annotator'}
function canEdit(){return Boolean(S.canEditBoxes || isWorker() || isAdmin())}
function canReview(){return Boolean(S.canReview || isSupervisor() || role()==='reviewer')}

async function loadOrganizations(){
 const select = el('selectOrg');
 if(!select) return;
 try {
  const res = await api('cvat/organizations');
  const orgs = res.organizations || [];
  select.innerHTML = orgs.map(o => 
   `<option value="${htmlEscape(o.slug)}" ${o.slug === res.active_org ? 'selected' : ''}>${htmlEscape(o.name || o.slug)} (${(o.role||'user').toUpperCase()})</option>`
  ).join('');
  if(S.user && res.active_org !== undefined) {
   S.user.active_org = res.active_org;
  }
 } catch(e) {
  console.warn('Could not load organizations:', e);
  select.innerHTML = '<option value="" selected>Cá nhân (Personal Workspace)</option>';
 }
}

function initOrgSelector(){
 const select = el('selectOrg');
 if(!select || select.dataset.bound) return;
 select.dataset.bound = 'true';
 select.onchange = async (ev) => {
  const newOrg = ev.target.value;
  try {
   setBusy(true);
   toast(`Đang chuyển sang tổ chức: ${newOrg || 'Cá nhân'}...`);
   const res = await api('cvat/organization/set', { org_slug: newOrg });
   toast(res.message || 'Đã chuyển tổ chức');
   if(S.user) {
    S.user.role = res.role;
    S.user.active_org = res.active_org;
   }
   updateRoleUI();
   await renderCvatJobs();
  } catch(e) {
   toast(`Lỗi chuyển tổ chức: ${e.message}`, true);
   await loadOrganizations();
  } finally {
   setBusy(false);
  }
 };
}

function updateRoleUI(){
 const admin=isAdmin(), superv=isSupervisor(), worker=isWorker();
 const isRev=canReview();
 document.body.classList.toggle('reviewer-mode', !admin && !worker);
 if(el('importRoleNotice')) el('importRoleNotice').hidden=true;
 if(el('importBar')) el('importBar').hidden=true;
 if(el('operatorUsername')){
  el('operatorUsername').textContent=S.user?.username||'—';
  el('operatorUsername').title=S.user?.username||'—';
 }
 if(el('operatorRoleBadge')){
  const r=role().toUpperCase();
  el('operatorRoleBadge').textContent=r||'CHƯA ĐĂNG NHẬP';
  el('operatorRoleBadge').className=`operator-role-badge role-${role()}`;
 }
 if(el('btnLogout')) el('btnLogout').hidden=false;
 if(el('btnAdminUsers')) el('btnAdminUsers').hidden=!admin;

 // Phân quyền thanh điều hướng Sidebar theo vai trò:
 // - Worker / Annotator: Chỉ thấy CVAT Jobs & Review Workspace
 // - Supervisor: Thấy thêm QA Report, Audit History
 // - Admin: Toàn quyền (bao gồm cả Projects / Phiên làm việc offline)
 if(el('navCvatJobs')) el('navCvatJobs').hidden = false;
 if(el('navWorkspace')) el('navWorkspace').hidden = false;
 if(el('navProjects')) el('navProjects').hidden = !admin;
 if(el('navReport')) el('navReport').hidden = !superv;
 if(el('navAudit')) el('navAudit').hidden = !superv;
 if(el('btnTuneThresholds')) el('btnTuneThresholds').hidden = !admin;
 if(el('btnBenchmarkGT')) el('btnBenchmarkGT').hidden = !admin;

 // Ẩn toàn bộ nút tạo / lưu / xuất offline & benchmark đối với Worker & Supervisor, CHỈ Admin mới thấy:
 for(const id of ['btnCreateProject','btnSaveProject','btnSaveSession','btnDemo','btnExport','btnProjectPageCreate','btnBenchmark']){
  const button=el(id);
  if(button) button.hidden = !admin;
 }

 // Quyền sửa bounding box trên thanh công cụ Workspace:
 const canModifyBox = admin || S.canEditBoxes || (worker && !S.cvatJob);
 for(const id of ['btnCreateMode','btnSaveBox','btnDeleteBox','btnStitch','btnSwap','btnUndo','btnRedo']){
  const button=el(id);
  if(button) button.hidden = !canModifyBox;
 }
 for(const id of ['fieldTrack','fieldClass','fieldX','fieldY','fieldW','fieldH']) {
  if(el(id)) el(id).disabled = !canModifyBox;
 }

 // Quyền trên CVAT Job: Annotator không quét thô thuật toán và không tự tạo issue cho mình
 const isCvatAnnotator = Boolean(S.cvatJob && S.cvatJobEffectiveRole === 'annotator' && !admin);
 if(el('btnScan')) el('btnScan').hidden = isCvatAnnotator;
 if(el('btnCvatRescan')) el('btnCvatRescan').hidden = isCvatAnnotator;
 if(el('btnCreateIssue')) el('btnCreateIssue').hidden = isCvatAnnotator;
 if(el('btnBoxRaiseIssue')) el('btnBoxRaiseIssue').hidden = isCvatAnnotator;
 if(el('cvatWorkspaceConfigSelect')) el('cvatWorkspaceConfigSelect').disabled = isCvatAnnotator;

 setBusy(false);undoButtons();selectBox(S.selectedUid);renderIssues();
}
async function initAuth(){
 const gate=el('authGate'),form=el('authForm');
 try{
  const me=await api('auth/me').catch(()=>null);
  if(me?.user){
   S.user=me.user;
   gate.hidden=true;
   updateRoleUI();
   await loadOrganizations();
   initOrgSelector();
   await loadCvatConfigs();
   showPage('cvatJobs');
   renderCvatJobs();
   return;
  }
 }catch(e){el('authMessage').textContent='Không kết nối được backend: '+e.message}
 el('authDescription').textContent='Đăng nhập bằng tài khoản CVAT của bạn (cvat.trantuli.id.vn); tự động đồng bộ vai trò & danh sách job.';
 el('authSubmit').textContent='Đăng nhập qua CVAT';
 form.onsubmit=async ev=>{
  ev.preventDefault();const username=el('authUsername').value.trim(),password=el('authPassword').value;
  try{
   const r=await api('auth/login',{username,password});
   S.user=r.user;
   gate.hidden=true;
   el('authMessage').textContent='';
   updateRoleUI();
   await loadOrganizations();
   initOrgSelector();
   await loadCvatConfigs();
   showPage('cvatJobs');
   renderCvatJobs();
   toast('Đã đăng nhập qua CVAT: '+username+' ('+r.user.role+')');
  }catch(e){el('authMessage').textContent=e.message}
 };
}
async function openUserDialog(){
 if(role()!=='admin')return;
 try{
  const res=await api('auth/cvat-users');
  const users=res.users||[];
  const rowsHtml=users.map(u=>`
   <tr style="border-bottom:1px solid #eef2f7;">
    <td style="padding:8px 6px;"><b>${htmlEscape(u.username)}</b><br><small style="color:#78869e;">ID: ${u.id}${u.is_staff?' · Staff':''}</small></td>
    <td style="padding:8px 6px;"><span class="pill" style="margin:0;font-size:10px;">${htmlEscape(u.org_role||u.groups.join(', ')||'User')}</span></td>
    <td style="padding:8px 6px;">
     <select class="user-role-select" data-user="${htmlEscape(u.username)}" style="font-size:11px;padding:4px 6px;border-radius:6px;border:1px solid #c8d4ea;">
      <option value="annotator" ${u.role==='annotator'?'selected':''}>Annotator (Sửa nhãn)</option>
      <option value="reviewer" ${u.role==='reviewer'?'selected':''}>Reviewer (Kiểm duyệt QC)</option>
      <option value="admin" ${u.role==='admin'?'selected':''}>Admin / Lead (Toàn quyền)</option>
     </select>
    </td>
   </tr>
  `).join('');

  openDialog('Quản lý vai trò tài khoản CVAT',`
   <p style="margin-bottom:10px;">Phân vai trò trực tiếp cho các tài khoản CVAT khi làm việc trên Studio:</p>
   <table style="width:100%;border-collapse:collapse;font-size:12px;">
    <thead><tr style="text-align:left;border-bottom:2px solid #d3daea;color:#64748b;"><th style="padding:6px;">Tài khoản</th><th style="padding:6px;">CVAT Group/Org</th><th style="padding:6px;">Vai trò trên Studio</th></tr></thead>
    <tbody>${rowsHtml}</tbody>
   </table>
   <small style="color:#64748b;margin-top:12px;display:block;">Thay đổi có hiệu lực ngay lập tức. Annotator sẽ chỉ thấy các job được gán; Reviewer sẽ thấy các job ở stage Validation.</small>
  `,[
   {label:'Đóng',onClick:closeDialog}
  ]);

  document.querySelectorAll('.user-role-select').forEach(sel=>{
   sel.onchange=async(ev)=>{
    const uname=ev.target.getAttribute('data-user');
    const newRole=ev.target.value;
    try{
     await api('auth/set-role',{username:uname,role:newRole});
     toast(`Đã gán vai trò [${newRole.toUpperCase()}] cho tài khoản ${uname}`);
    }catch(e){
     toast(`Lỗi khi gán vai trò: ${e.message}`,true);
    }
   };
  });
 }catch(e){
  toast(`Lỗi khi tải danh sách người dùng: ${e.message}`,true);
 }
}
wire();refreshAll();updateProjectTitle();loadCvatConfigs();initAuth();
