#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从上游仓库同步 version.json。

上游：DJB-Developer/wechat-android-history-versions

同步失败（网络问题、上游改结构、返回内容异常）时保留本地现有数据并正常退出，
不让整条流水线挂掉——旧数据生成的页面依然是有效页面。

用法: python scripts/sync.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "version.json")

UPSTREAM = ("https://raw.githubusercontent.com/DJB-Developer/"
            "wechat-android-history-versions/main/version.json")

REQUIRED = {"name", "version", "publish_date", "url"}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "wechat-apk-versions-sync"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def valid(data):
    """上游必须是非空列表，且每条记录都带齐必需字段。"""
    if not isinstance(data, list) or not data:
        return False, "返回内容不是非空列表"
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            return False, "第 %d 条记录不是对象" % i
        missing = REQUIRED - set(row)
        if missing:
            return False, "第 %d 条记录缺少字段: %s" % (i, ", ".join(sorted(missing)))
    return True, ""


def load_local():
    if not os.path.exists(DEST):
        return None
    try:
        with open(DEST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf:
        try:
            reconf(encoding="utf-8")
        except (ValueError, OSError):
            pass

    local = load_local()

    try:
        text = fetch(UPSTREAM)
        remote = json.loads(text)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("同步失败（%s），保留本地 version.json" % e)
        if local is None:
            print("错误：本地也没有可用的 version.json，无法继续")
            return 1
        return 0

    ok, why = valid(remote)
    if not ok:
        print("上游数据异常（%s），保留本地 version.json" % why)
        return 0 if local is not None else 1

    if local == remote:
        print("上游无变化，共 %d 条记录" % len(remote))
        return 0

    before = len(local) if local else 0
    with open(DEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(remote, f, ensure_ascii=False, indent=2)
        f.write("\n")

    old_v = {r.get("version") for r in local} if local else set()
    new_v = sorted({r.get("version") for r in remote} - old_v, reverse=True)
    print("已更新 version.json：%d -> %d 条记录" % (before, len(remote)))
    if new_v:
        print("新增版本：%s" % ", ".join(v for v in new_v if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
