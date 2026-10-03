#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 聚合源自动更新（终极智能甄别版）
- 1. 【强力预淘汰】：测速前直接清洗垃圾占位符（配置中心/本地/预告/说明）、残缺站、死链仓
- 2. 【Type 3 真实物理测速评分】：并发测试核心 Jar 包真实下载带宽(KB/s)与响应延迟，死 Jar 站点直接淘汰
- 3. 【口碑大源与4K秒播加权】：主动识别 嗷呜、饭太硬、肥猫、玩偶4K 等顶级大源并推上首页
- 4. 精品版 (tvbox.json / 根路径 /)：精选 120 站 (95 个高分真实可用高清爬虫 + 25 个实测秒播采集) + 10 最快直播
- 5. 全量版 (tvbox_full.json / 路径 /all)：1300+ 站点海量全收录，无任何数量限制
- 6. jsDelivr 智能还原 + 套娃代理清洗，标准化为单层 gh-proxy.com
- 7. 爬虫站专属 Jar 继承 + api 相对路径补全 + 去广告 rules/flags 保留 + 阿里 DoH
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
BOUTIQUE_LIVES_LIMIT = 10  # 精品版只取 10 个最快直播源
MAX_SPIDERS_PER_SOURCE = 8  # 精品版中单个来源最多允许入选的爬虫站数量（保证多大源百花齐放）

# 强淘汰黑名单：非影视类占位符、垃圾广告站
SKIP_KEYWORDS = [
    "配置中心", "本地", "预告", "说明", "更新", "推送", "测试", "公告",
    "留言", "网盘配置", "扫码", "失效", "防失联", "备用", "教程", "公众号"
]

# 口碑大源白名单（给予高额基础质量加分）
TOP_TIER_SOURCES = ["aowu", "嗷呜", "fty", "饭太硬", "feimao", "肥猫", "qiao", "巧技", "xiaoma", "小马", "moyu", "摸鱼", "drpy", "道长"]

# 高清秒播高频关键词（画质加分）
QUALITY_KEYWORDS = ["4k", "秒播", "蓝光", "原画", "玩偶", "木偶", "瓜子", "厂长", "金牌", "秋天", "低端", "libvio", "专线"]


def gh_proxy_url(url):
    """智能 GitHub 代理清洗转换器：还原 jsDelivr，剥离套娃前缀，标准化为单一代理"""
    if not isinstance(url, str) or not url.startswith("http"):
        return url
    
    md5_suffix = ""
    if ";md5;" in url:
        parts = url.split(";md5;", 1)
        url, md5_val = parts[0], parts[1]
        md5_suffix = f";md5;{md5_val}"

    # 1. 还原并标准化 jsDelivr 链接
    m_jsd = re.search(r'https?://(?:[\w-]+\.)?jsdelivr\.net/gh/([^/@]+)/([^/@]+)(?:@([^/]+))?/(.+)', url)
    if m_jsd:
        user = m_jsd.group(1)
        repo = m_jsd.group(2)
        branch = m_jsd.group(3) or "master"
        path = m_jsd.group(4)
        raw_target = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        return f"https://gh-proxy.com/{raw_target}{md5_suffix}"

    # 2. 提取纯粹的 GitHub 根路径并包裹代理
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


# ─────────────────────────────────────────────────────────────────────────────
# 核心测速与评分引擎
# ─────────────────────────────────────────────────────────────────────────────

def test_jar_speed(clean_jar_url):
    """【真实物理测速】：测试核心 Jar 引擎的下载连通性、TTFB 延迟与实测带宽(KB/s)"""
    t0 = time.time()
    try:
        # 拉取前 128KB 字节分片进行真实吞吐测试
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
    """单个采集站测试任务"""
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
    print(f"[{ts}] 开始执行 TVBox 智能甄别版聚合更新...")

    # ── 1. 获取源列表 ──
    html = curl("https://tvbox.clbug.com/user.php", 20)
    src_urls = re.findall(r'data-url="([^"]+)"', html)
    src_names = re.findall(r'<td class="td-name">([^<]+)</td>', html)
    raw_sources = [(n.strip(), u.strip().replace("&amp;", "&"))
                   for n, u in zip(src_names, src_urls)
                   if u.strip() and not u.strip().startswith("#")]
    print(f"  源列表获取完成: 共 {len(raw_sources)} 个源")

    # ── 2. 【阶段一：源级预淘汰】探测可用性与快速剔除死链源 ──
    available = []
    for name, url in raw_sources:
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
        # 超时过长(>5秒)或非正常状态码直接淘汰
        if lat < 5000:
            available.append((name, url, lat))
        sys.stdout.write(f"\r  源级预淘汰进度: {len(available)}/{len(raw_sources)}")
        sys.stdout.flush()
    print()
    available.sort(key=lambda x: x[2])
    print(f"  初筛可用源数量: {len(available)} 个 (已剔除 {len(raw_sources) - len(available)} 个超时/死链源)")

    # ── 3. 抓取、合并与【阶段二：站点级强力清洗】 ──
    all_sites, all_parses = [], []
    all_rules, all_flags = [], []
    site_keys, seen_collect_hosts, live_keys = set(), set(), set()
    rule_keys, flag_keys = set(), set()
    spider_jars = {}
    collect_sources = {}
    all_lives_with_lat = []
    eliminated_sites_count = 0

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
            raw_name = s.get("name", key)
            api = s.get("api", "")
            st = s.get("type", -1)

            # 强淘汰 1：无 key、无 api、或类型不合法的废站
            if not key or not api or st not in (0, 1, 3) or key in site_keys:
                eliminated_sites_count += 1
                continue

            # 强淘汰 2：命中垃圾占位符黑名单
            if any(kw in raw_name or kw in key for kw in SKIP_KEYWORDS):
                eliminated_sites_count += 1
                continue

            # 强淘汰 3：爬虫站无自身 jar 且源未提供全局 spider（必死站）
            if st == 3 and not s.get("jar") and not abs_spider:
                eliminated_sites_count += 1
                continue

            # 采集站域名排重
            if st in (0, 1) and isinstance(api, str) and api.startswith("http"):
                api_host = urlparse(api).netloc.lower()
                if api_host in seen_collect_hosts:
                    continue
                seen_collect_hosts.add(api_host)

            site_keys.add(key)
            s["name"] = f"[{lat}ms|{name}] {raw_name}"
            s["_raw_name"] = raw_name
            s["_lat"] = lat
            s["_src_name"] = name
            s["_src_url"] = url

            # 爬虫站继承专属 Jar
            if st == 3 and not s.get("jar") and abs_spider:
                s["jar"] = abs_spider

            # 相对路径修复 + GitHub 代理清洗
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

            # 补全 api 相对路径 (./lib/drpy2.min.js, ./py/xxx.py) 为绝对路径
            if isinstance(api, str) and api:
                if api.startswith(("./", "../", "/")):
                    api = resolve_url(url, api)
                s["api"] = gh_proxy_url(api)

            if s.get("jar"):
                s["jar"] = gh_proxy_url(s["jar"])

            all_sites.append(s)

            # 收集待测速采集站
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
    print(f"  [站点级预清洗] 成功过滤掉 {eliminated_sites_count} 个垃圾占位符与残缺站点！")

    # ── 4. 【核心创新：Type 3 爬虫引擎真实测速与智能评分模型】 ──
    print(f"  启动 Jar 引擎真实带宽测速 (分析爬虫站质量)...")
    unique_jars = set()
    for s in all_sites:
        if s.get("type") == 3 and s.get("jar"):
            clean_j = s["jar"].split(";md5;")[0]
            if clean_j.startswith("http"):
                unique_jars.add(clean_j)

    jar_speed_map = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_jar = {executor.submit(test_jar_speed, j): j for j in unique_jars}
        for fut in as_completed(future_to_jar):
            j_url = future_to_jar[fut]
            try:
                ok, ttfb, speed = fut.result()
                jar_speed_map[j_url] = (ok, ttfb, speed)
            except Exception:
                jar_speed_map[j_url] = (False, 9999, 0)
    print(f"  已完成 {len(jar_speed_map)} 个核心 Jar 引擎的真实吞吐测速！")

    # 为所有爬虫站进行综合性能评分
    scored_spiders = []
    dead_spider_count = 0
    for s in all_sites:
        if s.get("type") != 3:
            continue
        
        jar_url = s.get("jar", "").split(";md5;")[0]
        jar_info = jar_speed_map.get(jar_url, (False, 9999, 0))
        jar_ok, jar_ttfb, jar_speed = jar_info
        
        # 强淘汰：如果该站依赖的 Jar 本身已经 404 或死链，直接淘汰！
        if not jar_ok:
            dead_spider_count += 1
            continue

        # 评分模型：Jar 真实下载速度分 + 延迟扣分
        score = min(jar_speed // 50, 100) - (jar_ttfb // 100)

        # 口碑大源加分 (+50分)
        src_name = s.get("_src_name", "").lower()
        src_url = s.get("_src_url", "").lower()
        if any(ts in src_name or ts in src_url for ts in TOP_TIER_SOURCES):
            score += 50

        # 高清秒播关键词加分 (+30分)
        raw_name = s.get("_raw_name", "").lower()
        if any(qk in raw_name for qk in QUALITY_KEYWORDS):
            score += 30

        s["_score"] = score
        scored_spiders.append(s)

    # 爬虫站按综合得分从高到低排序
    scored_spiders.sort(key=lambda x: -x.get("_score", 0))
    print(f"  优质爬虫站甄别完成: 成功识别 {len(scored_spiders)} 个可用爬虫站 (剔除死 Jar 站点 {dead_spider_count} 个)")

    # ── 5. CMS 采集站并发播放测速 ──
    print(f"  启动采集站切片播放测速 (共 {len(collect_sources)} 个采集站)...")
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
            sys.stdout.write(f"\r  采集测速进度: {completed}/{total} (有效可用: {len(collect_results)})")
            sys.stdout.flush()
    print()

    # 采集站速度降序，延迟升序
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

    # ── 6. 生成 tvbox.json（精品主力版：120 站高分智能筛选） ──
    # 1. 提取实测最快的前 25 个优质采集站 (Type 0/1)
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

    needed_spiders = BOUTIQUE_LIMIT - len(top_cms_sites)  # 需要补充的高分爬虫站 (95个)

    # 2. 多源平权策略选取高分爬虫站（单源最多取 MAX_SPIDERS_PER_SOURCE 个，嗷呜/饭太硬等神源优先上榜）
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

    # 若尚未凑满，继续由剩余高分爬虫补齐
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
    print(f"  [精品主力版] 输出完成 (tvbox.json): 甄选 {len(boutique_sites)} 站 (高分爬虫:{len(balanced_spiders)} 采集:{len(top_cms_sites)}) + 精选 {len(boutique_lives)} 个最快直播源")

    # ── 7. 生成 tvbox_full.json / tvbox_all.json（全量版，路径 /all） ──
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
    print(f"  [全量版] 输出完成 (tvbox_full.json & tvbox_all.json): 包含全部 {len(all_sites)} 个站点 + 全量直播")

    # ── 8. 生成 tvbox_multi.json（多仓版） ──
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

    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 所有任务顺利完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
