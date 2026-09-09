# -*- coding: utf-8 -*-
"""承認された投稿案を Instagram に公開する。

ig_weekly_draft.py が起票した Issue にユーザーが「OK」とコメントすると、
GitHub Actions 経由でこのスクリプトが動く。Issue本文から素材と文章を読み、
Instagram API(Instagramログイン方式・graph.instagram.com)で公開する。
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

GRAPH = "https://graph.instagram.com/v23.0"


def gh_api(path, data=None, method=None):
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json"},
        method=method or ("POST" if data is not None else "GET"),
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def ig_api(path, params, method="GET"):
    params = dict(params, access_token=os.environ["IG_ACCESS_TOKEN"])
    qs = urllib.parse.urlencode(params)
    if method == "GET":
        req = urllib.request.Request(f"{GRAPH}{path}?{qs}")
    else:
        req = urllib.request.Request(f"{GRAPH}{path}", data=qs.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def parse_issue(body):
    mtype = re.search(r"\*\*種類\*\*:\s*(\w+)", body)
    murl = re.search(r"\*\*素材URL\*\*:\s*(\S+)", body)
    cap = re.search(r"--- 本文ここから ---\s*\n(.*?)\n--- 本文ここまで ---", body, re.S)
    if not (mtype and murl and cap):
        raise ValueError("Issue本文から 種類/素材URL/本文 を読み取れません")
    return mtype.group(1).upper(), murl.group(1), cap.group(1).strip()


def main():
    issue_no = os.environ["ISSUE_NUMBER"]
    # 本文はイベント時点ではなく、いま現在のものを読む(直前の編集を反映するため)
    issue = gh_api(f"/issues/{issue_no}")
    if issue.get("state") != "open":
        print("Issueがクローズ済みのため何もしません")
        return 0
    media_type, media_url, caption = parse_issue(issue["body"])

    me = ig_api("/me", {"fields": "user_id,username"})
    uid = me.get("user_id") or me.get("id")
    print(f"アカウント: @{me.get('username')} ({uid})")

    params = {"caption": caption}
    if media_type == "REELS":
        params.update(media_type="REELS", video_url=media_url)
    else:
        params.update(image_url=media_url)
    creation = ig_api(f"/{uid}/media", params, method="POST")
    cid = creation["id"]

    # 動画は処理待ちが必要。写真も稀にかかるので同じ扱いにする
    for _ in range(30):
        st = ig_api(f"/{cid}", {"fields": "status_code"})
        if st.get("status_code") in (None, "FINISHED"):
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"メディア処理に失敗: {st}")
        time.sleep(10)

    result = ig_api(f"/{uid}/media_publish", {"creation_id": cid}, method="POST")
    media_id = result.get("id", "?")
    print(f"投稿しました: media_id={media_id}")

    link = ""
    try:
        info = ig_api(f"/{media_id}", {"fields": "permalink"})
        link = info.get("permalink", "")
    except Exception:
        pass

    gh_api(f"/issues/{issue_no}/comments",
           {"body": f"✅ 投稿しました。{link}"})
    gh_api(f"/issues/{issue_no}", {"state": "closed"}, method="PATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
