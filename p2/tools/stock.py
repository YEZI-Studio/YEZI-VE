#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""免费素材站：搜、下、入库、烘焙叠层（2026-11-05）。

只用标准库（编排器用 python3 -I 直接 import；烘焙叠层另起引擎 Python）。不登录、不绕过任何人机验证；只下免费授权的素材。

站点（不登录、不用 key）：
  pexels   Pexels 官方视频 / 图片 sitemap（robots.txt 写明 Allow: /sitemaps/）：标题、描述、作者、时长、直接下载地址
           （videos.pexels.com 的 1080p 文件）都在里面。一次下 28 个分片（约 70 MB）建本地全文索引，以后搜索不联网。
           网站搜索页 / 视频页对脚本返回 403（Cloudflare 人机验证），内部 api/v* 在 robots 里禁止：都不碰。
           本机配置了 PEXELS_API_KEY（环境变量或 ~/.config/yezi-ve/pexels.key）时改走官方 API（运行时读，不打印不落盘）。
  mixkit   Mixkit 视频 sitemap（assets.mixkit.co/sitemaps，robots 允许）：标题、描述、标签、720p 地址；下载时换 1080p。
           授权 Mixkit Stock Video Free License（免费商用，不要求署名）。
  commons  Wikimedia Commons API（实时搜）：只收 CC0 / 公有领域 / CC BY（要署名的记作者）；CC BY-SA / NC / ND 不收。
           大于 1080p 的取 Commons 自己转好的 1080p。
  nasa     images-api.nasa.gov（实时搜，默认不开）：NASA 媒体使用指南（一般无版权，不能暗示 NASA 背书）。
  pixabay  只有本机有 PIXABAY_API_KEY 才能用（官网和 sitemap 对脚本全是 Cloudflare 验证）。
  coverr   sitemap 能读，但视频地址要 API key（cdn 直接跳首页）：不接。
  STOCK_FAKE=1：假站点（不联网）。search 返回素材包里已有的小文件（一个素材包都没有时用 ffmpeg 合成几条测试画面），fetch 复制一条；bake_overlay 只写表不烘（STOCK_FAKE_BAKE=1 才烘）。

接口（编排器 lib/stockreq.py 调）：
  search(query, kind='video', n=20, sites=None) -> [{site, id, key, title, url, w, h, fps, dur, page, author, license, license_url,
         tags, descr, thumb, stype_guess, score}]      query 里 -word 是排除词；默认只给横屏、短边 ≥ 720 的
  fetch(item, pack_dir, use='', note='', stype=None) -> {'path', 'name', 'source'}
         下到 pack_dir/footage/<站>-<id>.<ext>（断点续传、校验大小、ffprobe），sources.json 追加一条，CATALOG.md 补一行
  bake_overlay(pack_dir, name, kind, window, by=None) -> overlays.json 里的一条（调引擎 src.tools.elements bake + sheet + 自动验收）
         by='stockreq'（自动通道）：不碰人工整理过的 kind；先在临时表里烘、量，过了验收才并进 overlays.json
  drop_overlay(pack_dir, kind, name, src, why, only_by=False)   撤一个窗口（only_by：只撤自动通道写的）
  sheet(path, out, n=8)                到货联系表（编排器每条到货都出一张：<素材包>/_review/stock_<文件>.jpg，再复制进镜头的 ctx/look/stock/）
  check_overlays(pack_dir)             现有窗口按自动验收量一遍（短划线 / 贴边 / 大片平的），不改表
  annotate(pack_dir, name, **fields)   看过之后改来源类型 / 内容 / 状态
  reject(pack_dir, name, why)          看过不收：文件移到 pack_dir/_rejected/，sources.json 记进 rejected

命令行：
  python3 -I stock.py sites                         哪些站现在能用（只判 key 有没有，不读值）
  python3 -I stock.py index [--site pexels|mixkit|all] [--kind video|image] [--refresh]
  python3 -I stock.py search "black background bubbles -animation" [--n 20] [--sites pexels,mixkit,commons] [--json]
  python3 -I stock.py fetch pexels:855709 --pack ../assets/霓虹 --use '雨夜街头' --note '霓虹倒影'
  python3 -I stock.py sheet FILE --out SHEET.jpg [--n 8]     看图用的联系表（8 帧拼一张）
  python3 -I stock.py bake --pack DIR --name FILE --kind bubbles --window '{"src": 2, "dur": 4, "key": "median"}'
  python3 -I stock.py annotate --pack DIR --name FILE --set stype=元素 --set content=...
  python3 -I stock.py reject --pack DIR --name FILE --why '整屏都是字幕'
  python3 -I stock.py check --pack DIR [--kind bubbles]     叠层自动验收量一遍（要先烘过）

素材包的表（sources.json / overlays.json / CATALOG.md）每次读改写都拿 <素材包>/.stock.lock 文件锁（两集并行不丢更新）。
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
P2 = os.path.dirname(HERE)
MD = os.path.dirname(P2)
ASSETS = os.path.join(P2, "assets")
SEARCH_DIR = os.path.join(ASSETS, "_search")
INDEX_DIR = os.path.join(SEARCH_DIR, "index")
ENGINE = os.path.join(P2, "chroma", "engine")
ENGINE_PY = os.path.join(ENGINE, ".venv", "bin", "python")
LOG_DIR = os.path.join(MD, "logs", "p2", "stock")
BIN_PATH = os.pathsep.join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])
UA = ("yezi-ve-stock/1.0 (personal short-video tool run by its end user; downloads free-license stock footage "
      "only; about 1 request per second)")
KEY_DIR = os.path.expanduser("~/.config/yezi-ve")
FAKE = os.environ.get("STOCK_FAKE") == "1"

LIC = {
    "pexels": ("Pexels License：免费商用，不要求署名", "https://www.pexels.com/license/"),
    # Mixkit 同一个站有两种授权。条目页 data-license="videoFree" 才是 Free License（免费商用）；"videoRestricted" =
    # 「720p Version for Personal Use only」（Mixkit Restricted License，只许个人用）——fetch 前逐条读条目页核，不是 videoFree 的不下
    "mixkit": ("Mixkit Stock Video Free License：免费商用，不要求署名（条目页 data-license=videoFree 已核）", "https://mixkit.co/license/#videoFree"),
    "pixabay": ("Pixabay Content License：免费商用，不要求署名", "https://pixabay.com/service/license-summary/"),
    "nasa": ("NASA 媒体使用指南：一般不受版权保护，不要求署名，不能暗示 NASA 背书", "https://www.nasa.gov/nasa-brand-center/images-and-media/"),
    "fake": ("假站点（测试用，不对外）", ""),
}
SITE_NAME = {"pexels": "Pexels", "mixkit": "Mixkit", "commons": "Wikimedia Commons", "nasa": "NASA Image and Video Library",
             "pixabay": "Pixabay", "fake": "假站点"}
DEFAULT_SITES = ("pexels", "mixkit", "commons")
MIN_GAP = {"www.pexels.com": 1.0, "api.pexels.com": 1.0, "videos.pexels.com": 0.5, "images.pexels.com": 0.5, "mixkit.co": 1.0,
           "assets.mixkit.co": 0.5, "commons.wikimedia.org": 1.0, "upload.wikimedia.org": 5.0, "images-api.nasa.gov": 1.0,
           "images-assets.nasa.gov": 1.0, "pixabay.com": 1.0, "cdn.pixabay.com": 0.5}
MAX_BYTES = 700 * 1024 * 1024          # 一条最多 700 MB（4K 长片不下：先找 1080p）
MAX_TRIES = 5
CG_WORDS = ("animation", "animated", "3d", "render", "rendered", "cgi", "motion graphics", "motion graphic", "digital art",
            "computer generated", "simulation", "visualization", "visualisation", "illustration", "abstract background", "loop",
            "loopable", "seamless", "particles background", "generated", "ai generated")
TEXT_WORDS = ("subtitle", "subtitles", "caption", "captions", "text overlay", "title card", "lower third", "watermark",
              "infographic", "lecture", "presentation", "news")

_last = {}


# ----------------------------------------------------------------------------- 小工具
def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "stock.log"), "a", encoding="utf-8") as fh:
            fh.write(f"[{_now()}] {msg}\n")
    except OSError:
        pass


def _read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(p, obj):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)


def _write_text(p, s):
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(s)
    os.replace(tmp, p)


_LOCKS = {}


@contextmanager
def pack_lock(pack_dir):
    """素材包的表（sources.json / overlays.json / CATALOG.md）读改写时加文件锁（<素材包>/.stock.lock，fcntl.flock）：同一母题的
    两集（DeepSeek / Codex 版）并行时各自的 stock 子进程不会互相丢更新。只锁改表那一下，不锁下载、不锁烘焙。同一进程里可重入。"""
    key = os.path.abspath(pack_dir)
    ent = _LOCKS.get(key)
    if ent:
        ent[1] += 1
        try:
            yield
        finally:
            ent[1] -= 1
        return
    os.makedirs(key, exist_ok=True)
    fh = open(os.path.join(key, ".stock.lock"), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _LOCKS[key] = [fh, 1]
        yield
    finally:
        _LOCKS.pop(key, None)
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _redact(url):
    """日志里的地址去掉 query 里的 key（Pixabay 的 key 在 query 里）"""
    try:
        u = urllib.parse.urlsplit(url)
        q = [(k, "***" if k.lower() in ("key", "api_key", "apikey", "token") else v) for k, v in urllib.parse.parse_qsl(u.query)]
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))
    except Exception:  # noqa: BLE001
        return "(url)"


def _throttle(url):
    host = urllib.parse.urlsplit(url).netloc
    gap = MIN_GAP.get(host, 1.0)
    t = _last.get(host, 0.0)
    w = t + gap - time.time()
    if w > 0:
        time.sleep(w)
    _last[host] = time.time()


def _key(site):
    """本机配置的素材站 API key（环境变量或 ~/.config/yezi-ve/<站>.key）；只在运行时读，不打印、不写文件、不进日志。"""
    env = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}.get(site)
    if not env:
        return None
    v = os.environ.get(env)
    if v:
        return v.strip()
    p = os.path.join(KEY_DIR, f"{site}.key")
    try:
        with open(p, encoding="utf-8") as fh:
            v = fh.read().strip()
        return v or None
    except OSError:
        return None


class RateLimited(RuntimeError):
    """站点限流（429 且 Retry-After 很长）：这一台主机冷却到期前不再请求。"""


COOL_MAX_WAIT = 180.0                      # Retry-After 超过这个就不等了：记冷却，这一条报「限流」，过后再下


def _cool_path():
    return os.path.join(INDEX_DIR, "cooldown.json")


def cooling(url):
    """这台主机还要冷却多少秒（0 = 能请求）"""
    host = urllib.parse.urlsplit(url).netloc
    until = float((_read_json(_cool_path()) or {}).get(host) or 0)
    return max(0.0, until - time.time())


def _cool_set(url, secs):
    host = urllib.parse.urlsplit(url).netloc
    d = _read_json(_cool_path()) or {}
    d[host] = time.time() + float(secs)
    try:
        _write_json(_cool_path(), d)
    except OSError:
        pass
    _log(f"{host} 限流：冷却 {float(secs):.0f} s（到 {datetime.fromtimestamp(d[host]).strftime('%H:%M:%S')}），期间不再请求")


def _retry_after(e):
    ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
    try:
        return float(ra) if ra is not None else None
    except ValueError:
        return None


def http(url, headers=None, method="GET", timeout=60, tries=MAX_TRIES, secret=False):
    """GET / HEAD -> (status, headers dict(lower), body bytes)。429 / 5xx / 断网按退避重试（Retry-After 最多等 180 s；
    更长的记冷却、直接返回 429），重试有上限；404 / 403 直接返回不重试。secret=True：日志不写地址。"""
    h = {"User-Agent": UA, "Accept-Encoding": "identity"}
    h.update(headers or {})
    last = None
    if cooling(url) > 0:
        return 429, {}, b""
    for a in range(tries):
        _throttle(url)
        try:
            req = urllib.request.Request(url, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = b"" if method == "HEAD" else r.read()
                return r.status, {k.lower(): v for k, v in r.headers.items()}, body
        except urllib.error.HTTPError as e:
            last = e.code
            ra = _retry_after(e)
            if e.code == 429 and ra and ra > COOL_MAX_WAIT:
                _cool_set(url, ra)
                return 429, {}, b""
            if e.code in (429, 500, 502, 503, 504) and a + 1 < tries:
                wait = ra if ra else min(60.0, 3.0 * 2 ** a)
                _log(f"HTTP {e.code} {'(secret)' if secret else _redact(url)}，{wait:.0f} s 后重试（第 {a + 1} 次）")
                time.sleep(wait)
                continue
            return e.code, {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, b""
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            last = f"{type(e).__name__}"
            if a + 1 < tries:
                time.sleep(min(60.0, 3.0 * 2 ** a))
                continue
    _log(f"放弃 {'(secret)' if secret else _redact(url)}：{last}")
    return (last if isinstance(last, int) else 0), {}, b""


def http_json(url, headers=None, secret=False):
    st, _, b = http(url, headers=headers, secret=secret)
    if st != 200 or not b:
        return None
    try:
        return json.loads(b.decode("utf-8"))
    except ValueError:
        return None


def _strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _dims_from_name(url):
    """Pexels 文件名里的 hd_1920_1080_30fps -> (w, h, fps)"""
    m = re.search(r"_(\d{3,4})_(\d{3,4})_(\d+(?:\.\d+)?)fps", url or "")
    if m:
        return int(m.group(1)), int(m.group(2)), float(m.group(3))
    return None, None, None


def guess_stype(item):
    """从标题 / 描述 / 标签猜来源类型（看图之后用 annotate 改）：CG / 带字幕 / 实拍"""
    t = " ".join(str(item.get(k) or "") for k in ("title", "descr", "tags")).lower()
    words = set(re.findall(r"[a-z0-9]+", t))
    for w in CG_WORDS:
        if (" " in w and w in t) or w in words:
            return "CG"
    for w in TEXT_WORDS:
        if (" " in w and w in t) or w in words:
            return "带字幕"
    return "实拍"


# ----------------------------------------------------------------------------- 本地索引（sitemap）
SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
VID = "{http://www.google.com/schemas/sitemap-video/1.1}"
IMG = "{http://www.google.com/schemas/sitemap-image/1.1}"
SITEMAPS = {
    ("pexels", "video"): "https://www.pexels.com/sitemaps/en-US/video-sitemap.xml.gz",
    ("pexels", "image"): "https://www.pexels.com/sitemaps/en-US/photo-sitemap.xml.gz",
    ("mixkit", "video"): "https://assets.mixkit.co/sitemaps/sitemap-video.xml.gz",
}


def _db_path(site, kind):
    return os.path.join(INDEX_DIR, f"{site}_{kind}.sqlite")


def _db(site, kind, create=False):
    p = _db_path(site, kind)
    if not create and not os.path.exists(p):
        return None
    os.makedirs(INDEX_DIR, exist_ok=True)
    c = sqlite3.connect(p, timeout=60)
    c.execute("CREATE TABLE IF NOT EXISTS shards (url TEXT PRIMARY KEY, lastmod TEXT, n INTEGER, at TEXT)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS v USING fts5(id UNINDEXED, title, descr, tags, url UNINDEXED, w UNINDEXED, "
              "h UNINDEXED, fps UNINDEXED, dur UNINDEXED, page UNINDEXED, author UNINDEXED, thumb UNINDEXED, pub UNINDEXED, "
              "shard UNINDEXED, tokenize='porter unicode61')")
    return c


def _gunzip(b):
    try:
        return gzip.decompress(b)
    except OSError:
        return b


def _parse_shard(site, kind, data):
    rows = []
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != SM + "url":
            continue
        loc = (el.findtext(SM + "loc") or "").strip()
        m = re.search(r"-(\d+)/?$", loc)
        pid = m.group(1) if m else hashlib.md5(loc.encode()).hexdigest()[:10]
        if kind == "video":
            v = el.find(VID + "video")
            if v is None:
                el.clear()
                continue
            title = re.sub(r"\s*·\s*Free Stock Video\s*$", "", (v.findtext(VID + "title") or "").strip())
            url = (v.findtext(VID + "content_loc") or "").strip()
            w, h, fps = _dims_from_name(url)
            up = v.find(VID + "uploader")
            tags = ", ".join(t.text.strip() for t in v.findall(VID + "tag") if t.text)
            try:
                dur = float(v.findtext(VID + "duration") or 0) or None
            except ValueError:
                dur = None
            rows.append((pid, title, (v.findtext(VID + "description") or "").strip(), tags, url, w, h, fps, dur, loc,
                         (up.text or "").strip() if up is not None else "", (v.findtext(VID + "thumbnail_loc") or "").strip(),
                         (v.findtext(VID + "publication_date") or "")[:10]))
        else:
            im = el.find(IMG + "image")
            if im is None:
                el.clear()
                continue
            url = (im.findtext(IMG + "loc") or "").strip()
            title = (im.findtext(IMG + "title") or "").strip()
            cap = (im.findtext(IMG + "caption") or "").strip()
            slug = re.sub(r"[-/]+", " ", re.sub(r"^.*/photo/|-?\d+/?$", "", loc)).strip()
            rows.append((pid, title or slug, cap, "", url, None, None, None, None, loc, "", url, ""))
        el.clear()
    return rows


def build_index(site="pexels", kind="video", refresh=False, log=print, max_shards=None):
    """下载站点 sitemap 分片，建 / 更新本地全文索引（只重下 lastmod 变了的分片）。返回条数。"""
    root = SITEMAPS.get((site, kind))
    if not root:
        raise ValueError(f"{site} 没有 {kind} 的 sitemap 索引")
    st, _, b = http(root, timeout=120)
    if st != 200:
        raise RuntimeError(f"{site} sitemap 索引读不了（HTTP {st}）")
    idx = ET.fromstring(_gunzip(b))
    shards = [((s.findtext(SM + "loc") or "").strip(), (s.findtext(SM + "lastmod") or "").strip()) for s in idx.findall(SM + "sitemap")]
    if max_shards:
        shards = shards[:max_shards]
    c = _db(site, kind, create=True)
    have = dict(c.execute("SELECT url, lastmod FROM shards").fetchall())
    done = 0
    for i, (u, lm) in enumerate(shards):
        if not refresh and have.get(u) == lm:
            continue
        st, _, b = http(u, timeout=180)
        if st != 200:
            log(f"  {site} 分片 {i + 1}/{len(shards)} 读不了（HTTP {st}）：跳过")
            continue
        rows = _parse_shard(site, kind, _gunzip(b))
        with c:
            c.execute("DELETE FROM v WHERE shard = ?", (u,))
            c.executemany("INSERT INTO v VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [r + (u,) for r in rows])
            c.execute("INSERT OR REPLACE INTO shards VALUES (?,?,?,?)", (u, lm, len(rows), _now()))
        done += 1
        log(f"  {site} {kind} 分片 {i + 1}/{len(shards)}：{len(rows)} 条")
    if site == "pexels" and kind == "video":
        _merge_tsv(c)
    n = c.execute("SELECT count(*) FROM v").fetchone()[0]
    c.close()
    _log(f"index {site}/{kind}: 更新 {done} 个分片，共 {n} 条")
    return n


def _merge_tsv(c):
    """以前用浏览器搜到的 Pexels 结果（assets/_search/pexels_*.tsv：带标签）并进索引（shard = tsv）"""
    rows = []
    for fn in sorted(os.listdir(SEARCH_DIR)) if os.path.isdir(SEARCH_DIR) else []:
        if not (fn.startswith("pexels_") and fn.endswith(".tsv")):
            continue
        with open(os.path.join(SEARCH_DIR, fn), encoding="utf-8") as fh:
            for line in fh.read().split("\n")[1:]:
                f = line.split("\t")
                if len(f) < 15 or not f[1].isdigit():
                    continue
                url = f[13] or f[10]
                w, h, fps = _dims_from_name(url)
                try:
                    dur = float(f[6])
                except ValueError:
                    dur = None
                rows.append((f[1], f[2], f[3], f[15] if len(f) > 15 else "", url, w, h, fps, dur,
                             f"https://www.pexels.com/video/{f[1]}/", f[7], f[14], "", "tsv"))
    with c:
        c.execute("DELETE FROM v WHERE shard = 'tsv'")
        known = {r[0] for r in c.execute("SELECT id FROM v")}
        c.executemany("INSERT INTO v VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [r for r in rows if r[0] not in known])


_TSV = {}


def _tsv_links():
    """{pexels id: 1080p 地址}（assets/_search/pexels_*.tsv 第 14 列）"""
    if not _TSV and os.path.isdir(SEARCH_DIR):
        for fn in sorted(os.listdir(SEARCH_DIR)):
            if fn.startswith("pexels_") and fn.endswith(".tsv"):
                with open(os.path.join(SEARCH_DIR, fn), encoding="utf-8") as fh:
                    for line in fh.read().split("\n")[1:]:
                        f = line.split("\t")
                        if len(f) >= 14 and f[1].isdigit() and f[13]:
                            _TSV.setdefault(f[1], f[13])
        _TSV.setdefault("_", "")
    return _TSV


def index_age_days(site, kind="video"):
    p = _db_path(site, kind)
    return (time.time() - os.path.getmtime(p)) / 86400.0 if os.path.exists(p) else None


def _fts_query(query):
    """搜索词 → (要的词, 排除的词)。空格分开的一段以 - 开头才是排除词（-animation）；一段里面带连字符 / 点的（QX-7、A-320、12.5）
    是一个词组「qx 7」，留在要的词里（不然 QX-7 会被拆成「qx，不要 7」，反倒把 QX-7 的标题都排除了）。"""
    inc, exc = [], []
    for chunk in query.lower().split():
        ws = [w.replace("'", "") for w in re.findall(r"[\w']+", chunk)]
        if chunk.startswith("-"):
            exc += ws
        elif ws:
            inc.append(" ".join(ws))
    inc = [w for w in inc if w and w not in ("a", "an", "the", "of", "in", "on", "with", "and", "or", "to", "for")]
    return inc, [w for w in exc if w]


def _search_index(site, kind, query, n, landscape=True, min_short=720):
    c = _db(site, kind)
    if c is None:
        return []
    inc, exc = _fts_query(query)
    if not inc:
        return []
    q = " OR ".join(f'"{w}"' for w in inc)
    if exc:
        q = f"({q}) NOT ({' OR '.join(chr(34) + w + chr(34) for w in exc)})"
    try:
        cur = c.execute("SELECT id, title, descr, tags, url, w, h, fps, dur, page, author, thumb, pub, bm25(v, 0, 4.0, 1.0, 2.0) AS s "
                        "FROM v WHERE v MATCH ? ORDER BY s LIMIT ?", (q, max(200, n * 20)))
        rows = cur.fetchall()
    except sqlite3.OperationalError as e:
        _log(f"index 搜索出错 {site}: {e}")
        rows = []
    c.close()
    out, seen = [], set()
    for r in rows:
        pid, title, descr, tags, url, w, h, fps, dur, page, author, thumb, pub, s = r
        if pid in seen or not url:
            continue
        seen.add(pid)
        text = f"{title} {descr} {tags}".lower()
        if kind == "video" and w and h:
            if landscape and h > w:
                continue
            if min(w, h) < min_short:
                continue
        elif kind == "video" and landscape and re.search(r"\bvertical\b|\bportrait (?:format|video|shot)\b", text):
            continue                                              # Mixkit 的 sitemap 没有分辨率：标题 / 描述写了竖屏的先去掉
        lic, lic_url = LIC[site]
        hit = sum(1 for wd in inc if wd in text or wd in re.sub(r"[-.]", " ", text))   # 覆盖的词越多越前（bm25 按 OR 算，单词高频的会抢前面）
        it = dict(site=site, id=str(pid), key=f"{site}:{pid}", title=title, descr=descr, tags=tags, url=url, w=w, h=h, fps=fps,
                  dur=dur, page=page, author=author, license=lic, license_url=lic_url, thumb=thumb, pub=pub,
                  score=round(-float(s), 3), _hit=hit)
        it["stype_guess"] = guess_stype(it)
        out.append(it)
    out.sort(key=lambda d: (-d["_hit"], -d["score"]))
    for d in out:
        d.pop("_hit", None)
    if site == "mixkit":                                          # 核过是 Restricted（只许个人用）的不给；没核过的 fetch 时再核
        known = _mixkit_lic_cache()
        out = [d for d in out if (known.get(str(d["id"])) or {}).get("lic") in (None, "videoFree")]
        for d in out:
            if (known.get(str(d["id"])) or {}).get("lic") == "videoFree":
                d["license_checked"] = True
    return out[:n]


# ----------------------------------------------------------------------------- Mixkit 授权逐条核（11-05 复核发现的坑）
MIXKIT_LIC_RE = re.compile(r'data-license="(video[A-Za-z]+)"')


def _mixkit_lic_path():
    return os.path.join(INDEX_DIR, "mixkit_license.json")


def _mixkit_lic_cache():
    return _read_json(_mixkit_lic_path()) or {}


def mixkit_license(item):
    """Mixkit 条目页上这一条的授权：同一个站有 Free（data-license="videoFree"：免费商用）和 Restricted（"videoRestricted"：
    「720p Version for Personal Use only」，只许个人用）两种，sitemap 里分不出来，只能读条目页。
    -> (授权代号 | None, 说明)。读到的结果缓存在 _search/index/mixkit_license.json（不重复请求）。"""
    pid = str(item.get("id") or "")
    hit = _mixkit_lic_cache().get(pid)
    if hit and hit.get("lic"):
        return hit["lic"], "缓存"
    page = str(item.get("page") or "")
    if not page.startswith("https://mixkit.co/"):
        return None, "没有 mixkit.co 条目页地址"
    st, _, b = http(page, timeout=60, tries=3)
    if st != 200 or not b:
        return None, f"条目页 HTTP {st}"
    s = b.decode("utf-8", "replace")
    m = MIXKIT_LIC_RE.search(s)
    lic = m.group(1) if m else None
    if not lic:
        if "Mixkit Restricted License" in s or "Personal Use only" in s:
            lic = "videoRestricted"
        elif re.search(r'"copyrightNotice"\s*:\s*"Free"', s):
            lic = "videoFree"
    if lic:
        try:
            d = _mixkit_lic_cache()
            d[pid] = {"lic": lic, "page": page, "at": _now()}
            _write_json(_mixkit_lic_path(), d)
        except OSError:
            pass
        _log(f"mixkit:{pid} 条目页授权 {lic}")
    return lic, "条目页"


# ----------------------------------------------------------------------------- 实时站点
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
CC_OK = re.compile(r"^(cc0|cc[- ]?zero|public domain|pd\b|pd-|cc[- ]by[- ]\d|cc[- ]by$|attribution$)", re.I)
CC_BAD = re.compile(r"(sa|nc|nd|share|non-?commercial|no ?deriv|gfdl|fair use)", re.I)


def commons_license_ok(short):
    s = (short or "").strip()
    if not s:
        return False
    if CC_OK.match(s) and not CC_BAD.search(s.replace("CC0", "").replace("cc0", "")):
        return True
    return False


def _commons_pages(params):
    q = dict(action="query", format="json", **params)
    d = http_json(COMMONS_API + "?" + urllib.parse.urlencode(q))
    return list(((d or {}).get("query") or {}).get("pages", {}).values())


def _commons_item(p, kind):
    info = (p.get("videoinfo") or p.get("imageinfo") or [{}])[0]
    if not info:
        return None
    em = info.get("extmetadata") or {}
    g = lambda k: (em.get(k) or {}).get("value") or ""  # noqa: E731
    short = _strip_tags(g("LicenseShortName"))
    if not commons_license_ok(short):
        return {"_rejected": f"{p.get('title')}：授权 {short or '不明'}，不收"}
    w, h = info.get("width"), info.get("height")
    url = orig = (info.get("url") or "").split("?")[0]
    if kind == "video" and h and h > 1080:                        # 大于 1080p：取 Commons 转好的 1080p
        for dv in info.get("derivatives") or []:
            if dv.get("height") == 1080 and "webm" in (dv.get("type") or ""):
                url, w, h = dv["src"], dv.get("width") or w, 1080
                break
    artist = _strip_tags(g("Artist"))[:160]
    need_by = g("AttributionRequired") == "true" or short.lower().startswith("cc by")
    lic = f"{short}" + (f"；要署名：{artist}" if need_by else "（不要求署名）")
    title = p.get("title", "")
    it = dict(site="commons", id=title, key=f"commons:{title}", title=title[5:] if title.startswith("File:") else title,
              descr=_strip_tags(g("ImageDescription"))[:400], tags=_strip_tags(g("Categories"))[:300], url=url, w=w, h=h,
              fps=None, dur=round(info["duration"], 2) if info.get("duration") else None, page=info.get("descriptionurl", ""),
              author=artist, license=lic, license_url=g("LicenseUrl"), thumb="", pub=(g("DateTimeOriginal") or "")[:10],
              size=info.get("size") if url == orig else None, orig_url=orig, mime=info.get("mime"), attribution_required=need_by)
    it["stype_guess"] = guess_stype(it)
    return it


def _search_commons(query, kind, n):
    inc, exc = _fts_query(query)
    words = " ".join(inc) + "".join(f" -{w}" for w in exc)
    ft = "filetype:video" if kind == "video" else "filetype:bitmap"
    prop = "videoinfo" if kind == "video" else "imageinfo"
    pre = "vi" if kind == "video" else "ii"
    out, rej = [], []
    for qq in [words, " ".join(inc[:2])] if len(inc) > 2 else [words]:
        params = {"generator": "search", "gsrsearch": f"{qq} {ft}", "gsrnamespace": 6, "gsrlimit": min(50, n * 2),
                  "prop": prop, f"{pre}prop": "size|url|mime|extmetadata" + ("|derivatives" if kind == "video" else ""),
                  f"{pre}extmetadatafilter": "LicenseShortName|LicenseUrl|Artist|AttributionRequired|ImageDescription|Categories|DateTimeOriginal"}
        pages = sorted(_commons_pages(params), key=lambda p: p.get("index", 0))
        for p in pages:
            it = _commons_item(p, kind)
            if not it:
                continue
            if "_rejected" in it:
                rej.append(it["_rejected"])
                continue
            if kind == "video" and it.get("w") and it.get("h") and min(it["w"], it["h"]) < 720:
                continue
            if all(o["id"] != it["id"] for o in out):
                out.append(it)
        if len(out) >= n:
            break
    if rej:
        _log("commons 授权不收：" + "；".join(rej[:12]))
    return out[:n]


def commons_lookup(title, kind="video"):
    t = title if title.startswith("File:") else "File:" + title
    prop = "videoinfo" if kind == "video" else "imageinfo"
    pre = "vi" if kind == "video" else "ii"
    pages = _commons_pages({"titles": t, "prop": prop, f"{pre}prop": "size|url|mime|extmetadata" + ("|derivatives" if kind == "video" else "")})
    for p in pages:
        it = _commons_item(p, kind)
        if it and "_rejected" not in it:
            return it
    return None


def _search_nasa(query, kind, n):
    inc, _ = _fts_query(query)
    d = http_json("https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"q": " ".join(inc), "media_type": kind if kind == "video" else "image"}))
    out = []
    for it in (((d or {}).get("collection") or {}).get("items") or [])[: n * 2]:
        dd = (it.get("data") or [{}])[0]
        desc = dd.get("description") or ""
        if re.search(r"©|copyright", desc, re.I):                  # 第三方版权的不收
            continue
        nid = dd.get("nasa_id")
        if not nid:
            continue
        lic, lic_url = LIC["nasa"]
        x = dict(site="nasa", id=nid, key=f"nasa:{nid}", title=dd.get("title", ""), descr=_strip_tags(desc)[:400],
                 tags=", ".join(dd.get("keywords") or [])[:300], url="", w=None, h=None, fps=None, dur=None,
                 page=f"https://images.nasa.gov/details/{urllib.parse.quote(nid)}", author=dd.get("center") or "NASA",
                 license=lic, license_url=lic_url, thumb=((it.get("links") or [{}])[0]).get("href", ""), pub=(dd.get("date_created") or "")[:10])
        x["stype_guess"] = guess_stype(x)
        out.append(x)
        if len(out) >= n:
            break
    return out


def _search_pexels_api(query, kind, n, key):
    inc, exc = _fts_query(query)
    path = "videos/search" if kind == "video" else "v1/search"
    d = http_json(f"https://api.pexels.com/{path}?" + urllib.parse.urlencode({"query": " ".join(inc), "per_page": min(80, n * 2),
                                                                                 "orientation": "landscape"}),
                  headers={"Authorization": key}, secret=True)
    out = []
    lic, lic_url = LIC["pexels"]
    for v in (d or {}).get("videos" if kind == "video" else "photos") or []:
        if kind == "video":
            files = [f for f in v.get("video_files") or [] if (f.get("file_type") or "").endswith("mp4") and f.get("height")]
            files.sort(key=lambda f: (abs(min(f["width"], f["height"]) - 1080), -(f.get("fps") or 0)))
            if not files:
                continue
            f = files[0]
            x = dict(site="pexels", id=str(v["id"]), key=f"pexels:{v['id']}", title=re.sub(r"[-/]+", " ", v.get("url", "").rstrip("/").split("/")[-1]),
                     descr="", tags=", ".join(v.get("tags") or []), url=f["link"], w=f["width"], h=f["height"], fps=f.get("fps"),
                     dur=v.get("duration"), page=v.get("url", ""), author=(v.get("user") or {}).get("name", ""), license=lic,
                     license_url=lic_url, thumb=v.get("image", ""), pub="")
        else:
            x = dict(site="pexels", id=str(v["id"]), key=f"pexels:{v['id']}", title=v.get("alt", ""), descr="", tags="",
                     url=(v.get("src") or {}).get("original", ""), w=v.get("width"), h=v.get("height"), fps=None, dur=None,
                     page=v.get("url", ""), author=v.get("photographer", ""), license=lic, license_url=lic_url,
                     thumb=(v.get("src") or {}).get("medium", ""), pub="")
        if any(w in f"{x['title']} {x['tags']}".lower() for w in exc):
            continue
        x["stype_guess"] = guess_stype(x)
        out.append(x)
    return out[:n]


def _search_pixabay_api(query, kind, n, key):
    inc, exc = _fts_query(query)
    path = "api/videos/" if kind == "video" else "api/"
    d = http_json(f"https://pixabay.com/{path}?" + urllib.parse.urlencode({"key": key, "q": " ".join(inc)[:100], "per_page": min(200, max(3, n * 2)),
                                                                          "safesearch": "true"}), secret=True)
    out = []
    lic, lic_url = LIC["pixabay"]
    for v in (d or {}).get("hits") or []:
        if kind == "video":
            vs = v.get("videos") or {}
            f = vs.get("large") if (vs.get("large") or {}).get("height", 0) and vs["large"]["height"] <= 1080 else vs.get("medium")
            f = f or vs.get("large") or {}
            if not f.get("url"):
                continue
            x = dict(site="pixabay", id=str(v["id"]), key=f"pixabay:{v['id']}", title=v.get("tags", ""), descr="", tags=v.get("tags", ""),
                     url=f["url"], w=f.get("width"), h=f.get("height"), fps=None, dur=v.get("duration"), page=v.get("pageURL", ""),
                     author=v.get("user", ""), license=lic, license_url=lic_url, thumb=f.get("thumbnail", ""), pub="")
        else:
            x = dict(site="pixabay", id=str(v["id"]), key=f"pixabay:{v['id']}", title=v.get("tags", ""), descr="", tags=v.get("tags", ""),
                     url=v.get("largeImageURL", ""), w=v.get("imageWidth"), h=v.get("imageHeight"), fps=None, dur=None,
                     page=v.get("pageURL", ""), author=v.get("user", ""), license=lic, license_url=lic_url, thumb=v.get("previewURL", ""), pub="")
        if any(w in x["tags"].lower() for w in exc):
            continue
        x["stype_guess"] = guess_stype(x)
        out.append(x)
    return out[:n]


# ----------------------------------------------------------------------------- 假站点（STOCK_FAKE=1）
def _fake_pool():
    out = []
    for mot in sorted(os.listdir(ASSETS)) if os.path.isdir(ASSETS) else []:
        d = os.path.join(ASSETS, mot, "footage")
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if fn.lower().endswith((".mp4", ".webm", ".mov")) and os.path.isfile(p):
                out.append((os.path.getsize(p), p))
    out.sort()
    return [p for _, p in out[:6]] or _fake_synth()


def _fake_synth(n=6):
    """一个素材包都没有时（空包起跑的 mock）：用 ffmpeg 合成几条 8 秒的测试画面当假站点的素材（不联网，缓存在系统临时目录）。"""
    d = os.path.join(tempfile.gettempdir(), "yezi-ve-fake-stock")
    os.makedirs(d, exist_ok=True)
    srcs = ["testsrc2=size=1920x1080:rate=30", "cellauto=size=1920x1080:rate=30:rule=110",
            "life=size=1920x1080:rate=30:mold=10:ratio=0.4:life_color=#d8f4ff:death_color=#0a0a12",
            "rgbtestsrc=size=1920x1080:rate=30", "smptehdbars=size=1920x1080:rate=30", "testsrc=size=1920x1080:rate=30"]
    out = []
    for i, s in enumerate(srcs[:n]):
        p = os.path.join(d, f"fake_{i + 1}.mp4")
        if not os.path.exists(p):
            tmp = p + ".part.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", s, "-t", "8", "-pix_fmt", "yuv420p",
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", tmp], capture_output=True, timeout=300)
            if os.path.exists(tmp):
                os.replace(tmp, p)
        if os.path.exists(p):
            out.append(p)
    return out


def _search_fake(query, kind, n):
    pool = _fake_pool()
    inc, _ = _fts_query(query)
    out = []
    for i, p in enumerate(pool[: max(1, min(n, len(pool)))]):
        fid = f"{hashlib.md5((query + str(i)).encode()).hexdigest()[:8]}"
        out.append(dict(site="fake", id=fid, key=f"fake:{fid}", title=f"fake {' '.join(w.replace(' ', '-') for w in inc)} #{i + 1}",
                        descr=f"假站点：{os.path.basename(p)}",
                        tags=", ".join(inc), url="file://" + p, w=1920, h=1080, fps=30.0, dur=8.0, page="file://" + p, author="fake",
                        license=LIC["fake"][0], license_url="", thumb="", pub="", stype_guess="实拍", score=1.0 - i * 0.1))
    return out


# ----------------------------------------------------------------------------- search
def available_sites(kind="video"):
    """{site: (能用 bool, 一句话)}；只判 key 有没有，不读出 key"""
    out = {}
    for s in ("pexels", "mixkit", "commons", "nasa", "pixabay"):
        if s == "pexels":
            k = _key("pexels") is not None
            age = index_age_days("pexels", kind)
            out[s] = (k or age is not None, "官方 API（本机有 key）" if k else
                      (f"本地 sitemap 索引（{age:.0f} 天前建）" if age is not None else "本地 sitemap 索引还没建：stock.py index --site pexels"))
        elif s == "mixkit":
            age = index_age_days("mixkit", "video")
            out[s] = (kind == "video" and age is not None, "只有视频；" + (f"本地 sitemap 索引（{age:.0f} 天前建）" if age is not None
                                                                     else "索引还没建：stock.py index --site mixkit"))
        elif s == "pixabay":
            k = _key("pixabay") is not None
            out[s] = (k, "官方 API（本机有 key）" if k else "没有 key（网站对脚本是 Cloudflare 验证，不绕）")
        else:
            out[s] = (True, "实时 API")
    return out


def search(query, kind="video", n=20, sites=None, landscape=True, min_short=720, auto_index=True):
    """几个站一起搜，按站轮流排（每站各自按相关度）。返回条目都是免费授权、能直接下载的。"""
    if FAKE:
        return _search_fake(query, kind, n)
    sites = list(sites or DEFAULT_SITES)
    per = {}
    for s in sites:
        try:
            if s == "pexels":
                k = _key("pexels")
                if k:
                    per[s] = _search_pexels_api(query, kind, n, k)
                else:
                    if auto_index and index_age_days("pexels", kind) is None and kind == "video":
                        build_index("pexels", kind, log=lambda m: _log(m))
                    per[s] = _search_index("pexels", kind, query, n, landscape, min_short)
            elif s == "mixkit":
                if kind != "video":
                    continue
                age = index_age_days("mixkit", "video")
                if auto_index and (age is None or age > 14):
                    build_index("mixkit", "video", log=lambda m: _log(m))
                per[s] = _search_index("mixkit", "video", query, n, landscape, min_short)
            elif s == "commons":
                per[s] = _search_commons(query, kind, n)
            elif s == "nasa":
                per[s] = _search_nasa(query, kind, n)
            elif s == "pixabay":
                k = _key("pixabay")
                per[s] = _search_pixabay_api(query, kind, n, k) if k else []
        except Exception as e:  # noqa: BLE001
            _log(f"search {s} 出错：{type(e).__name__}: {str(e)[:200]}")
            per[s] = []
    out = []
    i = 0
    while len(out) < n and any(i < len(v) for v in per.values()):
        for s in sites:
            v = per.get(s) or []
            if i < len(v) and len(out) < n:
                out.append(v[i])
        i += 1
    try:
        os.makedirs(SEARCH_DIR, exist_ok=True)
        with open(os.path.join(SEARCH_DIR, "stock_searches.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now(), "query": query, "kind": kind, "sites": sites,
                                 "hits": [{k: d.get(k) for k in ("key", "title", "w", "h", "fps", "dur", "url", "page")} for d in out]},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass
    return out


def lookup(key, kind="video"):
    """'pexels:855709' / 'mixkit:178' / 'commons:File:Xxx.webm' -> 条目（命令行 fetch 用）"""
    site, _, pid = key.partition(":")
    if site in ("pexels", "mixkit"):
        c = _db(site, kind)
        if c is not None:
            r = c.execute("SELECT id, title, descr, tags, url, w, h, fps, dur, page, author, thumb, pub FROM v WHERE id = ? LIMIT 1", (pid,)).fetchone()
            c.close()
            if r:
                lic, lic_url = LIC[site]
                it = dict(zip(("id", "title", "descr", "tags", "url", "w", "h", "fps", "dur", "page", "author", "thumb", "pub"), r),
                          site=site, key=key, license=lic, license_url=lic_url)
                it["stype_guess"] = guess_stype(it)
                return it
        return None
    if site == "commons":
        return commons_lookup(pid, kind)
    return None


# ----------------------------------------------------------------------------- 下载
def _ffprobe(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,avg_frame_rate,r_frame_rate,codec_name:format=duration,size", "-of", "json", path],
                           capture_output=True, text=True, timeout=120, env={"PATH": BIN_PATH, "HOME": os.path.expanduser("~")})
        j = json.loads(r.stdout or "{}")
        s = (j.get("streams") or [{}])[0]
        fps = None
        for k in ("avg_frame_rate", "r_frame_rate"):
            a, _, b = str(s.get(k) or "0/0").partition("/")
            if float(b or 0) > 0 and float(a or 0) > 0:
                fps = round(float(a) / float(b), 3)
                break
        dur = float((j.get("format") or {}).get("duration") or 0) or None
        return dict(w=s.get("width"), h=s.get("height"), fps=fps, dur=round(dur, 2) if dur else None,
                    size=int((j.get("format") or {}).get("size") or os.path.getsize(path)), codec=s.get("codec_name"))
    except Exception:  # noqa: BLE001
        return None


def _remux(path):
    """ffmpeg -c copy 重新封装（修封装头里坏掉的时长；画面不重编码）。成功返回 True。"""
    tmp = path + f".remux{os.getpid()}" + os.path.splitext(path)[1]
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", path, "-map", "0", "-c", "copy", tmp],
                       capture_output=True, text=True, timeout=600, env={"PATH": BIN_PATH, "HOME": os.path.expanduser("~")})
    m = _ffprobe(tmp) if r.returncode == 0 and os.path.exists(tmp) else None
    if m and m.get("dur") and m["dur"] < 4 * 3600:
        os.replace(tmp, path)
        _log(f"重新封装 {os.path.basename(path)}：时长头修好（{m['dur']} s）")
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def _head(url):
    st, hd, _ = http(url, method="HEAD", timeout=30, tries=3)
    return st, int(hd.get("content-length") or 0), hd.get("content-type", "")


def resolve_url(item):
    """下载地址换成 ≤ 1080p 的那一份（Pexels uhd -> hd；Mixkit 720 -> 1080；NASA 读文件清单）。-> (url, 预计大小, 说明)"""
    site, url = item["site"], item.get("url") or ""
    if site == "fake":
        return url, None, ""
    if site == "pexels" and not re.search(r"hd_1920_1080_|_1920_1080_", url):
        alt = _tsv_links().get(str(item.get("id")))                 # 浏览器搜过的结果里有 1080p 的地址（新文件名的 hd 版猜不出来）
        if alt and alt != url and re.search(r"_1920_1080_", alt):
            st, size, ct = _head(alt)
            if st == 200 and size:
                return alt, size, "→ 1080p（_search/*.tsv）"
    if site == "pexels" and re.search(r"uhd_\d+_\d+_", url):
        fps = (_dims_from_name(url)[2] or 30)
        fs = f"{fps:g}"
        base = url.rsplit("/", 1)[0]
        stem = url.rsplit("/", 1)[1].split("-")[0].split("_")[0]
        for w, h in ((1920, 1080), (2048, 1080), (1920, 1012)):
            cand = f"{base}/{stem}-hd_{w}_{h}_{fs}fps.mp4"
            st, size, ct = _head(cand)
            if st == 200 and size:
                return cand, size, "uhd → hd"
    if site == "mixkit":
        m = re.search(r"/videos/(\d+)/\1-(\d+)\.mp4", url)
        if m:
            cand = f"https://assets.mixkit.co/videos/{m.group(1)}/{m.group(1)}-1080.mp4"
            st, size, ct = _head(cand)
            if st == 200 and size:
                return cand, size, "720 → 1080"
        # 换不到 1080p 的多半是 Restricted 条目（只给 720p、只许个人用）：fetch 前已按条目页核过授权，这里只记一笔
        _log(f"mixkit {item.get('id')}：换不到 1080p（{'没有 /videos/<id>/ 地址' if not m else 'HEAD 不通'}），只有 720p；授权以条目页为准")
    if site == "nasa":
        d = http_json(f"https://images-api.nasa.gov/asset/{urllib.parse.quote(item['id'])}")
        hrefs = [x.get("href", "") for x in (((d or {}).get("collection") or {}).get("items") or [])]
        for pat in ("~large.mp4", "~orig.mp4", "~medium.mp4", "~large.jpg", "~orig.jpg"):
            for hh in hrefs:
                if hh.endswith(pat):
                    st, size, _ = _head(hh.replace("http://", "https://"))
                    if st == 200:
                        return hh.replace("http://", "https://"), size, pat
        return "", None, "NASA 文件清单里没有能用的文件"
    if site == "commons":                                         # 大小 Commons API 给了（转码的没有）；少一次 HEAD，免得 upload.wikimedia.org 限流
        return url, (item.get("size") if url == (item.get("orig_url") or url) and "/transcoded/" not in url else None), ""
    st, size, ct = _head(url)
    if st != 200:
        return url, None, f"HEAD {st}"
    return url, size, ""


def _download(url, dest, expect=None, limit=None):
    """断点续传下载到 dest（先写 dest.part）；大小对得上才改名。limit = 字节上限（默认 MAX_BYTES）。-> 字节数"""
    limit = limit or MAX_BYTES
    part = dest + ".part"
    total = expect
    for a in range(MAX_TRIES):
        cw = cooling(url)
        if cw > 0:
            raise RateLimited(f"{urllib.parse.urlsplit(url).netloc} 限流中，{cw:.0f} s 后再下（已下的部分留在 .part，下次续传）")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if total and have == total:
            os.replace(part, dest)
            return total
        hdr = {"User-Agent": UA, "Accept-Encoding": "identity"}
        if have:
            hdr["Range"] = f"bytes={have}-"
        _throttle(url)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=90) as r:
                if have and r.status == 200:                      # 服务器不认断点：从头来
                    have = 0
                cr = r.headers.get("Content-Range") or ""
                m = re.search(r"/(\d+)$", cr)
                if m:
                    total = int(m.group(1))
                elif r.headers.get("Content-Length"):
                    total = have + int(r.headers["Content-Length"])
                if total and total > limit:
                    raise RuntimeError(f"文件 {total / 1e6:.0f} MB，超过上限 {limit / 1e6:.0f} MB")
                with open(part, "ab" if have else "wb") as fh:
                    shutil.copyfileobj(r, fh, 1 << 20)
            got = os.path.getsize(part)
            if total and got != total:
                _log(f"下载不完整 {os.path.basename(dest)}：{got}/{total}，续传（第 {a + 1} 次）")
                continue
            os.replace(part, dest)
            return got
        except urllib.error.HTTPError as e:
            if e.code == 416 and have and (total is None or have >= total):
                os.replace(part, dest)
                return have
            ra = _retry_after(e)
            if e.code == 429 and ra and ra > COOL_MAX_WAIT:
                _cool_set(url, ra)
                raise RateLimited(f"{urllib.parse.urlsplit(url).netloc} 限流（Retry-After {ra:.0f} s），过后再下")
            if e.code in (429, 500, 502, 503, 504) and a + 1 < MAX_TRIES:
                _log(f"下载 HTTP {e.code} {os.path.basename(dest)}，{(ra or min(60.0, 5.0 * 2 ** a)):.0f} s 后重试（第 {a + 1} 次）")
                time.sleep(ra if ra else min(60.0, 5.0 * 2 ** a))
                continue
            raise RuntimeError(f"HTTP {e.code}")
        except RuntimeError:
            raise
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            _log(f"下载出错 {os.path.basename(dest)}：{type(e).__name__}，{5 * 2 ** a} s 后续传")
            time.sleep(min(60.0, 5.0 * 2 ** a))
    raise RuntimeError(f"重试 {MAX_TRIES} 次还没下完")


def license_ok(item):
    site = item.get("site")
    if site in ("pexels", "mixkit", "pixabay", "nasa", "fake"):
        return bool(item.get("license"))
    if site == "commons":
        return commons_license_ok((item.get("license") or "").split("；")[0].split("（")[0])
    return False


def _safe(s):
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.U).strip("_")
    return s[:120]


def file_name(item, url=None):
    url = url or item.get("url") or ""
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower() or ".mp4"
    if ext not in (".mp4", ".webm", ".mov", ".ogv", ".mkv", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"):
        ext = ".mp4"
    if item["site"] == "commons":
        t = item["id"][5:] if item["id"].startswith("File:") else item["id"]
        stem = "commons-" + _safe(os.path.splitext(t)[0])
        return stem + os.path.splitext(t)[1].lower() if os.path.splitext(url)[1].lower() == os.path.splitext(t)[1].lower() else stem + ext
    return f"{item['site']}-{_safe(str(item['id']))}{ext}"


def _stype_fields(stype):
    s = stype or ""
    return {"cg": True} if s.startswith("CG") else ({"burned_text": True} if s.startswith("带字幕") else {})


def _sources(pack_dir):
    p = os.path.join(pack_dir, "sources.json")
    d = _read_json(p) or {"motif": os.path.basename(os.path.abspath(pack_dir)), "note": "stock.py 建的素材清单", "items": []}
    d.setdefault("items", [])
    return p, d


def fetch(item, pack_dir, use="", note="", stype=None, query=None, status="已下载（stock.py）", max_mb=None):
    """下一条到 pack_dir/footage/，登记 sources.json + CATALOG.md。授权不明确的不下（ValueError）；Mixkit 先读条目页核授权，
    不是 videoFree（免费商用）的不下。max_mb = 这一条的大小上限（编排器的请求通道给 300，免得 4K 长片卡住一镜）。
    素材包里已经登记过同一个文件（人工整理过的条目）：不重下、不改它的 use / content / stype / status / source，
    只补空着的元数据（作者、site_id），这次的用途记进它的 requests 列表。
    -> {'path': 绝对路径, 'name': 文件名, 'source': sources.json 里的那条, 'reused': 素材包里原来就有}"""
    if not license_ok(item):
        raise ValueError(f"授权不明确，不下：{item.get('key')} {item.get('license')}")
    if item.get("site") == "mixkit" and not FAKE:
        lic, why = mixkit_license(item)
        if lic != "videoFree":
            _log(f"不下 {item.get('key')}：Mixkit 条目页授权 {lic or '核不了（' + why + '）'}")
            raise ValueError(f"Mixkit {item.get('key')} 不是免费商用授权（条目页 {lic or '核不了：' + why}；"
                             "videoRestricted = 720p、只许个人用），不下")
    fdir = os.path.join(pack_dir, "footage")
    os.makedirs(fdir, exist_ok=True)
    url, size, why = resolve_url(item)
    if not url:
        raise RuntimeError(f"{item.get('key')} 没有能下的地址：{why}")
    name = file_name(item, url)
    for it0 in (_read_json(os.path.join(pack_dir, "sources.json")) or {}).get("items", []):   # 清单里已经登记过（待下载）的沿用老文件名
        pg = (it0.get("page_url") or "").rstrip("/")
        if pg and pg in ((item.get("page") or "").rstrip("/"), f"https://www.pexels.com/video/{item.get('id')}") and it0.get("file"):
            if os.path.splitext(it0["file"])[1].lower() == os.path.splitext(name)[1].lower():
                name = os.path.basename(it0["file"])
            break
    dest = os.path.join(fdir, name)
    rel = f"footage/{name}"
    reg = next((it for it in (_read_json(os.path.join(pack_dir, "sources.json")) or {}).get("items", []) if it.get("file") == rel), None)
    if reg is not None and os.path.isfile(dest):                 # 素材包里原来就有：直接用，不动人工写的字段
        with pack_lock(pack_dir):
            p, d = _sources(pack_dir)
            ent = next((it for it in d["items"] if it.get("file") == rel), None)
            if ent is not None:
                fill = {"site_id": item.get("key"), "creator": item.get("author") or ""}
                for k, v in fill.items():
                    if v and not ent.get(k):
                        ent[k] = v
                if use or note or query:
                    rq = list(ent.get("requests") or [])
                    rq.append({"at": _now(), "use": use, "note": note, "query": query or ""})
                    ent["requests"] = rq[-20:]
                _write_json(p, d)
        _log(f"复用 {item.get('key')} -> {os.path.relpath(dest, MD)}（素材包里原来就有，登记不改）")
        return {"path": os.path.abspath(dest), "name": name, "source": ent or reg, "reused": True}
    limit = int(max_mb * 1e6) if max_mb else MAX_BYTES
    if FAKE:
        if not os.path.exists(dest):
            shutil.copy2(url[len("file://"):], dest)
    elif not os.path.exists(dest) or (size and os.path.getsize(dest) != size):
        if size and size > limit:
            raise RuntimeError(f"{item.get('key')} 文件 {size / 1e6:.0f} MB 超过上限 {limit / 1e6:.0f} MB（找 1080p 的版本）")
        _log(f"下载 {item.get('key')} -> {os.path.relpath(dest, MD)}（{(size or 0) / 1e6:.1f} MB{'，' + why if why else ''}）")
        _download(url, dest, size, limit)
    m = _ffprobe(dest) or {}
    if m.get("dur") and m["dur"] > 4 * 3600 and not FAKE:          # 封装头里的时长坏了（Commons 的老 webm 常见）：-c copy 重新封装
        fixed = _remux(dest)
        if fixed:
            m = _ffprobe(dest) or m
    if not m.get("w") or (item.get("site") != "fake" and not str(name).lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"))
                          and not m.get("dur")):
        bad = dest + ".bad"
        os.replace(dest, bad)
        raise RuntimeError(f"{name} 不是能读的视频 / 图片（ffprobe 读不出），改名 {os.path.basename(bad)}")
    st = stype or item.get("stype_guess") or guess_stype(item)
    if use == "overlay" and st == "实拍":
        st = "元素（待烘焙）"
    content = (note + "｜" if note else "") + f"原标题：{item.get('title', '')}"
    ent = dict(file=rel, status=status, use=use, content=content, w=m.get("w"), h=m.get("h"), fps=m.get("fps"), dur=m.get("dur"),
               size=m.get("size"), source=SITE_NAME.get(item["site"], item["site"]), page_url=item.get("page", ""), file_url=url,
               creator=item.get("author", ""), license=item.get("license", "") + (f"（{item['license_url']}）" if item.get("license_url") else ""),
               stype=st, search_query=query or "", site_id=item.get("key"), fetched_at=_now(), **_stype_fields(st))
    if item.get("site") == "mixkit" and not FAKE:
        ent["license_checked"] = "Mixkit 条目页 data-license=videoFree（stock.py 下载前核过）"
    with pack_lock(pack_dir):
        p, d = _sources(pack_dir)
        old = next((i for i, it in enumerate(d["items"]) if it.get("file") == rel), None)
        if old is None:
            d["items"].append(ent)
        else:                                                      # 清单里登记过（待下载）/ 别的进程刚登记了：人工字段留着，
            o = d["items"][old]                                    # 实测的技术字段（尺寸、时长、下载地址）更新，空的补上
            for k in ("w", "h", "fps", "dur", "size", "file_url", "fetched_at"):
                if ent.get(k) not in (None, ""):
                    o[k] = ent[k]
            if "待" in str(o.get("status") or ""):
                o["status"] = status
            for k, v in ent.items():
                if v not in (None, "") and not o.get(k):
                    o[k] = v
            ent = o
        _write_json(p, d)
        if old is None:
            catalog_row(pack_dir, ent)
    _log(f"入库 {item.get('key')} -> {os.path.relpath(dest, MD)}（{m.get('w')}×{m.get('h')}@{m.get('fps')} {m.get('dur')} s，{st}）")
    return {"path": os.path.abspath(dest), "name": name, "source": ent}


# ----------------------------------------------------------------------------- CATALOG.md
CAT_COLS = ["#", "文件", "状态", "用在哪", "内容", "来源类型", "时长", "分辨率", "来源", "许可"]


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _row_for(ent, cols, n):
    lic = (ent.get("license") or "").split("（http")[0]
    fps = ent.get("fps")
    v = {"#": str(n), "文件": f"`{ent['file']}`", "状态": ent.get("status", ""), "用在哪": ent.get("use", ""),
         "内容": (ent.get("content") or "").replace("|", "／"), "来源类型": ent.get("stype") or "实拍",
         "时长": f"{float(ent['dur']):.1f} s" if ent.get("dur") else "待实测",
         "分辨率": f"{ent.get('w')}×{ent.get('h')}" + (f"@{float(fps):g}" if fps else ""),
         "来源": f"[{ent.get('source', '')}]({ent.get('page_url', '')})" if ent.get("page_url") else ent.get("source", ""),
         "许可": lic.replace("|", "／")}
    return "| " + " | ".join(v.get(c, "") for c in cols) + " |"


def catalog_row(pack_dir, ent, remove=False):
    """CATALOG.md「## 实拍」表补 / 改一行（按文件名认）；表没有「来源类型」列就加上（老的行按 sources.json 填）。"""
    p = os.path.join(pack_dir, "CATALOG.md")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    else:
        lines = [f"# 素材包：{os.path.basename(os.path.abspath(pack_dir))}", "", "stock.py 建的素材清单（来源类型：实拍 / CG / 带字幕 / 元素）。",
                 "", "## 实拍", "", "| " + " | ".join(CAT_COLS) + " |", "|" + "---|" * len(CAT_COLS), ""]
    try:
        h0 = next(i for i, l in enumerate(lines) if l.strip().startswith("## 实拍"))
        t0 = next(i for i in range(h0, len(lines)) if lines[i].strip().startswith("|"))
    except StopIteration:
        lines += ["", "## 实拍", "", "| " + " | ".join(CAT_COLS) + " |", "|" + "---|" * len(CAT_COLS)]
        t0 = len(lines) - 2
    t1 = t0
    while t1 + 1 < len(lines) and lines[t1 + 1].strip().startswith("|"):
        t1 += 1
    cols = _cells(lines[t0])
    if "来源类型" not in cols:                                      # 加列：放在「内容」后面
        at = cols.index("内容") + 1 if "内容" in cols else len(cols)
        src = {it.get("file"): it for it in (_read_json(os.path.join(pack_dir, "sources.json")) or {}).get("items", [])}
        cols.insert(at, "来源类型")
        new = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for l in lines[t0 + 2:t1 + 1]:
            c = _cells(l)
            f = re.sub(r"^`|`$", "", c[cols.index("文件")] if "文件" in cols and cols.index("文件") < len(c) else "")
            it = src.get(f) or {}
            c.insert(at, it.get("stype") or ("CG" if it.get("cg") else "带字幕" if it.get("burned_text") else "实拍"))
            new.append("| " + " | ".join(c) + " |")
        lines[t0:t1 + 1] = new
        t1 = t0 + len(new) - 1
    rows = lines[t0 + 2:t1 + 1]
    fi = cols.index("文件") if "文件" in cols else 1
    hit = None
    nums = []
    for k, l in enumerate(rows):
        c = _cells(l)
        if c and c[0].isdigit():
            nums.append(int(c[0]))
        if len(c) > fi and c[fi].strip("`") == ent["file"]:
            hit = k
    if remove:
        if hit is not None:
            del lines[t0 + 2 + hit]
    elif hit is not None:
        n = _cells(rows[hit])[0]
        lines[t0 + 2 + hit] = _row_for(ent, cols, n)
    else:
        lines.insert(t1 + 1, _row_for(ent, cols, (max(nums) + 1) if nums else 1))
    _write_text(p, "\n".join(lines))


def annotate(pack_dir, name, **fields):
    """看过之后改 sources.json 那一条（stype / content / use / status / note / cg / burned_text），CATALOG 同步。"""
    with pack_lock(pack_dir):
        return _annotate(pack_dir, name, **fields)


def _annotate(pack_dir, name, **fields):
    p, d = _sources(pack_dir)
    rel = name if name.startswith("footage/") else f"footage/{name}"
    for it in d["items"]:
        if it.get("file") == rel:
            if "stype" in fields:
                it.pop("cg", None)
                it.pop("burned_text", None)
                it.update(_stype_fields(fields["stype"]))
            it.update({k: v for k, v in fields.items() if v is not None})
            _write_json(p, d)
            catalog_row(pack_dir, it)
            return it
    raise KeyError(f"{rel} 不在 {p}")


def reject(pack_dir, name, why):
    """看过不收：文件移到 <素材包>/_rejected/（不删），sources.json 去掉这条、记进 rejected；CATALOG 去掉这一行、记进「看过不收」。"""
    with pack_lock(pack_dir):
        return _reject(pack_dir, name, why)


def _reject(pack_dir, name, why):
    p, d = _sources(pack_dir)
    rel = name if name.startswith("footage/") else f"footage/{name}"
    it = next((x for x in d["items"] if x.get("file") == rel), None)
    src = os.path.join(pack_dir, rel)
    if os.path.exists(src):
        rd = os.path.join(pack_dir, "_rejected")
        os.makedirs(rd, exist_ok=True)
        os.replace(src, os.path.join(rd, os.path.basename(rel)))
    d["items"] = [x for x in d["items"] if x.get("file") != rel]
    rec = dict({k: (it or {}).get(k) for k in ("file", "source", "page_url", "license", "site_id", "content", "use")}, why=why, at=_now())
    d.setdefault("rejected", [])
    d["rejected"] = [x for x in d["rejected"] if x.get("file") != rel] + [rec]
    _write_json(p, d)
    if it:
        catalog_row(pack_dir, it, remove=True)
    cp = os.path.join(pack_dir, "CATALOG.md")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as fh:
            s = fh.read()
        head = "## 看过不收（stock.py）"
        line = f"- `{rel}`（{rec.get('site_id') or ''}，[{rec.get('source') or ''}]({rec.get('page_url') or ''})）：{why}"
        if head not in s:
            s = s.rstrip("\n") + f"\n\n{head}\n\n文件在 `_rejected/`（没删），不进素材清单。\n\n"
        if line not in s:
            s = s.rstrip("\n") + "\n" + line + "\n"
        _write_text(cp, s)
    return rec


# ----------------------------------------------------------------------------- 看图：联系表
def sheet(path, out, n=8, width=480):
    """8 帧（均匀取，掐头去尾）拼成 4×2 的联系表，左上角写秒数。-> out"""
    m = _ffprobe(path) or {}
    dur = float(m.get("dur") or 0)
    if not dur and not path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise RuntimeError(f"{path} 读不出时长")
    with tempfile.TemporaryDirectory(prefix="sheet-") as td:
        ts = [dur * (i + 0.5) / n for i in range(n)] if dur else [0.0]
        for i, t in enumerate(ts):
            subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale={width}:-2,drawtext=text='{t:.1f}s':x=8:y=8:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.5",
                            os.path.join(td, f"f{i:02d}.jpg")], capture_output=True, timeout=120, env={"PATH": BIN_PATH, "HOME": os.path.expanduser("~")})
        got = sorted(f for f in os.listdir(td) if f.endswith(".jpg"))
        if not got:
            raise RuntimeError(f"{path} 一帧都没抽出来")
        cols = min(4, len(got))
        rows = (len(got) + cols - 1) // cols
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(td, "f%02d.jpg"),
                        "-vf", f"tile={cols}x{rows}:padding=4:color=gray", "-frames:v", "1", "-q:v", "3", out],
                       capture_output=True, timeout=120, env={"PATH": BIN_PATH, "HOME": os.path.expanduser("~")})
    return out


# ----------------------------------------------------------------------------- 叠层（SR.element 的 overlays.json）
KIND_DEF = dict(blend="screen", tint=0.3, gam=0.7, glow=0.3, speed=1.0, hot=0.7)
KIND_KEYS = ("what", "blend", "tint", "gam", "glow", "speed", "hot", "interp", "fin")


def _engine_run(args, pack_dir, timeout=1800, elements=None):
    env = {"PATH": BIN_PATH, "HOME": os.path.expanduser("~"),
           "CHROMA_ELEMENTS": elements or os.path.join(os.path.abspath(pack_dir), "overlays.json")}
    r = subprocess.run([ENGINE_PY, "-m", "src.tools.elements"] + args, cwd=ENGINE, capture_output=True, text=True, timeout=timeout, env=env)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "bake.log"), "a", encoding="utf-8") as fh:
        fh.write(f"[{_now()}] elements {' '.join(args)} rc={r.returncode}\n{(r.stdout or '')[-3000:]}\n{(r.stderr or '')[-3000:]}\n")
    return r


# 叠层自动验收（11-05 复核 A2 / A4：覆盖率挡不住「贴图矩形边」「短划线雪」「整块平的」）。在引擎 Python 里读烘好的键（16 bit png），
# 量四样：cov 覆盖率；side = 四条边内侧 20–80 px 的键均值 / 键最密那 2 % 区块（60 px 见方）的均值（素材内容贴着画面边：放到一点（pos）时会露出直边）；
# dash = 小亮块（12–800 px）里细长（长短轴比 ≥2.5 / ≥3）的占比（运动模糊成的「短划线雪」）；flat = 键里大片平的（15 px 内几乎不变）
# 占整幅画面的比例（整块软烟 / 死白矩形）。另出一张「放到一点」检查图：键按 c 放到画面中心偏右下、叠在暗底上，贴边的直线一眼能看到。
# 校准：按 26 个烘过的叠层窗口实测（短划线雪、水面线、整块烟不过；小点、虚焦颗粒、气泡过），阈值见下。
OV_LIMITS = dict(cov_max=0.5, cov_min=0.0005, dash_n=60, dash=0.5, dash3=0.38, flat=0.08, side=0.2)
_OVCHECK = r"""
import json, os, sys
import cv2
import numpy as np
d, out, placed, cx, cy = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
fs = sorted(f for f in os.listdir(d) if f.endswith('.png') and '.tmp' not in f)
pick = [fs[int(i)] for i in np.linspace(0, len(fs) - 1, min(8, len(fs)))]
R = dict(cov=[], side=[], side_abs=[], dash=[], dash3=[], dash_n=[], flat=[], plat=[])
mid = None
for q, f in enumerate(pick):
    k = cv2.imread(os.path.join(d, f), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535.0
    if k.shape[1] != 1920:
        k = cv2.resize(k, (1920, 1080), interpolation=cv2.INTER_AREA)
    if q == len(pick) // 2:
        mid = k
    H, W = k.shape
    on = k > 0.1
    R['cov'].append(float(on.mean()))
    ref = float(np.percentile(cv2.resize(k, (32, 18), interpolation=cv2.INTER_AREA), 98)) + 1e-3
    st = [float(k[:, 20:80].mean()), float(k[:, W - 80:W - 20].mean()), float(k[20:80, :].mean()), float(k[H - 80:H - 20, :].mean())]
    R['side'].append(max(st) / ref)
    R['side_abs'].append(max(st))
    R['plat'].append(float((k >= 0.9).sum()) / max(1, int(on.sum())))
    m_ = cv2.blur(k, (15, 15))
    sd = np.sqrt(np.maximum(cv2.blur(k * k, (15, 15)) - m_ * m_, 0))
    on2 = k > 0.25
    R['flat'].append(float(((sd < 0.015) & on2).sum()) / float(k.size))
    n, lab, _, _ = cv2.connectedComponentsWithStats((k > 0.3).astype(np.uint8), connectivity=8)
    if n <= 1:
        R['dash'].append(0.0); R['dash3'].append(0.0); R['dash_n'].append(0)
        continue
    ys, xs = np.nonzero(lab)
    l = lab[ys, xs]
    xs = xs.astype(np.float64); ys = ys.astype(np.float64)
    cnt = np.bincount(l, minlength=n).astype(np.float64)
    c = np.maximum(cnt, 1)
    mx, my = np.bincount(l, xs, n) / c, np.bincount(l, ys, n) / c
    vxx = np.bincount(l, xs * xs, n) / c - mx * mx + 1 / 12
    vyy = np.bincount(l, ys * ys, n) / c - my * my + 1 / 12
    vxy = np.bincount(l, xs * ys, n) / c - mx * my
    tr, det = vxx + vyy, vxx * vyy - vxy * vxy
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    el = np.sqrt((tr / 2 + disc) / np.maximum(tr / 2 - disc, 1e-6))
    sel = (cnt >= 12) & (cnt <= 800)
    sel[0] = False
    sm = max(1, int(sel.sum()))
    R['dash'].append(float((sel & (el >= 2.5)).sum()) / sm)
    R['dash3'].append(float((sel & (el >= 3.0)).sum()) / sm)
    R['dash_n'].append(int((sel & (el >= 2.5)).sum()))
res = dict(frames=len(pick), cov=round(float(np.mean(R['cov'])), 4), side=round(float(np.median(R['side'])), 3),
           side_abs=round(float(np.median(R['side_abs'])), 4), dash=round(float(np.median(R['dash'])), 3),
           dash3=round(float(np.median(R['dash3'])), 3), dash_n=int(np.median(R['dash_n'])), flat=round(float(np.median(R['flat'])), 4),
           plat=round(float(np.median(R['plat'])), 3))
if mid is not None and placed:
    s_ = 0.75
    M = np.float32([[s_, 0, 960 + 300 - s_ * cx], [0, s_, 540 + 180 - s_ * cy]])
    kk = cv2.warpAffine(mid, M, (1920, 1080), flags=cv2.INTER_LINEAR, borderValue=0)
    ink = np.full((1080, 1920, 3), 0.04, np.float32)
    lt = np.clip(kk, 0, 1.5)[..., None] ** 1.25 * np.array([1.0, 0.97, 0.9], np.float32)
    img = 1 - (1 - ink) * (1 - np.clip(lt, 0, 1))
    img = (np.clip(img, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
    x0, y0 = int(960 + 300 - s_ * cx), int(540 + 180 - s_ * cy)
    cv2.rectangle(img, (x0, y0), (x0 + int(1920 * s_), y0 + int(1080 * s_)), (0, 0, 160), 1)
    cv2.putText(img, 'placed at c (pos test, red = source frame)  side %.2f / %.4f' % (res['side'], res['side_abs']), (16, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    os.makedirs(os.path.dirname(os.path.abspath(placed)), exist_ok=True)
    cv2.imwrite(placed, cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])
    res['placed'] = placed
with open(out, 'w') as fh:
    json.dump(res, fh)
print(json.dumps(res))
"""


def _bake_dir(kind, w):
    """引擎烘好的这个窗口在哪（engine/cache/element/<ver>/<kind>/<文件名>_<src>_<dur>，和 SR._el_dir 同一个算法）"""
    stem = os.path.splitext(os.path.basename(w.get("file") or w.get("clip") or ""))[0]
    tail = os.path.join(kind, f"{stem}_{float(w.get('src', 0.0)):.2f}_{float(w.get('dur', 4.0)):.2f}")
    hits = [p for p in glob.glob(os.path.join(ENGINE, "cache", "element", "*", tail)) if os.path.isdir(p)]
    return max(hits, key=os.path.getmtime) if hits else None


def overlay_metrics(kind, w, placed=None):
    """一个烘好的窗口的自动验收量（见 OV_LIMITS 上面的说明）。量不了返回 {'error': ...}。"""
    d = _bake_dir(kind, w)
    if not d:
        return {"error": "找不到烘好的键（引擎 cache/element 里没有这个窗口）"}
    mp = os.path.join(d, "meta.json")
    meta = _read_json(mp) or {}
    c = w.get("c") or (960, 540)
    with tempfile.TemporaryDirectory(prefix="ovcheck-") as td:
        out = os.path.join(td, "m.json")
        r = subprocess.run([ENGINE_PY, "-I", "-c", _OVCHECK, d, out, placed or "", str(float(c[0])), str(float(c[1]))],
                           capture_output=True, text=True, timeout=600, env={"PATH": BIN_PATH, "HOME": os.path.expanduser("~")})
        m = _read_json(out)
    if r.returncode != 0 or not m:
        return {"error": f"量不了（rc {r.returncode}）：{(r.stderr or '').strip().splitlines()[-1:] or ''}"}
    if meta.get("cov") is not None:
        m["cov_bake"] = meta["cov"]
    return m


def ov_verdict(m, lim=None):
    """自动验收：-> (收不收, 原因, 能不能放到一点用 pos_ok)。原因写给人看（报告 / 登记里）。"""
    L = dict(OV_LIMITS, **(lim or {}))
    if not isinstance(m, dict) or m.get("error") or m.get("cov") is None:
        return False, "没量到验收数据：" + str((m or {}).get("error") or "没有覆盖率"), False
    if m["cov"] > L["cov_max"]:
        return False, f"去背景没去干净（键覆盖 {m['cov'] * 100:.0f} % 画面，像贴图）", False
    if m["cov"] < L["cov_min"]:
        return False, "去背景后几乎什么都没留下", False
    if m.get("dash_n", 0) >= L["dash_n"] and (m.get("dash", 0) >= L["dash"] or m.get("dash3", 0) >= L["dash3"]):
        return False, f"满屏细长亮条（短划线 {m['dash'] * 100:.0f} %、{m['dash_n']} 条）：运动模糊成的「线段雪」，禁用的劣质特效", False
    if m.get("flat", 0) >= L["flat"]:
        return False, f"大片平的（{m['flat'] * 100:.0f} % 画面几乎没有纹理）：整块软烟 / 死白，叠上去像一张贴图", False
    pos_ok = m.get("side", 9) < L["side"]
    return True, "", pos_ok


def _ov_table(pack_dir):
    p = os.path.join(pack_dir, "overlays.json")
    d = _read_json(p) or {"note": "实拍元素叠层表（SR.element；stock.py 建）。", "ver": "e1", "kinds": {}, "tested_not_used": {}}
    d.setdefault("kinds", {})
    return p, d


def _window(name, window):
    w = {"clip": window.get("clip") or os.path.splitext(name)[0], "file": name}
    w.update({k: v for k, v in window.items() if k not in ("clip", "file")})
    w.setdefault("src", 0.0)
    w.setdefault("dur", 4.0)
    w.setdefault("key", "median")
    return w


def _same(x, name, src):
    return x.get("file") == name and abs(float(x.get("src", 0)) - float(src)) < 1e-6


def bake_overlay(pack_dir, name, kind, window, kind_spec=None, sheet_out=None, bake=True, by=None, timeout=1800):
    """把素材 name（pack_dir/footage/ 里的文件名）的一个窗口烘成 kind 的叠层，量自动验收，出检查图。
    window = {src, dur, key, lo, hi, gam, dil, blur, res, edge, cut / ell / below / above / ragged, c, what, clip}；kind_spec = 这个
    kind 的整体参数（what / blend / tint / gam / glow / speed / hot / interp / fin），只在新建或给了时写。

    by=None（人工：F / 看过图的人调参）：直接写进 overlays.json（同一个文件 + 起点的窗口替换），再烘焙、量、出检查图。
    by='stockreq' 等（自动通道，没人看图）：
      · 人工整理过的 kind（有没标 by 的窗口）不往里加、不替换任何窗口（ValueError：换个 kind 名）；
      · 先写进一张临时表（<素材包>/overlays.stage-<pid>.json）烘焙、量，过了自动验收（ov_verdict）才并进 overlays.json，
        没过记进 tested_not_used；超时 / 出错时正式表一个字都没改；
      · 窗口带 by、auto_check（验收量）、pos_ok（素材贴边的 = False：只能整屏用，不能 pos 放到一点）；默认 edge 160 软边。
    -> 那一条窗口（另带 _bake：rc、stats、ok、why、pos_ok、merged、检查图路径）"""
    if not by:
        return _bake_manual(pack_dir, name, kind, window, kind_spec, sheet_out, bake)
    w = _window(name, dict({"edge": 160.0}, **window))
    w["by"] = by
    with pack_lock(pack_dir):
        p, d = _ov_table(pack_dir)
        K0 = d["kinds"].get(kind)
        if K0 and any(not x.get("by") for x in K0.get("wins", [])):
            raise ValueError(f"叠层 {kind} 是人工整理过的（有没标 by 的窗口），自动通道不往里加、不替换：换个 kind 名")
        K = dict(K0) if K0 else dict(KIND_DEF, what=(kind_spec or {}).get("what") or window.get("what") or kind, wins=[])
        for k in KIND_KEYS:
            if kind_spec and k in kind_spec:
                K[k] = kind_spec[k]
        K["wins"] = [x for x in (K0 or {}).get("wins", []) if not _same(x, name, w["src"])] + [w]
        ver = d.get("ver", "e1")
    wi = len(K["wins"]) - 1
    out = dict(w)
    if FAKE and os.environ.get("STOCK_FAKE_BAKE") != "1":       # mock：不烘，直接并进表
        info = {"skipped": "STOCK_FAKE", "ok": True, "pos_ok": False, "merged": True}
        w["pos_ok"] = False
        with pack_lock(pack_dir):
            p, d = _ov_table(pack_dir)
            Kn = d["kinds"].get(kind) or {k: v for k, v in K.items() if k != "wins"}
            Kn["wins"] = [x for x in Kn.get("wins", []) if not _same(x, name, w["src"])] + [w]
            d["kinds"][kind] = Kn
            _write_json(p, d)
        out["_bake"] = info
        return out
    stage = os.path.join(pack_dir, f"overlays.stage-{os.getpid()}-{_safe(kind)}.json")
    _write_json(stage, {"note": "stock.py 自动通道的临时表（烘焙、量完就删）", "ver": ver, "kinds": {kind: K}})
    info = {"rc": None}
    try:
        if bake:
            r = _engine_run(["bake", "--kind", kind], pack_dir, timeout=timeout, elements=stage)
            info["rc"] = r.returncode
            if r.returncode != 0:
                info["err"] = (r.stderr or "")[-600:]
            else:
                tag = f"{_safe(kind)}_{wi}"
                placed = os.path.join(pack_dir, "_review", f"el_{tag}_placed.jpg")
                m = overlay_metrics(kind, w, placed)
                info["stats"] = m
                ok, why, pos_ok = ov_verdict(m)
                so = sheet_out or os.path.join(pack_dir, "_review", f"el_{_safe(kind)}{'' if ok else '.rejected'}.jpg")
                r2 = _engine_run(["sheet", "--out", so, "--kind", kind], pack_dir, timeout=600, elements=stage)
                if ok and r2.returncode != 0:                      # 检查图都出不来（抽不出原片帧）：没人能事后看图，不收
                    ok, why = False, f"检查图出不来（elements sheet rc {r2.returncode}）"
                info.update(ok=ok, why=why, pos_ok=pos_ok, placed=m.get("placed"), sheet=so if r2.returncode == 0 else None,
                            sheet_rc=r2.returncode)
    except subprocess.TimeoutExpired:
        info["err"] = f"烘焙超时（{timeout}s）"
    finally:
        try:
            os.remove(stage)
        except OSError:
            pass
    ok = bool(info.get("ok"))
    with pack_lock(pack_dir):
        p, d = _ov_table(pack_dir)
        if ok:
            w.update(auto_check={k: v for k, v in (info.get("stats") or {}).items() if k != "placed"}, pos_ok=bool(info.get("pos_ok")))
            if not w["pos_ok"]:
                w["what"] = (str(w.get("what") or "") + "（只能整屏用：素材贴着画面边，pos 放到一点会露出直边）").strip()
            Kn = d["kinds"].get(kind)
            if Kn and any(not x.get("by") for x in Kn.get("wins", [])):
                ok = False
                info["why"] = f"并表时 {kind} 已经有人工窗口，没并"
            else:
                Kn = Kn or {k: v for k, v in K.items() if k != "wins"}
                Kn["wins"] = [x for x in Kn.get("wins", []) if not _same(x, name, w["src"])] + [w]
                d["kinds"][kind] = Kn
                out = dict(w)
        if not ok:
            d.setdefault("tested_not_used", {})[f"{kind}:{os.path.splitext(name)[0]}@{w['src']}"] = \
                f"自动通道（{by}）验收没过：{info.get('why') or info.get('err') or '烘焙失败'}（{_now()[:16]}）"
        _write_json(p, d)
    info["ok"], info["merged"] = ok, ok
    out["_bake"] = info
    return out


def _bake_manual(pack_dir, name, kind, window, kind_spec=None, sheet_out=None, bake=True):
    """人工调参的老路子：写进 overlays.json（同一个文件 + 起点的窗口替换），烘焙、出检查图、量自动验收（只报告，不拦）。"""
    with pack_lock(pack_dir):
        p, d = _ov_table(pack_dir)
        K = d["kinds"].get(kind)
        if K is None:
            K = dict(KIND_DEF, what=(kind_spec or {}).get("what") or window.get("what") or kind, wins=[])
        for k in KIND_KEYS:
            if kind_spec and k in kind_spec:
                K[k] = kind_spec[k]
        w = _window(name, window)
        wins = [x for x in K.get("wins", []) if not _same(x, name, w["src"])]
        wins.append(w)
        K["wins"] = wins
        d["kinds"][kind] = K
        _write_json(p, d)
    out = dict(w)
    if FAKE and os.environ.get("STOCK_FAKE_BAKE") != "1":
        out["_bake"] = {"skipped": "STOCK_FAKE"}
        return out
    if not bake:
        return out
    r = _engine_run(["bake", "--kind", kind], pack_dir)
    info = {"rc": r.returncode, "out": (r.stdout or "").strip().splitlines()[-len(wins):]}
    if r.returncode == 0:
        wi = len(wins) - 1
        so = sheet_out or os.path.join(pack_dir, "_review", f"el_{kind}.jpg")
        r2 = _engine_run(["sheet", "--out", so, "--kind", kind], pack_dir)
        m = overlay_metrics(kind, w, os.path.join(pack_dir, "_review", f"el_{_safe(kind)}_{wi}_placed.jpg"))
        ok, why, pos_ok = ov_verdict(m)
        info.update(sheet=so if r2.returncode == 0 else None, sheet_rc=r2.returncode, stats=m, ok=ok, why=why, pos_ok=pos_ok)
    else:
        info["err"] = (r.stderr or "")[-600:]
    out["_bake"] = info
    return out


def drop_overlay(pack_dir, kind, name=None, src=None, why="", only_by=False):
    """叠层表里撤掉一个窗口（name + src；name=None 撤整个 kind），记进 tested_not_used。
    only_by=True（自动通道用）：只撤自动通道自己写的窗口（带 by 的），人工整理过的窗口一个都不碰。"""
    with pack_lock(pack_dir):
        p = os.path.join(pack_dir, "overlays.json")
        d = _read_json(p) or {}
        K = (d.get("kinds") or {}).get(kind)
        if not K:
            return None
        if name is None:
            if only_by and any(not x.get("by") for x in K.get("wins", [])):
                return None
            d["kinds"].pop(kind)
            d.setdefault("tested_not_used", {})[kind] = why or "撤下"
        else:
            keep = [x for x in K["wins"] if not ((x.get("file") == name and (src is None or abs(float(x.get("src", 0)) - float(src)) < 1e-6))
                                                 and (x.get("by") or not only_by))]
            gone = [x for x in K["wins"] if x not in keep]
            K["wins"] = keep
            if not keep:
                d["kinds"].pop(kind)
            for x in gone:
                d.setdefault("tested_not_used", {})[f"{kind}:{os.path.splitext(name)[0]}@{x.get('src')}"] = why or "撤下"
        _write_json(p, d)
        return d


def check_overlays(pack_dir, kinds=None):
    """把素材包 overlays.json 里现有的窗口按自动验收量一遍（不改表；人工复核用）：-> {kind/wi: {stats, ok, why, pos_ok}}。
    要先烘过（stock.py bake 或引擎 src.tools.elements bake）。"""
    _, d = _ov_table(pack_dir)
    out = {}
    for k, K in d["kinds"].items():
        if kinds and k not in kinds:
            continue
        for wi, w in enumerate(K.get("wins", [])):
            m = overlay_metrics(k, w, os.path.join(pack_dir, "_review", f"el_{_safe(k)}_{wi}_placed.jpg"))
            ok, why, pos_ok = ov_verdict(m)
            out[f"{k}/{wi}"] = {"file": w.get("file"), "src": w.get("src"), "stats": m, "ok": ok, "why": why, "pos_ok": pos_ok,
                                "pos_ok_in_table": w.get("pos_ok")}
    return out


# ----------------------------------------------------------------------------- 命令行
def _print_items(items, as_json=False):
    if as_json:
        print(json.dumps(items, indent=1, ensure_ascii=False))
        return
    for i, it in enumerate(items):
        res = f"{it.get('w')}×{it.get('h')}" + (f"@{float(it['fps']):g}" if it.get("fps") else "")
        dur = f"{float(it['dur']):.0f}s" if it.get("dur") else "?s"
        print(f"{i + 1:2d}. {it['key']:<34s} {res:<16s} {dur:>5s}  [{it.get('stype_guess')}] {it.get('title', '')[:70]}")
        if it.get("descr"):
            print(f"      {it['descr'][:150]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="免费素材站：搜、下、入库、烘焙叠层")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("sites")
    s = sp.add_parser("index"); s.add_argument("--site", default="all"); s.add_argument("--kind", default="video")
    s.add_argument("--refresh", action="store_true"); s.add_argument("--max-shards", type=int)
    s = sp.add_parser("search"); s.add_argument("query"); s.add_argument("--kind", default="video"); s.add_argument("--n", type=int, default=20)
    s.add_argument("--sites"); s.add_argument("--json", action="store_true"); s.add_argument("--portrait", action="store_true")
    s = sp.add_parser("fetch"); s.add_argument("key"); s.add_argument("--pack", required=True); s.add_argument("--use", default="")
    s.add_argument("--note", default=""); s.add_argument("--stype"); s.add_argument("--kind", default="video"); s.add_argument("--query")
    s.add_argument("--max-mb", type=float)
    s = sp.add_parser("sheet"); s.add_argument("file"); s.add_argument("--out", required=True); s.add_argument("--n", type=int, default=8)
    s = sp.add_parser("bake"); s.add_argument("--pack", required=True); s.add_argument("--name", required=True); s.add_argument("--kind", required=True)
    s.add_argument("--window", required=True, help="JSON"); s.add_argument("--spec", help="kind 整体参数 JSON"); s.add_argument("--sheet-out")
    s.add_argument("--by", help="自动通道（不并进人工 kind，过验收才进表）")
    s = sp.add_parser("check"); s.add_argument("--pack", required=True); s.add_argument("--kind", action="append")
    s = sp.add_parser("annotate"); s.add_argument("--pack", required=True); s.add_argument("--name", required=True)
    s.add_argument("--set", action="append", default=[], help="k=v")
    s = sp.add_parser("reject"); s.add_argument("--pack", required=True); s.add_argument("--name", required=True); s.add_argument("--why", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "sites":
        for k, (ok, msg) in available_sites().items():
            print(f"{k:8s} {'能用' if ok else '不能用'}  {msg}")
    elif a.cmd == "index":
        sites = ["pexels", "mixkit"] if a.site == "all" else [a.site]
        for st in sites:
            if st == "mixkit" and a.kind != "video":
                continue
            n = build_index(st, a.kind, refresh=a.refresh, max_shards=a.max_shards)
            print(f"{st} {a.kind}: {n} 条 -> {_db_path(st, a.kind)}")
    elif a.cmd == "search":
        _print_items(search(a.query, a.kind, a.n, a.sites.split(",") if a.sites else None, landscape=not a.portrait), a.json)
    elif a.cmd == "fetch":
        it = lookup(a.key, a.kind)
        if not it:
            print(f"找不到 {a.key}（先 index / search）", file=sys.stderr)
            return 2
        r = fetch(it, a.pack, a.use, a.note, a.stype, a.query, max_mb=a.max_mb)
        print(json.dumps({k: r[k] for k in ("path", "name")}, ensure_ascii=False))
    elif a.cmd == "sheet":
        print(sheet(a.file, a.out, a.n))
    elif a.cmd == "bake":
        r = bake_overlay(a.pack, a.name, a.kind, json.loads(a.window), json.loads(a.spec) if a.spec else None, a.sheet_out, by=a.by)
        print(json.dumps(r, indent=1, ensure_ascii=False))
    elif a.cmd == "check":
        for k, v in check_overlays(a.pack, a.kind).items():
            st = v["stats"] or {}
            print(f"{k:14s} {'收' if v['ok'] else '不收'} {'pos 可' if v['pos_ok'] else '只整屏'}  cov {st.get('cov')} side {st.get('side')}/"
                  f"{st.get('side_abs')} dash {st.get('dash')}/{st.get('dash3')} n {st.get('dash_n')} flat {st.get('flat')}  {v['file']}@{v['src']}  {v['why']}")
    elif a.cmd == "annotate":
        kv = dict(x.split("=", 1) for x in a.set)
        for k in ("cg", "burned_text"):
            if k in kv:
                kv[k] = kv[k].lower() in ("1", "true", "yes")
        print(json.dumps(annotate(a.pack, a.name, **kv), indent=1, ensure_ascii=False))
    elif a.cmd == "reject":
        print(json.dumps(reject(a.pack, a.name, a.why), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
