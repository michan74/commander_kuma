import sys
from pathlib import Path

import cv2
import numpy as np

from barlines import PENCIL_DARKNESS, detect_barlines
from detect_measures import binarize
from staves import detect_staves, measure_ruler, straighten

# 使い方: debug_barlines.py <写真> <段番号>
# 指定した段の縦成分の濃さ・幅を出し、小節線判定(spans/stem/darkness)の実態を確認する
src, si = Path(sys.argv[1]), int(sys.argv[2])
img = cv2.imread(str(src))
binary = binarize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
_, space = measure_ruler(binary)
staff = detect_staves(binary, space)[si]
st = straighten(img, staff, space)
gray = cv2.cvtColor(st["image"], cv2.COLOR_BGR2GRAY)

print(f"space={space}px PENCIL_DARKNESS={PENCIL_DARKNESS}")
print("\n=== detect_barlines の候補 (x順) ===")
cands = detect_barlines(binarize(gray), gray, st, space)
for c in cands:
    print(f"x={c['x']:4d} w={c['w']:2d} darkness={c['darkness']:5.1f} "
          f"spans={int(c['spans'])} stem={int(c['stem'])} ok={int(c['ok'])}")

# NOTE: detect_barlines は幅が太い成分(bw>space)を候補にする前に捨てる。
#   鉛筆と重なって弾かれた小節線を見つけるため、全縦成分を幅も含めて出す
print("\n=== 全縦成分 (段のx範囲内, x順) ===")
staff_h = space * 4
vertical = cv2.morphologyEx(
    binarize(gray), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(staff_h * 0.7))))
n, labels, stats, _ = cv2.connectedComponentsWithStats(vertical)
rows = []
for i in range(1, n):
    x, y, bw, bh, _ = stats[i]
    cx = x + bw // 2
    if not (st["x0"] <= cx <= st["x1"]):
        continue
    darkness = float(255 - gray[labels == i].mean())
    rows.append((cx, bw, bh, darkness, "幅太い" if bw > space else ""))
for cx, bw, bh, dk, note in sorted(rows):
    print(f"x={cx:4d} w={bw:2d} h={bh:3d} darkness={dk:5.1f} {note}")
