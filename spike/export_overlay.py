"""小節を、元写真のピクセル座標のポリゴンに変換して overlay.json に出す。
Web で楽譜写真の上に小節枠を重ねるために使う(段階1の matrix を再利用)。

使い方: python export_overlay.py <out_dir> <元写真> [measures.json|corrected_measures.json]
  既定は measures.json(段階1の素の検出。9/10段正解でAI補正より正確だと確認済み)。
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def main():
    out_dir = Path(sys.argv[1])
    src = Path(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else "measures.json"
    measures = json.loads((out_dir / name).read_text())
    report = {r["staff"]: r for r in json.loads((out_dir / "staves.json").read_text())}
    h, w = cv2.imread(str(src)).shape[:2]

    out = []
    for m in measures:
        r = report[m["staff"]]
        inv = cv2.invertAffineTransform(np.array(r["matrix"], dtype=np.float64))
        ox, oy = r["crop"]["x"], r["crop"]["y"]
        b = m["bbox"]
        corners = np.array([[b["x"], b["y"], 1], [b["x"] + b["w"], b["y"], 1],
                            [b["x"] + b["w"], b["y"] + b["h"], 1], [b["x"], b["y"] + b["h"], 1]])
        poly = corners @ inv.T + [ox, oy]
        out.append({"index": m["index"], "staff": m["staff"],
                    "points": [[round(float(px), 1), round(float(py), 1)] for px, py in poly]})

    (out_dir / "overlay.json").write_text(
        json.dumps({"image": src.name, "width": w, "height": h, "measures": out}, indent=2))
    print(f"measures={len(out)} image={w}x{h}")


if __name__ == "__main__":
    main()
