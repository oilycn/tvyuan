# 📺 TVBox 极速聚合源（自动测速 & 智能优化版）

自动聚合全网优质 TVBox 影视源，GitHub Actions 定时自动执行：真实切片播放测速、死链清洗、Spider Jar 依赖智能继承与 GitHub/jsDelivr 国内镜像加速。

---

## 🚀 订阅配置地址（开箱即用）

直接复制以下链接粘贴到 **TVBox / 影视仓 / FongMi / OK影视** 等客户端的配置地址中即可：

### 🌟 1. 精品主力版（推荐首选 🏆）
> **专为家庭与日常观影打造：极速秒播、纯净无广、精选央视卫视直播。**
* **配置地址**：
  ```text
  https://tv.gnoix.com
  ```
  *(备用直连：`https://tv.gnoix.com/tvbox.json`)*
* **规格亮点**：
  * **120 个极速源**：前 95 个超清低延迟爬虫站（厂长、玩偶、低端等秒播大站置顶） + 25 个实测带宽最快的优质采集站；
  * **10 个超快精选直播源**：实测最低延迟的央视、卫视、地方高清直播；
  * **全员秒搜**：全部 120 站均开启聚合搜索，电视搜片 1 秒出结果；
  * **纯净去广**：内置去广告规则拦截贴片菠菜广告 + 阿里安全 DoH 防宽带劫持 + Bing 每日超清壁纸。

---

### 🗄️ 2. 全量海量版（淘冷门剧大库 🔍）
> **收纳全网所有能找到的资源站，片源极其庞大，适合查找冷门影视、动漫番剧。**
* **配置地址**：
  ```text
  https://tv.gnoix.com/all
  ```
  *(备用直连：`https://tv.gnoix.com/tvbox_full.json`)*
* **规格亮点**：
  * **1,300+ 站点超大海量全收录**：涵盖全网所有爬虫站与采集站；
  * **全量直播**：收纳所有抓取到的 IPTV 直播频道；
  * **分级搜索保护**：前 25 站开启快速聚合搜索，避免千站并发导致电视盒子卡死。

---

### 📦 3. 独立多仓版（多仓切换 📑）
> **保留各大源作者（饭太硬、肥猫、巧技等）各自的原版独立仓库，支持随时切仓。**
* **配置地址**：
  ```text
  https://tv.gnoix.com/multi
  ```
  *(备用直连：`https://tv.gnoix.com/tvbox_multi.json`)*
* **适用客户端**：
  * **影视仓**：首页 → 配置 → 多仓地址
  * **FongMi**：设置 → 配置 → 多仓

---

## 🛠️ GitHub Raw 应急备用线路（免代理直连）

如果您的自定义域名遭遇网络波动，可直接使用以下经加速代理的 GitHub 原始文件订阅：

| 版本规格 | 应急订阅地址 |
| :--- | :--- |
| **精品版** | `https://gh-proxy.com/https://raw.githubusercontent.com/oilycn/tvyuan/master/tvbox.json` |
| **全量版** | `https://gh-proxy.com/https://raw.githubusercontent.com/oilycn/tvyuan/master/tvbox_full.json` |
| **多仓版** | `https://gh-proxy.com/https://raw.githubusercontent.com/oilycn/tvyuan/master/tvbox_multi.json` |

---

## ✨ 核心技术升级与优化特性

本项目基于开源架构进行了深度二次开发与稳定性重构：

1. **真实播放测速（切片级）**：
   拒绝仅测 Ping 延迟，脚本直接下载 m3u8 主列表并拉取多个 `.ts` 视频分片测算真实下载速度（KB/s），优选真实不卡顿的站点。
2. **Spider Jar 依赖智能继承**：
   爬虫站点（Type 3）自动继承原作者独立 Jar 包地址，彻底解决全网合并后出现的 `ClassNotFound` 报错与解析失效问题。
3. **GitHub / jsDelivr 全局国内智能代理**：
   * 自动将国内已失效的 `cdn.jsdelivr.net` 还原为标准格式；
   * 自动剥离第三方套娃加速域名，统一标准化为高速镜像代理，无需梯子即可拉取所有规则与 Jar 包。
4. **分级搜索保护机制**：
   彻底杜绝由于千站并发搜索导致的电视芯片 100% 满载与遥控器卡死。
5. **DNS 防劫持**：
   全系配置默认注入阿里巴巴安全 DoH（DNS-over-HTTPS），解决部分宽带对视频切片的 DNS 拦截与篡改。

---

## 📱 客户端推荐与下载

* **TVBox-OSC 原版**：[GitHub Releases](https://github.com/o0HalfLife0o/TVBoxOSC/releases)
* **影视仓 (支持多仓/单仓/弹幕)**：[网盘下载](https://pan.wpcoder.cn/?dir=tvbox)
* **FongMi (蜂蜜/猫影视精简版)**：[GitHub Releases](https://github.com/FongMi/Release/releases)

---

## ⚖️ 免责声明

* 本项目所有接口与数据均自动收集于互联网公开渠道，仅供学习、测试与个人电视设备调优交流使用；
* 本仓库不存储、不制作、不传播任何视音频内容及版权文件；
* 请在遵守当地法律法规的前提下使用，请勿用于任何商业用途。
