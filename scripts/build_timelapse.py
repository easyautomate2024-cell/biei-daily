# -*- coding: utf-8 -*-
"""
美瑛の丘の1年をタイムラプスにするスクリプト(GitHub Actions で週1実行)

Sentinel-2 の過去データをさかのぼり、一定期間ごとに「いちばん晴れた日」を
1枚ずつ選んで並べる。黒い土から緑、金色、刈り取り、そして雪へ——
畑のパッチワークが1年かけて塗り替わる様子をそのまま動画にする。

一度作った駒は photos/sat/tl/ に残し、次回は足りない期間だけ取りに行く。
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_satellite import (  # noqa: E402
    HILLS_BBOX, SEARCH_BBOX, STAC, fetch_json, read_crop, visible_pct,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME_DIR = os.path.join(ROOT, "photos", "sat", "tl")
META_PATH = os.path.join(ROOT, "data", "timelapse.json")
VIDEO_PATH = os.path.join(ROOT, "photos", "sat", "timelapse.mp4")
POSTER_PATH = os.path.join(ROOT, "photos", "sat", "timelapse-poster.jpg")

LOOKBACK_DAYS = 365   # さかのぼる期間
BUCKET_DAYS = 7       # この日数ごとに1枚選ぶ(晴れ次第で1年最大50枚前後)
MIN_VISIBLE = 90      # 地表がこれ以上見えた日だけ採用(雲の日は駒にしない)

# 雪原は真っ白なので「地表が見えた割合」では雲と区別できず、冬が丸ごと抜ける。
# 冬のあいだはシーン側の雲判定(雪と雲を分けている)を頼りにする。
#
# 雪と雲の区別は明るさではなく「きめ」でつく。雲に覆われた日は画像が
# 真っ平ら(標準偏差ほぼ0)になるが、晴れた雪原には森・川・道の線が残る。
# 実測: 晴れた雪の日 24〜53 / 一面の雲 0〜1。
WINTER_MONTHS = (12, 1, 2, 3)
WINTER_MAX_CLOUD = 45
WINTER_MIN_TEXTURE = 12
FRAME_WIDTH = 720
FPS = 1 / 1.5         # 1駒1.5秒。書き出し時に駒間をディゾルブでつなぐ
OUT_FPS = 24          # 最終動画のフレームレート(中間コマは前後の駒の混合)



def render_winter(f):
    """雪景色を生データ(16bit)から現像する。

    観賞用のvisual画像は雪の明るさで飽和し(9割が255)、雪面の起伏が消える。
    生の反射率には階調が丸ごと残っているので、パーセンタイルで引き伸ばして
    から軽いガンマをかけ、白の中のきめを見えるようにする。"""
    bands = []
    for name in ("red", "green", "blue"):
        a = read_crop(f["assets"][name]["href"], HILLS_BBOX)
        if a.size == 0:
            return None
        bands.append(a[:, :, 0].astype(np.float32))
    raw = np.stack(bands, axis=-1)
    lo = np.percentile(raw, 0.5)
    hi = np.percentile(raw, 99.7)
    v = np.clip((raw - lo) / max(hi - lo, 1.0), 0.0, 1.0)
    return (255.0 * v ** (1 / 1.25)).astype(np.uint8)


def bucket_of(date_str, origin):
    """撮影日が何番目の期間に入るか"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - origin).days // BUCKET_DAYS


def label_for(date_str):
    # 実行環境に日本語フォントがあるとは限らないので、焼き込む文字は数字だけにする
    return date_str.replace("-", ".")


def draw_label(img, text):
    """右下に撮影日を焼き込む。動画は文字を重ねられないので画像側に入れる"""
    d = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:      # 古い Pillow は size を取らない
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad, margin = 10, 16
    x1, y1 = img.width - margin, img.height - margin
    x0, y0 = x1 - tw - pad * 2, y1 - th - pad * 2
    d.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=(0, 0, 0, 140))
    d.text((x0 + pad - box[0], y0 + pad - box[1]), text, font=font, fill=(255, 255, 255, 235))
    return img


def load_meta():
    try:
        with open(META_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def search_scenes(start, end):
    url = (
        f"{STAC}?bbox={SEARCH_BBOX}"
        f"&datetime={start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}"
        "&limit=500"
    )
    items = fetch_json(url)["features"]
    items.sort(key=lambda f: f["properties"]["datetime"])
    return items


def build_frames(meta):
    os.makedirs(FRAME_DIR, exist_ok=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    origin = datetime(start.year, start.month, start.day)

    known = {f["date"]: f for f in meta.get("frames", [])
             if os.path.exists(os.path.join(FRAME_DIR, f["file"]))}
    filled = {bucket_of(d, origin) for d in known}

    # 一度落として「雲で使えない」と分かったシーンは二度と変わらないので覚えておく。
    # 覚えないと、晴れた駒が1枚も採れない期間の候補を毎週ぜんぶ落とし直すことになる。
    rejected = {r["id"]: r for r in meta.get("rejected", [])}

    try:
        items = search_scenes(start, end)
    except Exception as e:
        print(f"シーン検索に失敗: {e}")
        return None

    # 期間ごとに候補をまとめ、雲の少ない順に試す(広域雲量は目安にしかならない)
    buckets = {}
    skipped = 0
    for f in items:
        date = f["properties"]["datetime"][:10]
        b = bucket_of(date, origin)
        if b in filled:
            continue
        if f["id"] in rejected:
            skipped += 1
            continue
        buckets.setdefault(b, []).append(f)
    print(f"直近{LOOKBACK_DAYS}日の通過: {len(items)}回 / 既存の駒 {len(known)}枚 / "
          f"判定済みで再取得を省いたシーン {skipped}件")

    def reject(scene, date, why):
        """恒久的に使えないシーンとして記録する。
        通信エラーのような一時的な失敗をここに入れてはいけない(永久に除外されてしまう)"""
        rejected[scene["id"]] = {"id": scene["id"], "date": date, "why": why}

    added = 0
    for b in sorted(buckets):
        for f in sorted(buckets[b], key=lambda x: x["properties"].get("eo:cloud_cover", 100)):
            date = f["properties"]["datetime"][:10]
            is_winter = int(date[5:7]) in WINTER_MONTHS
            cloud = f["properties"].get("eo:cloud_cover", 100)
            if is_winter and cloud > WINTER_MAX_CLOUD:
                reject(f, date, f"冬の雲量{cloud:.0f}%")
                continue
            try:
                arr = read_crop(f["assets"]["visual"]["href"], HILLS_BBOX)
            except Exception as e:
                # 通信の失敗かもしれないので覚えない。次回また試す
                print(f"  {date}: 切り出し失敗 ({e})")
                continue
            if arr.size == 0:
                reject(f, date, "切り出しが空")
                continue
            vis = visible_pct(arr)
            if is_winter:
                texture = float(arr[:, :, :3].mean(axis=2).std())
                if texture < WINTER_MIN_TEXTURE:
                    print(f"  {date}: きめ{texture:.1f}は雲の一面。次の候補へ。")
                    reject(f, date, f"きめ{texture:.1f}")
                    continue
                rendered = render_winter(f)   # 白飛びしないよう生データから現像
                if rendered is not None:
                    arr = rendered
            elif vis < MIN_VISIBLE:
                reject(f, date, f"地表可視{vis}%")
                continue

            img = Image.fromarray(arr[:, :, :3])
            img = img.resize(
                (FRAME_WIDTH, max(1, round(img.height * FRAME_WIDTH / img.width))),
                Image.LANCZOS,
            )
            img = draw_label(img, label_for(date))
            name = f"tl-{date}.jpg"
            img.save(os.path.join(FRAME_DIR, name), quality=78, optimize=True)
            known[date] = {"date": date, "file": name, "visible": vis}
            added += 1
            print(f"  {date}: 地表可視{vis}% -> {name}")
            break   # この期間は決まったので次へ

    # 期間から外れた古い駒は捨てる
    limit = start.strftime("%Y-%m-%d")
    for date in [d for d in known if d < limit]:
        old = known.pop(date)
        path = os.path.join(FRAME_DIR, old["file"])
        if os.path.exists(path):
            os.remove(path)
            print(f"  期間外の駒を削除: {old['file']}")

    # 判定済みの記録も期間から外れたら捨てる(そのまま貯め続けると際限なく増える)
    kept = sorted((r for r in rejected.values() if r["date"] >= limit),
                  key=lambda r: (r["date"], r["id"]))

    print(f"駒を{added}枚追加。合計{len(known)}枚 / 判定済みシーン{len(kept)}件")
    return [known[d] for d in sorted(known)], kept


def encode(frames):
    """ffmpeg で連番から MP4 を作る。高さは偶数に揃える必要がある"""
    if len(frames) < 4:
        print("駒が足りないので動画は作りません。")
        return False
    listfile = os.path.join(FRAME_DIR, "frames.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for fr in frames:
            f.write(f"file '{fr['file']}'\nduration {1 / FPS:.4f}\n")
        f.write(f"file '{frames[-1]['file']}'\n")   # 最後の駒は明示が必要

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", listfile,
        # framerate フィルタが駒と駒の中間コマを混合で作る=ディゾルブ。
        # 硬い切り替えより、季節が溶けるように変わって見える
        "-vf", f"framerate=fps={OUT_FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "29",
        "-movflags", "+faststart", "-an",
        VIDEO_PATH,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        # 生のトレースバックだと原因が分かりにくいので、何が無いのかを名指しする
        os.remove(listfile)
        print("ffmpeg が見つかりません。ワークフローの ffmpeg 導入手順を確認してください。")
        return False
    os.remove(listfile)
    if r.returncode != 0:
        print(f"ffmpeg 失敗: {r.stderr.strip()[:300]}")
        return False

    Image.open(os.path.join(FRAME_DIR, frames[-1]["file"])).save(
        POSTER_PATH, quality=80, optimize=True)
    size = os.path.getsize(VIDEO_PATH) / 1024 / 1024
    print(f"動画を書き出しました: {len(frames)}駒 / {size:.1f}MB")
    return True


def save_meta(meta):
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def main():
    meta = load_meta()
    built = build_frames(meta)
    if built is None:
        return 1
    frames, rejected = built

    # 駒と判定の記録は、動画の書き出しが失敗しても残す。
    # ここで捨てると、落としたばかりの駒を次回また落とし直すことになる。
    # 一方 from/to/count/built_at はサイトが今ある動画の説明として読む値なので、
    # 書き出しに成功したときだけ差し替える(でないと「52枚」と出しながら22枚の
    # 動画を再生することになる)
    meta["frames"] = frames
    meta["rejected"] = rejected
    save_meta(meta)

    if not encode(frames):
        print("駒と判定の記録は残したので、次回は続きから作り直せます。")
        return 1

    meta["from"] = frames[0]["date"]
    meta["to"] = frames[-1]["date"]
    meta["count"] = len(frames)
    meta["fps"] = FPS
    meta["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_meta(meta)
    print("timelapse.json 更新完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
