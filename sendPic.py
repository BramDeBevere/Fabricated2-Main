import os
import io
import time
import tempfile
import subprocess
import shutil
import requests
import numpy as np
from datetime import datetime, timezone
from PIL import Image

API_URL = os.environ.get('API_URL', 'http://10.10.252.185:8000/detections')
CAPTURE_INTERVAL = 0.5  # seconds between frames
YOLO_MODEL_PATH = os.environ.get('YOLO_MODEL', 'yolov8n.pt')
YOLO_CONFIDENCE = float(os.environ.get('YOLO_CONF', '0.25'))

_model = None


def load_model():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(YOLO_MODEL_PATH)
        print(f"Loaded YOLO model: {YOLO_MODEL_PATH}")
    return _model


def _camera_cmd():
    for cmd in ("rpicam-jpeg", "libcamera-jpeg"):
        if shutil.which(cmd):
            return cmd
    return None


def capture_jpeg_libcamera():
    cmd = _camera_cmd()
    if cmd is None:
        raise RuntimeError("Neither rpicam-jpeg nor libcamera-jpeg found in PATH.")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
    try:
        result = subprocess.run(
            [cmd, "-n", "-t", "500", "-o", temp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"{cmd} failed")
        with open(temp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def detect_people(jpeg_bytes):
    model = load_model()
    image = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    img_array = np.array(image)
    width, height = image.size  # PIL returns (width, height)

    results = model.track(
        source=img_array,
        classes=[0],  # COCO class 0 = person
        conf=YOLO_CONFIDENCE,
        persist=True,
        verbose=False,
    )

    people = []
    if results and results[0].boxes is not None:
        for idx, box in enumerate(results[0].boxes):
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            conf = float(box.conf[0].item()) if box.conf is not None else None
            track_id = int(box.id[0]) if box.id is not None else idx
            people.append({
                'id': track_id,
                'confidence': round(conf, 4) if conf is not None else None,
                'bbox': {
                    'x1': round(x1, 2), 'y1': round(y1, 2),
                    'x2': round(x2, 2), 'y2': round(y2, 2),
                },
                'center': {'x': round(cx, 2), 'y': round(cy, 2)},
                'center_normalized': {
                    'x': round(cx / width, 4) if width else 0.0,
                    'y': round(cy / height, 4) if height else 0.0,
                },
            })

    return people, width, height


def post_detections(people, width, height):
    payload = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'frame_width': width,
        'frame_height': height,
        'person_count': len(people),
        'people': people,
    }
    try:
        requests.post(API_URL, json=payload, timeout=2)
    except Exception as e:
        print(f"POST failed: {e}")


def run_with_libcamera():
    load_model()
    print(f"Starting — libcamera-jpeg — target interval: {CAPTURE_INTERVAL}s")
    while True:
        t0 = time.time()
        try:
            jpeg = capture_jpeg_libcamera()
            people, w, h = detect_people(jpeg)
            post_detections(people, w, h)
            print(f"{len(people)} person(s) | {time.time() - t0:.2f}s")
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(max(0.0, CAPTURE_INTERVAL - (time.time() - t0)))


def run_with_picamera2():
    from picamera2 import Picamera2

    camera_info = Picamera2.global_camera_info()
    if not camera_info:
        raise RuntimeError("No camera detected. Check cable and raspi-config.")

    load_model()
    camera = Picamera2()
    camera.configure(camera.create_still_configuration({"format": "RGB888"}))
    camera.start()
    time.sleep(1)
    print(f"Starting — picamera2 — target interval: {CAPTURE_INTERVAL}s")

    try:
        while True:
            t0 = time.time()
            try:
                img_array = camera.capture_array()  # (H, W, 3) RGB
                height, width = img_array.shape[:2]
                people, w, h = detect_people_from_array(img_array, width, height)
                post_detections(people, w, h)
                print(f"{len(people)} person(s) | {time.time() - t0:.2f}s")
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(max(0.0, CAPTURE_INTERVAL - (time.time() - t0)))
    finally:
        camera.stop()
        camera.close()


def detect_people_from_array(img_array, width, height):
    model = load_model()
    results = model.track(
        source=img_array,
        classes=[0],
        conf=YOLO_CONFIDENCE,
        persist=True,
        verbose=False,
    )

    people = []
    if results and results[0].boxes is not None:
        for idx, box in enumerate(results[0].boxes):
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            conf = float(box.conf[0].item()) if box.conf is not None else None
            track_id = int(box.id[0]) if box.id is not None else idx
            people.append({
                'id': track_id,
                'confidence': round(conf, 4) if conf is not None else None,
                'bbox': {
                    'x1': round(x1, 2), 'y1': round(y1, 2),
                    'x2': round(x2, 2), 'y2': round(y2, 2),
                },
                'center': {'x': round(cx, 2), 'y': round(cy, 2)},
                'center_normalized': {
                    'x': round(cx / width, 4) if width else 0.0,
                    'y': round(cy / height, 4) if height else 0.0,
                },
            })

    return people, width, height


if __name__ == "__main__":
    if _camera_cmd():
        run_with_libcamera()
    else:
        run_with_picamera2()
