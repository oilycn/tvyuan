#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 聚合源自动更新（多仓递归解包 + 网盘扫码站彻底淘汰 + 纯直链秒播版）
- 1. 【多仓与单仓智能分流】：自动递归展开多仓中的子单仓，神级子源零遗漏；严格净化多仓输出
- 2. 【网盘扫码站绝对过滤】：全面剔除阿里/夸克/115/玩偶等需要扫码登录 Cookie/Token 的网盘源，实现 100% 免扫码纯直链秒播
- 3. 【强力预淘汰】：测速前直接清洗垃圾占位符（配置中心/本地/预告/说明）、残缺站、死链仓
- 4. 【Type 3 真实物理测速评分】：并发测试核心 Jar 包真实下载带宽(KB/s)与响应延迟，死 Jar 站点直接淘汰
- 5. 【极简符号视觉优化】：移除冗长粗暴的前缀，改用 ⚡ ✨ 🚀 极简符号标识
- 6. 【Jar 自动镜像本地化】：将所有站点的核心 Jar 同步下载到本仓库 ./jars/，通过 tv.gnoix.com/jars/ 提供秒开加速
- 7. 【专属私有代理加速】：所有 GitHub 外链统一经由自身域名 https://tv.gnoix.com/https://... 加速分发，彻底摆脱第三方
- 8. 精品版 (tvbox.json / 根路径 /)：精选 120 站 (95 个高分纯直链爬虫 + 25 个实测秒播采集) + 10 最快直播
- 9. 全量版 (tvbox_full.json / 路径 /all)：海量全收录，无任何数量限制
"""
import json
import sys
import re
import subprocess
import os
import time
import hashlib
import urllib.parse
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
# 私有加速域名
MY_HOST = os.environ.get("CF_PROXY", "https://tv.gnoix.com").rstrip("/")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "oilycn/tvyuan")  # 当前 GitHub 仓库 (owner/repo)
GH_BRANCH = "master"  # 默认分支
JARS_DIR = os.path.join(WORK_DIR, "jars")  # 存放本地同步 Jar 的目录
BOUTIQUE_LIMIT = 120  # 精品版固定凑齐 120 个最强源
BOUTIQUE_LIVES_LIMIT = 10  # 精品版只取 10 个最快直播源
MAX_SPIDERS_PER_SOURCE = 8  # 精品版中单个来源最多允许入选的爬虫站数量（保证多大源百花齐放）

# 强淘汰黑名单：非影视类占位符、垃圾广告站
SKIP_KEYWORDS = [
    "配置中心", "本地", "预告", "说明", "更新", "推送", "测试", "公告",
    "留言", "网盘配置", "扫码", "失效", "防失联", "备用", "教程", "公众号"
]

# 【网盘/扫码特征库】：需要登录Cookie/Token/扫码的网盘站，精品版直接一律剔除！
PAN_EXCLUDE_KEYWORDS = [
    "网盘", "阿里", "夸克", "115", "uc", "玩偶", "木偶", "多多", "蜡笔", "至臻", "云盘",
    "pan", "wex", "wogg", "wobg", "yunpan", "upyun", "mydrive", "seedhub", "panwebshare",
    "fourkzn", "fourkfox", "fourkfm"
]

# 口碑大源白名单（给予高额基础质量加分）
TOP_TIER_SOURCES = ["aowu", "嗷呜", "fty", "饭太硬", "feimao", "肥猫", "qiao", "巧技", "xiaoma", "小马", "moyu", "摸鱼", "drpy", "道长"]

# 高清秒播高频关键词（画质加分）
QUALITY_KEYWORDS = ["4k", "秒播", "蓝光", "原画", "厂长", "金牌", "秋天", "低端", "libvio", "专线"]


def is_pan_site(site):
    """严格检测是否为网盘/扫码依赖站点"""
    raw_name = site.get("_raw_name", site.get("name", "")).lower()
    api = str(site.get("api", "")).lower()
    key = str(site.get("key", "")).lower()
    ext = str(site.get("ext", "")).lower()

    # 1. 检测名字、key、api 是否命中网盘关键词
    for kw in PAN_EXCLUDE_KEYWORDS:
        if kw in raw_name or kw in api or kw in key:
            return True

    # 2. 检测 ext 中是否包含 Token / Cookie / 网盘特征
    pan_ext_flags = ["token", "cookie", "alipan", "quark", "oauth", "alipanshare", "open_token"]
    if any(flag in ext for flag in pan_ext_flags):
        return True

    return False


def get_safe_download_url(u):
    """处理包含中文域名或特殊字符的 URL，将其转为合法的 IDN/Punycode 编码"""
    clean = u.split(";md5;")[0]
    p = urllib.parse.urlsplit(clean)
    try:
        host = p.netloc.encode("idna").decode("ascii")
    except Exception:
        host = p.netloc
    return urllib.parse.urlunsplit((p.scheme, host, urllib.parse.quote(p.path), p.query, p.fragment))


def get_jar_storage_name(jar_url):
    """根据 Jar 的原始路径生成唯一且稳定的本地文件名：[MD5前8位]_[原文件名]"""
    clean = jar_url.split(";md5;")[0]
    p = urllib.parse.urlsplit(clean)
    base_name = os.path.basename(p.path) or "spider.jar"
    base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name)
    if not any(base_name.endswith(ext) for ext in ('.jar', '.bin', '.txt', '.png', '.jpg')):
        base_name += ".jar"
    h = hashlib.md5(clean.encode("utf-8")).hexdigest()[:8]
    return f"{h}_{base_name}"


def sync_and_localize_jars(site_list, default_spider_url):
    """
    【Jar 自动镜像同步模块】：
    1. 收集入选站点涉及的所有不同 Jar 包（包括默认全局 spider）
    2. 多线程下载到仓库本地 ./jars/ 目录，持久化保存
    3. 本地计算权威真实 MD5
    4. 自动改写站点中的 jar 属性为自己的域名直链: https://tv.gnoix.com/jars/<fname>;md5;<real_md5>
    """
    os.makedirs(JARS_DIR, exist_ok=True)

    unique_jars = set()
    if default_spider_url:
        unique_jars.add(default_spider_url)
    for s in site_list:
        if s.get("jar"):
            unique_jars.add(s["jar"])

    jar_list = [j for j in unique_jars if j and j.startswith("http")]
    print(f"\n[Jar 镜像同步] 检测到 {len(jar_list)} 个不同的核心 Jar 包，开始镜像下载与校验...")

    jar_map = {}

    def _download_single_jar(j_url):
        fname = get_jar_storage_name(j_url)
        local_path = os.path.join(JARS_DIR, fname)
        src_url = get_safe_download_url(j_url)

        downloaded = False
        temp_target = local_path + ".tmp"
        curl_cmd = [
            "curl", "-s", "-L", "--connect-timeout", "6", "--max-time", "25",
            "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", src_url, "-o", temp_target
        ]
        try:
            subprocess.run(curl_cmd, capture_output=True, timeout=30)
            if os.path.exists(temp_target) and os.path.getsize(temp_target) > 1024:
                if os.path.exists(local_path):
                    os.remove(local_path)
                os.rename(temp_target, local_path)
                downloaded = True
            elif os.path.exists(temp_target):
                os.remove(temp_target)
        except Exception:
            if os.path.exists(temp_target):
                try: os.remove(temp_target)
                except Exception: pass

        if not downloaded and ("github.com" in src_url or "raw.githubusercontent.com" in src_url):
            proxy_url = f"{MY_HOST}/{src_url}"
            curl_cmd2 = [
                "curl", "-s", "-L", "--connect-timeout", "6", "--max-time", "30",
                "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", proxy_url, "-o", temp_target
            ]
            try:
                subprocess.run(curl_cmd2, capture_output=True, timeout=35)
                if os.path.exists(temp_target) and os.path.getsize(temp_target) > 1024:
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    os.rename(temp_target, local_path)
                    downloaded = True
                elif os.path.exists(temp_target):
                    os.remove(temp_target)
            except Exception:
                if os.path.exists(temp_target):
                    try: os.remove(temp_target)
                    except Exception: pass

        if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
            with open(local_path, "rb") as f:
                real_md5 = hashlib.md5(f.read()).hexdigest()
            new_url = f"{MY_HOST}/jars/{fname};md5;{real_md5}"
            status_text = "新同步" if downloaded else "沿用缓存"
            sz_kb = os.path.getsize(local_path) // 1024
            return j_url, new_url, f"  [OK] [{status_text}] {fname} ({sz_kb}KB, MD5: {real_md5[:8]}...)"
        else:
            return j_url, j_url, f"  [FAIL] 保留原链接: {j_url[:60]}"

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_download_single_jar, j) for j in jar_list]
        for f in as_completed(futures):
            j_url, new_url, log_msg = f.result()
            jar_map[j_url] = new_url
            print(log_msg)

    for s in site_list:
        if s.get("jar") and s["jar"] in jar_map:
            s["jar"] = jar_map[s["jar"]]

    new_default_spider = jar_map.get(default_spider_url, default_spider_url)
    print(f"[Jar 镜像同步] 同步完成！所有可用 Jar 已成功托管在当前仓库 ./jars/ 中并通过 {MY_HOST} 加速。\n")
    return new_default_spider


def gh_proxy_url(url):
    """私有 GitHub 代理转换器：还原 jsDelivr，剥离套娃前缀，标准化为你自己的域名代理"""
    if not isinstance(url, str) or not url.startswith("http"):
        return url
    
    md5_suffix = ""
    if ";md5;" in url:
        parts = url.split(";md5;", 1)
        url, md5_val = parts[0], parts[1]
        md5_suffix = f";md5;{md5_val}"

    m_jsd = re.search(r'https?://(?:[\w-]+\.)?jsdelivr\.net/gh/([^/@]+)/([^/@]+)(?:@([^/]+))?/(.+)', url)
    if m_jsd:
        user = m_jsd.group(1)
        repo = m_jsd.group(2)
        branch = m_jsd.group(3) or "master"
        path = m_jsd.group(4)
        raw_target = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        return f"{MY_HOST}/{raw_target}{md5_suffix}"

    m = re.search(r'((?:https?://)?(?:raw\.githubusercontent\.com|github\.com)/[^\s"\';]+)', url)
    if m:
        raw_target = m.group(1)
        if not raw_target.startswith("http"):
            raw_target = "https://" + raw_target
        return f"{MY_HOST}/{raw_target}{md5_suffix}"
    
    return f"{url}{md5_suffix}"


def curl(url, timeout=10, via_proxy=False):
    """带超时控制的 HTTP 请求"""
    actual_url = f"{MY_HOST}/{url}" if (via_proxy and MY_HOST) else url
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--connect-timeout", str(timeout),
             "--max-time", str(timeout * 2), "-A", "Mozilla/5.0", actual_url],
            capture_output=True, timeout=timeout * 2 + 5
        )
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def parse_json(raw):
    """增强型 JSON 解析器：自动过滤 BOM、// 和 /* */ 注释，容忍尾逗号"""
    if not raw or not raw.strip():
        return None
    raw = raw.lstrip('\ufeff')
    raw = re.sub(r'(?<!:)\/\/.*$', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\/\*[\s\S]*?\*\/', '', raw)
    raw = re.sub(r',(\s*[}\]])', r'\1', raw)
    try:
        return json.loads(raw, strict=False)
    except Exception:
        s, e = raw.find('{'), raw.rfind('}')
        if s >= 0 and e > s:
            try:
                return json.loads(raw[s:e + 1], strict=False)
            except Exception:
                pass
    return None


def resolve_url(base, path):
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{path}"
    return urljoin(base, path)


def resolve_spider(spider, source_url):
    if not spider:
        return ""
    md5_suffix = ""
    if ";md5;" in spider:
        parts = spider.split(";md5;", 1)
        spider_path, md5_val = parts[0], parts[1]
        md5_suffix = f";md5;{md5_val}"
    else:
        spider_path = spider

    if spider_path.startswith("http://") or spider_path.startswith("https://"):
        resolved = spider_path
    else:
        resolved = resolve_url(source_url, spider_path)

    return gh_proxy_url(f"{resolved}{md5_suffix}")


def extract_m3u8(t):
    return re.findall(r'(https?://[^\s"\'<>#\$]+?\.m3u8)', t)


def get_segments(media, media_url):
    urls = []
    lines = media.strip().split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF") and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and not nxt.startswith("#"):
                urls.append(resolve_url(media_url, nxt))
    return urls


def build_url(base, params):
    clean_base = base.rstrip("?&/")
    return clean_base + ("&" if "?" in clean_base else "?") + params


def test_jar_speed(clean_jar_url):
    """【真实物理测速】：测试核心 Jar 引擎的下载连通性与实测吞吐"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-r", "0-131071", "-o", "/dev/null", "-w", "%{http_code},%{size_download},%{time_total}",
             "--connect-timeout", "4", "--max-time", "8", "-L", "-A", "Mozilla/5.0", clean_jar_url],
            capture_output=True, timeout=10
        )
        parts = r.stdout.decode().strip().split(",")
        code = parts[0] if parts else "000"
        sz = int(float(parts[1])) if len(parts) > 1 and parts[1] else 0
        dl_time = float(parts[2]) if len(parts) > 2 and parts[2] else 99
        
        if code.startswith(("2", "3")) and sz > 1000:
            speed = int((sz / 1024) / max(dl_time, 0.05))
            ttfb = int(dl_time * 1000)
            return True, ttfb, speed
    except Exception:
        pass
    return False, 9999, 0


def test_play_speed(api, stype, use_proxy=False):
    """采集站切片级真实播放下载测速"""
    base = re.sub(r'[?&]ac=list.*', '', api.rstrip("/"))
    body = curl(build_url(base, "ac=list"), 12, via_proxy=use_proxy)
    if not body or len(body) < 50:
        return 0, 0, "列表失败"

    vids = []
    if stype == 0:
        vids = re.findall(r'<id>(\d+)</id>', body)[:3]
    else:
        try:
            j = json.loads(body, strict=False)
            vids = [str(v["vod_id"]) for v in (j.get("list") or [])[:3]]
        except Exception:
            return 0, 0, "解析失败"
    if not vids:
        return 0, 0, "无ID"

    for vid in vids:
        detail = curl(build_url(base, f"ac=detail&ids={vid}"), 12, via_proxy=use_proxy)
        if not detail:
            continue
        m3u8s = []
        if stype == 0:
            m3u8s = extract_m3u8(detail)
        else:
            try:
                dj = json.loads(detail, strict=False)
                for v in (dj.get("list") or []):
                    m3u8s.extend(extract_m3u8(v.get("vod_play_url", "")))
            except Exception:
                continue
        if not m3u8s:
            continue

        for play in m3u8s[:2]:
            t0 = time.time()
            master = curl(play, 10, via_proxy=use_proxy)
            ttfb = int((time.time() - t0) * 1000)
            if not master:
                continue

            media_url = None
            if "#EXT-X-STREAM-INF" in master:
                lines = master.strip().split("\n")
                for i, line in enumerate(lines):
                    if "STREAM-INF" in line and i + 1 < len(lines):
                        sub = lines[i + 1].strip()
                        if sub and not sub.startswith("#"):
                            media_url = resolve_url(play, sub)
                            break
            elif "#EXTINF" in master:
                media_url = play

            if not media_url:
                continue

            t1 = time.time()
            media = curl(media_url, 10, via_proxy=use_proxy)
            mms = int((time.time() - t1) * 1000)
            if "#EXTINF" not in media:
                continue

            segs = get_segments(media, media_url)
            if not segs:
                continue

            t2 = time.time()
            data = curl(segs[0], 10, via_proxy=use_proxy)
            ts = time.time() - t2
            sz = len(data.encode("utf-8") if isinstance(data, str) else data)
            speed = int((sz / 1024) / max(ts, 0.1))
            if speed > 10:
                return ttfb + mms, speed, "正常"

    return 0, 0, "下载慢"


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] TVBox 聚合更新（多仓递归解包 + 网盘扫码站彻底淘汰版）开始...")

    # ── 1. 抓取多仓源列表 ──
    print("\n[阶段 1/5] 正在抓取仓库订阅源列表...")
    user_php = curl("http://tvbox.clbug.com/user.php", 15)
    if not user_php or len(user_php) < 100:
        print("  多仓列表获取失败，重试备用拉取...")
        user_php = curl("http://tvbox.clbug.com/user.php", 20, via_proxy=True)

    urls = re.findall(r'https?://[^\s"\'<>]+', user_php)
    initial_urls = [u for u in urls if u.startswith("http") and not u.endswith((".m3u", ".txt", ".png", ".jpg"))]
    print(f"  原始解析到 {len(initial_urls)} 条潜在仓库链接，开始递归拆解多仓与单仓...")

    # ── 2. 【多仓与单仓递归解包引擎】 ──
    # 纯正的单仓池 (包含 sites 的真正影视源)
    single_warehouse_pool = {}
    # 真正的多仓列表 (写入 tvbox_multi.json)
    multi_storehouses = []
    seen_urls = set()

    def inspect_and_unpack(u, parent_name=""):
        if u in seen_urls:
            return
        seen_urls.add(u)

        t0 = time.time()
        raw = curl(u, 10)
        lat = int((time.time() - t0) * 1000)
        data = parse_json(raw)
        if not data or not isinstance(data, dict):
            return

        # 获取当前源的名字
        m = re.search(r'name["\']?\s*:\s*["\']([^"\']+)["\']', raw)
        src_name = m.group(1).strip() if m else parent_name or urlparse(u).netloc

        # 判定 A：这是【多仓】（包含 urls 或 storeHouse）
        sub_list = data.get("urls") or data.get("storeHouse") or []
        if isinstance(sub_list, list) and len(sub_list) > 0:
            multi_storehouses.append({"sourceName": f"[{lat}ms] {src_name}", "sourceUrl": gh_proxy_url(u)})
            # 核心递归：把多仓里面的几十个子源，全部“抖出来”加入单仓待爬池！
            for item in sub_list:
                if isinstance(item, dict):
                    sub_url = item.get("url") or item.get("sourceUrl") or ""
                    sub_name = item.get("name") or item.get("sourceName") or src_name
                    if sub_url and sub_url.startswith("http"):
                        sub_abs = resolve_url(u, sub_url)
                        inspect_and_unpack(sub_abs, parent_name=sub_name)

        # 判定 B：这是【单仓】（包含 sites）
        if "sites" in data and isinstance(data.get("sites"), list):
            single_warehouse_pool[u] = (src_name, lat, data)

    print("  正在并发解包多仓并提取底层单仓...")
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(inspect_and_unpack, u) for u in initial_urls]
        for f in as_completed(futures):
            f.result()

    print(f"  解包完成！成功提取纯单仓: {len(single_warehouse_pool)} 个，纯多仓: {len(multi_storehouses)} 个")

    # ── 3. 解析有效单仓并执行【强淘汰 + 网盘扫码彻底过滤】 ──
    print("\n[阶段 2/5] 遍历单仓站点，彻底过滤网盘扫码站与垃圾占位符...")
    all_sites = []
    site_keys = set()
    spider_jars = {}
    collect_sources = {}
    all_lives_with_lat = []
    live_keys = set()
    all_parses = []
    all_rules = []
    rule_keys = set()
    all_flags = []
    flag_keys = set()
    seen_collect_hosts = set()
    eliminated_garbage = 0
    eliminated_pan = 0

    for url, (name, lat, data) in single_warehouse_pool.items():
        spider = data.get("spider", "")
        abs_spider = resolve_spider(spider, url) if spider else ""
        if abs_spider:
            spider_jars[abs_spider] = spider_jars.get(abs_spider, 0) + 1

        for s in (data.get("sites") or []):
            if not isinstance(s, dict):
                continue
            key = s.get("key", "")
            raw_name = s.get("name", key)
            api = s.get("api", "")
            st = s.get("type", -1)

            # 强淘汰 1：无 key、无 api、或类型不合法的废站
            if not key or not api or st not in (0, 1, 3) or key in site_keys:
                eliminated_garbage += 1
                continue

            # 强淘汰 2：命中垃圾占位符黑名单
            if any(kw in raw_name or kw in key for kw in SKIP_KEYWORDS):
                eliminated_garbage += 1
                continue

            # 强淘汰 3：【核心过滤】命中网盘/扫码登录/Cookie 特征的站点彻底淘汰！
            s["_raw_name"] = raw_name
            if is_pan_site(s):
                eliminated_pan += 1
                continue

            # 强淘汰 4：爬虫站无自身 jar 且源未提供全局 spider（必死站）
            if st == 3 and not s.get("jar") and not abs_spider:
                eliminated_garbage += 1
                continue

            # 采集站域名排重
            if st in (0, 1) and isinstance(api, str) and api.startswith("http"):
                api_host = urlparse(api).netloc.lower()
                if api_host in seen_collect_hosts:
                    continue
                seen_collect_hosts.add(api_host)

            site_keys.add(key)
            s["name"] = raw_name  # 保持纯净的原生站点名
            s["_lat"] = lat
            s["_src_name"] = name
            s["_src_url"] = url

            # 爬虫站继承专属 Jar
            if st == 3 and not s.get("jar") and abs_spider:
                s["jar"] = abs_spider

            # 相对路径修复 + 私有域名代理清洗
            ext = s.get("ext", "")
            if isinstance(ext, str) and ext:
                is_rel_file = ext.startswith(("./", "../", "/")) or (
                    not ext.startswith(("http://", "https://", "clan://", "push://", "file://", "{", "["))
                    and not "\n" in ext
                    and ext.endswith((".json", ".txt", ".js", ".jar", ".bin", ".py"))
                )
                if is_rel_file:
                    ext = resolve_url(url, ext)
                s["ext"] = gh_proxy_url(ext)

            if isinstance(api, str) and api:
                if api.startswith(("./", "../", "/")):
                    api = resolve_url(url, api)
                s["api"] = gh_proxy_url(api)

            if s.get("jar"):
                s["jar"] = gh_proxy_url(s["jar"])

            all_sites.append(s)

            if st in (0, 1) and isinstance(api, str) and api.startswith("http") and api not in collect_sources:
                collect_sources[api] = (name, st)

        # 收集直播源
        for l in (data.get("lives") or []):
            u = l.get("url", "") if isinstance(l, dict) else ""
            if u and u not in live_keys:
                live_keys.add(u)
                l_copy = dict(l) if isinstance(l, dict) else {"url": u}
                l_copy["url"] = gh_proxy_url(u)
                all_lives_with_lat.append((lat, l_copy))

        for p in (data.get("parses") or []):
            if isinstance(p, dict) and p.get("url"):
                all_parses.append(p)

        for r in (data.get("rules") or []):
            rk = r.get("name") if isinstance(r, dict) else str(r)
            if rk and rk not in rule_keys:
                rule_keys.add(rk)
                all_rules.append(r)

        for f in (data.get("flags") or []):
            if isinstance(f, str) and f not in flag_keys:
                flag_keys.add(f)

    print(f"  清洗完毕！剔除废站垃圾: {eliminated_garbage} 个，【成功剔除网盘扫码站】: {eliminated_pan} 个！")
    print(f"  保留高质量【纯免登录直链候选站】: {len(all_sites)} 个")

    # ── 4. Type 3 爬虫站：Jar 引擎真实连通性与吞吐并发测速 ──
    print("\n[阶段 3/5] 正在对免登录 Type 3 核心 Jar 包执行物理测速与淘汰...")
    unique_jars = set()
    for s in all_sites:
        if s.get("type") == 3 and s.get("jar"):
            clean_j = s["jar"].split(";md5;")[0]
            if clean_j.startswith("http"):
                unique_jars.add(clean_j)

    jar_speed_cache = {}
    print(f"  涉及独立 Jar 包共 {len(unique_jars)} 个，开始多线程网络测速...")

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(test_jar_speed, j): j for j in unique_jars}
        for f in as_completed(futures):
            j = futures[f]
            ok, ttfb, spd = f.result()
            jar_speed_cache[j] = (ok, ttfb, spd)

    dead_jar_count = sum(1 for v in jar_speed_cache.values() if not v[0])
    print(f"  测速完毕！存活 Jar: {len(unique_jars) - dead_jar_count} 个，淘汰死 Jar: {dead_jar_count} 个")

    scored_spiders = []
    for s in all_sites:
        if s.get("type") != 3:
            continue
        jar_url = s.get("jar", "").split(";md5;")[0]
        jar_info = jar_speed_cache.get(jar_url, (False, 9999, 0))
        is_alive, jar_lat, jar_spd = jar_info

        if not is_alive:
            continue

        raw_name = s["_raw_name"]
        raw_name_lower = raw_name.lower()
        src_name_lower = s["_src_name"].lower()

        score = float(jar_spd) - (jar_lat * 0.15)
        is_top_tier = any(kw in src_name_lower for kw in TOP_TIER_SOURCES) or any(kw in raw_name_lower for kw in TOP_TIER_SOURCES)
        is_quality = any(kw in raw_name_lower for kw in QUALITY_KEYWORDS)

        if is_top_tier:
            score += 500
        if is_quality:
            score += 200

        s["_score"] = score
        # 极简符号：顶级神源 ⚡，4K画质 ✨，普通原生干净站名
        if is_top_tier:
            s["name"] = f"⚡ {raw_name}"
        elif is_quality:
            s["name"] = f"✨ {raw_name}"
        else:
            s["name"] = raw_name

        scored_spiders.append(s)

    scored_spiders.sort(key=lambda x: x["_score"], reverse=True)
    print(f"  优质免登录直链爬虫站筛选完毕，共计 {len(scored_spiders)} 个活跃站")

    # ── 5. Type 0/1 采集站切片级播放测速 ──
    print(f"\n[阶段 4/5] 正在对 {len(collect_sources)} 个采集站进行真实切片下载测速...")
    collect_results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(test_play_speed, api, st): (api, st) for api, (name, st) in collect_sources.items()}
        for f in as_completed(futures):
            api, st = futures[f]
            ttfb, spd, status = f.result()
            if spd > 30:
                collect_results.append((ttfb, spd, api, st))

    collect_results.sort(key=lambda x: x[1], reverse=True)
    print(f"  采集站测速完毕！保留秒播采集站: {len(collect_results)} 个")

    # ── 6. 精选 120 站纯免登录秒播整合 ──
    print(f"\n[阶段 5/5] 整合甄选 120 站、镜像本地 Jar 并通过 {MY_HOST} 生成各版本配置...")
    top_cms_sites = []
    for idx, (ttfb, speed, api, stype) in enumerate(collect_results[:25], 1):
        clean_name = urlparse(api).netloc or f"采集站{idx}"
        for s in all_sites:
            if s.get("api") == api or s.get("api") == gh_proxy_url(api):
                clean_name = re.sub(r'^[\[\⚡\✨\🚀\📶].*?\s*', '', s.get("_raw_name", clean_name)).strip()
                break
        
        symbol = "🚀" if speed > 2000 else "⚡" if speed > 500 else "📶"
        top_cms_sites.append({
            "key": f"c_{idx}_{clean_name}",
            "name": f"{symbol} {clean_name}",
            "type": stype,
            "api": gh_proxy_url(api),
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 0
        })

    needed_spiders = BOUTIQUE_LIMIT - len(top_cms_sites)

    source_distribution = {}
    balanced_spiders = []
    for s in scored_spiders:
        src = s.get("_src_name", "other")
        if source_distribution.get(src, 0) < MAX_SPIDERS_PER_SOURCE:
            source_distribution[src] = source_distribution.get(src, 0) + 1
            s_copy = dict(s)
            s_copy.pop("_src_name", None)
            s_copy.pop("_src_url", None)
            s_copy.pop("_raw_name", None)
            s_copy.pop("_score", None)
            s_copy["quickSearch"] = 1
            s_copy["searchable"] = 1
            balanced_spiders.append(s_copy)
            if len(balanced_spiders) >= needed_spiders:
                break

    if len(balanced_spiders) < needed_spiders:
        for s in scored_spiders:
            if s not in balanced_spiders:
                s_copy = dict(s)
                s_copy.pop("_src_name", None)
                s_copy.pop("_src_url", None)
                s_copy.pop("_raw_name", None)
                s_copy.pop("_score", None)
                s_copy["quickSearch"] = 1
                s_copy["searchable"] = 1
                balanced_spiders.append(s_copy)
                if len(balanced_spiders) >= needed_spiders:
                    break

    boutique_sites = balanced_spiders + top_cms_sites

    # 选出 10 个最快直播源
    all_lives_with_lat.sort(key=lambda x: x[0])
    boutique_lives = [l for lat, l in all_lives_with_lat[:BOUTIQUE_LIVES_LIMIT]]
    full_lives = [l for lat, l in all_lives_with_lat]

    # 解析线路优化
    clean_parses = []
    seen_parse_urls = set()
    for p in all_parses:
        u = p.get("url", "")
        if u and u not in seen_parse_urls:
            seen_parse_urls.add(u)
            p_copy = dict(p)
            p_copy["url"] = gh_proxy_url(u)
            if not p_copy.get("name"):
                p_copy["name"] = f"解析线路{len(clean_parses)+1}"
            clean_parses.append(p_copy)
    clean_parses.sort(key=lambda x: 0 if x.get("type") in (1, 2, 3) else 1)

    best_spider = max(spider_jars, key=spider_jars.get) if spider_jars else ""
    best_spider = gh_proxy_url(best_spider)

    # ── 【Jar 自动镜像本地化】: 下载核心 Jar 到本地 ./jars/ 并改写为自身域名直链 ──
    best_spider = sync_and_localize_jars(all_sites, best_spider)
    all_sites_jar_map = {s["key"]: s.get("jar") for s in all_sites if s.get("jar")}
    for s in boutique_sites:
        if s.get("key") in all_sites_jar_map:
            s["jar"] = all_sites_jar_map[s["key"]]

    # ── 7. 输出 tvbox.json（精品主力版：120 站 100% 免扫码纯直链） ──
    boutique_json = {
        "spider": best_spider,
        "wallpaper": "https://bing.img.run/rand_uhd.php",
        "doh": [
            {"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]}
        ],
        "sites": boutique_sites,
        "lives": boutique_lives,
        "parses": clean_parses[:8],
        "rules": all_rules,
        "flags": all_flags
    }
    with open(os.path.join(WORK_DIR, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(boutique_json, f, ensure_ascii=False, indent=2)
    print(f"  [精品主力版] 输出完成 (tvbox.json): 甄选 {len(boutique_sites)} 站 (纯直链高分爬虫:{len(balanced_spiders)} 采集:{len(top_cms_sites)}) + 精选 {len(boutique_lives)} 个最快直播源")

    # ── 8. 输出 tvbox_full.json / tvbox_all.json（全量版，路径 /all） ──
    for s in all_sites:
        s.pop("_src_name", None)
        s.pop("_src_url", None)
        s.pop("_raw_name", None)
        s.pop("_score", None)
        s.pop("_lat", None)
        s["searchable"] = 1
        s["quickSearch"] = 0

    full_json = {
        "spider": best_spider,
        "wallpaper": "https://bing.img.run/rand_uhd.php",
        "doh": [
            {"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]}
        ],
        "sites": all_sites,
        "lives": full_lives,
        "parses": clean_parses,
        "rules": all_rules,
        "flags": all_flags
    }
    with open(os.path.join(WORK_DIR, "tvbox_full.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(WORK_DIR, "tvbox_all.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    print(f"  [全量版] 输出完成 (tvbox_full.json & tvbox_all.json): 包含全部 {len(all_sites)} 个免扫码纯净站点 + 全量直播")

    # ── 9. 输出 tvbox_multi.json（真正纯净的多仓版） ──
    multi = {
        "storeHouse": multi_storehouses
    }
    with open(os.path.join(WORK_DIR, "tvbox_multi.json"), "w", encoding="utf-8") as f:
        json.dump(multi, f, ensure_ascii=False, indent=2)
    print(f"  [纯净多仓版] 输出完成 (tvbox_multi.json): {len(multi_storehouses)} 个独立多仓")

    # ── 10. 写入 sources.txt 记录 ──
    with open(os.path.join(WORK_DIR, "sources.txt"), "w", encoding="utf-8") as f:
        f.write(f"# 更新时间: {ts}\n\n")
        f.write("## 纯单仓列表\n")
        for u, (name, lat, _) in single_warehouse_pool.items():
            f.write(f"[{lat}ms] {name}\n{u}\n\n")
        f.write("\n## 纯多仓列表\n")
        for item in multi_storehouses:
            f.write(f"{item['sourceName']}\n{item['sourceUrl']}\n\n")

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 所有任务顺利完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
