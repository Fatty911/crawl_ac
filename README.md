# crawl_ac

一个面向实际选购的中国空调多源数据管线：按品牌热度抓取太平洋电脑网、按热度抓取
ZOL、按 15 日销量抓取京东，进行跨来源身份去重和证据合并，只把满足严格准入条件
的型号参数发布到 GitHub Pages。

站点域名：`https://ac.jiucai.eu.org`

## 覆盖品牌（config/brands.json）

格力、美的、海尔、奥克斯、TCL、海信、科龙、长虹、华凌、小米（中国空调市场前10，
太平洋电脑网均有对应品牌页）。

## 核心参数

每条记录包含：匹数、类型（壁挂/立柜）、冷暖、变频/定频、能效等级、APF、制冷量/功率、
制热量/功率、循环风量、内机/外机噪音、扫风方式、制冷剂、尺寸、重量、上市时间、
操控方式、价格，以及两个选购关键硬件参数：

- **节流装置**（`throttle_type`：电子膨胀阀/毛细管/未知）
- **外机铜管排数**（`coil_rows`：单排/1.6排/双排/未知）

后两项参数库不提供（实测太平洋/ZOL/京东参数表均无），由 `scripts/ai_extract_hardware.py`
联网查证提取（要求证据 URL，查不到标"未知"，fail closed）。

## 发布准入

Pages 中的每条数据都必须同时满足：

- 空调类型为壁挂式或立柜式（中央空调/移动空调拒绝）；
- 变频（定频拒绝）；
- APF 达分档及格线：壁挂式 ≥5.0（优选 ≥5.3）、立柜式 ≥4.2（优选 ≥4.5），
  未知 APF fail closed。

节流装置/铜管排数非硬门槛，但每条发布记录必须带这两个字段（未知允许，
前端标注并可筛选）。未知值 fail closed。

## 代理架构（机场节点持续切换）

- `scripts/setup_proxy_runtime.py`：解析 `PROXY_SUBSCRIPTIONS` secret（机场订阅），
  启动 mihomo，注入本地代理环境变量；
- `scripts/node_rotator.py`：每次请求前通过 mihomo API 显式切换节点
  （`PUT /proxies/PROXY`），round-robin + 运行时黑名单（被风控/503/checking 的
  节点本次运行内禁用并记入 `crawl_state/proxy_blacklist.json`，下次重新探测）；
- 规则 `GEOIP,LAN,DIRECT + MATCH,PROXY`：中国站点一律走代理，绝不 DIRECT 绕过。

## 数据流

```text
太平洋品牌最热门榜 ─► pconline-data-YYYYMMDD ─┐
                                               ├─► 型号归一/证据合并
ZOL 空调排行 ───────► zol-data-YYYYMMDD ───────┤
                                               ├─► AI 硬件参数提取
京东15日销量榜 ────► jd-data-YYYYMMDD ────────┤
                                               ├─► 严格准入
上次 Release 基线 ──► 保留 + 防缩小 ────────────┤
                                               ├─► 审计 + 证据报告
                                               └─► data-latest Release
                                                         │
                                                         └─► Pages SPA
```

每个 crawler artifact 少于 50 行会立即失败。合并采用归一化空调型号身份键
（KFR-XX.../...），`atomic_source_names` 将别名归一为 `PConline`/`ZOL`/`JD`。
上次已发布且仍满足规则的机型会被保留，superset 校验阻止意外丢数，审计拦截
来源整体回归。`scripts/validate_storage_policy.py` 做 fail-closed 护栏：
运行时 `data/` 不得被 Git 跟踪，artifact 必须使用已登记前缀。

## 仓库结构

- `scripts/crawl_pconline.py`：太平洋品牌"最热门"榜 + 完整参数页。
- `scripts/crawl_zol.py`：ZOL 空调排行 + 参数页（代理环境解析目录后接入）。
- `scripts/crawl_jd.py`：京东官方 15 日销量榜（服务端渲染 hotitem）+ 详情规格。
- `scripts/node_rotator.py`：机场节点持续切换 + 运行时黑名单。
- `scripts/ai_extract_hardware.py`：AI 联网查证铜管排数/节流装置（带证据 URL）。
- `scripts/merge_data.py`：身份去重、来源归一、证据合并、发布准入。
- `scripts/preserve_publish_baseline.py` / `scripts/verify_publish_superset.py`：
  基线保留与防缩小。
- `scripts/audit_pages_payload.py`：结构、资格、重复和来源回归审计。
- `scripts/download_latest_crawler_artifact.py`：下载各源最新成功 artifact。
- `scripts/analysis/merge_evidence_report.py`：合并证据统计。
- `docs/`：无框架的响应式 Pages 单页应用。
- `config/brands.json`：品牌清单；`config/filter_conditions.json`：筛选/排序单一配置源。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

抓取原始数据（需机场节点环境变量 `PROXY_SUBSCRIPTIONS`）：

```bash
python scripts/crawl_pconline.py --output data/raw/pconline/latest.json
python scripts/crawl_zol.py --output data/raw/zol/latest.json
python scripts/crawl_jd.py --output data/raw/jd/latest.json
```

合并和本地预览：

```bash
python scripts/merge_data.py \
  data/raw/pconline/latest.json data/raw/zol/latest.json data/raw/jd/latest.json \
  --output data/work/candidate.json --rejected-output data/work/rejected.json
python scripts/prepare_pages_payload.py \
  --input data/work/candidate.json --docs-dir docs
python -m http.server 8000 --directory docs
```

访问 `http://localhost:8000`。不要直接用 `file://` 打开页面（浏览器会阻止
`fetch()` 读取 JSON）。

## Actions 与发布

- `crawl-pconline.yml`：太平洋热度榜（周一/每日定时 + 手动）。
- `crawl-zol.yml` / `crawl-jd.yml`：ZOL/JD 排行（需机场节点，`--require-proxy`）。
- `crawl-trigger.yml`：外部 cron-job.org 触发 + 北京时间窗口预算。
- `merge-and-filter.yml`：任一爬虫成功后合并、AI 硬件提取、审计、滚动 Release、
  触发 Pages。
- `deploy-pages.yml`：从 `data-latest` Release 下载已审计 JSON，准备
  `docs/data/latest.json` + `docs/data/manifest.json`，GitHub Pages 部署并回读
  线上 manifest 核对。
- `AI_Auto_Fix_Monitor.yml`：爬虫失败自动诊断（免费端点优先）→ 诊断 issue →
  OpenCode Agent 生成修复补丁 → 验证应用，attempt-marker 防循环。
- `single-source-repair.yml`：单源数据诊断报告。

仓库需在 **Settings → Pages → Source** 选择 **GitHub Actions**。自定义域由
`docs/CNAME` 声明为 `ac.jiucai.eu.org`，DNS 侧 CNAME 到 `fatty911.github.io`。

工作流全部使用 action 的 `@main`，不 pin commit SHA。

## 数据契约摘要

Release 资产 `ac-latest.json` 顶层包含 `schema_version`、`generated_at`、
`count`、`sources`、`items` 和 `pipeline`。每个 item 至少包含：
`identity_key`、`title`、`brand`、`model`、`ac_type`、`inverter`、`apf`、
`throttle_type`、`coil_rows`、`atomic_source_names`、`source_count`、
`source_urls`、`source_ranks`。

价格与库存随时会变化，站点仅提供结构化选购线索，最终信息以来源页面为准。
