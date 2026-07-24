# -*- coding: utf-8 -*-
"""
衛星画像の自動更新スクリプト(GitHub Actions で毎日実行)

Sentinel-2 の公開データ(AWS Open Data / Element84 STAC)から、直近で最も新しい
「晴れた」美瑛のシーンを探し、10m解像度の切り出し画像を生成する。
新しい画像が無い日は何もしない(サイトは前回の画像を表示し続ける)。

あわせて直近の通過記録(晴れなかった日を含む)を data/satellite.json に残し、
小さなサムネイルを作る。「更新が止まっている」のか「雲で待っている」のかを
サイト側で説明できるようにするため。
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "photos", "sat")
META_PATH = os.path.join(ROOT, "data", "satellite.json")

STAC = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items"
SEARCH_BBOX = "142.42,43.53,142.50,43.60"  # 美瑛の丘・市街エリア
MAX_CLOUD = 35      # シーン全体の雲量がこれ以下なら採用
LOOKBACK_DAYS = 30  # 通過記録をさかのぼる日数(通過は約5日おき)
HISTORY_KEEP = 5    # 記録に残す通過回数(サムネイル枚数もこの数)

HILLS_BBOX = (142.42, 43.53, 142.50, 43.60)

# 切り出し範囲 (西, 南, 東, 北) と最大幅px
CROPS = {
    "sat-hills.jpg": (HILLS_BBOX, 1000),                       # 定番: 丘と市街
    "sat-wide.jpg": ((142.36, 43.47, 142.58, 43.64), 1400),    # 広域: 美瑛全域
}

THUMB_WIDTH = 420   # 通過記録のサムネイル幅
BRIGHT_LIMIT = 200  # これ以上明るいと「真っ白＝雲で地表が見えない」とみなす


def utcnow():
    return datetime.now(timezone.utc)


def load_meta():
    try:
        with open(META_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def find_items():
    """直近 LOOKBACK_DAYS の通過シーンを新しい順で返す"""
    end = utcnow()
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = (
        f"{STAC}?bbox={SEARCH_BBOX}"
        f"&datetime={start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}"
        "&limit=50"
    )
    with urllib.request.urlopen(url, timeout=90) as r:
        payload = r.read()
    items = json.loads(payload)["features"]
    items.sort(key=lambda f: f["properties"]["datetime"], reverse=True)
    return items


def read_crop(href, bbox):
    """指定範囲を切り出して RGB 配列で返す"""
    w, s, e, n = bbox
    with rasterio.open(href) as src:
        b = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
        data = src.read(window=from_bounds(*b, src.transform))
    return np.transpose(data, (1, 2, 0))


def visible_pct(img):
    """美瑛エリアで地表が見えた割合(真っ白でないピクセルの割合)"""
    return round(float((img.min(axis=2) < BRIGHT_LIMIT).mean()) * 100, 1)


def save_jpg(img, path, max_w, quality=82):
    im = Image.fromarray(img)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    im.save(path, quality=quality, optimize=True)
    return im.size


def build_history(items, old_passes, must_include=None):
    """直近の通過記録を作る。処理済みのシーンは再ダウンロードしない。
    掲載中のシーンは、記録の対象期間から外れても必ず残す(晴れの日との比較用)。"""
    known = {p.get("scene_id"): p for p in old_passes}
    targets = list(items[:HISTORY_KEEP])
    if must_include and not any(f["id"] == must_include for f in targets):
        extra = next((f for f in items if f["id"] == must_include), None)
        if extra is not None:
            targets.append(extra)

    passes = []
    for f in targets:
        sid = f["id"]
        date = f["properties"]["datetime"][:10]
        cloud = round(f["properties"].get("eo:cloud_cover", -1), 1)
        thumb = f"pass-{date}.jpg"

        prev = known.get(sid)
        if prev and "visible" in prev and os.path.exists(os.path.join(OUT_DIR, thumb)):
            passes.append(prev)
            continue

        try:
            img = read_crop(f["assets"]["visual"]["href"], HILLS_BBOX)
        except Exception as e:  # 1シーンの失敗で全体を止めない
            print(f"  {date}: 切り出し失敗のため記録をスキップ ({e})")
            continue
        if img.size == 0:
            print(f"  {date}: 範囲外のため記録をスキップ")
            continue

        vis = visible_pct(img)
        save_jpg(img, os.path.join(OUT_DIR, thumb), THUMB_WIDTH, quality=70)
        passes.append({
            "date": date,
            "scene_id": sid,
            "cloud": cloud,
            "visible": vis,
            "thumb": thumb,
        })
        print(f"  {date}: 雲量{cloud}% 地表可視{vis}% -> {thumb}")
    return passes


def adopt_scene(clear, meta):
    """晴れたシーンを掲載画像として書き出し、meta を更新する"""
    date = clear["properties"]["datetime"][:10]
    cloud = round(clear["properties"].get("eo:cloud_cover", -1), 1)
    print(f"新しい晴れシーン: {clear['id']} 撮影日={date} 雲量={cloud}%")
    with rasterio.open(clear["assets"]["visual"]["href"]) as src:
        rendered = {}
        for name, (bbox, max_w) in CROPS.items():
            w, s, e, n = bbox
            b = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
            img = np.transpose(src.read(window=from_bounds(*b, src.transform)), (1, 2, 0))
            if img.size == 0 or img.mean() < 12:
                print(f"  {name}: 画像が空/暗すぎ(範囲外の可能性)。差し替えを中止。")
                return False
            rendered[name] = img
        for name, img in rendered.items():
            _, max_w = CROPS[name]
            print(f"  {name}: {save_jpg(img, os.path.join(OUT_DIR, name), max_w)}")
    meta.update({
        "date": date,
        "cloud_cover": cloud,
        "scene_id": clear["id"],
        "updated_at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return True


def cleanup_thumbs(passes):
    keep = {p["thumb"] for p in passes}
    for name in os.listdir(OUT_DIR):
        if name.startswith("pass-") and name not in keep:
            os.remove(os.path.join(OUT_DIR, name))
            print(f"  古いサムネイルを削除: {name}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    meta = load_meta()

    try:
        items = find_items()
    except Exception as e:
        print(f"シーン検索に失敗: {e}")
        return 1
    if not items:
        print(f"直近{LOOKBACK_DAYS}日に美瑛上空の通過なし。")
        return 0

    print(f"直近{LOOKBACK_DAYS}日の通過: {len(items)}回")

    # 採用判定: 雲量が基準以下で最も新しいシーン
    clear = next((f for f in items
                  if f["properties"].get("eo:cloud_cover", 100) <= MAX_CLOUD), None)

    if clear is None:
        print(f"直近{LOOKBACK_DAYS}日に雲量{MAX_CLOUD}%以下のシーンなし。画像は据え置き。")
    elif clear["id"] == meta.get("scene_id"):
        print(f"掲載中のシーン({clear['id']})が最新の晴れ。画像は据え置き。")
    else:
        adopt_scene(clear, meta)

    passes = build_history(items, meta.get("passes", []), must_include=meta.get("scene_id"))

    for p in passes:
        p["used"] = (p["scene_id"] == meta.get("scene_id"))
    meta["passes"] = passes
    meta["checked_at"] = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    cleanup_thumbs(passes)
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("satellite.json 更新完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
