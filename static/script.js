const $ = id => document.getElementById(id);
const input = $('video'), drop = $('drop');
let busy = false;
input.onchange = () => { $('filename').textContent = input.files[0]?.name || '선택한 영상 없음'; };
for (const event of ['dragenter','dragover']) drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('drag');});
for (const event of ['dragleave','drop']) drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('drag');});
drop.addEventListener('drop',e=>{if(!busy && e.dataTransfer.files.length){input.files=e.dataTransfer.files;input.onchange();}});
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
$('form').onsubmit = async e => {
  e.preventDefault(); if(busy || !input.files.length)return;
  busy=true; $('start').disabled=true; input.disabled=true; $('download').hidden=true; $('count').textContent='';
  try {
    const data=new FormData();data.append('video',input.files[0]);
    const id=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/jobs');xhr.responseType='json';
      xhr.upload.onprogress=e=>{if(e.lengthComputable){$('progress').value=e.loaded/e.total*100;$('status').textContent=`업로드 중 ${Math.round(e.loaded/e.total*100)}%`;}};
      xhr.onload=()=>xhr.status===202?resolve(xhr.response.id):reject(Error(xhr.response?.error || '업로드에 실패했습니다.'));
      xhr.onerror=()=>reject(Error('서버에 연결할 수 없습니다. 실행 창을 확인하세요.'));
      xhr.send(data);
    });
    $('progress').value=0;
    while(true){
      const response=await fetch(`/api/jobs/${id}`);const job=await response.json();
      if(!response.ok)throw Error(job.error);
      $('status').textContent=job.message;$('progress').value=job.progress;$('count').textContent=`감지된 슬라이드 ${job.slides}장`;
      if(job.status==='error')throw Error(job.message);
      if(job.status==='done'){
        $('count').textContent=`총 ${job.pages}장 · 번호 없는 페이지 ${job.unnumbered}장`;
        $('download').href=`/api/jobs/${id}/download`;$('download').hidden=false;$('download').click();break;
      }
      await pause(1200);
    }
  } catch(error){$('status').textContent=error.message;}
  finally{busy=false;$('start').disabled=false;input.disabled=false;}
};
