'use strict';
const el=id=>document.getElementById(id);
const htmlEscape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={boxes:[],meta:{},session:null,issues:[],decisions:{},selectedUid:null,selectedIssue:null,
         frame:1,frameMap:new Map(),undo:[],redo:[],imageURLs:new Map(),images:new Map(),videoURL:null,
         mediaFiles:[],mediaNames:new Map(),mediaToken:null,mediaMode:'none',
         videoToken:null,videoMeta:null,videoError:'',projectId:null,projectName:null,
         user:null,tasks:{},cvatJob:null,
         isCreate:false,drag:null,sourceName:'',lastScan:null,labelsText:'',page:'cvatJobs',report:null};
let busy=false,toastTimer;
async function api(path,body={}){
 const res=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 let data;try{data=await res.json();}catch(e){throw Error('Máy chủ không trả JSON hợp lệ')}
 if(!res.ok)throw Error(data.error||`Lỗi HTTP ${res.status}`);
 return data;
}
function toast(text,error=false){const v=el('toast');v.textContent=text;v.className='toast show'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>v.className='toast',4400)}
function setBusy(v){busy=v;el('btnScan').disabled=v||!S.boxes.length;el('btnExport').disabled=v||!S.boxes.length||!canEdit();el('btnDemo').disabled=v||!canEdit();el('btnSaveSession').disabled=v||!S.boxes.length||!canEdit()}
function deepBoxes(){return S.boxes.map(b=>({...b}))}
function checkpoint(){if(!canEdit())return;S.undo.push(deepBoxes());if(S.undo.length>20)S.undo.shift();S.redo=[];undoButtons()}
function undoButtons(){el('btnUndo').disabled=!canEdit()||!S.undo.length;el('btnRedo').disabled=!canEdit()||!S.redo.length}
function indexBoxes(){S.frameMap=new Map();for(const b of S.boxes){if(!S.frameMap.has(b.frame_id))S.frameMap.set(b.frame_id,[]);S.frameMap.get(b.frame_id).push(b)} }
function resetDecisions(){S.decisions=Object.fromEntries(Object.entries(S.tasks).map(([id,t])=>[id,t.status]));S.selectedIssue=null}
function readMeta(){const width=Number(el('imageWidth').value),height=Number(el('imageHeight').value),fpsVal=Number(el('fps').value);
 return {...S.meta,width:width>0?width:null,height:height>0?height:null,fps:fpsVal>0?fpsVal:(S.meta.fps||5.0)}}
function setMetadata(data,name){
 clearMedia(); // Prevent previous dataset/demo media from leaking into a newly imported XML/MOT.
 S.boxes=data.boxes||[];S.meta={format:data.format,frame_base:data.frame_base??0,mode:data.mode||'video',
 width:data.width||null,height:data.height||null,total_frames:data.total_frames||null,labels:data.labels||[],task_name:data.task_name||''};
 S.session=(S.projectId && S.session && !restoringProject)?S.session:data.session;S.sourceName=data.source_name||name;S.undo=[];S.redo=[];S.selectedUid=null;S.issues=[];if(!S.projectId)S.tasks={};resetDecisions();S.lastScan=null;
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
 el('frameLabel').textContent=`Frame ${S.frame}`;el('frameRange').textContent=`${b.start} – ${b.end}`;selectBox(null);seekVideo();updateMediaStatus();draw();queueProjectSave()}
function refreshTimeline(){const b=timelineBounds();el('timeline').min=b.start;el('timeline').max=b.end;
 el('timeline').disabled=!S.boxes.length&&!S.videoToken&&!S.mediaFiles.length;el('frameCount').textContent=`${b.end-b.start+1} frames`;
 el('timelineMarks').innerHTML='';
 if(b.end<=b.start)return;
 const marks=new Map();for(const i of S.issues){const old=marks.get(i.frame);if(!old||({high:3,medium:2,low:1}[i.severity]>({high:3,medium:2,low:1}[old])))marks.set(i.frame,i.severity)}
 [...marks].slice(0,2000).forEach(([f,s])=>{const v=document.createElement('span');v.className=s;v.style.left=`${100*(f-b.start)/(b.end-b.start)}%`;el('timelineMarks').appendChild(v)});
}
function metrics(){const tracks=new Set(S.boxes.map(b=>b.track_id));el('metricBoxes').textContent=S.boxes.length.toLocaleString('vi-VN');
 el('metricTracks').textContent=tracks.size.toLocaleString('vi-VN');el('metricIssues').textContent=S.issues.length.toLocaleString('vi-VN');
 el('metricHigh').textContent=S.issues.filter(i=>i.severity==='high').length;
 el('issueSubtitle').textContent=S.lastScan?'Cần xác nhận từng issue':'Chưa quét';
 const reviewed=S.issues.filter(i=>S.decisions[i.id]).length;
 el('metricProgress').textContent=S.issues.length?Math.round(100*reviewed/S.issues.length)+'%':'—';
 el('issueCount').textContent=S.issues.length;}
function refreshAll(){metrics();refreshTimeline();setFrame(S.frame);renderIssues();setBusy(false)}
async function loadFile(file){try{
 setBusy(true);
 if(role()==='reviewer'&&S.projectId){
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
 if(role()==='reviewer')toast('Reviewer đã nhập dữ liệu ở chế độ chỉ xem/QC. Annotator cần đưa dataset vào Project trước khi QC được lưu và chuyển sửa.');
 await scan();
 }catch(e){toast(e.message,true)}finally{setBusy(false)}}
async function scan(){if(!S.boxes.length)return;try{setBusy(true);const r=await api('validate',{boxes:S.boxes,...readMeta()});S.issues=r.issues;S.lastScan=r;
 // A reviewed issue is linked to its still-identical stable issue id. New issues remain pending.
 renderIssues();metrics();refreshTimeline();draw();toast(`Hoàn tất: ${r.count} cảnh báo, ${r.stats.tracks} track`)}catch(e){toast(e.message,true)}finally{setBusy(false)}}
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
 const img=imageAtFrame(),v=el('videoElement');
 if(img&&img.complete&&img.naturalWidth){ctx.drawImage(img,0,0,c.width,c.height)}
 else if(S.videoURL&&v.readyState>=2){try{ctx.drawImage(v,0,0,c.width,c.height)}catch(e){}}
 else {ctx.strokeStyle='#ffffff10';ctx.lineWidth=1;for(let x=0;x<c.width;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,c.height);ctx.stroke()}for(let y=0;y<c.height;y+=40){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(c.width,y);ctx.stroke()}
   ctx.font='bold 19px system-ui';ctx.fillStyle='#fff';ctx.textAlign='center';
   ctx.fillText(S.videoError?'Không đọc được frame video':S.videoToken?'Đang tải frame video...':S.mediaFiles.length?`Chưa có ảnh cho Frame ${S.frame}`:'Chưa tải ảnh/video của dataset này',c.width/2,c.height/2);ctx.textAlign='left';}
 const rows=S.frameMap.get(S.frame)||[];
 ctx.textBaseline='top';ctx.font='bold 12px system-ui';
 for(const b of rows){const selected=b.uid===S.selectedUid, col=selected?'#fff000':colors(b.track_id);
 const x=b.x*sx,y=b.y*sy,bw=b.w*sx,bh=b.h*sy;
 ctx.strokeStyle=col;ctx.lineWidth=selected?3:1.8;ctx.strokeRect(x,y,bw,bh);
 const text=`#${b.track_id} · ${b.class_name}`;const tw=ctx.measureText(text).width+10;
 ctx.fillStyle=selected?'#edce00':'#152139db';ctx.fillRect(x,Math.max(0,y-21),tw,20);
 ctx.fillStyle=selected?'#182033':col;ctx.fillText(text,x+5,Math.max(0,y-19));
 if(selected){ctx.fillStyle='#fff000';ctx.fillRect(x+bw-5,y+bh-5,10,10)}
 }
 if(S.drag?.type==='create'&&S.drag.preview){const [x,y,bw,bh]=S.drag.preview;ctx.setLineDash([7,5]);ctx.strokeStyle='#66ffcb';ctx.lineWidth=2;ctx.strokeRect(x*sx,y*sy,bw*sx,bh*sy);ctx.setLineDash([])}
 el('canvasMessage').hidden=!!S.boxes.length||!!S.videoToken||!!S.mediaFiles.length;
}
function toWorld(evt){const c=el('viewer'),r=c.getBoundingClientRect(),m=canvasParams();return {x:(evt.clientX-r.left)*m.w/r.width,y:(evt.clientY-r.top)*m.h/r.height}}
function hit(p){const rows=[...(S.frameMap.get(S.frame)||[])].reverse();const m=canvasParams();
 for(const b of rows){const corner=Math.max(6,9/m.sx);if(Math.abs(p.x-(b.x+b.w))<corner&&Math.abs(p.y-(b.y+b.h))<corner)return {box:b,type:'resize'};
 if(p.x>=b.x&&p.x<=b.x+b.w&&p.y>=b.y&&p.y<=b.y+b.h)return {box:b,type:'move'}}return null}
function selectBox(uid){S.selectedUid=uid;const b=S.boxes.find(x=>x.uid===uid);
 for(const [id,k] of [['fieldUid','uid'],['fieldTrack','track_id'],['fieldClass','class_name'],['fieldX','x'],['fieldY','y'],['fieldW','w'],['fieldH','h']])el(id).value=b?b[k]:'';
 el('btnSaveBox').disabled=!b||!canEdit();el('btnDeleteBox').disabled=!b||!canEdit();el('btnStitch').disabled=!b||!canEdit();el('btnSwap').disabled=!b||!canEdit();draw()}
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
 verified:'Reviewer đã xác nhận',false_positive:'Không phải lỗi (Reviewer)'};
function handoffDialog(i,status){
 if(!S.projectId)return toast('Hãy tạo hoặc mở Project trước khi gửi yêu cầu QC.',true);
 const isReviewer=role()==='reviewer', allowed=(isReviewer&&status!=='fixed')||(canEdit()&&status==='fixed');
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
function renderIssues(){
 const dst=el('issueList'),filter=el('issueFilter').value;
 const merged=[...S.issues],seen=new Set(merged.map(i=>i.id));
 for(const [id,t] of Object.entries(S.tasks))if(!seen.has(id))merged.push({id,type:t.type,frame:t.frame,
 track_id:t.track_id,message:t.message||'Issue đã biến mất sau khi sửa – chờ Reviewer xác nhận',severity:'low',archived:true});
 const list=merged.filter(i=>{const status=S.tasks[i.id]?.status||null;
  return filter==='all'||(filter==='open'&&(!status||status==='needs_fix'||status==='fixed'))||
  (filter==='handoff'&&status==='needs_fix')||(filter==='fixed'&&status==='fixed')||
  (filter==='high'&&i.severity==='high')||i.type===filter;
 });
 dst.replaceChildren();if(!list.length){dst.innerHTML='<div class="side-empty">Không có cảnh báo phù hợp. Reviewer và Annotator làm việc qua các yêu cầu đã lưu trong Project.</div>';return}
 if(list.length>250){const note=document.createElement('p');note.className='side-empty';note.textContent=`Hiển thị 250/${list.length} issue`;dst.appendChild(note)}
 for(const i of list.slice(0,250)){
  const t=S.tasks[i.id],status=t?.status;
  const e=document.createElement('article');e.className=`issue-card ${i.severity||'low'}${S.selectedIssue===i.id?' selected':''}${status?' reviewed':''}`;
  e.innerHTML=`<div class="issue-headline"><b>${htmlEscape(i.type)}</b><span class="sev ${i.severity||'low'}">${htmlEscape(i.severity||'low')}</span></div>
 <div class="issue-info">Frame ${i.frame} · Track #${i.track_id}</div>
 ${status?`<span class="issue-status">${htmlEscape(statusText[status]||status)}</span>`:''}
 <p class="issue-message">${htmlEscape(i.message)}</p>
 ${t?.note?`<div class="issue-note">${htmlEscape(t.updated_by)}: ${htmlEscape(t.note)}</div>`:''}
 <div class="issue-actions"></div>`;
  const actions=e.querySelector('.issue-actions');
  const make=(label,cls,fn)=>{const b=document.createElement('button');b.textContent=label;b.className=cls;
   b.onclick=ev=>{ev.stopPropagation();fn()};actions.appendChild(b)};
  make('◎ Xem frame','',()=>{showPage('workspace');S.selectedIssue=i.id;setFrame(i.frame);renderIssues()});
  if(role()==='reviewer'){
   if(S.cvatJob){
    if(status==='false_positive'){
     make('↩ Khôi phục issue','fix',()=>{delete S.decisions[i.id];delete i.status;renderIssues();metrics();toast('Đã khôi phục issue.')});
    }else{
     make('✓ Bỏ qua (False positive)','reject',()=>{S.decisions[i.id]='false_positive';i.status='false_positive';renderIssues();metrics();toast('Đã đánh dấu bỏ qua issue này khi raise lên CVAT.')});
    }
   }else{
    if(!status||status==='needs_fix'){
     make('↪ Gửi Annotator','fix',()=>handoffDialog(i,'needs_fix'));
     if(!status)make('✓ Không phải lỗi','reject',()=>handoffDialog(i,'false_positive'));
    }
    if(status==='fixed'){
     make('✓ Xác nhận đã sửa','reject',()=>handoffDialog(i,'verified'));
     make('↩ Yêu cầu sửa tiếp','fix',()=>handoffDialog(i,'needs_fix'));
    }
   }
  }else if(canEdit()&&status==='needs_fix')make('✓ Đã sửa · Gửi lại QC','fix',()=>handoffDialog(i,'fixed'));
  e.onclick=()=>{S.selectedIssue=i.id;setFrame(i.frame);renderIssues()};dst.appendChild(e);
 }
}
function showPage(page){
 S.page=page;
 const isWs=page==='workspace';
 if(el('workspacePage'))el('workspacePage').hidden=!isWs;
 if(el('workspace'))el('workspace').hidden=!isWs;
 if(el('cvatJobsPage'))el('cvatJobsPage').hidden=page!=='cvatJobs';
 if(el('projectsPage'))el('projectsPage').hidden=page!=='projects';
 if(el('reportPage'))el('reportPage').hidden=page!=='report';
 if(el('auditPage'))el('auditPage').hidden=page!=='audit';
 if(el('importBar'))el('importBar').hidden=!isWs||S.mediaMode==='cvat-stream';
 if(el('cvatJobBanner'))el('cvatJobBanner').hidden=!isWs||!S.cvatJob;
 for(const [id,name] of [['navCvatJobs','cvatJobs'],['navProjects','projects'],['navWorkspace','workspace'],['navReport','report'],['navAudit','audit']]){
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
function help(){openDialog('Hướng dẫn sử dụng',`<p><b>1.</b> Tải MOT TXT/CSV hoặc CVAT for video XML, rồi chọn ảnh/video để đối chiếu. Với MOT, có thể tải thêm <b>labels.txt</b>.</p>
 <p><b>2.</b> Nếu có frames.zip, bấm <b>Tải frames.zip</b>; nếu ảnh rời, chọn nhiều ảnh JPG/PNG. Với XML có N frame và N ảnh, chọn <b>Ghép ảnh: Tự động</b> hoặc <b>Theo thứ tự (XML)</b>. Không mặc định số cuối tên ảnh là frame XML.</p><p><b>3.</b> Chọn <b>Tạo project</b> để lưu công việc. Video được giải mã bằng FFmpeg trên máy chủ rồi hiển thị JPEG theo từng frame. Khi XML được lấy mẫu, điều chỉnh <b>Video start</b> và <b>Step</b>. Có thể tải video trước để xem và vẽ box.</p><p><b>4.</b> Nhập kích thước W, H và FPS đúng; nhấn <b>Quét lỗi</b>.</p>
 <p><b>4.</b> <b>Reviewer:</b> xem issue, gửi về Annotator cùng ghi chú hoặc đánh dấu False Positive. Reviewer không được kéo/xóa/sửa box.</p>
 <p><b>5.</b> <b>Annotator:</b> mở project, xem yêu cầu, sửa annotation, sau đó bấm Đã sửa · Gửi lại QC. <b>Reviewer:</b> xác nhận hoặc yêu cầu sửa tiếp.</p><p><b>6.</b> Chỉ Annotator được click box để chỉnh Track ID, Class và tọa độ. Kéo box để di chuyển; kéo góc phải-dưới để thay kích thước; ＋ Box để vẽ box mới.</p>
 <p><b>6.</b> Quality Gate kiểm tra lại; xuất MOT / CVAT XML / JSON, hoặc tải báo cáo.</p>
 <p><b>Lưu ý:</b> Track ID trong CVAT dạng ảnh thường không tồn tại; hệ thống không suy luận ID thật từ thứ tự box. Gap do che khuất và ID Switch chỉ là cảnh báo cần reviewer kiểm tra.</p>`,[{label:'Đóng',onClick:closeDialog}])}
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
 el('currentProject').textContent=S.projectName||'Chưa chọn project';
 el('btnSaveProject').disabled=!S.projectId||!canEdit();
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
  if(el('cvatStatusSummary'))el('cvatStatusSummary').innerHTML=`Tài khoản: <b>${htmlEscape(res.username)}</b> · Vai trò: <b>${htmlEscape(res.role.toUpperCase())}</b> (${res.filtered_count}/${res.all_count} jobs)`;
  
  const userRole = role();
  let tabsConfig = [];
  if (userRole === 'annotator') {
   tabsConfig = [
    { key: 'my_todo', label: '📌 Việc cần làm', filter: j => j.stage === 'annotation' && j.state !== 'rejected', empty: 'Hiện không có job nào đang chờ bạn gán nhãn.' },
    { key: 'my_rejected', label: '⚠️ Bị trả về cần sửa', filter: j => j.stage === 'annotation' && j.state === 'rejected', empty: 'Tuyệt vời! Bạn không có job nào bị trả về cần sửa lỗi.' },
    { key: 'all', label: 'Tất cả việc của tôi', filter: () => true, empty: 'Chưa có job nào được phân công cho bạn.' },
   ];
  } else if (userRole === 'reviewer') {
   tabsConfig = [
    { key: 'validation', label: '🔍 Cần Review QC', filter: j => j.stage === 'validation', empty: 'Hiện không có job nào đang ở giai đoạn Validation cần QC.' },
    { key: 'rejected', label: '↩ Đã trả lại Annotator', filter: j => j.state === 'rejected', empty: 'Chưa có job nào bị trả lại cho annotator.' },
    { key: 'acceptance', label: '✓ Đã nghiệm thu', filter: j => j.stage === 'acceptance', empty: 'Chưa có job nào hoàn thành nghiệm thu.' },
    { key: 'all', label: 'Tất cả jobs', filter: () => true, empty: 'Không có job nào trên hệ thống.' },
   ];
  } else {
   tabsConfig = [
    { key: 'all', label: 'Tất cả jobs', filter: () => true, empty: 'Không có job nào trên hệ thống.' },
    { key: 'validation', label: '🔍 Validation (Cần QC)', filter: j => j.stage === 'validation', empty: 'Không có job nào ở giai đoạn Validation.' },
    { key: 'annotation', label: '✏️ Annotation (Annotator)', filter: j => j.stage === 'annotation', empty: 'Không có job nào ở giai đoạn Annotation.' },
    { key: 'acceptance', label: '✓ Acceptance (Nghiệm thu)', filter: j => j.stage === 'acceptance', empty: 'Chưa có job nào hoàn thành nghiệm thu.' },
   ];
  }

  if (!S.cvatActiveTab || !tabsConfig.some(t => t.key === S.cvatActiveTab)) {
   S.cvatActiveTab = tabsConfig[0].key;
  }

  const tabsEl = el('cvatRoleTabs');
  if (tabsEl) {
   tabsEl.innerHTML = '';
   for (const t of tabsConfig) {
    const count = (res.jobs || []).filter(t.filter).length;
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
  let list = (res.jobs || []).filter(activeTabConfig.filter);

  if(!list.length){
   dst.innerHTML=`<div class="side-empty">${activeTabConfig.empty}<br><small>Nhấn "Đồng bộ từ CVAT" để cập nhật dữ liệu mới nhất.</small></div>`;
   return;
  }

  dst.innerHTML='';
  for(const j of list){
   const card=document.createElement('div');card.className='cvat-card';
   const stageClass=j.stage||'annotation';
   const isAnnotator=userRole==='annotator',isReviewer=userRole==='reviewer',isAdmin=userRole==='admin';
   let actionsHtml=`<a href="${j.cvat_url}" target="_blank" class="outline small" title="Mở trực tiếp trên giao diện CVAT">🔗 CVAT</a>`;
   if(isAnnotator){
    if(j.stage==='annotation')actionsHtml+=`<button class="primary small btn-submit-validation" data-id="${j.id}">🚀 Gửi Reviewer (Nộp QC)</button>`;
   }
   if(isReviewer||isAdmin){
    if(j.stage==='validation'){
     actionsHtml+=`<button class="primary small btn-open-qc" data-id="${j.id}">🔍 Quét QC & Mở Studio</button>`;
    }else if(j.state==='rejected'&&j.stage==='annotation'){
     const targetAnnotator=j.annotator_name||j.assignee||'Annotator';
     actionsHtml+=`<span class="pill" style="margin-left:auto;background:#fef3c7;color:#b45309;font-weight:600;font-size:11px;">⏳ Chờ ${htmlEscape(targetAnnotator)} sửa & nộp lại</span>`;
    }else if(j.stage==='acceptance'){
     actionsHtml+=`<span class="pill" style="margin-left:auto;background:#dcfce7;color:#15803d;font-weight:600;font-size:11px;">✓ Đã nghiệm thu</span>`;
    }else{
     actionsHtml+=`<span class="pill" style="margin-left:auto;color:#64748b;font-size:11px;">✏️ Annotator đang làm nhãn</span>`;
    }
   }
   if(isAdmin)actionsHtml+=`<button class="ghost small btn-assign-job" data-id="${j.id}">👤 Gán Annotator</button>`;

   let statusBadge = `${j.stage} · ${j.state}`;
   let assigneeDisplay = `Assignee: <b>${htmlEscape(j.assignee || 'Chưa gán')}</b>`;
   if (j.state === 'rejected' && j.stage === 'annotation') {
    statusBadge = `⚠️ ĐÃ TRẢ LẠI ANNOTATOR`;
    const targetAnnotator = j.annotator_name || j.assignee || 'Annotator';
    assigneeDisplay = `Đã trả lại cho: <b>${htmlEscape(targetAnnotator)}</b> <small style="color:#b45309;">(Chờ sửa)</small>`;
   } else if (j.stage === 'validation') {
    statusBadge = `🔍 CẦN REVIEW QC`;
    if (j.annotator_name) {
     assigneeDisplay = `Reviewer: <b>${htmlEscape(j.reviewer_name || j.assignee || 'Reviewer')}</b> · Annotator: <b>${htmlEscape(j.annotator_name)}</b>`;
    }
   } else if (j.stage === 'acceptance') {
    statusBadge = `✓ ĐÃ NGHIỆM THU`;
   }

   card.innerHTML=`<div class="cvat-card-head"><div><div class="cvat-card-title">Job #${j.id} <span style="font-size:12px;color:#8592a6;font-weight:normal;">(Task #${j.task_id})</span></div><div class="cvat-card-sub"><b>${htmlEscape(j.task_name)}</b> · <i>${htmlEscape(j.project_name)}</i></div></div><span class="stage-badge ${stageClass}">${statusBadge}</span></div><div class="cvat-card-body"><div class="cvat-card-meta">${assigneeDisplay}</div><div class="cvat-card-meta">Frames: <b>${j.frame_count}</b> (${j.start_frame}–${j.stop_frame})</div></div><div class="cvat-card-actions">${actionsHtml}</div>`;
   const btnSubmit=card.querySelector('.btn-submit-validation');if(btnSubmit)btnSubmit.onclick=()=>submitCvatJobForReview(j.id, j);
   const btnOpen=card.querySelector('.btn-open-qc');if(btnOpen)btnOpen.onclick=()=>openCvatJobInStudio(j.id,j);
   const btnAssign=card.querySelector('.btn-assign-job');if(btnAssign)btnAssign.onclick=()=>assignCvatJobDialog(j.id,j);
   dst.appendChild(card);
  }
 }catch(e){dst.innerHTML=`<div class="side-empty" style="color:#d93442;">Lỗi kết nối CVAT: ${htmlEscape(e.message)}<br><small>Kiểm tra cấu hình .env (CVAT_HOST, CVAT_USER, CVAT_PASS)</small></div>`;}
}
async function submitCvatJobForReview(job_id, jobMeta){
 try{
  setBusy(true);
  const usersRes = await api('cvat/users').catch(() => ({users:[]}));
  const reviewers = (usersRes.users || []).filter(u => u.role === 'reviewer' || u.role === 'admin' || u.is_staff);
  const defaultRev = reviewers.find(u => u.role === 'reviewer') || reviewers[0];
  const optionsHtml = reviewers.length 
    ? reviewers.map(u => `<option value="${u.id}" ${defaultRev && defaultRev.id === u.id ? 'selected' : ''}>${htmlEscape(u.username)} (${u.role === 'reviewer' ? 'Reviewer' : 'Admin / Staff'})</option>`).join('')
    : '<option value="">-- Mặc định --</option>';

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
  setBusy(true);toast(`Đang tải annotation Job #${job_id} từ CVAT và chạy quét lỗi idcheck...`);
  const fps=Number(el('cvatFpsInput')?.value||5.0),config=el('cvatConfigSelect')?.value||'config_project_3.yaml';
  const r=await api('cvat/job/open',{job_id,fps,config});
  clearMedia();S.projectId=null;S.projectName=null;
  S.cvatJob={id:r.job_id,task_id:r.task_id,stage:r.stage,state:r.state,assignee:r.assignee,cvat_url:r.cvat_url,total_frames:r.total_frames,frame_base:r.frame_base,width:r.width,height:r.height,fps:r.fps};
  S.boxes=r.boxes||[];S.meta={format:'cvat',frame_base:r.frame_base??0,mode:'video',width:r.width,height:r.height,total_frames:r.total_frames,fps:r.fps,labels:r.labels||['Car'],task_name:`CVAT Job #${r.job_id}`};
  S.session=null;S.sourceName=`CVAT Job #${r.job_id} (Task #${r.task_id})`;S.undo=[];S.redo=[];S.selectedUid=null;S.tasks={};resetDecisions();
  S.issues=r.issues||[];S.lastScan={count:r.count,counts:r.counts,stats:r.stats,notes:[`Nạp trực tiếp từ CVAT API (Job #${r.job_id}).`,`Đã quét ${r.count} issues bằng idcheck core rules (${r.fps} FPS).`]};
  if(r.width)el('imageWidth').value=r.width;if(r.height)el('imageHeight').value=r.height;el('fps').value=r.fps;el('datasetName').textContent=S.sourceName;
  indexBoxes();undoButtons();
  const startF=r.frame_base||0,totalF=r.total_frames||(r.boxes.length?Math.max(...r.boxes.map(b=>b.frame_id))-startF+1:1);
  S.imageURLs=new Map();S.images=new Map();
  for(let f=startF;f<startF+totalF;f++)S.imageURLs.set(f,`/api/cvat/frame/${r.job_id}/${f}`);
  S.mediaMode='cvat-stream';el('mediaInfo').textContent=`⚡ CVAT Live Stream · Job #${r.job_id} · ${totalF} frames trực tiếp từ server`;
  el('cvatJobBanner').hidden=false;
  el('cvatBannerTitle').textContent=`CVAT Job #${r.job_id} · Task #${r.task_id}`;
  el('cvatBannerSubtitle').textContent=`Stage: ${(r.stage||'').toUpperCase()} (${r.state}) · Assignee: ${r.assignee||'Chưa gán'} · ${r.count} issues phát hiện`;
  el('btnCvatExternal').href=r.cvat_url;
  if(el('currentProject')) el('currentProject').textContent=`CVAT Job #${r.job_id} (Task #${r.task_id})`;
  if(el('datasetName')) el('datasetName').textContent=`⚡ Task #${r.task_id} · Job #${r.job_id}`;
  const isValidation = r.stage === 'validation';
  if(el('btnCvatAccept')) el('btnCvatAccept').hidden = !isValidation;
  if(el('btnCvatReject')) el('btnCvatReject').hidden = !isValidation;
  S.frame=startF;refreshAll();showPage('workspace');
  toast(`Đã nạp Job #${r.job_id}: ${r.boxes.length} boxes, ${r.count} issues phát hiện.`);
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
 openDialog('Nghiệm thu CVAT Job',
  `<p>Bạn có chắc chắn muốn nghiệm thu <b>CVAT Job #${S.cvatJob.id}</b>?</p><p>Trạng thái trên CVAT sẽ chuyển thành <b>stage=acceptance, state=completed</b>.</p>`,
  [{label:'Hủy',onClick:closeDialog},{label:'✓ Nghiệm thu hoàn tất',className:'primary',onClick:async()=>{
    closeDialog();try{setBusy(true);const res=await api('cvat/job/accept',{job_id:S.cvatJob.id});
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
function wire(){
 el('navCvatJobs').onclick=()=>{showPage('cvatJobs');renderCvatJobs()};
 el('btnRefreshCvat').onclick=renderCvatJobs;
 if(el('cvatStageFilter')) el('cvatStageFilter').onchange=renderCvatJobs;
 el('btnCvatReject').onclick=rejectCvatJobDialog;
 el('btnCvatAccept').onclick=acceptCvatJobDialog;
 el('btnAdminUsers').onclick=openUserDialog;el('btnLogout').onclick=async()=>{
 await api('auth/logout').catch(()=>{});location.reload()};
 el('btnSaveSession').onclick=()=>{const data={project_type:'t021_project',version:2,source_name:S.sourceName,meta:readMeta(),boxes:S.boxes,decisions:S.decisions,saved_at:new Date().toISOString()};download('track_qa_session.t021.json',JSON.stringify(data,null,2),'application/json')};
 el('btnCreateProject').onclick=askCreateProject;el('btnSaveProject').onclick=()=>saveProject(false);
 el('btnProjectPageCreate').onclick=askCreateProject;el('navProjects').onclick=async()=>{showPage('projects');await renderProjects()};
 el('btnDemo').onclick=loadDemo;el('btnHelp').onclick=help;el('btnScan').onclick=scan;el('btnExport').onclick=()=>{if(canEdit())exportDialog()};
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
 el('issueFilter').onchange=renderIssues;el('btnQuality').onclick=async()=>{if(!S.boxes.length)return;try{const r=await api('quality',{boxes:S.boxes,...readMeta(),project_id:S.projectId});openDialog('Quality Gate',
 `<p><b>${r.pass_gate?'PASS':'CHƯA PASS'}</b></p><p>${r.structural_blockers.length} lỗi cấu trúc chặn xuất sạch. ${r.unresolved_high.length} cảnh báo HIGH chưa xử lý; ${(r.pending_handoffs||[]).length} yêu cầu chưa kết thúc QC.</p><p>${htmlEscape(r.disclaimer)}</p>`,[{label:'Đóng',onClick:closeDialog}])}catch(e){toast(e.message,true)}};
 el('navWorkspace').onclick=()=>showPage('workspace');el('navReport').onclick=()=>showPage('report');el('navAudit').onclick=()=>showPage('audit');
 el('btnSaveReport').onclick=()=>download('t021_qa_report.json',JSON.stringify(reportObj(),null,2),'application/json');
 el('btnRefreshAudit').onclick=renderAudit;
 el('btnBenchmark').onclick=()=>openDialog('Đánh giá với Ground Truth',`<p>Tải JSON gồm hai mảng <code>predicted</code> và <code>ground_truth</code>, mỗi sự kiện có type, frame, track_id. Matching 1–1, tolerance ±2 frame.</p><label>JSON Ground Truth & Predictions<input id="benchmarkFile" type="file" accept=".json"></label>`,
 [{label:'Hủy',onClick:closeDialog},{label:'Đánh giá',className:'primary',onClick:async()=>{const f=el('benchmarkFile').files[0];if(!f)return toast('Chọn file JSON benchmark',true);
 try{const data=JSON.parse(await f.text()),r=await api('evaluate',data);closeDialog();openDialog('Kết quả Benchmark',`<p>TP: ${r.tp} · FP: ${r.fp} · FN: ${r.fn}</p><p>Precision: <b>${(100*r.precision).toFixed(1)}%</b> · Recall: <b>${(100*r.recall).toFixed(1)}%</b> · F1: <b>${(100*r.f1).toFixed(1)}%</b></p><p>Mục tiêu: ${r.target_met?'Đạt':'Chưa đạt'}. ${htmlEscape(r.note)}</p>`,[{label:'Đóng',onClick:closeDialog}])}catch(e){toast(e.message,true)}}}]);
 el('dialogClose').onclick=closeDialog;el('dialog').onclick=e=>{if(e.target===el('dialog'))closeDialog()};
 window.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||!el('dialog').hidden)return;
 if(e.key==='ArrowRight')setFrame(S.frame+1);else if(e.key==='ArrowLeft')setFrame(S.frame-1);
 else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();el('btnUndo').click()}});
 const c=el('viewer');c.addEventListener('pointerdown',e=>{if(!S.boxes.length&&!S.isCreate)return;const p=toWorld(e);if(!canEdit()){const selection=hit(p);selectBox(selection?.box?.uid||null);return}c.setPointerCapture(e.pointerId);
 if(S.isCreate){S.drag={type:'create',start:p,preview:[p.x,p.y,0,0]};return}
 const v=hit(p);if(!v){selectBox(null);return}
 selectBox(v.box.uid);S.drag={type:v.type,start:p,orig:{...v.box},preview:null};});
 c.addEventListener('pointermove',e=>{if(!S.drag)return;const p=toWorld(e),d=S.drag;
 if(d.type==='create'){const x=Math.min(d.start.x,p.x),y=Math.min(d.start.y,p.y);d.preview=[x,y,Math.abs(p.x-d.start.x),Math.abs(p.y-d.start.y)];draw();return}
 const b=S.boxes.find(v=>v.uid===d.orig.uid);if(!b)return;
 if(d.type==='move'){b.x=Math.max(0,d.orig.x+p.x-d.start.x);b.y=Math.max(0,d.orig.y+p.y-d.start.y)}
 else if(d.type==='resize'){b.w=Math.max(2,d.orig.w+p.x-d.start.x);b.h=Math.max(2,d.orig.h+p.y-d.start.y)}
 draw();});
 c.addEventListener('pointerup',async e=>{if(!S.drag)return;const d=S.drag;S.drag=null;
 if(d.type==='create'){draw();const [x,y,w,h]=d.preview;if(w<4||h<4)return;
 openDialog('Tạo Bounding Box',`<label>Track ID<input id="newTrack" type="number" value="${S.boxes.reduce((m,b)=>Math.max(m,b.track_id),0)+1}"></label><label>Class<input id="newClass" value="Car"></label>`,
 [{label:'Hủy',onClick:closeDialog},{label:'Tạo box',className:'primary',onClick:()=>{const tid=Number(el('newTrack').value),cls=el('newClass').value;closeDialog();doFix('create_box',{frame_id:S.frame,track_id:tid,x,y,w,h,class_name:cls})}}]);return}
 const box=S.boxes.find(v=>v.uid===d.orig.uid);if(!box)return;
 const moved=JSON.stringify([box.x,box.y,box.w,box.h])!==JSON.stringify([d.orig.x,d.orig.y,d.orig.w,d.orig.h]);
 if(!moved)return;
 const updated={uid:d.orig.uid,x:Math.round(box.x*100)/100,y:Math.round(box.y*100)/100,w:Math.round(box.w*100)/100,h:Math.round(box.h*100)/100};
 Object.assign(box,{x:d.orig.x,y:d.orig.y,w:d.orig.w,h:d.orig.h});
 await doFix('update_box',updated,'Đã cập nhật box sau khi kéo');});
 c.addEventListener('pointercancel',()=>{if(S.drag?.orig){const b=S.boxes.find(x=>x.uid===S.drag.orig.uid);if(b)Object.assign(b,S.drag.orig)}S.drag=null;draw()});
}
function role(){return S.user?.role||''}
function canEdit(){return role()==='annotator'}
function updateRoleUI(){
 const edit=canEdit(),admin=role()==='admin',isRev=role()==='reviewer',isAnno=role()==='annotator';
 document.body.classList.toggle('reviewer-mode',!edit);
 if(el('importRoleNotice')) el('importRoleNotice').hidden=role()!=='reviewer';
 if(el('operatorTag')) el('operatorTag').textContent=`${S.user?.username||'—'} · ${role().toUpperCase()}`;
 if(el('btnLogout')) el('btnLogout').hidden=false;
 if(el('btnAdminUsers')) el('btnAdminUsers').hidden=!admin;

 // Phân quyền thanh điều hướng Sidebar theo vai trò:
 // Annotator làm việc trên CVAT nên trên Web App chỉ cần tab CVAT Jobs:
 if(el('navWorkspace')) el('navWorkspace').hidden = isAnno;
 if(el('navProjects')) el('navProjects').hidden = isAnno || isRev;
 if(el('navReport')) el('navReport').hidden = isAnno;
 if(el('navAudit')) el('navAudit').hidden = isAnno;

 // Ẩn các nút offline/demo độc lập tránh gây rối giao diện CVAT pipeline:
 for(const id of ['btnCreateProject','btnSaveProject','btnCreateMode','btnSaveBox','btnDeleteBox',
 'btnStitch','btnSwap','btnUndo','btnRedo','btnExport','btnSaveSession','btnDemo']){
  const button=el(id);if(button)button.hidden=true;
 }
 for(const id of ['fieldTrack','fieldClass','fieldX','fieldY','fieldW','fieldH']) {
  if(el(id)) el(id).disabled=!edit;
 }
 if(el('btnProjectPageCreate')) el('btnProjectPageCreate').hidden=true;
 setBusy(false);undoButtons();selectBox(S.selectedUid);renderIssues();
}
async function initAuth(){
 const gate=el('authGate'),form=el('authForm');
 try{
  const me=await api('auth/me').catch(()=>null);
  if(me?.user){S.user=me.user;gate.hidden=true;updateRoleUI();showPage('cvatJobs');renderCvatJobs();return}
 }catch(e){el('authMessage').textContent='Không kết nối được backend: '+e.message}
 el('authDescription').textContent='Đăng nhập bằng tài khoản CVAT của bạn (cvat.trantuli.id.vn); tự động đồng bộ vai trò & danh sách job.';
 el('authSubmit').textContent='Đăng nhập qua CVAT';
 form.onsubmit=async ev=>{
  ev.preventDefault();const username=el('authUsername').value.trim(),password=el('authPassword').value;
  try{
   const r=await api('auth/login',{username,password});S.user=r.user;gate.hidden=true;
   el('authMessage').textContent='';updateRoleUI();showPage('cvatJobs');renderCvatJobs();toast('Đã đăng nhập qua CVAT: '+username+' ('+r.user.role+')');
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
wire();refreshAll();updateProjectTitle();initAuth();
