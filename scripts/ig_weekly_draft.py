# -*- coding: utf-8 -*-
"""IG週次投稿の「案」を GitHub Issue として起票する(毎週日曜朝に自動実行)

いきなり投稿はしない。素材と本文をIssueにまとめ、ユーザーが
「OK」とコメントしたときだけ ig_publish.py が実際に投稿する(承認モード)。

素材の選び方:
- 今週あたらしい晴れの衛星画像が採用されていれば、それを写真投稿
- 晴れがなかった週は、直近の通過サムネイルで「雲の週」を正直に報告
"""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://easyautomate2024-cell.github.io/biei-daily"

CAPTION_TAIL = (
    "\n\nサイト「今日の美瑛」で毎日更新中。\n"
    "👉 プロフィールのリンクから\n\n"
    "#美瑛 #衛星画像 #パッチワークの丘 #北海道 #今日の美瑛"
)


def jst_now():
    return datetime.now(timezone(timedelta(hours=9)))


def md(s):
    return f"{int(s[5:7])}/{int(s[8:10])}"


def load(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def build_draft():
    sat = load("satellite.json")
    now = datetime.now(timezone.utc)
    updated = datetime.strptime(sat["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    passes = sat.get("passes", [])
    week = [p for p in passes
            if (now - datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days <= 7]
    clear = [p for p in week if p.get("visible", 0) >= 90]

    if (now - updated).days <= 8:
        # 今週あたらしい晴れがあった
        d = sat["date"]
        change = f"今週の通過{len(week)}回のうち、晴れは{len(clear)}回でした。" if week else ""
        caption = (
            f"🛰️ 今週の美瑛({md(d)}撮影)\n\n"
            f"衛星がとらえた、いちばん新しい晴れの丘です。\n"
            f"{change}"
            f"{CAPTION_TAIL}"
        )
        return {
            "media_type": "IMAGE",
            "media_url": f"{SITE}/photos/sat/sat-hills.jpg?d={d}",
            "caption": caption,
            "note": f"素材: 掲載中の衛星画像(撮影 {d}・広域雲量{sat.get('cloud_cover')}%)",
        }

    # 晴れがなかった週
    latest = passes[0] if passes else None
    if latest is None:
        return None
    caption = (
        f"🛰️ 今週の美瑛は、ずっと雲の下でした。\n\n"
        f"直近の通過({md(latest['date'])})でも、地表が見えたのは"
        f"{latest.get('visible', 0):.0f}%。\n"
        f"衛星は5日おきに通っています。次の晴れをお楽しみに。"
        f"{CAPTION_TAIL}"
    )
    return {
        "media_type": "IMAGE",
        "media_url": f"{SITE}/photos/sat/{latest['thumb']}",
        "caption": caption,
        "note": f"素材: 雲の週の通過サムネイル({latest['date']})",
    }


def create_issue(draft):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    today = jst_now().strftime("%Y-%m-%d")
    body = f"""<!-- ig-post-draft -->
{draft['note']}

**種類**: {draft['media_type']}
**素材URL**: {draft['media_url']}

--- 本文ここから ---
{draft['caption']}
--- 本文ここまで ---

---
- 投稿してよければ **「OK」とコメント** してください(投稿されます)
- 本文を直したいときは、上の本文欄を**直接編集**してから「OK」
- 今週は休載にするなら、このIssueを**クローズ**してください
"""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": f"[IG投稿案] {today}", "body": body}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        num = json.loads(r.read())["number"]
    print(f"投稿案を Issue #{num} として起票しました")


def main():
    draft = build_draft()
    if draft is None:
        print("素材が見つからないため起票しません")
        return 0
    create_issue(draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
