from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread
import atexit
import os
import shutil
import tempfile
import time
import uuid
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from processor import process_video

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024**3
app.config['TRUSTED_HOSTS'] = ['127.0.0.1', 'localhost']
ROOT = Path(tempfile.mkdtemp(prefix='ppt-web-'))
jobs = {}
lock = Lock()
pool = ThreadPoolExecutor(max_workers=1)
TTL = 3600
atexit.register(lambda: shutil.rmtree(ROOT, ignore_errors=True))


def update(job_id, **values):
    with lock:
        jobs[job_id].update(values)


def worker(job_id, source, result):
    try:
        update(job_id, status='processing', message='영상 분석 중')
        info = process_video(source, result, lambda **kw: update(job_id, **kw))
        update(job_id, status='done', progress=100, message='변환 완료', **info)
    except Exception as exc:
        app.logger.exception('Conversion failed')
        result.unlink(missing_ok=True)
        update(job_id, status='error', message=str(exc) or '처리에 실패했습니다.')
    finally:
        source.unlink(missing_ok=True)
        update(job_id, finished=time.time())


def cleanup():
    while True:
        time.sleep(60)
        with lock:
            expired = [key for key, job in jobs.items() if job.get('finished') and time.time()-job['finished'] > TTL]
            for key in expired:
                shutil.rmtree(ROOT/key, ignore_errors=True)
                del jobs[key]


Thread(target=cleanup, daemon=True).start()


@app.get('/')
def index():
    return render_template('index.html')


@app.post('/api/jobs')
def upload():
    # Reject cross-origin browser submissions to the local service.
    if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
        return jsonify(error='허용되지 않은 요청입니다.'), 403
    file = request.files.get('video')
    if not file or not file.filename:
        return jsonify(error='영상 파일을 선택하세요.'), 400
    name = file.filename.replace('\\', '/').split('/')[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in {'.mp4', '.mov', '.mkv'}:
        return jsonify(error='MP4, MOV, MKV만 지원합니다.'), 400
    with lock:
        if sum(j['status'] in ('queued', 'processing') for j in jobs.values()) >= 3:
            return jsonify(error='대기 중인 작업이 많습니다. 완료 후 다시 시도하세요.'), 429
        job_id = uuid.uuid4().hex
        jobs[job_id] = dict(status='queued', progress=0, slides=0, message='처리 대기 중', filename=Path(name).stem+'.pdf')
    directory = ROOT/job_id
    directory.mkdir()
    source, result = directory/('input'+suffix), directory/'result.pdf'
    try:
        file.save(source)
        pool.submit(worker, job_id, source, result)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        with lock:
            jobs.pop(job_id, None)
        return jsonify(error='업로드를 저장하지 못했습니다.'), 500
    return jsonify(id=job_id), 202


@app.get('/api/jobs/<job_id>')
def status(job_id):
    with lock:
        job = jobs.get(job_id)
        return (jsonify(dict(job)), 200) if job else (jsonify(error='작업이 없거나 만료되었습니다.'), 404)


@app.get('/api/jobs/<job_id>/download')
def download(job_id):
    with lock:
        job = jobs.get(job_id)
        if not job or job['status'] != 'done':
            return jsonify(error='다운로드할 PDF가 없거나 만료되었습니다.'), 404
        # Extend retention for repeat downloads; never delete mid-response.
        job['finished'] = time.time()
        return send_file(ROOT/job_id/'result.pdf', as_attachment=True, download_name=job['filename'], mimetype='application/pdf')


@app.errorhandler(RequestEntityTooLarge)
def too_large(_):
    return jsonify(error='영상은 최대 8GB까지 업로드할 수 있습니다.'), 413


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', '5050')), debug=False, threaded=True)
