#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 聚合源自动更新（个人全量旗舰定制版）
- 1. 爬虫站(type 3)自动继承专属 Jar，彻底解决 ClassNotFound
- 2. 补全站点 ext 相对路径为绝对 URL
- 3. 全量保留并合并去广告规则 (rules) 与解码标识 (flags)
- 4. 增强 JSON 容错（过滤 // 与 /* */ 注释）
- 5. 域名级深度排重（彻底解决红牛、量子等镜像站重复刷屏）
- 6. 剔除死链采集站（仅收纳真实播放测速通过的有效源）
- 7. 分级聚合搜索（前 25 个最快站开启快速搜索，避免电视搜片卡死十几秒）
- 8. 解析线路优化（优先排布 JSON 快速解析，最多精选 15 条）
- 9. 内置阿里 DoH 防宽带劫持 + Bing 每日超清壁纸
- 10. 8 线程并发测速，2 分钟内极速完成更新
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
MAX_FULL_SITES = 120  # 全量版保留站点上限（防止电视盒子内存溢出崩溃，推荐 100-150）


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
    # 过滤单行注释（避免误伤 http://）
    raw = re.sub(r'(?<!:)\/\/.*$', '', raw, flags=re.MULTILINE)
    # 过滤多行注释
    raw = re.sub(r'\/\*[\s\S]*?\*\/', '', raw)
    # 去除尾逗号
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
        return spider

    resolved = resolve_url(source_url, spider_path)
    return f"{resolved}{md5_suffix}"


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
    return base.rstrip("/") + ("&" if "?" in base else "?") + params


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
    """单个采集站的测试任务"""
    api, (src_name, stype) = item
    for attempt in range(2):
        use_proxy = (attempt == 1 and bool(CF_PROXY))
        ttfb, speed, st = test_play_speed(api, stype, use_proxy=use_proxy)
        if st == "OK":
            return (ttfb, speed, api, stype)
        if attempt < 1:
            time.sleep(1)
    return None


def main():
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] 开始执行 TVBox 全量旗舰版源更新...")

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

    # ── 3. 抓取、域名排重与合并 ──
    all_sites, all_lives, all_parses = [], [], []
    all_rules, all_flags = [], []
    site_keys, seen_api_hosts, live_keys = set(), set(), set()
    rule_keys, flag_keys = set(), set()
    spider_jars = {}
    collect_sources = {}

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

            # 【排重优化】：基于 Key 和 API 域名双重去重，彻底消灭重复采集站
            api_host = urlparse(api).netloc if (isinstance(api, str) and api.startswith("http")) else ""
            if not key or key in site_keys or (api_host and api_host in seen_api_hosts):
                continue

            site_keys.add(key)
            if api_host:
                seen_api_hosts.add(api_host)

            s["name"] = f"[{lat}ms|{name}] {s.get('name', key)}"
            s["_lat"] = lat

            # 【核心修复 1】：爬虫站 (type 3) 继承所属源的专属 Jar
            if s.get("type") == 3 and not s.get("jar") and abs_spider:
                s["jar"] = abs_spider

            # 【核心修复 2】：自动修复 ext 相对路径
            ext = s.get("ext", "")
            if isinstance(ext, str) and ext:
                if ext.startswith("./") or (not ext.startswith(("http://", "https://", "clan://", "{", "[")) and not "\n" in ext and ("." in ext or "/" in ext)):
                    s["ext"] = resolve_url(url, ext)

            all_sites.append(s)

            # 收集待测速采集站
            st = s.get("type", -1)
            if st in (0, 1) and api_host and api not in collect_sources:
                collect_sources[api] = (name, st)

        # 合并直播源
        for l in (data.get("lives") or []):
            u = l.get("url", "") if isinstance(l, dict) else ""
            if u and u not in live_keys:
                live_keys.add(u)
                all_lives.append(l)

        # 收集解析线路
        for p in (data.get("parses") or []):
            if isinstance(p, dict) and p.get("url"):
                all_parses.append(p)

        # 【核心修复 3】：合并去广告规则 rules 与解码器 flags
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
            sys.stdout.write(f"\r  测速进度: {completed}/{total} (有效站: {len(collect_results)})")
            sys.stdout.flush()
    print()

    # 速度降序，延迟升序
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

    # 【死链清洗】：采集站只有测速成功的才进入全量版，超时的死站直接丢弃
    valid_sites = []
    for s in all_sites:
        st = s.get("type", -1)
        api = s.get("api", "")
        if st in (0, 1):
            if api in speed_map:
                s["_speed"] = speed_map[api][1]
                s["_speed_ttfb"] = speed_map[api][0]
                valid_sites.append(s)
            # 测速失败的死采集站直接忽略
        else:
            # 爬虫站保留
            valid_sites.append(s)

    # 排序：采集站优先按播放速度，爬虫站按源延迟
    def full_sort_key(s):
        st = s.get("type", -1)
        if st in (0, 1):
            return (0, -s.get("_speed", 0), s.get("_speed_ttfb", 99999), s.get("_lat", 99999))
        return (1, 0, 0, s.get("_lat", 99999))

    valid_sites.sort(key=full_sort_key)

    # 置顶索尼、360 到采集站最前
    pinned_sites = [[] for _ in PINNED_APIS]
    other_collect, other_sites = [], []
    for s in valid_sites:
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
    ordered_sites = [x for group in pinned_sites for x in group] + other_collect + other_sites

    # 【分级搜索优化】：前 25 个站启用快速聚合搜索；后面的站只允许单站搜索，避免电视搜片卡死！
    for idx, s in enumerate(ordered_sites):
        s["searchable"] = 1
        s["quickSearch"] = 1 if idx < 25 else 0
        s.pop("_lat", None)
        s.pop("_speed", None)
        s.pop("_speed_ttfb", None)

    # ── 5. 解析接口 (parses) 深度去重与优化 ──
    clean_parses = []
    seen_parse_urls = set()
    for p in all_parses:
        u = p.get("url", "")
        if u and u not in seen_parse_urls:
            seen_parse_urls.add(u)
            clean_parses.append(p)
    # 优先排序：type=1 (JSON 快速解析) 靠前，网页嗅探靠后，精选前 15 条
    clean_parses.sort(key=lambda x: 0 if x.get("type") == 1 else 1)
    clean_parses = clean_parses[:15]

    # ── 6. 生成 tvbox_full.json（全量旗舰版）──
    best_spider = max(spider_jars, key=spider_jars.get) if spider_jars else ""
    final_full_sites = ordered_sites[:MAX_FULL_SITES] if (MAX_FULL_SITES and MAX_FULL_SITES > 0) else ordered_sites

    full_json = {
        "spider": best_spider,
        "wallpaper": "https://bing.img.run/rand_uhd.php",  # Bing 每日超清壁纸
        "doh": [
            {"name": "AliDNS", "url": "https://dns.alidns.com/dns-query", "ips": ["223.5.5.5", "223.6.6.6"]}
        ],
        "sites": final_full_sites,
        "lives": all_lives,
        "parses": clean_parses,
        "rules": all_rules,
        "flags": all_flags
    }
    with open(os.path.join(WORK_DIR, "tvbox_full.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    print(f"  全量旗舰版输出完成: 包含 {len(final_full_sites)} 个精炼站点 (保留 rules: {len(all_rules)} 条, 解析: {len(clean_parses)} 条)")

    # ── 7. 生成 tvbox_multi.json（多仓版）──
    pinned_repos = set()
    for api_key in collect_sources:
        for kw in PINNED_APIS:
            if kw in api_key:
                pinned_repos.add(collect_sources[api_key][0])
    pinned_avail = [(n, u, l) for n, u, l in available if n in pinned_repos]
    other_avail = [(n, u, l) for n, u, l in available if n not in pinned_repos]
    multi = {
        "storeHouse": [{"sourceName": f"[{lat}ms] {name}", "sourceUrl": url}
                       for name, url, lat in pinned_avail + other_avail]
    }
    with open(os.path.join(WORK_DIR, "tvbox_multi.json"), "w", encoding="utf-8") as f:
        json.dump(multi, f, ensure_ascii=False, indent=2)
    print(f"  多仓版输出完成: {len(available)} 个独立仓库")

    # ── 8. 生成 tvbox.json（简洁版）──
    SIMPLE_LIMIT = 10
    collect_sites = []
    for ttfb, speed, api, stype in collect_results[:SIMPLE_LIMIT]:
        clean_name = api.split("/")[2]
        for s in all_sites:
            if s.get("api") == api:
                clean_name = re.sub(r'^\[.*?\]\s*', '', s.get("name", clean_name))
                break
        stable = "稳" if speed > 500 else "中" if speed > 100 else "慢"
        collect_sites.append({
            "key": clean_name,
            "name": f"[{speed}KB/s|{ttfb}ms|{stable}] {clean_name}",
            "type": stype,
            "api": api,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 0
        })

    collect_json = {
        "spider": "",
        "wallpaper": "https://bing.img.run/rand_uhd.php",
        "sites": collect_sites,
        "lives": [],
        "parses": clean_parses[:5],
        "rules": all_rules
    }
    with open(os.path.join(WORK_DIR, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(collect_json, f, ensure_ascii=False, indent=2)
    print(f"  简洁版输出完成: 固定最快 {len(collect_sites)} 站")

    # ── 9. 写入 sources.txt 记录 ──
    with open(os.path.join(WORK_DIR, "sources.txt"), "w", encoding="utf-8") as f:
        f.write(f"# 更新时间: {ts}\n\n")
        for name, url, lat in available:
            f.write(f"[{lat}ms] {name}\n{url}\n\n")

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 任务顺利完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
