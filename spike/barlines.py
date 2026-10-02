import cv2
import numpy as np

# NOTE: 印刷の線は190以上、鉛筆の書き込みは150〜170程度だった(dance.jpeg で計測)
PENCIL_DARKNESS = 180


def _side_ink(ink, x, bw, y_end, space):
    """縦線の端の左右それぞれに、五線以外のインクがどれだけあるか(0〜1)"""
    y0, y1 = max(0, int(y_end - space * 0.7)), int(y_end + space * 0.7)
    left = ink[y0:y1, max(0, int(x - space)):max(0, x - 2)]
    right = ink[y0:y1, x + bw + 2:int(x + bw + space)]
    return (left.mean() / 255 if left.size else 0.0, right.mean() / 255 if right.size else 0.0)


def _lean(left, right, thresh=0.15):
    """端のインクが左右どちらに偏っているか(-1: 左, 0: なし, 1: 右)"""
    if abs(left - right) < thresh:
        return 0
    return -1 if left > right else 1


def _looks_like_stem(top_ink, bottom_ink):
    # NOTE: 符頭は符尾の片端の片側にだけ付く。拍子記号や休符は小節線の両端で同じ側に並ぶので区別できる
    top, bottom = _lean(*top_ink), _lean(*bottom_ink)
    return (top == 0) != (bottom == 0) or top * bottom < 0


def detect_barlines(binary, gray, staff, space):
    staff_h = space * 4
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(staff_h * 0.7)))
    )
    staff_lines = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(space * 2), 1))
    )
    ink = cv2.subtract(binary, staff_lines)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(vertical)
    tol = staff_h * 0.2
    cands = []
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        if bw > space:
            continue
        cx = x + bw // 2
        if not (staff["x0"] <= cx <= staff["x1"]):
            continue
        t, b = staff["top"][cx], staff["bottom"][cx]
        if y + bh < t or y > b:
            continue
        darkness = float(255 - gray[labels == i].mean())
        spans = abs(y - t) < tol and abs(y + bh - b) < tol
        top_ink = _side_ink(ink, x, bw, y, space)
        bottom_ink = _side_ink(ink, x, bw, y + bh, space)
        stem = _looks_like_stem(top_ink, bottom_ink)
        cands.append({
            "x": int(cx), "y0": int(y), "y1": int(y + bh), "w": int(bw),
            "spans": bool(spans), "stem": bool(stem), "darkness": round(darkness, 1),
            "ok": bool(spans and not stem and darkness >= PENCIL_DARKNESS),
        })
    cands.sort(key=lambda c: c["x"])
    return cands


def _merge_close(xs, gap):
    merged = []
    for x in xs:
        if merged and x - merged[-1][-1] < gap:
            merged[-1].append(x)
        else:
            merged.append([x])
    return [int(np.mean(g)) for g in merged]


def build_measures(staff, cands, space):
    staff_h = space * 4
    xs = _merge_close([c["x"] for c in cands if c["ok"]], space * 2)
    x0, x1 = staff["x0"], staff["x1"]
    edges = [x0] + [x for x in xs if x - x0 > space * 2]
    # NOTE: 段の最後の小節線より後ろは、次の段の予告拍子なので小節にしない
    if x1 - edges[-1] > staff_h * 1.5:
        edges.append(x1)
    boxes = []
    for a, b in zip(edges, edges[1:]):
        y0 = int(staff["top"][a:b + 1].min())
        y1 = int(staff["bottom"][a:b + 1].max())
        boxes.append({"x": int(a), "y": y0, "w": int(b - a), "h": y1 - y0})
    return boxes
