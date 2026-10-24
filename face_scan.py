# -*- coding: utf-8 -*-
"""在图库中扫描动漫人脸，裁剪出清晰完整的脸部候选。"""
import cv2
import json
import sys
import numpy as np
from pathlib import Path


def imread_u(path):
    """cv2.imread 不支持中文路径，改用 imdecode"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def imwrite_u(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(str(path))
    return ok

PICS = [Path(r"C:\Users\赵朔\Pictures")]
OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)
CASCADE = str(OUT / "lbpcascade_animeface.xml")

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# OpenCV 在中文路径下无法加载模型，先复制到纯英文路径
import shutil
MODEL_ASCII = r"C:\Windows\Temp\dut_animeface.xml"
shutil.copy(str(OUT / "lbpcascade_animeface.xml"), MODEL_ASCII)
cascade = cv2.CascadeClassifier(MODEL_ASCII)
if cascade.empty():
    sys.exit("模型加载失败")

cands = []
files = []
for base in PICS:
    for p in base.rglob("*"):
        if p.suffix.lower() in EXTS and p.is_file():
            files.append(p)
print(f"扫描 {len(files)} 张图片...")

for p in files:
    try:
        img = imread_u(p)
    except Exception:
        continue
    if img is None:
        continue
    h, w = img.shape[:2]
    if max(h, w) < 500:
        continue
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5,
                                     minSize=(int(max(h, w) * 0.08),) * 2)
    if len(faces) == 0:
        continue
    faces = sorted(faces, key=lambda f: f[2], reverse=True)
    x, y, fw, fh = faces[0]
    # 清晰度：脸部宽度至少占图宽 9%，且 ≥150px
    if fw < 150 or fw / w < 0.09:
        continue
    dominance = fw / faces[1][2] if len(faces) > 1 else 99
    score = fw * min(dominance, 3) * (1.0 if fw / w <= 0.6 else 0.5)
    side = int(fw * 1.9)
    cx, cy = x + fw // 2, y + fh // 2
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - int(side * 0.56))
    x2 = min(w, x1 + side)
    y2 = min(h, y1 + side)
    crop = img[y1:y2, x1:x2]
    if crop.shape[0] < 200 or crop.shape[1] < 200:
        continue
    cands.append({"file": str(p), "face": [int(x), int(y), int(fw), int(fh)],
                  "img": [w, h], "score": round(score, 1),
                  "faces": int(len(faces))})

cands.sort(key=lambda c: -c["score"])
# 保存前 5 个候选裁剪图
for i, c in enumerate(cands[:5]):
    img = imread_u(c["file"])
    x, y, fw, fh = c["face"]
    side = int(fw * 1.9)
    cx, cy = x + fw // 2, y + fh // 2
    x1 = max(0, cx - side // 2)
    y1 = max(0, cy - int(side * 0.56))
    crop = img[y1:min(c["img"][0], y1 + side), x1:min(c["img"][1] - 0, x1 + side)]
    imwrite_u(OUT / f"face_candidate_{i}.png", crop)

(OUT / "face_candidates.json").write_text(
    json.dumps(cands[:10], ensure_ascii=False, indent=1), encoding="utf-8")
for c in cands[:10]:
    print(f"score={c['score']:>7} 脸={c['face'][2]}px 人数={c['faces']}  {Path(c['file']).name}")
if not cands:
    print("未找到合格候选")
