# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 現状

まだ実装コードは存在しない（コミットなし）。`spec.md` に書かれた構想のみがあり、`docs/` は空。ビルド・lint・テストのコマンドは未整備なので、導入した時点でこのファイルに追記すること。

## プロダクト概要（`spec.md` より）

「指揮者っくま」: 楽譜の写真を読み込ませると、指揮を振ってくれる Web アプリ。変拍子や GP（ゲネラルパウゼ）のある曲の練習を助ける。

- 入力: YouTube 動画の URL と楽譜画像
- 出力: 動画の再生に合わせて、指揮の動作（またはメトロノーム音）と楽譜上の現在位置バーが動く
- 指揮の表現モード: メトロノーム音モード / 振る動作を見るモード

## 予定されている構成

- バックエンド: Python FastAPI
- フロントエンド: Vue か React（未確定）

### 処理パイプライン

1. **楽譜処理**: 画像を 2 値化 → AI に渡して JSON 化。JSON は `title` / `composer` / `part` / `tempo`（`mark`, `bpm`, `beat_unit`）/ `measures`（小節番号ごとの `time_signature`）を持つ。7/8 や 5/8 などの変拍子を小節単位で保持するのが要点。
2. **YouTube 処理**: `yt-dlp -x --audio-format mp3` で音声抽出 → Librosa で拍・テンポを抽出。
3. 抽出した拍と楽譜 JSON の小節構造を突き合わせ、フロントエンドで指揮アニメーション／小節バーを同期させる。

## 設計書の記法

`spec.md` を新規作成・更新する際は、`design-doc-style` スキルの記法ルールに従う。
