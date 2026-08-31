# -*- coding: utf-8 -*-
"""
その朝の予報を丸ごと残すスクリプト(GitHub Actions で毎日実行)

観測台帳(data/observations.csv)はスコアと実際の見え方を突き合わせて係数を
直すためのものだが、そこに書くスコアは「その日サイトが実際に出していた値」で
なければ意味がない。ところが Open-Meteo のアーカイブAPIが返すのは実測・再解析値
であって、「8/30の朝に出ていた予報」は後からでは永久に取れない。

そこで毎朝、サイトと同じ問い合わせをして hourly をそのまま1行ずつ貯める。

スコアそのものではなく「入力」を残すのが肝心なところ:
  - スコアの計算式は app.js の1箇所にあるままでいい(Python へ移植するとズレる)
  - 「直近48時間の雨」のような窓の取り方を後で見直しても、全期間を計算し直せる
    (7/16の深夜の雨が窓から漏れた件がまさにこれ)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT, "data", "forecast-log.jsonl")

# 日本には夏時間が無く UTC+9 で固定なので、zoneinfo(Windowsでは tzdata が要る)は使わない
JST = timezone(timedelta(hours=9))

# app.js と同じ地点・同じ変数。ここがズレると記録の意味がなくなる
LAT, LON = 43.489, 142.621
HOURLY_VARS = ("cloud_cover", "precipitation", "wind_speed_10m",
               "relative_humidity_2m", "temperature_2m", "dew_point_2m")

# 記録する範囲。対象日Dの池スコアは D-2 からの雨を見るので、そこまでさかのぼる。
# 後ろは翌朝の霧(D+1の明け方)まで届くように1日ぶん多めに取る。
DAYS_BEFORE = 2
DAYS_AFTER = 1

RETRY = 3
TIMEOUT = 60
UA = {"User-Agent": "biei-daily (+https://easyautomate2024-cell.github.io/biei-daily/)"}


def fetch_json(url):
    """一時的な通信エラーは数回まで待って試し直す"""
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            if attempt < RETRY - 1:
                wait = 5 * (attempt + 1)
                print(f"  取得に失敗({e})。{wait}秒待って再試行")
                time.sleep(wait)
    raise last


def already_logged(date_str):
    """同じ日の行が既にあるか。手動実行で朝の記録を上書きしないため"""
    if not os.path.exists(LOG_PATH):
        return False
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("date") == date_str:
                    return True
            except ValueError:
                continue   # 壊れた行は無視して続ける
    return False


def build_record(today, data):
    """hourly から必要な範囲だけ切り出す。
    時刻文字列を1件ずつ持つと嵩むので、開始時刻と本数だけ持って値は並びで残す。"""
    hourly = data["hourly"]
    start = (today - timedelta(days=DAYS_BEFORE)).strftime("%Y-%m-%dT00:00")
    end = (today + timedelta(days=DAYS_AFTER)).strftime("%Y-%m-%dT23:00")

    idx = [i for i, t in enumerate(hourly["time"]) if start <= t <= end]
    if not idx:
        raise ValueError(f"必要な時間帯({start}〜{end})が応答に含まれていない")

    # 欠けがあると「並びで残す」前提が崩れるので、連続していることを確かめる
    expected = (DAYS_BEFORE + DAYS_AFTER + 1) * 24
    if len(idx) != expected or idx[-1] - idx[0] != len(idx) - 1:
        raise ValueError(f"時間が連続していない(期待{expected}時間・実際{len(idx)}時間)")

    rec = {
        "date": today.strftime("%Y-%m-%d"),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": hourly["time"][idx[0]],
        "hours": len(idx),
    }
    for key in HOURLY_VARS:
        rec[key] = [hourly[key][i] for i in idx]
    return rec


def main():
    today = datetime.now(JST).date()
    date_str = today.strftime("%Y-%m-%d")

    if already_logged(date_str):
        print(f"{date_str} は記録済み。何もしない。")
        return 0

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&hourly={','.join(HOURLY_VARS)}"
        f"&past_days={DAYS_BEFORE + 1}&forecast_days={DAYS_AFTER + 2}"
        "&timezone=Asia%2FTokyo"
    )
    try:
        data = fetch_json(url)
        rec = build_record(today, data)
    except Exception as e:
        print(f"予報の取得に失敗: {e}")
        return 1

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    rain = sum(v for v in rec["precipitation"] if v is not None)
    print(f"{date_str} の予報を記録: {rec['start']} から{rec['hours']}時間 / "
          f"期間の雨 {rain:.1f}mm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
