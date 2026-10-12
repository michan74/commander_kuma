import json
import os
import shutil
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

# NOTE: 段階2は画像を見て削除・結合を判定するだけなので Flash 系で十分。
#   無料枠は「モデルごと」に小さい(Gemini 3 flash は1日数回)。全段を1リクエストにまとめて消費を抑える。
#   使えるIDは `genai.Client().models.list()` で確認できる(このアカウントは 2.5 系が 404)
MODEL = "gemini-3.7-flash"


# NOTE: 段階2はAIに座標を出させない。削除・結合・分割はすべて番号で返させる(spec.md 段階2)。
#   枠番号は staff_XX.jpg のオレンジ番号(measures.json の index)、D番号はマゼンタの分割候補線
#   (split_candidates.json)と一致する。分割位置の座標は段階1の検出値を使い、AIは選ぶだけ
class Split(BaseModel):
    index: int = Field(description="複数小節を含んでいる枠の番号")
    at: list[int] = Field(description="その枠を割る位置の、マゼンタのD番号(見落とした小節線に重なるもの)")


class Issue(BaseModel):
    indices: list[int] = Field(description="対象の枠番号")
    note: str = Field(description="削除・結合・分割では直せない誤りの説明")


class StaffResult(BaseModel):
    staff: int = Field(description="段の番号")
    deletes: list[int] = Field(
        description="小節でない領域に付いた枠の番号(音部記号・拍子記号だけの部分、段末の予告拍子など)")
    merges: list[list[int]] = Field(
        description="本来1つの小節が複数の枠に分かれているときの、まとめるべき枠番号の組")
    splits: list[Split] = Field(
        description="小節線の見落としで1枠に複数小節が入っていて、その位置にD番号の候補線があるもの")
    issues: list[Issue] = Field(
        description="削除・結合・分割では直せない誤り(見落としの位置にD番号の候補線が無い等)")


class Result(BaseModel):
    staves: list[StaffResult]


# NOTE: 無料枠が小さいので、段ごとに呼ばず全段を1リクエストにまとめる。各段の画像の前に「段N:」を付けて渡す
PROMPT = """吹奏楽のパート譜の各段(五線1行)を、水平になるよう切り出した画像を段の順に並べます。

各画像には小節検出の結果を描き込んであります。
- オレンジの番号付きの長方形: 検出した小節候補(この番号が枠番号)
- 緑の縦線: 小節線として採用した線 / 赤の縦線: 符尾や鉛筆として除外した線
- マゼンタ(紫)の D番号付きの縦線: 小節線かもしれないが除外した線(分割候補)

段ごとに、番号付きの枠が実際の小節と合っているかを確認し、誤りを補正指示にしてください。

誤りの種類と対応:
- 誤検出: 音符の符尾・和音・鉛筆の線を小節線と見なし、1つの小節が2つ以上の枠に分かれている
  -> まとめるべき枠番号を merges に組で入れる (例: [[3, 4]])
- 余分な枠: 小節でない領域に枠が付いている(段の先頭の音部記号・拍子記号だけの部分、段末の予告拍子)
  -> その番号を deletes に入れる
- 見落とし: 1つの枠が実際には複数の小節を含む(小節線を見落とした)
  -> 見落とした小節線の位置にマゼンタの D番号の線があれば、splits に {index: 枠番号, at: [D番号]} を入れる
     (枠が3小節なら、2本のD番号を at に並べる)
  -> 見落とした位置に D番号の線が無ければ、分割位置を示せないので issues に枠番号とメモを入れる

座標は出さないでください。枠番号とD番号だけで答えます。誤りがなければ空の配列を返します。
staves には、渡したすべての段(staff 番号)の結果を入れてください。"""


def log_request(out_dir, text_parts, indices_by_staff):
    # NOTE: AIに何を渡したかを確認できるよう、直前1リクエスト分だけ last_sent/ に残す(毎回上書き)
    log = out_dir / "last_sent"
    if log.exists():
        shutil.rmtree(log)
    log.mkdir()
    (log / "prompt.txt").write_text("\n".join(text_parts))
    for si in indices_by_staff:
        shutil.copy(out_dir / f"staff_{si:02d}.jpg", log / f"staff_{si:02d}.jpg")


def ask(client, out_dir, indices_by_staff):
    contents, text_parts = [PROMPT], [PROMPT]
    for si, indices in indices_by_staff.items():
        label = f"段{si} (小節候補番号 {indices}): staff_{si:02d}.jpg"
        contents.append(label)
        text_parts.append(label)
        contents.append(types.Part.from_bytes(
            data=(out_dir / f"staff_{si:02d}.jpg").read_bytes(), mime_type="image/jpeg"))
    log_request(out_dir, text_parts, indices_by_staff)
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=Result)
    # NOTE: 混雑時の 503 や接続切れ(RemoteProtocolError)は一時的なので、数回まで待って再試行する
    for attempt in range(5):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config).parsed
        except (errors.ServerError, httpx.HTTPError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def union_bbox(bboxes):
    x0 = min(b["x"] for b in bboxes)
    y0 = min(b["y"] for b in bboxes)
    x1 = max(b["x"] + b["w"] for b in bboxes)
    y1 = max(b["y"] + b["h"] for b in bboxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def split_bbox(bbox, cuts):
    # NOTE: 分割候補のxで枠を縦に割る。各小節のy・hは元の枠のまま使う(段階3の読解には十分)
    xs = [bbox["x"]] + sorted(cuts) + [bbox["x"] + bbox["w"]]
    return [{"x": a, "y": bbox["y"], "w": b - a, "h": bbox["h"]} for a, b in zip(xs, xs[1:])]


def apply_corrections(measures, deletes, merges, splits):
    deletes = set(deletes)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for group in merges:
        for a, b in zip(group, group[1:]):
            parent[find(a)] = find(b)

    by_index = {m["index"]: m for m in measures}
    result, consumed = [], set()
    for m in measures:
        i = m["index"]
        if i in deletes:
            continue
        root = find(i) if i in parent else i
        if root in consumed:
            continue
        consumed.add(root)
        comp = sorted(j for j in by_index
                      if (find(j) if j in parent else j) == root and j not in deletes)
        if not comp:
            continue
        leader = by_index[comp[0]]
        bbox = union_bbox([by_index[j]["bbox"] for j in comp])
        cuts = sorted(x for j in comp for x in splits.get(j, [])
                      if bbox["x"] < x < bbox["x"] + bbox["w"])
        # NOTE: change は人間の手直し用。AIが変えた枠だけ可視化で色を変えて目を向けさせる
        change = "split" if cuts else "merge" if len(comp) > 1 else None
        for piece in split_bbox(bbox, cuts):
            out = {"index": len(result), "staff": leader["staff"], "bbox": piece}
            if change:
                out["change"] = change
            result.append(out)
    return result


# NOTE: 人が「どこをAIが直したか」を一目で見て手直しできるよう、変更の種類で枠の色を変える
COLORS = {None: (0, 140, 255), "split": (0, 0, 255), "merge": (255, 100, 0)}  # そのまま橙 / 分割赤 / 結合青


def draw_corrected(src_img, report, corrected):
    # NOTE: 補正後の枠(段ごとの水平化座標)を matrix の逆変換で元写真に戻して重ねる
    out = src_img.copy()
    by_staff = {r["staff"]: r for r in report}
    for m in corrected:
        r = by_staff[m["staff"]]
        inv = cv2.invertAffineTransform(np.array(r["matrix"], dtype=np.float64))
        ox, oy = r["crop"]["x"], r["crop"]["y"]
        b = m["bbox"]
        corners = np.array([[b["x"], b["y"], 1], [b["x"] + b["w"], b["y"], 1],
                            [b["x"] + b["w"], b["y"] + b["h"], 1], [b["x"], b["y"] + b["h"], 1]])
        poly = (corners @ inv.T + [ox, oy]).astype(np.int32)
        change = m.get("change")
        color = COLORS[change]
        cv2.polylines(out, [poly], True, color, 6 if change else 4)
        cv2.putText(out, str(m["index"]), (int(poly[0][0]) + 8, int(poly[0][1]) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, color, 3)
    return out


def main():
    out_dir = Path(sys.argv[1])
    src = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY (または GOOGLE_API_KEY) が未設定です")

    measures = json.loads((out_dir / "measures.json").read_text())
    split_cands = json.loads((out_dir / "split_candidates.json").read_text())
    cand_x = {c["id"]: c["x"] for c in split_cands}
    staves = sorted({m["staff"] for m in measures})
    indices_by_staff = {si: [m["index"] for m in measures if m["staff"] == si] for si in staves}

    # NOTE: 成功した応答を保存し、再実行ではそれを使う(クォータを無駄に消費しない)
    cache = out_dir / "corrections.json"
    if cache.exists():
        result = Result.model_validate_json(cache.read_text())
    else:
        result = ask(genai.Client(), out_dir, indices_by_staff)
        cache.write_text(result.model_dump_json(indent=2))

    by_staff = {s.staff: s for s in result.staves}
    deletes, merges, splits = [], [], {}
    for si in staves:
        s = by_staff.get(si)
        if s is None:
            print(f"staff {si}: 応答に無し")
            continue
        deletes += s.deletes
        merges += s.merges
        for sp in s.splits:
            splits[sp.index] = [cand_x[d] for d in sp.at if d in cand_x]
        print(f"staff {si}: deletes={s.deletes} merges={s.merges} "
              f"splits={[(sp.index, sp.at) for sp in s.splits]} issues={len(s.issues)}")

    corrected = apply_corrections(measures, deletes, merges, splits)
    (out_dir / "corrected_measures.json").write_text(json.dumps(corrected, indent=2))

    added = sum(len(cuts) for cuts in splits.values())
    print(f"\nmeasures: {len(measures)} -> {len(corrected)} "
          f"(deletes={len(deletes)} merges={len(merges)} splits=+{added})")
    for s in result.staves:
        for issue in s.issues:
            print(f"ISSUE: staff {s.staff} 枠 {issue.indices}: {issue.note}")

    if src is not None:
        report = json.loads((out_dir / "staves.json").read_text())
        cv2.imwrite(str(out_dir / "corrected_overview.jpg"),
                    draw_corrected(cv2.imread(str(src)), report, corrected))


if __name__ == "__main__":
    main()
