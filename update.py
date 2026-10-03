#!/usr/bin/env python3
"""
TVBox 聚合源自动更新
每小时 GitHub Actions 自动执行：
  1. tvbox.json       → 简洁版（采集站，播放测速排序）
  2. tvbox_full.json  → 全量版（399站合并，带spider）
  3. tvbox_multi.json → 多仓版（27个独立仓库）
"""
import json, sys, re, subprocess, os, time
import urllib.parse
from urllib.parse import urljoin, urlparse

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CF_PROXY = os.environ.get("CF_PROXY", "")  # Cloudflare Worker 代理地址

def curl(url, timeout=10, via_proxy=False):
    actual_url = f"{CF_PROXY}?u={urllib.parse.quote(url, safe='')}" if (via_proxy and CF_PROXY) else url
    try:
        r = subprocess.run(["curl","-s","-L","--connect-timeout",str(timeout),
                           "--max-time",str(timeout*2),"-A","Mozilla/5.0",actual_url],
                          capture_output=True, timeout=timeout*2+5)
        return r.stdout.decode("utf-8", errors="replace")
    except: return ""

def parse_json(raw):
    raw = raw.lstrip('﻿'); raw = re.sub(r',(\s*[}\]])', r'\1', raw)
    try: return json.loads(raw, strict=False)
    except:
        s,e = raw.find('{'), raw.rfind('}')
        if s>=0 and e>s:
            try: return json.loads(raw[s:e+1], strict=False)
            except: pass
    return None

def resolve_spider(spider, source_url):
    if not spider: return ""
    if spider.startswith("http"): return spider
    if spider.startswith("./"):
        p = urlparse(source_url)
        return f"{p.scheme}://{p.netloc}{spider[1:]}"
    return spider

def resolve_url(base, path):
    if path.startswith("http"): return path
    if path.startswith("/"): return f"{urlparse(base).scheme}://{urlparse(base).netloc}{path}"
    return urljoin(base, path)

def extract_m3u8(t):
    return re.findall(r'(https?://[^\s"\'<>#\$]+?\.m3u8)', t)

def get_segments(media, media_url):
    urls = []
    lines = media.strip().split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF") and i+1 < len(lines):
            nxt = lines[i+1].strip()
            if nxt and not nxt.startswith("#"):
                urls.append(resolve_url(media_url, nxt))
    return urls

def build_url(base, params):
    return base.rstrip("/") + ("&" if "?" in base else "?") + params

def test_play_speed(api, stype, use_proxy=False):
    """真实播放测速：尝试多个视频+多个分片，跳过404"""
    base = re.sub(r'[?&]ac=list.*', '', api.rstrip("/"))
    body = curl(build_url(base, "ac=list"), 15, via_proxy=use_proxy)
    if not body or len(body) < 50: return 0, 0, "列表失败"

    # 获取视频ID列表（多试几个）
    vids = []
    if stype == 0:
        vids = re.findall(r'<id>(\d+)</id>', body)[:3]
    else:
        try:
            j = json.loads(body, strict=False)
            vids = [str(v["vod_id"]) for v in (j.get("list") or [])[:3]]
        except: return 0, 0, "解析失败"
    if not vids: return 0, 0, "无ID"

    for vid in vids:
        detail = curl(build_url(base, f"ac=detail&ids={vid}"), 15, via_proxy=use_proxy)
        if not detail: continue
        m3u8s = []
        if stype == 0:
            m3u8s = extract_m3u8(detail)
        else:
            try:
                dj = json.loads(detail, strict=False)
                for v in (dj.get("list") or []):
                    m3u8s.extend(extract_m3u8(v.get("vod_play_url", "")))
            except: continue
        if not m3u8s: continue

        for play in m3u8s[:2]:  # 每个视频最多试2个m3u8源
            t0 = time.time()
            master = curl(play, 15, via_proxy=use_proxy)
            ttfb = int((time.time() - t0) * 1000)
            if not master: continue
            media_url = None
            if "#EXT-X-STREAM-INF" in master:
                for i, line in enumerate(master.strip().split("\n")):
                    if "STREAM-INF" in line:
                        sub = master.strip().split("\n")[i+1].strip() if i+1 < len(master.strip().split("\n")) else ""
                        if sub and not sub.startswith("#"):
                            media_url = resolve_url(play, sub); break
            elif "#EXTINF" in master: media_url = play
            if not media_url: continue
            t1 = time.time()
            media = curl(media_url, 15, via_proxy=use_proxy)
            mms = int((time.time() - t1) * 1000)
            if "#EXTINF" not in media: continue
            segs = get_segments(media, media_url)
            if not segs: continue

            # 下载分片，跳过404，至少成功2个才算通过
            tb, tt, ok = 0, 0, 0
            for s in segs[:8]:
                if ok >= 3: break  # 够了
                seg_url = f"{CF_PROXY}?u={urllib.parse.quote(s, safe='')}" if (use_proxy and CF_PROXY) else s
                r = subprocess.run(["curl", "-s", "-o", "/dev/null",
                                   "-w", "%{http_code},%{size_download},%{time_total}",
                                   "--connect-timeout", "8", "--max-time", "20", seg_url],
                                  capture_output=True, timeout=25)
                parts = r.stdout.decode().strip().split(",")
                code = parts[0] if parts else "000"
                sz = int(float(parts[1])) if len(parts) > 1 and parts[1] else 0
                dl = float(parts[2]) if len(parts) > 2 and parts[2] else 99
                if code.startswith("2") and sz > 1000: tb += sz; tt += dl; ok += 1
            if ok >= 2:
                speed = int((tb / 1024) / tt) if tt > 0 else 0
                return ttfb + mms, speed, "OK"
    return 0, 0, "全部失败"

def main():
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] 开始更新...")

    # ── 1. 获取源列表 ──
    html = curl("https://tvbox.clbug.com/user.php", 20)
    src_urls = re.findall(r'data-url="([^"]+)"', html)
    src_names = re.findall(r'<td class="td-name">([^<]+)</td>', html)
    sources = [(n.strip(), u.strip().replace("&amp;", "&"))
               for n, u in zip(src_names, src_urls)
               if u.strip() and not u.strip().startswith("#")]
    print(f"  源列表: {len(sources)}")

    # ── 2. 测延迟 + 抓取 ──
    available = []
    for name, url in sources:
        try:
            t0 = time.time()
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                               "--connect-timeout", "5", "--max-time", "10",
                               "-L", "-A", "Mozilla/5.0", url],
                              capture_output=True, timeout=15)
            code = r.stdout.decode().strip()
            lat = int((time.time() - t0) * 1000) if code.startswith(("2", "3")) else 99999
        except: lat = 99999
        if lat < 99999: available.append((name, url, lat))
        sys.stdout.write(f"\r  测速: {len(available)}/{len(sources)}"); sys.stdout.flush()
    print()
    available.sort(key=lambda x: x[2])
    print(f"  可用: {len(available)}")

    # ── 3. 抓取并合并所有源 ──
    all_sites, all_lives, all_parses = [], [], []
    site_keys, live_keys, parse_keys = set(), set(), set()
    spider_jars = {}
    # 记录每个采集站来自哪个源
    collect_sources = {}  # api -> (source_name, stype)

    for name, url, lat in available:
        sys.stdout.write(f"\r  合并: {name} ({lat}ms)"); sys.stdout.flush()
        data = parse_json(curl(url, 15))
        if not data: continue

        spider = data.get("spider", "")
        if spider:
            abs_spider = resolve_spider(spider, url)
            spider_jars[abs_spider] = spider_jars.get(abs_spider, 0) + 1

        for s in (data.get("sites") or []):
            key = s.get("key", "")
            if not key or key in site_keys: continue
            site_keys.add(key)
            s["name"] = f"[{lat}ms|{name}] {s.get('name', key)}"
            s["_lat"] = lat
            # 【核心修复】：如果是爬虫站(type 3)且站点本身没有指定jar，自动继承原仓库的专属jar
            if s.get("type") == 3 and not s.get("jar") and spider:
                s["jar"] = abs_spider
            all_sites.append(s)
            # 记录采集站
            st = s.get("type", -1)
            api = s.get("api", "")
            if st in (0, 1) and api.startswith("http") and api not in collect_sources:
                collect_sources[api] = (name, st)

        for l in (data.get("lives") or []):
            u = l.get("url", "")
            if u and u not in live_keys: live_keys.add(u); all_lives.append(l)
        for p in (data.get("parses") or []):
            u = p.get("url", "")
            if u and u not in parse_keys: parse_keys.add(u); all_parses.append(p)
    print()

    # ── 4. 采集站播放测速（直连2次 + CF代理1次）──
    print(f"  播放测速: 测 {len(collect_sources)} 个采集站...")
    collect_results = []
    for api, (src_name, stype) in collect_sources.items():
        for attempt in range(3):
            use_proxy = (attempt == 2 and CF_PROXY)
            ttfb, speed, st = test_play_speed(api, stype, use_proxy=use_proxy)
            if st == "OK":
                collect_results.append((ttfb, speed, api, stype)); break
            if attempt < 2: time.sleep(2)
        sys.stdout.write(f"\r  {len(collect_results)} 可用/{len(collect_sources)} 测试"); sys.stdout.flush()
    print()

    # 按持续速度排序（速度快→慢，同速按首帧快）
    collect_results.sort(key=lambda x: (-x[1], x[0]))

    # 置顶指定采集站：索尼第一、360第二
    PINNED_APIS = ["suoniapi.com", "360zy.com"]
    pinned = [[] for _ in PINNED_APIS]
    rest = []
    for item in collect_results:
        api = item[2]
        placed = False
        for i, kw in enumerate(PINNED_APIS):
            if kw in api:
                pinned[i].append(item); placed = True; break
        if not placed:
            rest.append(item)
    collect_results = [x for group in pinned for x in group] + rest

    # 标记采集站的播放速度，用于全量版排序
    speed_map = {api: (ttfb, speed) for ttfb, speed, api, _ in collect_results}
    for s in all_sites:
        api = s.get("api", "")
        if api in speed_map:
            s["_speed"] = speed_map[api][1]
            s["_speed_ttfb"] = speed_map[api][0]

    # 全量版排序：采集站(type 0/1)排前面按播放速度，爬虫站(type 3)排后面按源延迟
    def full_sort_key(s):
        st = s.get("type", -1)
        if st in (0, 1):
            return (0, -s.get("_speed", 0), s.get("_speed_ttfb", 99999), s.get("_lat", 99999))
        return (1, 0, 0, s.get("_lat", 99999))
    all_sites.sort(key=full_sort_key)

    # 全量版置顶：索尼、360 移到采集站最前面
    pinned_sites = [[] for _ in PINNED_APIS]
    other_collect = []
    other_sites = []
    for s in all_sites:
        if s.get("type") not in (0, 1):
            other_sites.append(s); continue
        api = s.get("api", "")
        placed = False
        for i, kw in enumerate(PINNED_APIS):
            if kw in api:
                pinned_sites[i].append(s); placed = True; break
        if not placed:
            other_collect.append(s)
    all_sites = [x for group in pinned_sites for x in group] + other_collect + other_sites
    for s in all_sites:
        s.pop("_lat", None)
        s.pop("_speed", None)
        s.pop("_speed_ttfb", None)

    # ── 5. 生成 tvbox_full.json（全量版）──
    best_spider = max(spider_jars, key=spider_jars.get) if spider_jars else ""
    full_json = {"spider": best_spider, "sites": all_sites, "lives": all_lives, "parses": all_parses}
    with open(os.path.join(WORK_DIR, "tvbox_full.json"), "w", encoding="utf-8") as f:
        json.dump(full_json, f, ensure_ascii=False, indent=2)
    types = {}
    for s in all_sites: types[s.get("type", -1)] = types.get(s.get("type", -1), 0) + 1
    print(f"  全量版: {len(all_sites)} 站点 (采集:{types.get(0,0)+types.get(1,0)} 爬虫:{types.get(3,0)})")

    # ── 6. 生成 tvbox_multi.json（多仓版）──
    # 多仓版置顶：包含索尼/360的仓库排前面
    pinned_repos = set()
    for api_key in collect_sources:
        for kw in PINNED_APIS:
            if kw in api_key:
                pinned_repos.add(collect_sources[api_key][0])
    pinned_avail = [(n, u, l) for n, u, l in available if n in pinned_repos]
    other_avail = [(n, u, l) for n, u, l in available if n not in pinned_repos]
    multi = {"storeHouse": [{"sourceName": f"[{lat}ms] {name}", "sourceUrl": url}
                            for name, url, lat in pinned_avail + other_avail]}
    with open(os.path.join(WORK_DIR, "tvbox_multi.json"), "w", encoding="utf-8") as f:
        json.dump(multi, f, ensure_ascii=False, indent=2)
    print(f"  多仓版: {len(available)} 个仓库")

    # ── 7. 生成 tvbox.json（简洁版，固定前10个最快采集站）──
    SIMPLE_LIMIT = 10
    collect_sites = []
    for ttfb, speed, api, stype in collect_results[:SIMPLE_LIMIT]:
        # 从全量站点中找名称
        clean_name = api.split("/")[2]
        for s in all_sites:
            if s.get("api") == api:
                clean_name = re.sub(r'^\[.*?\]\s*', '', s.get("name", clean_name))
                break
        stable = "稳" if speed > 500 else "中" if speed > 100 else "慢"
        collect_sites.append({
            "key": clean_name,
            "name": f"[{speed}KB/s|{ttfb}ms|{stable}] {clean_name}",
            "type": stype, "api": api,
            "searchable": 1, "quickSearch": 1, "filterable": 0
        })

    collect_json = {"spider": "", "sites": collect_sites, "lives": [], "parses": []}
    with open(os.path.join(WORK_DIR, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(collect_json, f, ensure_ascii=False, indent=2)

    print(f"  简洁版: {len(collect_sites)}/{SIMPLE_LIMIT} 站（共 {len(collect_results)} 个可用）")
    for i, (ttfb, speed, api, _) in enumerate(collect_results[:SIMPLE_LIMIT], 1):
        stable = "🟢" if speed > 500 else "🟡" if speed > 100 else "🔴"
        host = api.split("/")[2][:25]
        print(f"    #{i} [{speed}KB/s|{ttfb}ms] {stable} {host}")

    # ── 8. 源列表 ──
    with open(os.path.join(WORK_DIR, "sources.txt"), "w") as f:
        f.write(f"# {ts}\n\n")
        for name, url, lat in available: f.write(f"[{lat}ms] {name}\n{url}\n\n")

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 更新完成!")
    return 0

if __name__ == "__main__": sys.exit(main())
