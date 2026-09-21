#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 version.json 生成：
  1. 每个版本一个目录 + README.md   (GitHub 仓库内页面，收录 /tree/main/<版本号>/)
  2. docs/ 下的静态 HTML 站点 + sitemap.xml (GitHub Pages)
  3. 根目录 README.md 索引

页面为均衡双语：中英两段内容对等，标题同时包含两种语言的完整词组。

依据（Google Trends 全球近 12 个月相对热度）：
  微信下载 46.3 / wechat apk 12.5      —— 中文头部词更大，但被官网占据，打不动
  wechat apk download 27.3 / 微信 apk 下载 0.2  —— 「APK 下载」说法英文独占
  微信历史版本 5.6 / wechat old version 4.8     —— 「历史版本」说法中文略胜
两个真正能争的词一边一个，量级相当，因此不偏向任何一方。
版本号长尾在两种语言下都低于 Trends 分辨率，靠数量和低竞争取胜。

用法: python scripts/build.py
"""

import json
import os
import re
import shutil
import sys
from datetime import date, datetime
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "version.json")
DOCS = os.path.join(ROOT, "docs")

# 站点地址由 GitHub Actions 注入的 GITHUB_REPOSITORY 推导，改仓库名不会让 canonical 失效
_SLUG = os.environ.get("GITHUB_REPOSITORY") or "wfxu/wechat-android-apk-versions"
_OWNER, _NAME = _SLUG.split("/", 1)

SITE = "https://%s.github.io/%s" % (_OWNER.lower(), _NAME)
REPO = "https://github.com/%s" % _SLUG
PKG = "com.tencent.mm"

# 源数据里个别记录 version 字段为空（如微信 6.6 / 6.2），版本号只存在于 name 中
NAME_RE = re.compile(r"(\d+(?:\.\d+)*)\s*for\s*Android", re.I)

FNAME_RE = re.compile(
    r"weixin(?P<v>\d+)android(?P<code>\d+)"
    r"(?:_0x(?P<hex>[0-9a-fA-F]+))?"
    r"(?:_(?P<arch>arm64|arm32|armeabi[\w-]*|x86_64|x86|x64|universal))?"
    r"(?:_(?P<seq>\d+))?"
    r"\.apk$",
    re.I,
)


def vkey(v):
    """版本号排序键：8.0.78 -> (8, 0, 78, 0)"""
    parts = [int(p) for p in re.findall(r"\d+", v)]
    return tuple(parts + [0] * (4 - len(parts)))[:4]


def parse_url(url):
    """从官方 APK 文件名里解出 versionCode / 构建号 / 架构。"""
    url = url.strip()
    fname = url.rsplit("/", 1)[-1]
    m = FNAME_RE.search(fname)
    out = {"url": url, "file": fname, "code": None, "hex": None, "arch": None}
    if m:
        out["code"] = m.group("code")
        out["hex"] = ("0x" + m.group("hex").lower()) if m.group("hex") else None
        out["arch"] = (m.group("arch") or "").lower() or None
    return out


def load() -> "tuple[list, list]":
    """读取 version.json，按版本号分组并补齐排序 / 相邻版本 / 间隔天数。"""
    with open(DATA, encoding="utf-8") as f:
        raw = json.load(f)

    groups = {}
    skipped = []
    for row in raw:
        ver = (row.get("version") or "").strip()
        if not ver:
            m = NAME_RE.search(row.get("name") or "")
            ver = m.group(1) if m else ""
        if not re.fullmatch(r"\d+(?:\.\d+)*", ver):
            skipped.append(row.get("url", "?"))
            continue
        g = groups.setdefault(
            ver, {"version": ver, "date": row["publish_date"], "builds": []}
        )
        # 同一版本多条记录时，保留最早的发布日期
        if row["publish_date"] < g["date"]:
            g["date"] = row["publish_date"]
        b = parse_url(row["url"])
        if b["url"] not in {x["url"] for x in g["builds"]}:
            g["builds"].append(b)

    vers = sorted(groups.values(), key=lambda g: vkey(g["version"]), reverse=True)

    total = len(vers)
    for i, g in enumerate(vers):
        g["rank"] = i + 1                      # 1 = 最新
        g["newer"] = vers[i - 1] if i > 0 else None
        g["older"] = vers[i + 1] if i + 1 < total else None
        g["slug"] = g["version"]
        g["codes"] = sorted({b["code"] for b in g["builds"] if b["code"]})
        g["archs"] = sorted({b["arch"] for b in g["builds"] if b["arch"]})

    # 与上一版本的间隔天数
    for g in vers:
        g["gap"] = None
        if g["older"]:
            try:
                d1 = datetime.strptime(g["date"], "%Y-%m-%d").date()
                d0 = datetime.strptime(g["older"]["date"], "%Y-%m-%d").date()
                g["gap"] = (d1 - d0).days
            except ValueError:
                pass
    return vers, skipped


# ---------------------------------------------------------------- 文案片段

ARCH_EN = {"arm64": "arm64 (64-bit ARM)", "arm32": "arm32 (32-bit ARM)",
           "x86": "x86", "x86_64": "x86_64", "x64": "x64"}
ARCH_ZH = {"arm64": "arm64（64 位 ARM）", "arm32": "arm32（32 位 ARM）",
           "x86": "x86", "x86_64": "x86_64", "x64": "x64"}


def archs_of(g):
    return [str(a) for a in g["archs"] if a]


def arch_en(g):
    a = archs_of(g)
    return ", ".join(ARCH_EN.get(x, x) for x in a) if a else "universal (no ABI split)"


def arch_zh(g):
    a = archs_of(g)
    return "、".join(ARCH_ZH.get(x, x) for x in a) if a else "通用包（未区分 CPU 架构）"


def codes_of(g):
    return "、".join(g["codes"]) if g["codes"] else "-"


def summary_zh(g):
    """只陈述能从数据推出的事实，不编造更新日志。"""
    bits = [
        "微信（WeChat）Android %s 于 %s 发布" % (g["version"], g["date"]),
        "本页收录该版本 %d 个腾讯官方下载地址" % len(g["builds"]),
    ]
    if g["codes"]:
        bits.append("内部版本号（versionCode）为 %s" % "、".join(g["codes"]))
    bits.append("安装包架构为 %s" % arch_zh(g))
    if g["gap"] is not None and g["older"]:
        bits.append("距上一个版本 %s 相隔 %d 天" % (g["older"]["version"], g["gap"]))
    return "，".join(bits) + "。"


def summary_en(g):
    bits = [
        "WeChat (Weixin) for Android %s was released on %s" % (g["version"], g["date"]),
        "this page lists %d official Tencent download link%s"
        % (len(g["builds"]), "" if len(g["builds"]) == 1 else "s"),
    ]
    if g["codes"]:
        bits.append("versionCode %s" % ", ".join(g["codes"]))
    bits.append("built for %s" % arch_en(g))
    if g["gap"] is not None and g["older"]:
        bits.append("shipped %d days after WeChat %s" % (g["gap"], g["older"]["version"]))
    return ", ".join(bits) + "."


def meta_desc(g):
    """中英各含一个完整词组，两种查询都能命中。"""
    d = ("微信 %s 安卓版官方 APK 下载地址，发布于 %s，versionCode %s，%s，共 %d 个官方链接。"
         " Download WeChat %s APK for Android, released %s, %d official Tencent CDN links."
         % (g["version"], g["date"], codes_of(g), arch_zh(g), len(g["builds"]),
            g["version"], g["date"], len(g["builds"])))
    return d[:320]


# ---------------------------------------------------------------- Markdown

def version_md(g, total):
    v = g["version"]
    L = []
    A = L.append
    A("# 微信 %s 安卓版 APK 下载 | WeChat %s APK Download for Android" % (v, v))
    A("")
    A("> %s" % summary_zh(g))
    A("")
    A("> %s" % summary_en(g))
    A("")

    # ------------------------------ 中文 ------------------------------
    A("## 微信 %s 下载地址" % v)
    A("")
    A("| # | 架构 | versionCode | 构建号 | 安装包文件名 | 下载 |")
    A("| :-- | :-- | :-- | :-- | :-- | :-- |")
    for i, b in enumerate(g["builds"], 1):
        A("| %d | %s | %s | %s | `%s` | [直接下载](%s) |" % (
            i, b["arch"] or "通用", b["code"] or "-", b["hex"] or "-",
            b["file"], b["url"]))
    A("")
    A("所有链接均指向腾讯官方域名（`dldir1.qq.com` / `dldir1v6.qq.com`），"
      "本仓库不做任何二次打包或转存。")
    A("")
    A("## 微信 %s 版本信息" % v)
    A("")
    A("| 项目 | 内容 |")
    A("| :-- | :-- |")
    A("| 软件名称 | 微信 WeChat |")
    A("| 版本号 | **%s** |" % v)
    A("| 平台 | Android |")
    A("| 发布日期 | %s |" % g["date"])
    A("| versionCode | %s |" % codes_of(g))
    A("| 应用包名 | `%s` |" % PKG)
    A("| CPU 架构 | %s |" % arch_zh(g))
    A("| 官方下载数 | %d |" % len(g["builds"]))
    A("| 版本序号 | %d 个历史版本中的第 %d 新 |" % (total, g["rank"]))
    if g["gap"] is not None:
        A("| 距上一版本 | %d 天 |" % g["gap"])
    A("")
    A("## 相邻版本")
    A("")
    if g["newer"]:
        A("- 更新版本：[微信 %s 安卓版下载](../%s/)（%s）"
          % (g["newer"]["version"], g["newer"]["slug"], g["newer"]["date"]))
    else:
        A("- 更新版本：无，微信 %s 是目前收录的最新安卓版本。" % v)
    if g["older"]:
        A("- 更早版本：[微信 %s 安卓版下载](../%s/)（%s）"
          % (g["older"]["version"], g["older"]["slug"], g["older"]["date"]))
    else:
        A("- 更早版本：无，微信 %s 是本仓库收录的最早安卓版本。" % v)
    A("")
    A("## 关于微信 %s" % v)
    A("")
    A("微信 %s 是腾讯于 %s 推出的 Android 客户端版本，在本仓库收录的 %d 个历史版本中"
      "排在第 %d 位。" % (v, g["date"], total, g["rank"]))
    if g["gap"] is not None and g["older"]:
        A("它与前一版本微信 %s（%s）相隔 %d 天。"
          % (g["older"]["version"], g["older"]["date"], g["gap"]))
    A("需要该版本的用户通常是为了适配旧机型、回退新版改动，或用于兼容性测试。"
      "各版本的功能更新说明请以 [微信官方更新日志](https://weixin.qq.com/updates) 为准。")
    A("")
    A("## 安装说明")
    A("")
    A("1. 点击上表中的链接下载 `%s`。" % g["builds"][0]["file"])
    A("2. 在系统设置中允许「安装未知来源应用」。")
    A("3. 若设备已安装更高版本的微信，需先卸载再安装微信 %s；"
      "Android 不允许降级覆盖安装。" % v)
    A("4. 卸载会清除本地聊天记录，降级前请先在微信内完成聊天记录备份。")
    A("")
    A("## 常见问题")
    A("")
    A("**微信 %s 是什么时候发布的？** %s。" % (v, g["date"]))
    A("")
    A("**微信 %s 的安装包能直接从官网下载吗？** 可以，上表的地址即为腾讯官方 CDN 地址，"
      "未经任何第三方修改。" % v)
    A("")
    A("**可以从新版微信降级到 %s 吗？** 可以，但必须先卸载当前版本，"
      "且卸载后未备份的本地聊天记录将无法恢复。" % v)
    A("")
    A("---")
    A("")

    # ------------------------------ English ------------------------------
    A("## Download WeChat %s APK" % v)
    A("")
    A("| # | ABI | versionCode | Build | APK file name | Download |")
    A("| :-- | :-- | :-- | :-- | :-- | :-- |")
    for i, b in enumerate(g["builds"], 1):
        A("| %d | %s | %s | %s | `%s` | [Download APK](%s) |" % (
            i, b["arch"] or "universal", b["code"] or "-", b["hex"] or "-",
            b["file"], b["url"]))
    A("")
    A("All links point to Tencent's official CDN (`dldir1.qq.com` / "
      "`dldir1v6.qq.com`). Nothing is re-hosted, repacked or modified here.")
    A("")
    A("## WeChat %s version information" % v)
    A("")
    A("| Field | Value |")
    A("| :-- | :-- |")
    A("| Application | WeChat (Weixin) |")
    A("| Version | **%s** |" % v)
    A("| Platform | Android |")
    A("| Release date | %s |" % g["date"])
    A("| versionCode | %s |" % codes_of(g))
    A("| Package name | `%s` |" % PKG)
    A("| ABI | %s |" % arch_en(g))
    A("| Official downloads | %d |" % len(g["builds"]))
    A("| Position in history | #%d of %d archived versions |" % (g["rank"], total))
    if g["gap"] is not None:
        A("| Days since previous release | %d |" % g["gap"])
    A("")
    A("## Nearby versions")
    A("")
    if g["newer"]:
        A("- Newer: [Download WeChat %s APK](../%s/) (%s)"
          % (g["newer"]["version"], g["newer"]["slug"], g["newer"]["date"]))
    else:
        A("- Newer: none — WeChat %s is the latest archived Android version." % v)
    if g["older"]:
        A("- Older: [Download WeChat %s APK](../%s/) (%s)"
          % (g["older"]["version"], g["older"]["slug"], g["older"]["date"]))
    else:
        A("- Older: none — WeChat %s is the oldest archived Android version." % v)
    A("")
    A("## About WeChat %s" % v)
    A("")
    A("WeChat %s is the Android client Tencent released on %s. It is #%d of the %d "
      "versions archived here." % (v, g["date"], g["rank"], total))
    if g["gap"] is not None and g["older"]:
        A("It arrived %d days after WeChat %s (%s)."
          % (g["gap"], g["older"]["version"], g["older"]["date"]))
    A("People usually look for this specific old version to support older devices, "
      "to roll back a change introduced in a newer release, or for compatibility "
      "testing. For the feature changelog, refer to "
      "[Tencent's official release notes](https://weixin.qq.com/updates).")
    A("")
    A("## How to install WeChat %s APK" % v)
    A("")
    A("1. Download `%s` from the table above." % g["builds"][0]["file"])
    A("2. Allow installation from unknown sources in Android settings.")
    A("3. If a newer WeChat build is already installed, uninstall it first — "
      "Android does not allow downgrading over an existing install.")
    A("4. Uninstalling clears local chat history. Back it up inside WeChat before "
      "downgrading to %s." % v)
    A("")
    A("## FAQ")
    A("")
    A("**When was WeChat %s released?** %s." % (v, g["date"]))
    A("")
    A("**Is this WeChat %s APK official?** Yes. Every link above resolves to "
      "Tencent's own CDN and the APK is unmodified." % v)
    A("")
    A("**Can I downgrade from a newer WeChat to %s?** Yes, but you must uninstall "
      "the current version first, and any local chat history that was not backed up "
      "will be lost." % v)
    A("")
    A("---")
    A("")
    A("[← 返回全部 %d 个版本 / All %d WeChat Android versions](../)　|　"
      "[在线浏览 / View online](%s/%s/)" % (total, total, SITE, g["slug"]))
    A("")
    return "\n".join(L)


def index_md(vers):
    total = len(vers)
    newest, oldest = vers[0], vers[-1]
    n_dl = sum(len(g["builds"]) for g in vers)
    L = []
    A = L.append
    A("# 微信安卓版历史版本 APK 下载大全 | WeChat for Android APK Archive")
    A("")
    A("收录 **%d 个微信 Android 历史版本**、共 %d 个腾讯官方下载地址，"
      "版本跨度 %s（微信 %s）至 %s（微信 %s）。**每个版本都有独立页面**，"
      "点击版本号即可查看该版本的下载地址、versionCode、发布日期与安装说明。"
      % (total, n_dl, oldest["date"], oldest["version"],
         newest["date"], newest["version"]))
    A("")
    A("**%d archived WeChat Android versions**, %d official Tencent download links, "
      "spanning WeChat %s (%s) to WeChat %s (%s). Every version has its own page "
      "with download links, versionCode, release date and install notes."
      % (total, n_dl, oldest["version"], oldest["date"],
         newest["version"], newest["date"]))
    A("")
    A("🌐 在线版 / Browse online：<%s/>" % SITE)
    A("")
    A("## 最新版本 / Latest version")
    A("")
    A("当前最新为 **[微信 %s](%s/)**，发布于 %s。"
      % (newest["version"], newest["slug"], newest["date"]))
    A("The newest archived build is **[WeChat %s](%s/)**, released %s."
      % (newest["version"], newest["slug"], newest["date"]))
    A("")
    A("## 全部版本 / All versions")
    A("")
    A("| 版本号 Version | 发布日期 Release date | versionCode | 架构 ABI | "
      "下载数 Links | 独立页面 Page |")
    A("| :-- | :-- | :-- | :-- | :-- | :-- |")
    for g in vers:
        A("| 微信 %s / WeChat %s | %s | %s | %s | %d | "
          "[微信 %s 下载 / Download WeChat %s APK](%s/) |" % (
              g["version"], g["version"], g["date"], codes_of(g),
              "、".join(archs_of(g)) if archs_of(g) else "通用 universal",
              len(g["builds"]), g["version"], g["version"], g["slug"]))
    A("")
    A("## 数据来源与声明 / Source and disclaimer")
    A("")
    A("- 全部下载地址来自腾讯官方域名 `dldir1.qq.com` / `dldir1v6.qq.com`，"
      "本仓库不转存、不二次打包、不修改任何安装包。"
      "Every download link points to Tencent's official CDN; no APK is re-hosted, "
      "repacked or modified.")
    A("- 原始数据整理自 "
      "[DJB-Developer/wechat-android-history-versions]"
      "(https://github.com/DJB-Developer/wechat-android-history-versions)，"
      "本仓库在其基础上把每个版本拆分为独立页面，并生成静态站点与 sitemap。")
    A("- 版本更新日志请以 [微信官方更新日志](https://weixin.qq.com/updates) 为准 / "
      "refer to Tencent's official release notes for the changelog.")
    A("- 微信、WeChat 为腾讯公司商标，本仓库与腾讯公司无隶属关系。"
      "WeChat and 微信 are trademarks of Tencent; this project is not affiliated "
      "with Tencent.")
    A("")
    A("## 相关项目 / Related projects")
    A("")
    A("- [微信 Windows 历史版本](https://github.com/tom-snow/wechat-windows-versions)")
    A("- [微信 Mac 历史版本](https://github.com/zsbai/wechat-versions)")
    A("")
    A("## 本仓库结构 / Repository layout")
    A("")
    A("```")
    A("version.json                原始数据，唯一数据源 / source of truth")
    A("scripts/sync.py             每日同步上游 / pulls upstream data daily")
    A("scripts/build.py            生成器 / generator")
    A("%s/README.md%s独立版本页 / one page per version" % (newest["slug"], " " * 14))
    A("%s/README.md" % vers[1]["slug"])
    A("...                         共 %d 个版本目录 / version directories" % total)
    A("docs/                       GitHub Pages 静态站 + sitemap.xml")
    A("```")
    A("")
    A("修改 `version.json` 后由 GitHub Actions 自动重新生成全部页面，无需手工维护。")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------- HTML

CSS = """*{box-sizing:border-box}
body{margin:0;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:#1f2328;background:#fff}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 64px}
header.site{border-bottom:1px solid #d1d9e0;background:#f6f8fa}
header.site .wrap{padding:14px 16px}
header.site a{color:#0969da;text-decoration:none;font-weight:600}
h1{font-size:1.7rem;line-height:1.35;margin:.6em 0 .3em}
h2{font-size:1.25rem;margin:1.8em 0 .6em;padding-bottom:.3em;border-bottom:1px solid #d1d9e0}
p,li{color:#1f2328}
a{color:#0969da}
.lead{background:#f6f8fa;border-left:4px solid #0969da;padding:12px 16px;border-radius:0 6px 6px 0;margin:1em 0}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.92rem;display:block;overflow-x:auto}
th,td{border:1px solid #d1d9e0;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f6f8fa;white-space:nowrap}
code{background:#eff1f3;padding:.15em .4em;border-radius:4px;font-size:.88em;word-break:break-all}
.dl{display:inline-block;background:#1f883d;color:#fff;padding:5px 12px;border-radius:6px;text-decoration:none;font-size:.88rem;white-space:nowrap}
.dl:hover{background:#1a7f37}
nav.adj{display:flex;gap:12px;flex-wrap:wrap;margin:1.5em 0}
nav.adj a{flex:1 1 240px;border:1px solid #d1d9e0;border-radius:6px;padding:10px 14px;text-decoration:none;background:#fff}
nav.adj span{display:block;font-size:.78rem;color:#59636e}
.langbar{display:flex;gap:10px;margin:1.2em 0 0}
.langbar a{font-size:.85rem;border:1px solid #d1d9e0;border-radius:999px;padding:3px 12px;text-decoration:none;background:#f6f8fa}
section.alt{margin-top:2.5em;padding-top:.5em;border-top:3px double #d1d9e0}
footer{margin-top:3em;padding-top:1.2em;border-top:1px solid #d1d9e0;font-size:.85rem;color:#59636e}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:1em 0}
.grid a{border:1px solid #d1d9e0;border-radius:6px;padding:8px 10px;text-decoration:none;background:#fff}
.grid a b{display:block;color:#0969da}
.grid a span{font-size:.78rem;color:#59636e}
@media (prefers-color-scheme:dark){
body{background:#0d1117;color:#e6edf3}
header.site{background:#161b22;border-color:#30363d}
h2{border-color:#30363d}
p,li,td{color:#e6edf3}
.lead{background:#161b22}
th,td,nav.adj a,.grid a,section.alt,.langbar a{border-color:#30363d}
th,.langbar a{background:#161b22}
code{background:#161b22}
nav.adj a,.grid a{background:#0d1117}
footer{border-color:#30363d;color:#8d96a0}}
"""


def head(title, desc, canonical, extra=""):
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<link rel="canonical" href="%s">
<meta property="og:type" content="website">
<meta property="og:title" content="%s">
<meta property="og:description" content="%s">
<meta property="og:url" content="%s">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="stylesheet" href="%s/assets/style.css">
%s</head>
<body>
<header class="site"><div class="wrap"><a href="%s/">微信安卓历史版本 APK 下载 · WeChat for Android APK Archive</a></div></header>
<div class="wrap">
""" % (escape(title), escape(desc), canonical, escape(title), escape(desc),
       canonical, SITE, extra, SITE)


FOOT = """</div>
<footer><div class="wrap">
下载地址均来自腾讯官方 CDN，本站不转存、不修改安装包。微信、WeChat 为腾讯公司商标，本站与腾讯公司无隶属关系。<br>
All download links point to Tencent's official CDN; no APK is re-hosted or modified.
WeChat and 微信 are trademarks of Tencent; this project is not affiliated with Tencent.
· <a href="%s">GitHub</a>
</div></footer>
</body>
</html>
""" % REPO


def dl_table_html(g, lang):
    """lang='zh' | 'en'"""
    hdr = (["#", "架构", "versionCode", "构建号", "安装包文件名", "下载"]
           if lang == "zh" else
           ["#", "ABI", "versionCode", "Build", "APK file name", "Download"])
    uni = "通用" if lang == "zh" else "universal"
    btn = "下载 APK" if lang == "zh" else "Download APK"
    H = ["<table><thead><tr>%s</tr></thead><tbody>"
         % "".join("<th>%s</th>" % escape(h) for h in hdr)]
    for i, b in enumerate(g["builds"], 1):
        H.append("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
                 "<td><code>%s</code></td>"
                 '<td><a class="dl" href="%s" rel="nofollow">%s</a></td></tr>' % (
                     i, escape(b["arch"] or uni), escape(b["code"] or "-"),
                     escape(b["hex"] or "-"), escape(b["file"]), escape(b["url"]),
                     escape(btn)))
    H.append("</tbody></table>")
    return "\n".join(H)


def info_table_html(g, total, lang):
    if lang == "zh":
        rows = [
            ("软件名称", "微信 WeChat"),
            ("版本号", g["version"]),
            ("平台", "Android"),
            ("发布日期", g["date"]),
            ("versionCode", codes_of(g)),
            ("应用包名", PKG),
            ("CPU 架构", arch_zh(g)),
            ("官方下载数", str(len(g["builds"]))),
            ("版本序号", "%d 个历史版本中的第 %d 新" % (total, g["rank"])),
        ]
        if g["gap"] is not None:
            rows.append(("距上一版本", "%d 天" % g["gap"]))
    else:
        rows = [
            ("Application", "WeChat (Weixin)"),
            ("Version", g["version"]),
            ("Platform", "Android"),
            ("Release date", g["date"]),
            ("versionCode", codes_of(g)),
            ("Package name", PKG),
            ("ABI", arch_en(g)),
            ("Official downloads", str(len(g["builds"]))),
            ("Position in history", "#%d of %d archived versions"
             % (g["rank"], total)),
        ]
        if g["gap"] is not None:
            rows.append(("Days since previous release", str(g["gap"])))
    return ("<table><tbody>%s</tbody></table>"
            % "".join("<tr><th>%s</th><td>%s</td></tr>" % (escape(k), escape(x))
                      for k, x in rows))


def version_html(g, total):
    v = g["version"]
    canon = "%s/%s/" % (SITE, g["slug"])
    title = ("微信 %s 安卓版 APK 下载 | WeChat %s APK Download for Android" % (v, v))

    ld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "微信 %s 安卓版" % v,
        "alternateName": "WeChat %s for Android" % v,
        "softwareVersion": v,
        "operatingSystem": "Android",
        "applicationCategory": "CommunicationApplication",
        "datePublished": g["date"],
        "url": canon,
        "downloadUrl": g["builds"][0]["url"],
        "identifier": PKG,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CNY"},
        "publisher": {"@type": "Organization", "name": "腾讯 Tencent"},
    }
    crumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1,
             "name": "微信安卓历史版本 / WeChat for Android APK Archive",
             "item": SITE + "/"},
            {"@type": "ListItem", "position": 2,
             "name": "微信 %s / WeChat %s" % (v, v), "item": canon},
        ],
    }
    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "微信 %s 是什么时候发布的？" % v,
             "acceptedAnswer": {"@type": "Answer",
                                "text": "微信 %s 安卓版于 %s 发布。" % (v, g["date"])}},
            {"@type": "Question", "name": "When was WeChat %s released?" % v,
             "acceptedAnswer": {"@type": "Answer",
                                "text": "WeChat %s for Android was released on %s."
                                        % (v, g["date"])}},
            {"@type": "Question", "name": "可以从新版微信降级到 %s 吗？" % v,
             "acceptedAnswer": {"@type": "Answer",
                                "text": "可以，但必须先卸载当前版本，"
                                        "且卸载后未备份的本地聊天记录将无法恢复。"}},
            {"@type": "Question",
             "name": "Can I downgrade from a newer WeChat to %s?" % v,
             "acceptedAnswer": {"@type": "Answer",
                                "text": "Yes, but the current version must be "
                                        "uninstalled first, and local chat history "
                                        "that was not backed up will be lost."}},
        ],
    }
    extra = ""
    for obj in (ld, crumb, faq):
        extra += ('<script type="application/ld+json">%s</script>\n'
                  % json.dumps(obj, ensure_ascii=False))
    if g["newer"]:
        extra += '<link rel="prev" href="%s/%s/">\n' % (SITE, g["newer"]["slug"])
    if g["older"]:
        extra += '<link rel="next" href="%s/%s/">\n' % (SITE, g["older"]["slug"])

    H = [head(title, meta_desc(g), canon, extra)]
    A = H.append
    A("<h1>微信 %s 安卓版 APK 下载<br>WeChat %s APK Download for Android</h1>"
      % (escape(v), escape(v)))
    A('<div class="langbar"><a href="#zh">中文</a><a href="#en">English</a></div>')

    # ---- 中文 ----
    A('<section id="zh" lang="zh-CN">')
    A('<p class="lead">%s</p>' % escape(summary_zh(g)))
    A("<h2>微信 %s 下载地址</h2>" % escape(v))
    A(dl_table_html(g, "zh"))
    A("<p>所有链接均指向腾讯官方域名 <code>dldir1.qq.com</code> / "
      "<code>dldir1v6.qq.com</code>，本站不做任何二次打包或转存。</p>")
    A("<h2>微信 %s 版本信息</h2>" % escape(v))
    A(info_table_html(g, total, "zh"))
    A("<h2>相邻版本</h2>")
    A('<nav class="adj">')
    if g["newer"]:
        A('<a href="%s/%s/"><span>更新版本</span>微信 %s（%s）</a>'
          % (SITE, g["newer"]["slug"], escape(g["newer"]["version"]),
             g["newer"]["date"]))
    if g["older"]:
        A('<a href="%s/%s/"><span>更早版本</span>微信 %s（%s）</a>'
          % (SITE, g["older"]["slug"], escape(g["older"]["version"]),
             g["older"]["date"]))
    A("</nav>")
    A("<h2>关于微信 %s</h2>" % escape(v))
    p = ("微信 %s 是腾讯于 %s 推出的 Android 客户端版本，在本站收录的 %d 个历史版本中"
         "排在第 %d 位。" % (v, g["date"], total, g["rank"]))
    if g["gap"] is not None and g["older"]:
        p += ("它与前一版本微信 %s（%s）相隔 %d 天。"
              % (g["older"]["version"], g["older"]["date"], g["gap"]))
    p += "需要该版本的用户通常是为了适配旧机型、回退新版改动，或用于兼容性测试。"
    A("<p>%s</p>" % escape(p))
    A("<h2>微信 %s 安装说明</h2>" % escape(v))
    A("<ol>")
    A("<li>点击上表中的链接下载 <code>%s</code>。</li>"
      % escape(g["builds"][0]["file"]))
    A("<li>在系统设置中允许「安装未知来源应用」。</li>")
    A("<li>若设备已安装更高版本的微信，需先卸载再安装微信 %s；"
      "Android 不允许降级覆盖安装。</li>" % escape(v))
    A("<li>卸载会清除本地聊天记录，降级前请先在微信内完成聊天记录备份。</li>")
    A("</ol>")
    A("<h2>常见问题</h2>")
    for q, a in [
        ("微信 %s 是什么时候发布的？" % v, "%s。" % g["date"]),
        ("微信 %s 的安装包能直接从官网下载吗？" % v,
         "可以，上表的地址即为腾讯官方 CDN 地址，未经任何第三方修改。"),
        ("可以从新版微信降级到 %s 吗？" % v,
         "可以，但必须先卸载当前版本，且卸载后未备份的本地聊天记录将无法恢复。"),
    ]:
        A("<p><strong>%s</strong> %s</p>" % (escape(q), escape(a)))
    A("</section>")

    # ---- English ----
    A('<section id="en" class="alt" lang="en">')
    A('<p class="lead">%s</p>' % escape(summary_en(g)))
    A("<h2>Download WeChat %s APK</h2>" % escape(v))
    A(dl_table_html(g, "en"))
    A("<p>All links point to Tencent's official CDN "
      "(<code>dldir1.qq.com</code> / <code>dldir1v6.qq.com</code>). "
      "Nothing is re-hosted, repacked or modified here.</p>")
    A("<h2>WeChat %s version information</h2>" % escape(v))
    A(info_table_html(g, total, "en"))
    A("<h2>Nearby versions</h2>")
    A('<nav class="adj">')
    if g["newer"]:
        A('<a href="%s/%s/"><span>Newer</span>WeChat %s (%s)</a>'
          % (SITE, g["newer"]["slug"], escape(g["newer"]["version"]),
             g["newer"]["date"]))
    if g["older"]:
        A('<a href="%s/%s/"><span>Older</span>WeChat %s (%s)</a>'
          % (SITE, g["older"]["slug"], escape(g["older"]["version"]),
             g["older"]["date"]))
    A("</nav>")
    A("<h2>About WeChat %s</h2>" % escape(v))
    p = ("WeChat %s is the Android client Tencent released on %s. It is #%d of the "
         "%d versions archived here." % (v, g["date"], g["rank"], total))
    if g["gap"] is not None and g["older"]:
        p += (" It arrived %d days after WeChat %s (%s)."
              % (g["gap"], g["older"]["version"], g["older"]["date"]))
    p += (" People usually look for this specific old version to support older "
          "devices, to roll back a change introduced in a newer release, or for "
          "compatibility testing.")
    A("<p>%s</p>" % escape(p))
    A("<h2>How to install WeChat %s APK</h2>" % escape(v))
    A("<ol>")
    A("<li>Download <code>%s</code> from the table above.</li>"
      % escape(g["builds"][0]["file"]))
    A("<li>Allow installation from unknown sources in Android settings.</li>")
    A("<li>If a newer WeChat build is already installed, uninstall it first — "
      "Android does not allow downgrading over an existing install.</li>")
    A("<li>Uninstalling clears local chat history. Back it up inside WeChat before "
      "downgrading to %s.</li>" % escape(v))
    A("</ol>")
    A("<h2>FAQ</h2>")
    for q, a in [
        ("When was WeChat %s released?" % v, "%s." % g["date"]),
        ("Is this WeChat %s APK official?" % v,
         "Yes. Every link above resolves to Tencent's own CDN and the APK is "
         "unmodified."),
        ("Can I downgrade from a newer WeChat to %s?" % v,
         "Yes, but you must uninstall the current version first, and any local chat "
         "history that was not backed up will be lost."),
    ]:
        A("<p><strong>%s</strong> %s</p>" % (escape(q), escape(a)))
    A("</section>")

    A('<p><a href="%s/">← 返回全部 %d 个版本 / All %d WeChat Android versions</a></p>'
      % (SITE, total, total))
    A(FOOT)
    return "\n".join(H)


def index_html(vers):
    total = len(vers)
    newest, oldest = vers[0], vers[-1]
    n_dl = sum(len(g["builds"]) for g in vers)
    canon = SITE + "/"
    title = ("微信安卓版历史版本 APK 下载大全 | WeChat for Android APK Archive "
             "(%d versions)" % total)
    desc = ("收录 %d 个微信 Android 历史版本、共 %d 个腾讯官方 APK 下载地址，"
            "每个版本均有独立页面。Download any WeChat for Android APK: "
            "%d archived versions, from WeChat %s (%s) to WeChat %s (%s)."
            % (total, n_dl, total, oldest["version"], oldest["date"],
               newest["version"], newest["date"]))

    ld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "微信安卓版历史版本 / WeChat for Android version archive",
        "numberOfItems": total,
        "itemListElement": [
            {"@type": "ListItem", "position": g["rank"],
             "name": "微信 %s 安卓版 / WeChat %s for Android"
                     % (g["version"], g["version"]),
             "url": "%s/%s/" % (SITE, g["slug"])}
            for g in vers
        ],
    }
    extra = ('<script type="application/ld+json">%s</script>\n'
             % json.dumps(ld, ensure_ascii=False))

    H = [head(title, desc, canon, extra)]
    A = H.append
    A("<h1>微信安卓版历史版本 APK 下载大全<br>WeChat for Android APK Archive</h1>")
    A('<p class="lead" lang="zh-CN">收录 <strong>%d 个</strong>微信 Android 历史版本、'
      "共 %d 个腾讯官方下载地址，版本跨度 %s（微信 %s）至 %s（微信 %s）。"
      "每个版本均有独立页面。当前最新版本："
      '<a href="%s/%s/"><strong>微信 %s</strong></a>，发布于 %s。</p>'
      % (total, n_dl, oldest["date"], escape(oldest["version"]),
         newest["date"], escape(newest["version"]),
         SITE, newest["slug"], escape(newest["version"]), newest["date"]))
    A('<p class="lead" lang="en"><strong>%d</strong> archived WeChat Android versions '
      "and %d official Tencent download links, spanning WeChat %s (%s) to WeChat %s "
      "(%s). Every version has its own page. Latest archived build: "
      '<a href="%s/%s/"><strong>WeChat %s</strong></a>, released %s.</p>'
      % (total, n_dl, escape(oldest["version"]), oldest["date"],
         escape(newest["version"]), newest["date"],
         SITE, newest["slug"], escape(newest["version"]), newest["date"]))

    A("<h2>按版本快速跳转 / Jump to a version</h2>")
    A('<div class="grid">')
    for g in vers:
        A('<a href="%s/%s/"><b>微信 %s</b><span>WeChat %s · %s</span></a>'
          % (SITE, g["slug"], escape(g["version"]), escape(g["version"]), g["date"]))
    A("</div>")

    A("<h2>全部版本明细 / All versions</h2>")
    A("<table><thead><tr><th>版本号 Version</th><th>发布日期 Release date</th>"
      "<th>versionCode</th><th>架构 ABI</th><th>下载数 Links</th>"
      "<th>页面 Page</th></tr></thead><tbody>")
    for g in vers:
        A("<tr><td>微信 %s / WeChat %s</td><td>%s</td><td>%s</td><td>%s</td>"
          '<td>%d</td><td><a href="%s/%s/">微信 %s 下载 / '
          "Download WeChat %s APK</a></td></tr>" % (
              escape(g["version"]), escape(g["version"]), g["date"],
              escape(codes_of(g)),
              escape("、".join(archs_of(g)) if archs_of(g) else "通用 universal"),
              len(g["builds"]), SITE, g["slug"],
              escape(g["version"]), escape(g["version"])))
    A("</tbody></table>")

    A("<h2>数据来源与声明 / Source and disclaimer</h2>")
    A("<ul>")
    A("<li>全部下载地址来自腾讯官方域名 <code>dldir1.qq.com</code> / "
      "<code>dldir1v6.qq.com</code>，本站不转存、不二次打包、不修改任何安装包。"
      "Every download link points to Tencent's official CDN; no APK is re-hosted, "
      "repacked or modified.</li>")
    A('<li>原始数据整理自 <a href="https://github.com/DJB-Developer/'
      'wechat-android-history-versions" rel="nofollow">DJB-Developer/'
      'wechat-android-history-versions</a>。</li>')
    A('<li>版本更新日志请以 <a href="https://weixin.qq.com/updates" rel="nofollow">'
      "微信官方更新日志</a> 为准 / refer to Tencent's official release notes.</li>")
    A("<li>微信、WeChat 为腾讯公司商标，本站与腾讯公司无隶属关系。"
      "WeChat and 微信 are trademarks of Tencent; this project is not affiliated "
      "with Tencent.</li>")
    A("</ul>")
    A(FOOT)
    return "\n".join(H)


def sitemap(vers):
    today = date.today().isoformat()
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    L.append("  <url><loc>%s/</loc><lastmod>%s</lastmod>"
             "<changefreq>weekly</changefreq><priority>1.0</priority></url>"
             % (SITE, today))
    for g in vers:
        pri = "0.9" if g["rank"] <= 10 else ("0.7" if g["rank"] <= 40 else "0.5")
        L.append("  <url><loc>%s/%s/</loc><lastmod>%s</lastmod>"
                 "<changefreq>monthly</changefreq><priority>%s</priority></url>"
                 % (SITE, g["slug"], g["date"], pri))
    L.append("</urlset>")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 写盘

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    # Windows 控制台默认 GBK，直接 print 中文会乱码
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf:
        try:
            reconf(encoding="utf-8")
        except (ValueError, OSError):
            pass

    vers, skipped = load()
    total = len(vers)
    keep = {g["slug"] for g in vers}

    # 清掉上次生成、本次已不存在的版本目录
    for name in os.listdir(ROOT):
        p = os.path.join(ROOT, name)
        if os.path.isdir(p) and re.fullmatch(r"\d+(\.\d+)*", name) and name not in keep:
            shutil.rmtree(p)
    if os.path.isdir(DOCS):
        shutil.rmtree(DOCS)

    for g in vers:
        write(os.path.join(ROOT, g["slug"], "README.md"), version_md(g, total))
        write(os.path.join(DOCS, g["slug"], "index.html"), version_html(g, total))

    write(os.path.join(ROOT, "README.md"), index_md(vers))
    write(os.path.join(DOCS, "index.html"), index_html(vers))
    write(os.path.join(DOCS, "sitemap.xml"), sitemap(vers))
    write(os.path.join(DOCS, "assets", "style.css"), CSS)
    write(os.path.join(DOCS, "robots.txt"),
          "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % SITE)
    write(os.path.join(DOCS, ".nojekyll"), "")

    print("版本数        : %d" % total)
    print("下载地址总数  : %d" % sum(len(g["builds"]) for g in vers))
    print("版本目录      : %d 个 README.md" % total)
    print("静态页面      : %d 个 index.html + sitemap.xml" % (total + 1))
    print("最新 / 最早   : %s (%s) / %s (%s)"
          % (vers[0]["version"], vers[0]["date"], vers[-1]["version"], vers[-1]["date"]))
    miss = [g["version"] for g in vers if not g["codes"]]
    if miss:
        print("未解析出 versionCode 的版本: %s" % ", ".join(miss))
    if skipped:
        print("版本号无法识别、已跳过的记录 %d 条:" % len(skipped))
        for u in skipped:
            print("  - %s" % u)


if __name__ == "__main__":
    sys.exit(main())
