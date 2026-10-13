"""テンポの知識(音符単位の4分換算と、速度標語のBPM)。read_start.py / read_meter.py が共有する。

フロントのタイムラインは4分音符を1拍として動くので、楽譜の表記はここで4分換算に正規化する。
"""
import re

# 4分音符を1とした長さ。𝅗𝅥=134 は ♩=268 と同じ速さ(2分音符1つ = 4分音符2つ)
UNIT_FACTOR = {
    "quarter": 1.0,
    "half": 2.0,
    "eighth": 0.5,
    "dotted_quarter": 1.5,
    "dotted_half": 3.0,
    "whole": 4.0,
}

# NOTE: この範囲を外れた値は読み間違いとして捨てる(人に聞く方に倒す)
MIN_BPM, MAX_BPM = 20, 400

# 速度標語 → ♩のBPM。出典: 高校音楽の教科書の表
# TODO: 1点の値しか持たない。実際の速さは時代・作曲家で変わる(バロックの allegro は
#   ロマン派より遅い)。当面はこの値を確定値として使い、調整は後続。
TEMPO_WORDS = {
    "grave": 40,         # 重々しくゆるやかに
    "largo": 46,         # 幅広くゆるやかに
    "lento": 52,         # ゆるやかに
    "adagio": 56,        # ゆるやかに
    "larghetto": 60,     # Largo よりやや速く
    "adagietto": 66,
    "andante": 72,       # ゆっくり歩くような速さで
    "andantino": 80,     # Andante よりやや速く
    "maestoso": 88,      # 荘厳に
    "moderato": 96,      # 中ぐらいの速さで
    "allegretto": 108,   # やや速く
    "animato": 120,      # 元気に速く
    "allegro": 132,      # 速く
    "assai": 144,
    "vivace": 160,       # 活発に速く
    "presto": 184,       # 急速に
    "prestissimo": 208,
}


def quarter_bpm(value, unit):
    """テンポ表示(数値 + 音符単位)を4分音符換算のBPMにする。読めない組み合わせは None"""
    factor = UNIT_FACTOR.get(unit)
    if factor is None or not value:
        return None
    bpm = round(value * factor)
    return bpm if MIN_BPM <= bpm <= MAX_BPM else None


def word_bpm(text):
    """速度標語を♩のBPMにする。表に無い/どの標語か決まらない表記は None(人に聞く)"""
    if not text:
        return None
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return None
    if " ".join(words) in TEMPO_WORDS:
        return TEMPO_WORDS[" ".join(words)]
    # NOTE: 修飾語付き("Allegro con brio")は標語が1つだけ当たるので引ける。
    #   2つ当たる表記("Allegro assai" は allegro と assai の両方)は、どちらの速さか
    #   機械的に決められないので None にして人に聞く。修飾語による補正もしない。
    hits = {TEMPO_WORDS[w] for w in words if w in TEMPO_WORDS}
    return hits.pop() if len(hits) == 1 else None
