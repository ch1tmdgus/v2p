"""White-slide extraction. All thresholds are heuristics; see README."""
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import cv2
import numpy as np
import pytesseract
import img2pdf
from skimage.metrics import structural_similarity as ssim

SAMPLE_INTERVAL = .20
CHANGE_THRESHOLD = .80
STABLE_THRESHOLD = .97
MIN_STABLE_FRAMES = 2
DUPLICATE_THRESHOLD = .91
MAX_PAGE_NUMBER = 300


def small_gray(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (640, max(8, round(h * 640 / w))))
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def similarity(a, b):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(ssim(a, b, data_range=255))


def is_white_slide(frame):
    h, w = frame.shape[:2]
    image = cv2.resize(frame, (640, max(8, round(h * 640 / w))))
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 100, 255]))
    h, w = mask.shape
    center = mask[int(h*.06):int(h*.94), int(w*.08):int(w*.92)]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return bool(np.mean(mask > 0) >= .42 and np.mean(center > 0) >= .50
                and np.mean(gray < 170) >= .006)


def content_score(frame):
    # Count dark AND saturated colored ink, excluding the outer border/footer.
    h, w = frame.shape[:2]
    image = cv2.resize(frame, (640, max(8, round(h*640/w))))
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 2] < 205) | (hsv[:, :, 1] > 80)
    return int(np.count_nonzero(mask[5:-5, 5:-5]))


def detect_page_number(frame):
    h, w = frame.shape[:2]
    crop = frame[int(h*.84):h, int(w*.84):w]
    crop = cv2.resize(crop, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    _, binary = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    votes = {}
    for image in (gray, binary, cv2.bitwise_not(binary)):
        for psm in (6, 7, 11):
            data = pytesseract.image_to_data(image, config=f'--psm {psm} -c tessedit_char_whitelist=0123456789',
                                            output_type=pytesseract.Output.DICT, timeout=10)
            seen = set()
            for i, text in enumerate(data['text']):
                text = text.strip()
                confidence = float(data['conf'][i])
                if not text.isdecimal() or confidence < 25:
                    continue
                number = int(text)
                if not 1 <= number <= MAX_PAGE_NUMBER or number in seen:
                    continue
                seen.add(number)
                count, best = votes.get(number, (0, 0))
                votes[number] = (count + 1, max(best, confidence))
    return max(votes, key=lambda n: votes[n]) if votes else None


def process_video(video_path, output_pdf, progress=lambda **kw: None):
    pytesseract.get_tesseract_version()  # Fail clearly if the executable is missing.
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError('영상을 열 수 없습니다. 지원되는 코덱의 MP4/MOV/MKV인지 확인하세요.')
    slides = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('영상의 FPS를 읽을 수 없습니다.')
        step = max(1, round(fps * SAMPLE_INTERVAL))
        with TemporaryDirectory(prefix='slides-', dir=Path(output_pdf).parent) as temp:
            def add(frame, timestamp):
                if frame is None or not is_white_slide(frame):
                    return
                number = detect_page_number(frame)
                gray, score = small_gray(frame), content_score(frame)
                match = next((s for s in slides if number is not None and s['number'] == number), None)
                if number is None:
                    candidates = [(similarity(gray, s['gray']), s) for s in slides if s['number'] is None]
                    if candidates:
                        value, candidate = max(candidates, key=lambda pair: pair[0])
                        if value >= DUPLICATE_THRESHOLD:
                            match = candidate
                if match is not None and score < match['score']:
                    return
                if match is None:
                    match = {'number': number, 'order': len(slides), 'path': str(Path(temp)/f'{len(slides):05d}.png')}
                    slides.append(match)
                if not cv2.imwrite(match['path'], frame):
                    raise OSError('슬라이드 이미지를 저장하지 못했습니다. 디스크 공간을 확인하세요.')
                match.update(gray=gray, score=score, timestamp=timestamp)
                progress(slides=len(slides))

            previous = None
            stable_frame = None
            stable_time = 0
            stable_count = 0
            index = 0
            while cap.grab():
                if index % step:
                    index += 1
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    raise ValueError('영상 프레임을 해독하지 못했습니다.')
                gray = small_gray(frame)
                timestamp = index / fps
                if previous is not None:
                    score = similarity(previous, gray)
                    if score < CHANGE_THRESHOLD:
                        # Commit the most recent stable sample BEFORE the transition.
                        add(stable_frame, stable_time)
                        stable_frame = None
                        stable_count = 0
                    elif score >= STABLE_THRESHOLD:
                        stable_count += 1
                        if stable_count >= MIN_STABLE_FRAMES:
                            stable_frame, stable_time = frame.copy(), timestamp
                    else:
                        stable_count = 0
                previous = gray
                if index % (step * 10) == 0:
                    progress(progress=min(94, int(index / total * 94)) if total > 0 else 0,
                             message=f'{timestamp:.1f}초 분석 중', slides=len(slides))
                index += 1
            add(stable_frame, stable_time)
            if not slides:
                raise ValueError('안정된 흰 배경 슬라이드를 찾지 못했습니다. README의 감지 기준을 확인하세요.')
            slides.sort(key=lambda s: (s['number'] is not None, s['number'] if s['number'] is not None else s['order']))
            progress(progress=97, message='PDF 생성 중', slides=len(slides))
            with open(output_pdf, 'wb') as stream:
                img2pdf.convert([s['path'] for s in slides], outputstream=stream)
            return {'pages': len(slides), 'unnumbered': sum(s['number'] is None for s in slides)}
    finally:
        cap.release()
