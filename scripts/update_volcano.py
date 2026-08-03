# -*- coding: utf-8 -*-
"""
十勝岳の火山情報を蓄積するスクリプト(GitHub Actions で毎日実行)

気象庁のXMLフィードは直近ぶんしか保持しないため、ブラウザから読むだけでは
「いつ噴火があったか」を取りこぼす。毎日フィードを見に行き、十勝岳の記録だけを
data/volcano.json に貯める。噴火警戒レベルそのものはサイト側が気象庁の
警報JSONから直接読むので、ここでは扱わない。
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(ROOT, "data", "volcano.json")

FEED = "https://www.data.jma.go.jp/developer/xml/feed/eqvol_l.xml"
VOLCANO = "十勝岳"
KEEP = 8            # 保持する記録の数
TIMEOUT = 60

# フィードから拾う情報種別(噴火の発生と、状況の解説)
WANTED = ("噴火に関する火山観測報", "火山の状況に関する解説情報",
          "噴火警報・予報", "降灰予報（速報）", "降灰予報（詳細）")

UA = {"User-Agent": "biei-daily (+https://easyautomate2024-cell.github.io/biei-daily/)"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml):
    """フィードから十勝岳の該当エントリを新しい順で返す"""
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, flags=re.S):
        title = re.search(r"<title>([^<]*)</title>", e)
        content = re.search(r"<content[^>]*>(.*?)</content>", e, flags=re.S)
        updated = re.search(r"<updated>([^<]*)</updated>", e)
        ident = re.search(r"<id>([^<]*)</id>", e)
        if not (title and content and updated and ident):
            continue
        title, body = title.group(1), clean(content.group(1))
        if VOLCANO not in body or title not in WANTED:
            continue
        out.append({
            "kind": title,
            "datetime": updated.group(1),
            "text": body,
            "source": ident.group(1),
        })
    out.sort(key=lambda x: x["datetime"], reverse=True)
    return out


def is_eruption(rec):
    """噴火が実際に起きたことを伝える記録か。
    解説情報の『噴火が発生する可能性があります』を拾わないよう、
    種別か、完了を表す言い回しでのみ判定する。"""
    if rec["kind"] == "噴火に関する火山観測報":
        return True
    return bool(re.search(r"噴火が発生(?:し(?:まし|)た|し、)", rec.get("text", "")))


def main():
    try:
        entries = parse_feed(fetch(FEED))
    except Exception as e:
        print(f"フィード取得に失敗: {e}")
        return 1
    print(f"フィード内の{VOLCANO}関連: {len(entries)}件")

    try:
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    records = meta.get("records", [])
    known = {r.get("source") for r in records}

    added = 0
    for rec in entries:
        if rec["source"] in known:
            continue
        rec["eruption"] = is_eruption(rec)
        records.append(rec)
        added += 1
        print(f"  追加: {rec['datetime'][:16]} {rec['kind']}")

    if added == 0:
        print("新しい記録なし。")

    records.sort(key=lambda x: x["datetime"], reverse=True)
    records = records[:KEEP]

    meta["records"] = records
    meta["latest_eruption"] = next((r for r in records if r.get("eruption")), None)
    meta["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"volcano.json 更新完了(保持{len(records)}件・新規{added}件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
