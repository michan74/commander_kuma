import json
import sys
from pathlib import Path

import cv2
import numpy as np

from barlines import build_measures, detect_barlines
from staves import detect_staves, measure_ruler, quality, straighten

# TODO: エラーにする閾値は仮の値。読めない写真の例を集めて決める
#   dance.jpeg(読める写真)の実測: 曲がり 0.10〜2.16、検出率 0.43〜0.78
MIN_SPACE_PX = 8
MAX_CURVE = 3.0
MIN_COVERAGE = 0.3
MIN_WIDTH_RATIO = 0.8


def binarize(gray):
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )


def draw_staff(image, cands, boxes, start_index):
    out = image.copy()
    for c in cands:
        color = (0, 200, 0) if c["ok"] else (0, 0, 255)
        cv2.line(out, (c["x"], c["y0"]), (c["x"], c["y1"]), color, 3)
    for i, b in enumerate(boxes):
        cv2.rectangle(out, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]), (0, 140, 255), 3)
        cv2.putText(out, str(start_index + i), (b["x"] + 8, b["y"] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 140, 255), 3)
    return out


def draw_overview(img, straights, all_boxes):
    out = img.copy()
    index = 0
    for st, boxes in zip(straights, all_boxes):
        inv = cv2.invertAffineTransform(st["matrix"])
        ox, oy = st["crop"]["x"], st["crop"]["y"]
        for b in boxes:
            corners = np.array([[b["x"], b["y"], 1], [b["x"] + b["w"], b["y"], 1],
                                [b["x"] + b["w"], b["y"] + b["h"], 1], [b["x"], b["y"] + b["h"], 1]])
            poly = (corners @ inv.T + [ox, oy]).astype(np.int32)
            cv2.polylines(out, [poly], True, (0, 140, 255), 4)
            cv2.putText(out, str(index), (int(poly[0][0]) + 8, int(poly[0][1]) - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 140, 255), 3)
            index += 1
    return out


def main():
    src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = []

    img = cv2.imread(str(src))
    binary = binarize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))

    thickness, space = measure_ruler(binary)
    print(f"ruler: thickness={thickness}px space={space}px")
    if space < MIN_SPACE_PX:
        errors.append(f"線の間隔が {space}px しかない(引きすぎ)")

    staves = detect_staves(binary, space)
    if not staves:
        errors.append("五線が見つからない(寄りすぎ、または楽譜でない)")
    widths = [s["xs"][-1] - s["xs"][0] for s in staves]
    median_width = float(np.median(widths)) if widths else 0

    straights, all_boxes, measures, report = [], [], [], []
    for si, (staff, width) in enumerate(zip(staves, widths)):
        st = straighten(img, staff, space)
        curve = quality(st, space)
        width_ratio = width / median_width
        if curve > MAX_CURVE:
            errors.append(f"段{si}: 曲がりすぎ(線の間隔の {curve:.1f} 倍ずれている)")
        if staff["coverage"] < MIN_COVERAGE or width_ratio < MIN_WIDTH_RATIO:
            errors.append(f"段{si}: 五線が途切れている(検出 {staff['coverage']:.0%}、幅 {width_ratio:.0%})")

        gray = cv2.cvtColor(st["image"], cv2.COLOR_BGR2GRAY)
        cands = detect_barlines(binarize(gray), gray, st, space)
        boxes = build_measures(st, cands, space)
        cv2.imwrite(str(out_dir / f"staff_{si:02d}.jpg"), draw_staff(st["image"], cands, boxes, len(measures)))
        for b in boxes:
            measures.append({"index": len(measures), "staff": si, "bbox": b})
        straights.append(st)
        all_boxes.append(boxes)
        report.append({"staff": si, "crop": st["crop"], "angle": round(st["angle"], 2)})
        print(f"staff {si}: angle={st['angle']:+.2f} curve={curve:.2f} coverage={staff['coverage']:.2f} "
              f"width={width_ratio:.2f} ok={sum(c['ok'] for c in cands)} measures={len(boxes)}")

    cv2.imwrite(str(out_dir / "overview.jpg"), draw_overview(img, straights, all_boxes))
    (out_dir / "measures.json").write_text(json.dumps(measures, indent=2))
    (out_dir / "staves.json").write_text(json.dumps(report, indent=2))

    print(f"measures={len(measures)}")
    for e in errors:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
