#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取每个官方 APK 的元数据，结果缓存到 apkmeta.json。

不下载完整安装包（最新版 267MB，172 个约 50GB）。做法是：
  1. HEAD 请求拿文件大小、Last-Modified、链接是否还活着
  2. HTTP Range 取 ZIP 尾部，定位中央目录
  3. 在中央目录里找到 AndroidManifest.xml 的偏移，只 Range 取那一段
  4. 解压得到二进制 AXML，解析出 minSdkVersion / targetSdkVersion /
     versionCode / versionName

实测单个 APK 只需下载 0.3–1.8 MB。腾讯 CDN 支持 Range（返回 206）。

已抓过的 URL 会跳过，因此日常只处理新增版本。加 --refresh 强制重抓。

用法:
    python scripts/apkmeta.py            # 只补新增
    python scripts/apkmeta.py --refresh  # 全量重抓
    python scripts/apkmeta.py --limit 5  # 只处理 5 个，用于调试
"""

import argparse
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "version.json")
CACHE = os.path.join(ROOT, "apkmeta.json")

UA = "wechat-android-apk-versions/1.0 (+https://github.com/wfxu/wechat-android-apk-versions)"
TIMEOUT = 60

# AXML 里 android: 命名空间的属性靠资源 ID 标识，属性名字符串可能为空
ATTR_IDS = {
    0x0101021B: "version_code",
    0x0101021C: "version_name",
    0x0101020C: "min_sdk",
    0x01010270: "target_sdk",
}

# minSdkVersion -> 对应的 Android 系统版本
API_LEVELS = {
    8: "2.2", 9: "2.3", 10: "2.3.3", 11: "3.0", 12: "3.1", 13: "3.2",
    14: "4.0", 15: "4.0.3", 16: "4.1", 17: "4.2", 18: "4.3", 19: "4.4",
    20: "4.4W", 21: "5.0", 22: "5.1", 23: "6.0", 24: "7.0", 25: "7.1",
    26: "8.0", 27: "8.1", 28: "9", 29: "10", 30: "11", 31: "12",
    32: "12L", 33: "13", 34: "14", 35: "15", 36: "16",
}


def android_release(api):
    """把 API level 翻译成用户认得的 Android 版本号。"""
    if api is None:
        return None
    return API_LEVELS.get(api)


# ---------------------------------------------------------------- HTTP

def _open(url, headers=None, method="GET"):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h, method=method), timeout=TIMEOUT)


def head(url):
    """返回 (状态码, 大小, Last-Modified)。失败时状态码为 0。"""
    try:
        with _open(url, method="HEAD") as r:
            n = r.headers.get("Content-Length")
            return r.status, (int(n) if n else None), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        return e.code, None, None
    except Exception:
        return 0, None, None


def rng(url, a, b):
    with _open(url, {"Range": "bytes=%d-%d" % (a, b)}) as r:
        return r.read()


# ---------------------------------------------------------------- ZIP

def fetch_manifest(url, size):
    """只下载必要片段，返回 AndroidManifest.xml 的原始字节。"""
    tail_len = min(65557, size)
    tail = rng(url, size - tail_len, size - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise ValueError("未找到 ZIP EOCD")
    cdsize, cdoff = struct.unpack("<II", tail[i + 12:i + 20])
    if cdoff == 0xFFFFFFFF or cdsize == 0xFFFFFFFF:
        raise ValueError("ZIP64，暂不支持")

    cd = rng(url, cdoff, cdoff + cdsize - 1)
    p = 0
    while p + 46 <= len(cd):
        if cd[p:p + 4] != b"PK\x01\x02":
            break
        method = struct.unpack("<H", cd[p + 10:p + 12])[0]
        csize = struct.unpack("<I", cd[p + 20:p + 24])[0]
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        lho = struct.unpack("<I", cd[p + 42:p + 46])[0]
        if cd[p + 46:p + 46 + nlen] == b"AndroidManifest.xml":
            lfh = rng(url, lho, lho + 29)
            ln, le = struct.unpack("<HH", lfh[26:30])
            off = lho + 30 + ln + le
            blob = rng(url, off, off + csize - 1)
            return zlib.decompress(blob, -15) if method == 8 else blob
        p += 46 + nlen + elen + clen
    raise ValueError("中央目录里没有 AndroidManifest.xml")


# ---------------------------------------------------------------- AXML

def _strings(buf, off):
    """解析 RES_STRING_POOL 块，返回字符串列表。"""
    count, _styles, flags, strs_start = struct.unpack("<IIII", buf[off + 8:off + 24])
    del _styles
    utf8 = bool(flags & 0x100)
    offs = struct.unpack_from("<%dI" % count, buf, off + 28)
    base = off + strs_start
    out = []
    for o in offs:
        p = base + o
        try:
            if utf8:
                # [u16 长度][u8 字节数][数据][\x00]，长度字段可能是 1 或 2 字节
                n = buf[p]
                p += 2 if n & 0x80 else 1
                n = buf[p]
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | buf[p + 1]
                    p += 2
                else:
                    p += 1
                out.append(buf[p:p + n].decode("utf-8", "replace"))
            else:
                n = struct.unpack_from("<H", buf, p)[0]
                p += 2
                if n & 0x8000:
                    n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                    p += 2
                out.append(buf[p:p + n * 2].decode("utf-16-le", "replace"))
        except Exception:
            out.append("")
    return out


def parse_axml(buf):
    """从二进制 AndroidManifest.xml 里取出我们关心的四个字段。"""
    if len(buf) < 8 or buf[0:4] != b"\x03\x00\x08\x00":
        raise ValueError("不是 AXML")

    strs, resmap = [], []
    out = {"version_code": None, "version_name": None,
           "min_sdk": None, "target_sdk": None}

    p = 8
    while p + 8 <= len(buf):
        ctype, hsize, csize = struct.unpack_from("<HHI", buf, p)
        if csize < 8 or p + csize > len(buf):
            break
        if ctype == 0x0001:                     # RES_STRING_POOL
            strs = _strings(buf, p)
        elif ctype == 0x0180:                   # RES_XML_RESOURCE_MAP
            n = (csize - hsize) // 4
            resmap = list(struct.unpack_from("<%dI" % n, buf, p + hsize))
        elif ctype == 0x0102:                   # RES_XML_START_ELEMENT
            # chunk header 8 + lineNumber 4 + comment 4 = 16，之后是 attrExt：
            # ns(4) name(4) attributeStart(2) attributeSize(2) attributeCount(2) ...
            ext = p + 16
            name_i = struct.unpack_from("<I", buf, ext + 4)[0]
            tag = strs[name_i] if name_i < len(strs) else ""
            astart, asize, acount = struct.unpack_from("<HHH", buf, ext + 8)
            if tag in ("manifest", "uses-sdk"):
                for k in range(acount):
                    q = ext + astart + k * asize
                    a_name, a_raw = struct.unpack_from("<II", buf, q + 4)
                    dtype, data = struct.unpack_from("<BI", buf, q + 15)
                    key = ATTR_IDS.get(resmap[a_name]
                                       if a_name < len(resmap) else 0)
                    if not key:
                        continue
                    if dtype in (0x10, 0x11):           # INT_DEC / INT_HEX
                        out[key] = data
                    elif dtype == 0x03:                 # STRING
                        out[key] = (strs[data] if data < len(strs)
                                    else (strs[a_raw] if a_raw < len(strs) else None))
                    elif a_raw != 0xFFFFFFFF and a_raw < len(strs):
                        out[key] = strs[a_raw]
        p += csize
    return out


# ---------------------------------------------------------------- 主流程

def probe(url):
    rec = {"url": url, "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    status, size, lm = head(url)
    rec["status"] = status
    rec["size"] = size
    rec["last_modified"] = lm
    if status != 200 or not size:
        rec["error"] = "HEAD 失败或无 Content-Length"
        return rec
    try:
        info = parse_axml(fetch_manifest(url, size))
        rec.update(info)
        rec["android_release"] = android_release(info.get("min_sdk"))
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, e)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，全量重抓")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个，调试用")
    ap.add_argument("--workers", type=int, default=8, help="并发数")
    args = ap.parse_args()

    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf:
        try:
            reconf(encoding="utf-8")
        except (ValueError, OSError):
            pass

    with open(DATA, encoding="utf-8") as f:
        urls = []
        for row in json.load(f):
            u = (row.get("url") or "").strip()
            if u and u not in urls:
                urls.append(u)

    cache = {}
    if os.path.exists(CACHE) and not args.refresh:
        try:
            with open(CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except ValueError:
            cache = {}

    # 只抓没抓过的，以及上次抓失败的
    todo = [u for u in urls
            if u not in cache or cache[u].get("error") or not cache[u].get("size")]
    if args.limit:
        todo = todo[:args.limit]

    print("总链接 %d 个，缓存命中 %d 个，本次需要抓 %d 个"
          % (len(urls), len(urls) - len([u for u in urls if u not in cache]), len(todo)))
    if not todo:
        print("无需更新")
        return 0

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, u): u for u in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            rec = fu.result()
            cache[rec["url"]] = rec
            name = rec["url"].rsplit("/", 1)[-1]
            if rec.get("error"):
                fail += 1
                print("  [%3d/%d] ✗ %-52s %s" % (i, len(todo), name[:52],
                                                 rec["error"][:60]))
            else:
                ok += 1
                print("  [%3d/%d] ✓ %-52s %6.1f MB  minSdk %s (Android %s)"
                      % (i, len(todo), name[:52], (rec["size"] or 0) / 1048576,
                         rec.get("min_sdk"), rec.get("android_release") or "?"))

    with open(CACHE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("\n成功 %d，失败 %d，缓存已写入 %s" % (ok, fail, os.path.basename(CACHE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
