# -*- coding: utf-8 -*-
"""
青い池の状態を衛星で記録するスクリプト(GitHub Actions で毎日実行)

Sentinel-2 の晴れシーンから白金青い池の水面中心部の色を取り出し、
data/pond_color.json に蓄積する。目的はスコアの答え合わせ:
とくに「雨・雪どけ後の濁り」「結氷」という状態変化の検出。

真上からの色は地上で見る青とは別物(散乱光と空の映り込みがない)なので、
青さの絶対値は測らない。状態の分類だけを記録する。
"""

import colorsys
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(ROOT, "data", "pond_color.json")

STAC = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items"
SEARCH_BBOX = "142.610,43.490,142.620,43.496"   # 青い池周辺
POND_BOX = (142.6140, 43.4928, 142.6152, 43.4940)  # 水面の中心部(岸・木を含めない)
MAX_CLOUD = 50      # 池は小さいので広域雲量はゆるめに(白判定で最終的に弾く)
LOOKBACK_DAYS = 12

UA = {"User-Agent": "biei-daily (+https://easyautomate2024-cell.github.io/biei-daily/)"}
RETRY = 3           # 通信が切れることがあるので試す回数
RETRY_WAIT = 5      # 次の試行までの待ち秒数(回を追うごとに延ばす)


def fetch_json(url):
    """STACに問い合わせる。一時的な通信エラーは数回まで待って試し直す"""
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except Exception as e:      # 接続リセット・タイムアウト等
            last = e
            if attempt < RETRY - 1:
                wait = RETRY_WAIT * (attempt + 1)
                print(f"通信に失敗({e})。{wait}秒待って再試行します")
                time.sleep(wait)
    raise last


def classify(r, g, b, month):
    """色から池の状態を推定する。夏の白は雲なので None(=記録しない)"""
    if min(r, g, b) > 170:
        return "結氷・雪" if month in (11, 12, 1, 2, 3, 4) else None
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    deg = h * 360
    if s < 0.12:
        return "灰(濁り/影)"
    if 165 <= deg <= 260:
        return "青〜水色"
    if 140 <= deg < 165:
        return "青緑"
    if 60 <= deg < 140:
        return "緑"
    return "茶(濁り)"


def main():
    try:
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    records = meta.get("records", [])
    known = {r.get("scene_id") for r in records}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = (
        f"{STAC}?bbox={SEARCH_BBOX}"
        f"&datetime={start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}"
        "&limit=50"
    )
    try:
        items = fetch_json(url)["features"]
    except Exception as e:
        print(f"シーン検索に失敗: {e}")
        return 1

    items = [f for f in items if f["properties"].get("eo:cloud_cover", 100) <= MAX_CLOUD]
    print(f"直近{LOOKBACK_DAYS}日の候補シーン: {len(items)}件")

    added = 0
    for f in sorted(items, key=lambda x: x["properties"]["datetime"]):
        sid = f["id"]
        if sid in known:
            continue
        date = f["properties"]["datetime"][:10]
        try:
            with rasterio.open(f["assets"]["visual"]["href"]) as src:
                b = transform_bounds("EPSG:4326", src.crs, *POND_BOX)
                img = np.transpose(src.read(window=from_bounds(*b, src.transform)), (1, 2, 0)).astype(int)
        except Exception as e:
            print(f"  {date}: 読み取り失敗 ({e})")
            continue
        if img.size == 0:
            continue
        r_, g_, b_ = [int(x) for x in img.reshape(-1, 3).mean(axis=0)]
        label = classify(r_, g_, b_, int(date[5:7]))
        if label is None:
            print(f"  {date}: 池の上が雲のためスキップ")
            continue
        records.append({
            "date": date, "scene_id": sid,
            "cloud": round(f["properties"].get("eo:cloud_cover", -1), 1),
            "rgb": [r_, g_, b_], "state": label,
        })
        added += 1
        print(f"  {date}: RGB({r_},{g_},{b_}) → {label}")

    if added == 0:
        print("新しい記録なし。")
    records.sort(key=lambda x: x["date"])
    meta["records"] = records
    meta["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta.setdefault("note", "水面中心部の平均色。真上からの色は地上の見た目と別物なので状態分類のみを扱う")

    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"pond_color.json 更新完了(累計{len(records)}件・新規{added}件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
