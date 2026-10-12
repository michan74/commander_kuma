#!/bin/bash
# 段階1(小節検出)→段階2(AI補正)をまとめて回す。
# 使い方: spike/run.sh [写真(tmp/配下)] [出力先(tmp/配下)]
#   例:    spike/run.sh dance_movement.jpeg out_v2
# 段階2には GEMINI_API_KEY が要る。リポジトリ直下の .env から読み込む。
# .env が無ければ段階1だけ回して終わる。
set -e
cd "$(dirname "$0")/.."

SRC="${1:-dance_movement.jpeg}"
OUT="${2:-out_v2}"

ENV_OPT=()
[ -f .env ] && ENV_OPT=(--env-file .env)

docker build -q -t kuma-spike spike/ >/dev/null

docker run --rm "${ENV_OPT[@]}" \
  -v "$PWD/tmp:/work" -v "$PWD/spike:/spike" -w /spike kuma-spike \
  bash -c "python detect_measures.py /work/$SRC /work/$OUT \
           && if [ -n \"\$GEMINI_API_KEY\" ]; then python ai_correct.py /work/$OUT /work/$SRC; \
              else echo 'GEMINI_API_KEY 未設定のため段階2をスキップ'; fi"
