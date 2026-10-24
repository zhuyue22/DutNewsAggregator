# -*- coding: utf-8 -*-
"""v7 精细抠图：anime-seg 模型 + 紫色残留清理 + 孔洞填充 + 碎块清理"""
import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path

ROOT = Path(__file__).parent
sess = ort.InferenceSession(str(ROOT / "assets" / "isnetis.onnx"), providers=["CPUExecutionProvider"])
img = cv2.imdecode(np.fromfile(str(ROOT / "assets" / "char_crop.png"), dtype=np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]
pad = (h - w) // 2
sq = cv2.copyMakeBorder(img, 0, 0, pad, h - w - pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
res = cv2.resize(sq, (1024, 1024))
x = cv2.cvtColor(res, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
x = np.transpose(x, (2, 0, 1))[np.newaxis]
y = sess.run(["mask"], {"img": x})[0][0][0]
m = cv2.resize(y, (sq.shape[1], sq.shape[0]))[:, pad:pad + w]
alpha = np.clip((m - 0.35) / 0.3, 0, 1)
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
H, S_, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)

# 1) 紫色系背景残留（人物无紫色部件）
purpleish = (H >= 122) & (H <= 160) & (S_ >= 14) & (V <= 250)
remove = (purpleish & (alpha > 0.25)).astype(np.float32)
remove = cv2.GaussianBlur(remove, (0, 0), 1.0)
alpha = np.clip(alpha * (1 - remove), 0, 1)

# 2) 封闭孔洞（发隙间的窗格白/紫色）
fg = (alpha > 0.5).astype(np.uint8)
ff = fg.copy()
mm = np.zeros((h + 2, w + 2), np.uint8)
for sx in range(0, w, 30):
    for sy in (0, h - 1):
        cv2.floodFill(ff, mm, (sx, sy), 1, loDiff=0, upDiff=0, flags=4)
for sy in range(0, h, 30):
    for sx in (0, w - 1):
        cv2.floodFill(ff, mm, (sx, sy), 1, loDiff=0, upDiff=0, flags=4)
holes = (ff == 0)
n, lab = cv2.connectedComponents(holes.astype(np.uint8))
hole_mask = np.zeros((h, w), np.float32)
for i in range(1, n):
    region = (lab == i)
    area = int(region.sum())
    if area < 200 or area > 15000:
        continue
    meanS = float(S_[region].mean())
    meanV = float(V[region].mean())
    meanH = float(H[region].mean())
    if (122 <= meanH <= 165 and meanS >= 10) or (meanS < 35 and meanV > 175):
        hole_mask[region] = 1.0
hole_mask = cv2.GaussianBlur(hole_mask, (0, 0), 1.2)
alpha = np.clip(alpha * (1 - hole_mask), 0, 1)


# 3) 碎块清理：小面积独立前景块，颜色浅白/淡紫 → 移除
fg2 = (alpha > 0.5).astype(np.uint8)
n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(fg2, 8)
junk = np.zeros((h, w), np.float32)
for i in range(1, n2):
    area = stats2[i, cv2.CC_STAT_AREA]
    if area > 3000:
        continue
    region = (lab2 == i)
    meanH = float(H[region].mean())
    meanS = float(S_[region].mean())
    meanV = float(V[region].mean())
    brightish = (meanS < 45 and meanV > 165)
    purple = (122 <= meanH <= 165 and meanS >= 10)
    if brightish or purple:
        junk[region] = 1.0
print("junk removed:", int((junk > 0).sum()))
junk = cv2.GaussianBlur(junk, (0, 0), 1.2)
alpha = np.clip(alpha * (1 - junk), 0, 1)
alpha = cv2.GaussianBlur(alpha, (0, 0), 0.8)


# 4) 右缘/左缘窗格赘线：形态学开运算保留衣服主体块，只删细线
region = np.zeros((h, w), bool)
region[680:830, 858:w] = True
region[700:745, 0:70] = True
fgR = ((alpha > 0.5) & region).astype(np.uint8)
opened = cv2.morphologyEx(fgR, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
junk2 = (fgR - opened).astype(np.float32)
junk2 = cv2.GaussianBlur(junk2, (0, 0), 1.0)
print("surgical px:", int((junk2 > 0.5).sum()))
alpha = np.clip(alpha * (1 - junk2), 0, 1)


# 4.5) 区域边缘幽灵残影截断
region2 = np.zeros((h, w), bool)
region2[660:840, 850:w] = True
region2[690:755, 0:80] = True
sub = alpha[region2]
sub[sub < 0.4] = 0
alpha[region2] = sub
alpha = cv2.GaussianBlur(alpha, (0, 0), 0.6)

# 按主体包围盒裁剪
ys, xs = np.where(alpha > 0.1)
y1, y2 = max(0, ys.min() - 12), min(h - 1, ys.max() + 12)
x1, x2 = max(0, xs.min() - 12), min(w - 1, xs.max() + 12)
crop = img[y1:y2 + 1, x1:x2 + 1]
a = alpha[y1:y2 + 1, x1:x2 + 1]
b, g, r = cv2.split(crop)
out = cv2.merge([b, g, r, (a * 255).astype(np.uint8)])
ok, buf = cv2.imencode(".png", out)
buf.tofile(str(ROOT / "assets" / "face_final_v7.png"))
print("saved v7:", crop.shape[1], "x", crop.shape[0])
