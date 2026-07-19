# -*- coding: utf-8 -*-
"""
衛星画像の自動更新スクリプト(GitHub Actions で毎日実行)

Sentinel-2 の公開データ(AWS Open Data / Element84 STAC)から、直近で最も新しい
「晴れた」美瑛のシーンを探し、10m解像度の切り出し画像を生成する。
新しい画像が無い日は何もせず終了する(サイトは前回の画像を表示し続ける)。
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
MAX_CLOUD = 35     # シーン全体の雲量がこれ以下なら採用
LOOKBACK_DAYS = 14

# 切り出し範囲 (西, 南, 東, 北) と最大幅px
CROPS = {
    "sat-hills.jpg": ((142.42, 43.53, 142.50, 43.60), 1000),   # 定番: 丘と市街
    "sat-wide.jpg": ((142.36, 43.47, 142.58, 43.64), 1400),    # 広域: 美瑛全域
}


def find_scene():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = (
        f"{STAC}?bbox={SEARCH_BBOX}"
        f"&datetime={start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}"
        "&limit=30"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        items = json.load(r)["features"]
    ok = [f for f in items if f["properties"].get("eo:cloud_cover", 100) <= MAX_CLOUD]
    if not ok:
        return None
    ok.sort(key=lambda f: f["properties"]["datetime"], reverse=True)
    return ok[0]


def main():
    scene = find_scene()
    if scene is None:
        print(f"直近{LOOKBACK_DAYS}日に雲量{MAX_CLOUD}%以下のシーンなし。更新スキップ。")
        return 0

    date = scene["properties"]["datetime"][:10]
    cloud = round(scene["properties"].get("eo:cloud_cover", -1), 1)
    scene_id = scene["id"]

    # 既に同じシーンを掲載済みなら何もしない
    try:
        with open(META_PATH, encoding="utf-8") as f:
            if json.load(f).get("scene_id") == scene_id:
                print(f"掲載済みシーン({scene_id})が最新。更新スキップ。")
                return 0
    except (OSError, ValueError):
        pass

    visual = scene["assets"]["visual"]["href"]
    print(f"新シーン: {scene_id} 撮影日={date} 雲量={cloud}%")

    os.makedirs(OUT_DIR, exist_ok=True)
    with rasterio.open(visual) as src:
        for name, ((w, s, e, n), max_w) in CROPS.items():
            b = transform_bounds("EPSG:4326", src.crs, w, s, e, n)
            win = from_bounds(*b, src.transform)
            data = src.read(window=win)
            img = np.transpose(data, (1, 2, 0))
            if img.size == 0 or img.mean() < 12:
                print(f"  {name}: 画像が空/暗すぎ(範囲外の可能性)。全体を中止。")
                return 0
            im = Image.fromarray(img)
            if im.width > max_w:
                im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
            im.save(os.path.join(OUT_DIR, name), quality=82, optimize=True)
            print(f"  {name}: {im.size}")

    meta = {
        "date": date,
        "cloud_cover": cloud,
        "scene_id": scene_id,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("satellite.json 更新完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
