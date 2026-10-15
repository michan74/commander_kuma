"""段階3(の一部): 各小節の拍子を Gemini に読ませる。

段階2までで小節の枠(番号)は確定している。ここでは枠を「数えさせず」、
確定した各枠番号について拍子記号だけを答えさせる(数え間違いを防ぐ)。
出力 read_meter.json を、手動で読み取った meter.json(web/)と答え合わせできる。

使い方: python read_meter.py <out_dir> [GEMINI_MODEL は環境変数で上書き可]
"""
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from tempo import quarter_bpm

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

PROMPT = """吹奏楽のパート譜の各段(五線1行)を、水平に切り出した画像を段の順に並べます。
各画像にはオレンジの番号付きの長方形で「小節(枠)」を描いてあります。この番号が枠番号です。
画像には上下にある隣の段も一部写っていますが、**拍子を答えるのはオレンジの枠がある段だけ**です。
隣の段の拍子記号を読まないでください(テンポ表示は枠のある段の上下どちらに書かれていても構いません)。

拍子記号(4/4, 5/8, 2/4 など)は、拍子が変わる小節の先頭にだけ印刷されます。
枠に拍子記号の印刷が無ければ、その小節は前の小節と同じ拍子です。
重要: 拍子記号は段の切れ目では繰り返されません(音部記号・調号は毎段繰り返すが拍子は違う)。
段の最初の枠に拍子記号が無い場合、前の段の最後に小さく書かれた「予告の拍子記号」があれば
それがその段の最初の小節の拍子です。無ければ前の段の最後の小節と同じ拍子です。
渡した段は順番に並んでいるので、前の段の情報を使って判断してください。

渡したすべての枠番号について、その小節の拍子と、それを「どこから決めたか」(ts_source)を答えてください。
- printed: その枠に拍子記号が印刷されていた
- courtesy: 前の段の末尾の予告拍子から判断した
- carried: 画像に手がかりが無く、前の小節から引き継いだ
- unknown: どこにも手がかりが無く推測した
重要: 手がかりが無いときに 4/4 と推測して printed と答えないでください。unknown にしてください。
- 1つの枠の途中で拍子が変わる(小節線の見落としで2小節が1枠に入っている)場合は、
  time_signature に "4/4+2/4" のように連結し、changes_midbox=true にしてください。
- 分母が8の不規則拍子(5/8, 7/8 など)は、8分音符の連桁(ビーム=音符をつなぐ横棒)の区切りが
  拍のまとまりを表します。その区切りを8分音符の数で beat_groups に入れてください
  (例: 5/8 で「2つ+3つ」に連桁されていれば [2,3])。4分系(x/4)や判断できない時は空配列。
- 鉛筆の手書き(音名・丸囲みなど)は無視してください。
- 複数小節休み(長休符)に注意してください。太い横棒1本で描かれ、その真上に小節数の数字
  (例: 4)が書かれます。これは「その数ぶん休む」という意味で、1つの枠で複数小節を表します。
  見つけたら、その数字を rest_measures に入れてください(例: 4小節休みなら 4)。
  音符や通常の休符(全休符など1小節ぶん)しかない枠は rest_measures=1 です。
  数字が読めない時も 1 にしてください(勝手に推測しない)。
枠を増やしたり減らしたりせず、渡した枠番号ちょうどに1つずつ答えてください。

さらに、各段の一番右(最後の小節線より後ろ)に、次の段への「予告の拍子記号」が小さく
書かれていることがあります。見つけたら end_courtesies に {staff, ts} で報告してください
(これは次の段の最初の小節の拍子になります)。無ければ空配列で構いません。

テンポ表示(メトロノーム記号)も探してください。曲の途中で速さが変わるところに
「♩=134」「𝅗𝅥=72」「♩.=60」のように書かれています。見つけたら tempos に
{index(かかる小節の枠番号), value(数字), unit(音符の種類), text(書かれていた通りの表記)}
で報告してください。無ければ空配列です。
- **index は「その速さで演奏しはじめる小節」の枠番号**です。テンポ表示はかかる小節の
  **真上**に書かれるので、表示の真下にある枠の番号を答えてください。
- unit は quarter / half / eighth / dotted_quarter / dotted_half / whole のどれかです。
  音符が白抜き(符頭が塗りつぶされていない)なら 2分音符 half です。見間違えないでください。
- 「Allegro」「Molto vivo」のような速度標語だけで数字が無いものは報告しないでください。
- 「♩=𝅗𝅥」のような音符どうしのイコール(前後の速さの関係を示すもの)は数値ではないので
  報告しないでください。
- 鉛筆で書き込まれた数字(奏者のメモ)はテンポではありません。報告しないでください。"""


class MeasureMeter(BaseModel):
    index: int = Field(description="枠番号")
    time_signature: str = Field(description="拍子(例 4/4, 5/8)。途中で変わるなら 4/4+2/4 のように連結")
    # NOTE: 既定値は旧 read_meter.json(キャッシュ)が ts_source を持たないための後方互換
    ts_source: str = Field(
        default="carried",
        description="拍子をどこから決めたか。printed=その枠に印刷されていた / "
                    "courtesy=前段末の予告拍子から / carried=前の小節から引き継いだ / "
                    "unknown=手がかりが無く推測した")
    changes_midbox: bool = Field(description="1つの枠の途中で拍子が変わるか")
    beat_groups: list[int] = Field(
        description="分母が8の不規則拍子(5/8,7/8等)で、音符の連桁(ビーム)の区切りが示す拍の"
                    "まとまりを8分音符の数で(例 5/8の2+3なら[2,3])。4分系(x/4)や読めない時は空配列")
    rest_measures: int = Field(
        default=1,
        description="複数小節休み(長休符)なら、その枠が表す小節数。枠の上に書かれた数字"
                    "(太い横棒の上の 4 等)を入れる。通常の小節や判断できない時は 1")


class StaffCourtesy(BaseModel):
    staff: int = Field(description="段の番号")
    ts: str = Field(description="その段の最後に小さく書かれた、次段への予告拍子")


class TempoMark(BaseModel):
    index: int = Field(description="テンポ表示が書かれている枠番号")
    value: int = Field(description="メトロノーム記号の数字(例 134)")
    unit: str = Field(description="音符の種類 quarter / half / eighth / dotted_quarter / "
                                  "dotted_half / whole。白抜きの符頭は half")
    text: str = Field(description="書かれていた通りの表記(例 II Molto vivo (𝅗𝅥=134))")


class Result(BaseModel):
    measures: list[MeasureMeter]
    end_courtesies: list[StaffCourtesy] = Field(
        description="各段の末尾に予告拍子(次段最初の小節の拍子)が書かれていれば報告。無ければ空")
    # NOTE: default_factory が無いと、tempos を持たない既存の read_meter.json(キャッシュ)が
    #   読み込みで落ちる。後方互換のため既定値を置く
    tempos: list[TempoMark] = Field(
        default_factory=list,
        description="曲中のテンポ表示(メトロノーム記号)。速度標語だけ・音符どうしのイコール・"
                    "鉛筆の書き込みは含めない。無ければ空")


# NOTE: wide_NN(AI入力用の広いクロップ)を使う。staff_NN(検出用の狭いクロップ)だと
#   段の上下に書かれるテンポ表示が切れて、𝅗𝅥=134 を ♩=134 と誤読する(速さが半分になる)
def _wide_part(out_dir, si):
    return types.Part.from_bytes(
        data=(out_dir / f"wide_{si:02d}.jpg").read_bytes(), mime_type="image/jpeg")


def _generate(client, contents):
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=Result)
    for attempt in range(5):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config).parsed
        except (errors.ServerError, httpx.HTTPError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


# 従来方式: 全段を1リクエストで読む(モデルが段をまたいで拍子を引き継ぐ)
def ask(client, out_dir, indices_by_staff, hint=""):
    contents = [PROMPT + hint]
    for si, indices in indices_by_staff.items():
        contents.append(f"段{si} (枠番号 {indices}): wide_{si:02d}.jpg")
        contents.append(_wide_part(out_dir, si))
    return _generate(client, contents)


# 1段だけ読む(並列用)。段末予告の段番号は確定している si に固定しておく
def ask_staff(client, out_dir, si, indices, hint=""):
    contents = [PROMPT + hint, f"段{si} (枠番号 {indices}): wide_{si:02d}.jpg",
                _wide_part(out_dir, si)]
    r = _generate(client, contents)
    for c in r.end_courtesies:
        c.staff = si
    return r


# 並列方式: 段ごとにリクエストを分けて同時実行し、1つの Result にマージする。
#   出力生成が段ぶんに分散・並列化されるのでレイテンシが縮む。段をまたぐ拍子の引き継ぎは
#   モデルには見えないので、呼び出し側(main)で ts_source=carried/unknown を前方穴埋めする。
#   開始ヒントは「最初の枠」を含む段にだけ渡す(他段の頭に誤って適用されるのを防ぐ)。
def ask_parallel(client, out_dir, indices_by_staff, hint, first_idx):
    items = list(indices_by_staff.items())

    def one(item):
        si, indices = item
        h = hint if (first_idx is not None and first_idx in indices) else ""
        return ask_staff(client, out_dir, si, indices, h)

    with ThreadPoolExecutor(max_workers=min(8, len(items) or 1)) as ex:
        parts = list(ex.map(one, items))
    measures, courtesies, tempos = [], [], []
    for p in parts:
        measures.extend(p.measures)
        courtesies.extend(p.end_courtesies)
        tempos.extend(p.tempos)
    return Result(measures=measures, end_courtesies=courtesies, tempos=tempos)


# 段をまたぐ拍子の前方穴埋め。手がかりの無い枠(carried/unknown)を直前の確定拍子で埋める。
#   単発リクエストならモデルが全段を見て埋めていた値を、並列では Python が担う(単発でも無害)。
def carry_forward_ts(out):
    last = None
    for m in out:
        if m.get("ts_source") in ("printed", "courtesy", "user") and m.get("ts"):
            last = m["ts"]
        elif m.get("ts_source") in ("carried", "unknown") and last:
            m["ts"] = last
            try:
                m["beats"] = beats_of(last)
            except Exception:
                pass


def beats_of(ts):
    # NOTE: 4分音符を1拍とした拍数。"4/4"→4, "5/8"→2.5。連結 "4/4+2/4"→6 は各項の和
    total = Fraction(0)
    for part in ts.split("+"):
        n, d = part.strip().split("/")
        total += Fraction(int(n) * 4, int(d))
    return float(total)


def load_start(out_dir):
    """triage(read_start.py)が確定させた開始情報。無ければ None"""
    path = out_dir / "start.json"
    return json.loads(path.read_text()) if path.exists() else None


def hint_text(start):
    """確定した開始情報をプロンプトに差し込む文。確定していなければ空文字

    NOTE: 1小節目の拍子を誤ると carry-forward で後続すべてに伝播するので、triage で確定できた分は
      推測させず教える。1段目のクロップに拍子記号が入らない写真(段の左端が検出から外れる等)でも
      triage は全幅のページ頭画像から読めていることがある。
    """
    if not start:
        return ""
    lines = []
    if start["ts"]["value"]:
        lines.append(f"1番最初の小節の拍子は {start['ts']['value']} と確定しています。"
                     "最初の枠はこの拍子にしてください(ts_source は printed)。")
    if start["tempo"]["value"]:
        lines.append(f"曲の開始テンポは4分音符換算で {start['tempo']['value']} と確定しています"
                     f"({start['tempo']['text'] or ''})。これは tempos に報告しなくて構いません。")
    if not lines:
        return ""
    return "\n\n【確定している情報】\n" + "\n".join(lines)


def apply_start(out, start):
    """確定した開始情報を先頭の小節の anchor にする"""
    if not out or not start:
        return
    first = out[0]
    if start["ts"]["value"]:
        first["ts"] = start["ts"]["value"]
        first["beats"] = beats_of(start["ts"]["value"])
        first["ts_source"] = "user"
        first["confidence"] = 1.0
    if start["tempo"]["value"]:
        first["tempo"] = start["tempo"]["value"]
        first["tempo_text"] = start["tempo"]["text"]
        # NOTE: tempo は4分換算で持つが、表示を楽譜どおりの音符にするため元の単位も残す
        first["tempo_unit"] = start["tempo"].get("unit") or "quarter"
        first["tempo_source"] = "user"


def apply_courtesy(result, first_of_staff):
    # NOTE: 予告拍子を次段の最初の小節に引き継ぐ。拍子は段をまたいで繰り返されないため、
    #   段末の予告拍子が次段先頭の小節の拍子になる(例: staff7末尾の2/4 → staff8先頭 枠51)
    by_index = {m.index: m for m in result.measures}
    for c in result.end_courtesies:
        nxt = first_of_staff.get(c.staff + 1)
        if nxt is not None and nxt in by_index:
            by_index[nxt].time_signature = c.ts
            by_index[nxt].ts_source = "courtesy"


def main():
    out_dir = Path(sys.argv[1])
    # NOTE: self-consistency。METER_RUNS 回読ませて小節ごとに多数決し、割れ具合を信頼度にする。
    #   既定1回(サーバの通常処理は速さ優先)。信頼度が欲しい時は METER_RUNS=3 等で呼ぶ
    runs = max(1, int(os.environ.get("METER_RUNS", "1")))
    vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    if not vertex and not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY が未設定です(または Vertex なら GOOGLE_GENAI_USE_VERTEXAI=true)")

    measures = json.loads((out_dir / "measures.json").read_text())
    staves = sorted({m["staff"] for m in measures})
    indices_by_staff = {si: [m["index"] for m in measures if m["staff"] == si] for si in staves}
    first_of_staff = {si: min(idxs) for si, idxs in indices_by_staff.items()}
    all_idx = sorted(m["index"] for m in measures)

    start = load_start(out_dir)
    hint = hint_text(start)
    cache = out_dir / "read_meter.json"
    client = genai.Client()
    results = []
    if runs == 1 and cache.exists():
        r = Result.model_validate_json(cache.read_text())
        apply_courtesy(r, first_of_staff)
        results = [r]
    else:
        # 既定は段ごと並列(速さ優先)。METER_PARALLEL=0 で従来の全段1リクエストに戻せる
        parallel = os.environ.get("METER_PARALLEL", "1").lower() in ("1", "true")
        first_idx = all_idx[0] if all_idx else None
        for _ in range(runs):
            if parallel:
                r = ask_parallel(client, out_dir, indices_by_staff, hint, first_idx)
            else:
                r = ask(client, out_dir, indices_by_staff, hint)
            apply_courtesy(r, first_of_staff)
            results.append(r)
        cache.write_text(results[-1].model_dump_json(indent=2))

    ts_by_run = [{m.index: m.time_signature for m in r.measures} for r in results]
    bg_last = {m.index: m.beat_groups for m in results[-1].measures}
    # NOTE: ts_source は多数決を取らず最終 run の値を使う(拍子そのものは多数決)。
    #   既定 METER_RUNS=1 では差が出ないため
    src_last = {m.index: m.ts_source for m in results[-1].measures}
    rest_last = {m.index: getattr(m, "rest_measures", 1) for m in results[-1].measures}
    # NOTE: テンポは多数決を取らない(beat_groups と同じく最終 run の値を使う)。既定 METER_RUNS=1 では
    #   差が出ず、テンポ表示は拍子より読み間違いが少ないため
    tempo_last = {t.index: t for t in results[-1].tempos}

    out = []
    for i in all_idx:
        votes = Counter(run[i] for run in ts_by_run if i in run)
        if not votes:
            continue
        ts, cnt = votes.most_common(1)[0]
        try:
            b = beats_of(ts)
        except Exception:
            b = None
        m = {"index": i, "ts": ts, "beats": b, "beat_groups": bg_last.get(i, []),
             "ts_source": src_last.get(i, "carried"),
             "confidence": round(cnt / len(results), 2)}
        # 複数小節休み(長休符)。>1 のときだけ持たせる(検証用: 本当に数字を読めているか)
        if (rm := rest_last.get(i, 1)) and rm > 1:
            m["rest_measures"] = rm
        # NOTE: テンポは表示のある小節にだけ付ける(anchor)。全小節に展開しない。
        #   引き継ぎ(carry-forward)はフロントの measureTempos が担う
        t = tempo_last.get(i)
        if t is not None and (bpm := quarter_bpm(t.value, t.unit)) is not None:
            m["tempo"] = bpm
            m["tempo_text"] = t.text
            m["tempo_unit"] = t.unit or "quarter"  # 表示用に元の音符単位を残す(内部は4分換算)
            m["tempo_source"] = "printed"
        out.append(m)
    apply_start(out, start)
    carry_forward_ts(out)  # 段をまたぐ拍子の引き継ぎ(並列で各段が独立に読むため)
    (out_dir / "meter_ai.json").write_text(
        json.dumps({"measures": out}, ensure_ascii=False, indent=2))

    low = [m for m in out if m["confidence"] < 1.0]
    anchors = [m for m in out if "tempo" in m]
    print(f"model={MODEL} runs={runs}  {len(out)}枠  低信頼(読みが割れた)={len(low)}  "
          f"テンポ表示={len(anchors)}件"
          + (f"  開始ヒント={start['ts']['value']} / ♩={start['tempo']['value']}" if hint else ""))
    for m in anchors:
        print(f"  枠{m['index']}: ♩={m['tempo']}  {m['tempo_text']}")
    for m in low:
        print(f"  枠{m['index']}: {m['ts']} conf={m['confidence']}")


if __name__ == "__main__":
    main()
