import cv2
import numpy as np


def measure_ruler(binary):
    """線の太さと、五線の線の間隔(線の中心から次の線の中心まで)を返す"""
    blacks, whites = [], []
    for col in (binary[:, ::4] > 0).T:
        d = np.diff(np.concatenate(([0], col.astype(np.int8), [0])))
        starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
        blacks.append(ends - starts)
        whites.append(starts[1:] - ends[:-1])
    blacks, whites = np.concatenate(blacks), np.concatenate(whites)
    thickness = int(np.bincount(blacks).argmax())
    # NOTE: 1〜2px の白は2値化のノイズなので除く
    gap = int(np.bincount(whites[whites > thickness]).argmax())
    return thickness, gap + thickness


def _line_centers(profile, thresh=0.35):
    on = profile > thresh
    centers, start = [], None
    for y, v in enumerate(on):
        if v and start is None:
            start = y
        elif not v and start is not None:
            centers.append((start + y - 1) / 2)
            start = None
    return centers


def _staves_in_strip(centers, space):
    """等間隔に並ぶ5本の組をすべて返す。加線を含んでずれた組も候補に残し、段の予測位置で選ぶ"""
    found = []
    for i in range(len(centers) - 4):
        gaps = np.diff(centers[i:i + 5])
        if gaps.min() > space * 0.75 and gaps.max() < space * 1.3 and gaps.max() / gaps.min() < 1.3:
            found.append((centers[i], centers[i + 4]))
    return found


def _predict(track, x, n=6):
    arr = np.array(track[-n:])
    mids = (arr[:, 1] + arr[:, 2]) / 2
    if len(arr) < 3:
        return float(np.median(mids))
    slope, icpt = np.polyfit(arr[:, 0], mids, 1)
    return float(slope * x + icpt)


def _merge_tracks(tracks, strip_w):
    tracks = sorted(tracks, key=lambda t: t[0][0])
    i = 0
    while i < len(tracks):
        a = tracks[i]
        h = np.median([b - t for _, t, b in a])
        for b in tracks[i + 1:]:
            if b[0][0] < a[-1][0] - strip_w * 2:
                continue
            head = np.median([(t + bt) / 2 for _, t, bt in b[:6]])
            if abs(_predict(a, b[0][0]) - head) < h * 0.6:
                a.extend(p for p in b if p[0] > a[-1][0])
                tracks.remove(b)
                break
        else:
            i += 1
    return tracks


def _drop_parallel(tracks, space):
    """長い段と重なって並走する短い段(ずれた5本の組から生まれたもの)を捨てる"""
    kept = []
    for t in sorted(tracks, key=len, reverse=True):
        arr = np.array(t)
        mids = (arr[:, 1] + arr[:, 2]) / 2
        dup = False
        for k in kept:
            karr = np.array(k)
            inside = (arr[:, 0] >= karr[0, 0]) & (arr[:, 0] <= karr[-1, 0])
            if inside.mean() < 0.5:
                continue
            kmids = np.interp(arr[inside, 0], karr[:, 0], (karr[:, 1] + karr[:, 2]) / 2)
            if np.median(np.abs(mids[inside] - kmids)) < space * 4:
                dup = True
                break
        if not dup:
            kept.append(t)
    return kept


def _running_median(y, k=9):
    padded = np.pad(y, k // 2, mode="edge")
    return np.array([np.median(padded[i:i + k]) for i in range(len(y))])


def detect_staves(binary, space, min_span=0.3):
    """段ごとに、短冊の中心xと五線の上端・下端の点列を返す"""
    h, w = binary.shape
    strip_w = int(space * 2)
    step = strip_w // 2
    tracks = []
    for sx in range(0, w - strip_w, step):
        profile = binary[:, sx:sx + strip_w].mean(axis=1) / 255
        cx = sx + strip_w / 2
        windows = _staves_in_strip(_line_centers(profile), space)
        used = []
        for t in tracks:
            pred = _predict(t, cx)
            best = min(windows, key=lambda wd: abs((wd[0] + wd[1]) / 2 - pred), default=None)
            if best is not None and abs((best[0] + best[1]) / 2 - pred) < space * 0.5:
                t.append((cx, *best))
                used.append((best[0] + best[1]) / 2)
        for top, bottom in windows:
            mid = (top + bottom) / 2
            if all(abs(mid - u) >= space * 4 for u in used):
                tracks.append([(cx, top, bottom)])
                used.append(mid)

    tracks = _merge_tracks([t for t in tracks if len(t) >= 3], strip_w)
    tracks = _drop_parallel(tracks, space)
    staves = []
    for t in tracks:
        arr = np.array(t)
        # NOTE: 五線の高さは線の間隔4つ分。外れるものは加線・連桁の組み合わせによる偽の五線
        if abs(np.median(arr[:, 2] - arr[:, 1]) / (space * 4) - 1) > 0.2:
            continue
        if arr[-1, 0] - arr[0, 0] < w * min_span:
            continue
        staves.append({
            "xs": arr[:, 0],
            "top": _running_median(arr[:, 1]),
            "bottom": _running_median(arr[:, 2]),
            "coverage": len(arr) / ((arr[-1, 0] - arr[0, 0]) / step + 1),
        })
    staves.sort(key=lambda s: np.median(s["top"]))
    return staves


def straighten(img, staff, space):
    """段を切り出して水平に回す。回した後の五線の上端・下端(xごと)も返す"""
    xs, top, bottom = staff["xs"], staff["top"], staff["bottom"]
    slope = np.polyfit(xs, (top + bottom) / 2, 1)[0]
    angle = float(np.degrees(np.arctan(slope)))

    ih, iw = img.shape[:2]
    margin = int(space * 4)
    x0, x1 = max(0, int(xs[0] - space * 2)), min(iw, int(xs[-1] + space * 2))
    y0, y1 = max(0, int(top.min() - margin)), min(ih, int(bottom.max() + margin))
    crop = img[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    m = cv2.getRotationMatrix2D((cw / 2, ch / 2), angle, 1.0)
    rotated = cv2.warpAffine(crop, m, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))

    def transform(ys):
        pts = np.stack([xs - x0, ys - y0, np.ones_like(xs)], axis=1)
        return pts @ m.T

    t_pts, b_pts = transform(top), transform(bottom)
    grid = np.arange(cw)
    return {
        "image": rotated,
        "crop": {"x": x0, "y": y0, "w": cw, "h": ch},
        "angle": angle,
        "matrix": m,
        "x0": int(max(0, t_pts[0, 0] - space)),
        "x1": int(min(cw - 1, t_pts[-1, 0] + space)),
        "top": np.interp(grid, t_pts[:, 0], t_pts[:, 1]),
        "bottom": np.interp(grid, b_pts[:, 0], b_pts[:, 1]),
        "points": (t_pts, b_pts),
    }


def quality(straight, space):
    """曲がり(直線からの最大のずれ、線の間隔の何倍か)を返す"""
    t_pts, b_pts = straight["points"]
    xs = t_pts[:, 0]
    mids = (t_pts[:, 1] + b_pts[:, 1]) / 2
    fit = np.polyval(np.polyfit(xs, mids, 1), xs)
    return float(np.abs(mids - fit).max() / space)
