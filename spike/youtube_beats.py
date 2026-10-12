"""YouTube の音声を抽出し、Librosa で拍とテンポを検出する(spec.md 段階2: YouTube処理)。

使い方: python youtube_beats.py <YouTube URL> <out_dir>
  out_dir/audio.mp3 に音声を落とし、out_dir/beats.json に {tempo, duration, beat_times} を出す。
  audio.mp3 が既にあれば再ダウンロードしない。
"""
import json
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np


def main():
    url = sys.argv[1]
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3 = out_dir / "audio.mp3"

    if not mp3.exists():
        subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "--no-playlist",
             "-o", str(out_dir / "audio.%(ext)s"), url],
            check=True,
        )

    y, sr = librosa.load(str(mp3))
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beats, sr=sr)
    data = {
        "tempo": round(float(np.asarray(tempo).item()), 1),
        "duration": round(float(librosa.get_duration(y=y, sr=sr)), 2),
        "beat_times": [round(float(t), 3) for t in beat_times],
    }
    (out_dir / "beats.json").write_text(json.dumps(data, indent=2))
    print(f"tempo={data['tempo']}bpm duration={data['duration']}s beats={len(beat_times)}")


if __name__ == "__main__":
    main()
