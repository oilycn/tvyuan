#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 聚合源自动更新（终极定制版 - 集成 jsDelivr 智能还原）
- 1. 【新增】jsDelivr 智能还原转换：将 cdn/fastly/gcore.jsdelivr.net/gh/user/repo@branch/path
     统一还原并转换为标准的 https://gh-proxy.com/https://raw.githubusercontent.com/...
- 2. 精品版 (tvbox.json / 根路径 /)：精选 120 站 (95 个低延迟高清爬虫 + 25 个实测极速采集) + 10 个最快直播源
- 3. 全量版 (tvbox_full.json / 路径 /all)：1300+ 站点超大海量全收录，无任何数量限制
- 4. 智能 GitHub 代理清洗：剥离任何套娃加速前缀，标准化为单层 gh-proxy.com
- 5. 爬虫站专属 Jar 继承 + 去广告 rules/flags 完整保留 + 阿里 DoH 防劫持 + 超清壁纸
"""
import json
import sys
import re
import subprocess
import os
import time
import urllib.parse
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CF_PROXY = os.environ.get("CF_PROXY", "").rstrip("/")  # Cloudflare Worker 代理地址
BOUTIQUE_LIMIT = 120  # 精品版固定凑齐 120 个最强源
BOUTIQUE_LIVES_LIMIT = 10  # 精品版只取 10 个最快的直播源


def gh_proxy_url(url):
    """
    智能 GitHub 代理清洗转换器：
    1. 自动将各类 jsdelivr 镜像 (cdn/fastly/gcore.jsdelivr.net/gh/user/repo@branch/path)
       还原并标准化为 https://gh-proxy.com/https://raw.githubusercontent.com/...
    2. 自动剥离第三方套娃加速域名（down.nigx.cn、ghfast.top、重复套娃 gh-proxy 等）
    3. 保留原有 ;md5; 校验
    4. 非 GitHub 链接原样保留
    """
    if not isinstance(url, str) or not url.startswith("http"):
        return url
    
    md5_suffix = ""
    if ";md5;" in url:
        parts = url.split(";md5;", 1)
        url, md5_val = parts[0], parts[1]
        md5_suffix = f";md5;{md5_val}"

    # 1. 识别并转换各类 jsDelivr 格式: /gh/user/repo@branch/path 或 /gh/user/repo/path
    m_jsd = re.search(r'https?://(?:[\w-]+\.)?jsdelivr\.net/gh/([^/@]+)/([^/@]+)(?:@([^/]+))?/(.+)', url)
    if m_jsd:
        user = m_jsd.group(1)
        repo = m_jsd.group(2)
        branch = m_jsd.group(3) or "master"
        path = m_jsd.group(4)
        raw_target = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        return f"https://gh-proxy.com/{raw_target}{md5_suffix}"

    # 2. 匹配并提取纯粹的 GitHub 根路径（剥离任何套娃前缀）
    m = re.search(r'((?:https?://)?(?:raw\.githubusercontent\.com|github\.com)/[^\s"\';]+)', url)
    if m:
        raw_target = m.group(1)
        if not raw_target.startswith("http"):
            raw_target = "https://" + raw_target
        return f"https://gh-proxy.com/{raw_target}{md5_suffix}"
    
    return f"{url}{md5_suffix}"


def curl(url, timeout=10, via_proxy=False):
    """带超时控制的 HTTP 请求"""
    actual_url = f"{CF_PROXY}?u={urllib.parse.quote(url, safe='')}" if (via_proxy and CF_PROXY) else url
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
    """安全解析绝对路径"""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith("/"):
        p = urlparse(base)
        return f"{p.scheme}://{p.netloc}{path}"
    return urljoin(base, path)


def resolve_spider(spider, source_url):
    """解析 Spider Jar 路径，保留原有 md5 校验后缀"""
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


def test_play_speed(api, stype, use_proxy=False):
    """切片级真实下载测速"""
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

            tb, tt, ok = 0, 0, 0
            for s in segs[:6]:
                if ok >= 2:
                    break
                seg_url = f"{CF_PROXY}?u={urllib.parse.quote(s, safe='')}" if (use_proxy and CF_PROXY) else s
                try:
                    r = subprocess.run(
                        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code},%{size_download},%{time_total}",
                         "--connect-timeout", "6", "--max-time", "12", seg_url],
                        capture_output=True, timeout=15
                    )
                    parts = r.stdout.decode().strip().split(",")
                    code = parts[0] if parts else "000"
                    sz = int(float(parts[1])) if len(parts) > 1 and parts[1] else 0
                    dl = float(parts[2]) if len(parts) > 2 and parts[2] else 99
                    if code.startswith("2") and sz > 1000:
                        tb += sz
                        tt += dl
                        ok += 1
                except Exception:
                    continue

            if ok >= 2:
                speed = int((tb / 1024) / tt) if tt > 0 else 0
                return ttfb + mms, speed, "OK"
    return 0, 0, "全部失败"


def _worker_test_speed(item):
    """增加全局异常捕获，确保单站故障绝不影响全局任务"""
    api, (src_name, stype) = item
    try:
        for attempt in range(2):
            use_proxy = (attempt == 1 and bool(CF_PROXY))
            ttfb, speed, st = test_play_speed(api, stype, use_proxy=use_proxy)
            if st == "OK":
                return (ttfb, speed, api, stype)
            if attempt < 1:
                time.sleep(1)
    except Exception:
        pass
    return None


def main():
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] 开始执行 TVBox 聚合更新...")

    # ── 1. 获取源列表 ──
    html = curl("https://tvbox.clbug.com/user.php", 20)
    src_urls = re.findall(r'data-url="([^"]+)"', html)
    src_names = re.findall(r'<td class="td-name">([^<]+)</td>', html)
    sources = [(n.strip(), u.strip().replace("&amp;", "&"))
               for n, u in zip(src_names, src_urls)
               if u.strip() and not u.strip().startswith("#")]
    print(f"  源列表获取完成: 共 {len(sources)} 个源")

    # ── 2. 源可用性探测 ──
    available = []
    for name, url in sources:
        try:
            t0 = time.time()
            r = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", "4", "--max-time", "8",
                 "-L", "-A", "Mozilla/5.0", url],
                capture_output=True, timeout=10
            )
            code = r.stdout.decode().strip()
            lat = int((time.time() - t0) * 1000) if code.startswith(("2", "3")) else 99999
        except Exception:
            lat = 99999
        if lat < 99999:
            available.append((name, url, lat))
        sys.stdout.write(f"\r  源可用性探测: {len(available)}/{len(sources)}")
        sys.stdout.flush()
    print()
    available.sort(key=lambda x: x[2])
    print(f"  可用源数量: {len(available)}")

    # ── 3. 抓取、合并与收集 ──
    all_sites, all_parses = [], []
    all_rules, all_flags = [], []
    site_keys, seen_collect_hosts, live_keys = set(), set(), set()
    rule_keys, flag_keys = set(), set()
    spider_jars = {}
    collect_sources = {}
    all_lives_with_lat = []  # 记录带有来源延迟的直播列表

    for name, url, lat in available:
        sys.stdout.write(f"\r  合并源: {name} ({lat}ms)")
        sys.stdout.flush()
        data = parse_json(curl(url, 12))
        if not data or not isinstance(data, dict):
            continue

        spider = data.get("spider", "")
        abs_spider = resolve_spider(spider, url) if spider else ""
        if abs_spider:
            spider_jars[abs_spider] = spider_jars.get(abs_spider, 0) + 1

        for s in (data.get("sites") or []):
            if not isinstance(s, dict):
                continue
            key = s.get("key", "")
            api = s.get("api", "")
            st = s.get("type", -1)

            if not key or key in site_keys:
                continue

            # 域名排重仅针对 CMS 采集站(type 0/1)
            if st in (0, 1) and isinstance(api, str) and api.startswith("http"):
                api_host = urlparse(api).netloc.lower()
                if api_host in seen_collect_hosts:
                    continue
                seen_collect_hosts.add(api_host)

            site_keys.add(key)
            s["name"] = f"[{lat}ms|{name}] {s.get('name', key)}"
            s["_lat"] = lat

            # 爬虫站继承专属 Jar
            if st == 3 and not s.get("jar") and abs_spider:
                s["jar"] = abs_spider

            # 相对路径修复 + jsDelivr / GitHub 代理清洗
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

            if s.get("jar"):
                s["jar"] = gh_proxy_url(s["jar"])
            if isinstance(api, str) and api.startswith("http"):
                s["api"] = gh_proxy_url(api)

            all_sites.append(s)

            # 收集待测速采集站
            if st in (0, 1) and isinstance(api, str) and api.startswith("http") and api not in collect_sources:
                collect_sources[api] = (name, st)

        # 收集直播源并记录其所属源的延迟（用于精品版选最快 10 个直播源）
        for l in (data.get("lives") or []):
            u = l.get("url", "") if isinstance(l, dict) else ""
            if u and u not in live_keys:
                live_keys.add(u)
                l_copy = dict(l) if isinstance(l, dict) else {"url": u}
                l_copy["url"] = gh_proxy_url(u)
                all_lives_with_lat.append((lat, l_copy))

        # 收集解析线路
        for p in (data.get("parses") or []):
            if isinstance(p, dict) and p.get("url"):
                all_parses.append(p)

        # 合并去广告规则与解码标识
        for r in (data.get("rules") or []):
            rk = r.get("name") if isinstance(r, dict) else str(r)
            if rk and rk not in rule_keys:
                rule_keys.add(rk)
                all_rules.append(r)

        for f in (data.get("flags") or []):
            if isinstance(f, str) and f not in flag_keys:
                flag_keys.add(f)
                all_flags.append(f)
    print()

    # ── 4. 并发多线程播放测速 ──
    print(f"  启动并发测速 (共 {len(collect_sources)} 个采集站)...")
    collect_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_worker_test_speed, item): item for item in collect_sources.items()}
        completed = 0
        total = len(futures)
        for fut in as_completed(futures):
            completed += 1
            res = fut.result()
            if res:
                collect_results.append(res)
            sys.stdout.write(f"\r  测速进度: {completed}/{total} (有效可用: {len(collect_results)})")
            sys.stdout.flush()
    print()

    # 测速排序：速度降序，延迟升序
    collect_results.sort(key=lambda x: (-x[1], x[0]))

    # 置顶规则：索尼、360
    PINNED_APIS = ["suoniapi.com", "360zy.com"]
    pinned = [[] for _ in PINNED_APIS]
    rest = []
    for item in collect_results:
        api = item[2]
        placed = False
        for i, kw in enumerate(PINNED_APIS):
            if kw in api:
                pinned[i].append(item)
                placed = True
                break
        if not placed:
            rest.append(item)
    collect_results = [x for group in pinned for x in group] + rest

    speed_map = {api: (ttfb, speed) for ttfb, speed, api, _ in collect_results}

    # 全量版站点保留（全部保留，包括未测速或失败的采集站，保证 1300+ 站不流失）
    for s in all_sites:
        st = s.get("type", -1)
        api = s.get("api", "")
        orig_api = re.sub(r'^https://gh-proxy\.com/', '', api)
        if st in (0, 1):
            if api in speed_map or orig_api in speed_map:
                res_tuple = speed_map.get(api) or speed_map.get(orig_api)
                s["_speed"] = res_tuple[1]
                s["_speed_ttfb"] = res_tuple[0]
            else:
                s["_speed"] = 0
                s["_speed_ttfb"] = 99999

    # 全量排序：采集站按速度，爬虫站按源延迟
    def full_sort_key(s):
        st = s.get("type", -1)
        if st in (0, 1):
            return (0, -s.get("_speed", 0), s.get("_speed_ttfb", 99999), s.get("_lat", 99999))
        return (1, 0, 0, s.get("_lat", 99999))

    all_sites.sort(key=full_sort_key)

    # 全量置顶置前
    pinned_sites = [[] for _ in PINNED_APIS]
    other_collect, other_sites = [], []
    for s in all_sites:
        if s.get("type") not in (0, 1):
            other_sites.append(s)
            continue
        api = s.get("api", "")
        placed = False
        for i, kw in enumerate(PINNED_APIS):
            if kw in api:
                pinned_sites[i].append(s)
                placed = True
                break
        if not placed:
            other_collect.append(s)
    ordered_all_sites = [x for group in pinned_sites for x in group] + other_collect + other_sites

    # 全量版搜索策略：前 25 站开快速搜索，其余关闭防卡死
    for idx, s in enumerate(ordered_all_sites):
        s["searchable"] = 1
        s["quickSearch"] = 1 if idx < 25 else 0
        s.pop("_lat", None)
        s.pop("_speed", None)
        s.pop("_speed_ttfb", None)

    # ── 5. 解析接口 (parses) 深度去重 ──
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
    clean_parses = clean_parses[:15]

    # 直播源：全量直播 vs 精品 10 个最快直播
    all_lives_with_lat.sort(key=lambda x: x[0])  # 按来源延迟由低到高排序
    boutique_lives = [l for lat, l in all_lives_with_lat[:BOUTIQUE_LIVES_LIMIT]]
    full_lives = [l for lat, l in all_lives_with_lat]

    best_spider = max(spider_jars, key=spider_jars.get) if spider_jars else ""
    best_spider = gh_proxy_url(best_spider)

    # ── 6. 生成 tvbox.json（精品主力版，默认根路径 / 访问）──
    # 策略：前 25 个最快采集站 + 前 95 个最低延迟优质爬虫站 = 刚好凑满 120 站
    top_cms_sites = []
    for idx, (ttfb, speed, api, stype) in enumerate(collect_results[:25], 1):
        clean_name = urlparse(api).netloc or f"采集站{idx}"
        for s in all_sites:
            if s.get("api") == api or s.get("api") == gh_proxy_url(api):
                clean_name = re.sub(r'^\[.*?\]\s*', '', s.get("name", clean_name))
                break
        stable = "稳" if speed > 500 else "中" if speed > 100 else "慢"
        top_cms_sites.append({
            "key": f"c_{idx}_{clean_name}",
            "name": f"[{speed}KB/s|{stable}] {clean_name}",
            "type": stype,
            "api": gh_proxy_url(api),
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 0
        })

    needed_spiders = BOUTIQUE_LIMIT - len(top_cms_sites)  # 需要补充的爬虫站数量 (95个)
    top_spider_sites = []
    for s in ordered_all_sites:
        if s.get("type") == 3:
            s_copy = dict(s)
            s_copy["quickSearch"] = 1  # 精品版全部开启快速搜索
            s_copy["searchable"] = 1
            top_spider_sites.append(s_copy)
            if len(top_spider_sites) >= needed_spiders:
                break

    # 优质爬虫排最前享受 4K 秒播，最快采集站紧随其后作为兜底
    boutique_sites = top_spider_sites + top_cms_sites

    boutique_json = {
        "spider": best_spider,
        "wallpaper": "https://bing.img.run/rand_uhd.php",
        "doh": [
            {"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]}
        ],
        "sites": boutique_sites,
        "lives": boutique_lives,     # 精选 10 个最快直播源
        "parses": clean_parses[:8],
        "rules": all_rules,
        "flags": all_flags
    }
    with open(os.path.join(WORK_DIR, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(boutique_json, f, ensure_ascii=False, indent=2)
    print(f"  [精品主力版] 输出完成 (tvbox.json): 凑满 {len(boutique_sites)} 站 (优质爬虫:{len(top_spider_sites)} 采集:{len(top_cms_sites)}) + 精选 {len(boutique_lives)} 个最快直播源")

    # ── 7. 生成 tvbox_full.json / tvbox_all.json（全量版，路径 /all 访问）──
    full_json = {
        "spider": best_spider,
        "wallpaper": "https://bing.img.run/rand_uhd.php",
        "doh": [
            {"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]}
        ],
        "sites": ordered_all_sites,
        "lives": full_lives,
        "parses": clean_parses,
        "rules": all_rules,
        "flags": all_flags
    }
    with open(os.path.join(WORK_DIR, "tvbox_full.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(WORK_DIR, "tvbox_all.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    print(f"  [全量版] 输出完成 (tvbox_full.json & tvbox_all.json): 包含全部 {len(ordered_all_sites)} 个站点 + 全量直播")

    # ── 8. 生成 tvbox_multi.json（多仓版）──
    pinned_repos = set()
    for api_key in collect_sources:
        for kw in PINNED_APIS:
            if kw in api_key:
                pinned_repos.add(collect_sources[api_key][0])
    pinned_avail = [(n, u, l) for n, u, l in available if n in pinned_repos]
    other_avail = [(n, u, l) for n, u, l in available if n not in pinned_repos]
    multi = {
        "storeHouse": [{"sourceName": f"[{lat}ms] {name}", "sourceUrl": gh_proxy_url(url)}
                       for name, url, lat in pinned_avail + other_avail]
    }
    with open(os.path.join(WORK_DIR, "tvbox_multi.json"), "w", encoding="utf-8") as f:
        json.dump(multi, f, ensure_ascii=False, indent=2)
    print(f"  [多仓版] 输出完成 (tvbox_multi.json): {len(available)} 个独立仓库")

    # ── 9. 写入 sources.txt 记录 ──
    with open(os.path.join(WORK_DIR, "sources.txt"), "w", encoding="utf-8") as f:
        f.write(f"# 更新时间: {ts}\n\n")
        for name, url, lat in available:
            f.write(f"[{lat}ms] {name}\n{url}\n\n")

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 所有规格全量更新完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
