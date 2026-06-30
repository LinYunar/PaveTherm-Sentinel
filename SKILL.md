---
name: pavetherm-sentinel
description: 沥青路表高温监测预警 skill。基于 Open-Meteo 天气 + 高德地理编码 + SHRP/LTPP 经验公式,对路表温度建模并触发 4 级预警(绿/黄/橙/红),输出飞书卡片/CSV/HTML dashboard。覆盖:监测点 CRUD、单点/批量查询、未来 N 天窗口、定时巡查、阈值告警、IM 推送闭环。触发词:"路表温度"、"沥青监测"、"路面高温"、"pavetherm"、"车辙风险"、"高温巡查"、"巡查一下"。⚠️ 复用此 skill 时用户需自行配置 4 个 key (高德/Tavily/飞书 chat_id/lark-cli), 不随 skill 打包。
metadata:
  openclaw:
    emoji: "🌡️"
    version: "0.5"
    last_updated: "2026-06-30"
    install:
      required: ["PAVETHERM_AMAP_KEY", "PAVETHERM_FEISHU_CHAT_ID", "PAVETHERM_LARK_CLI"]
      optional: ["PAVETHERM_TAVILY_KEY"]
      installer: "python3 scripts/install.py"
    secrets_storage: "~/.hermes/secrets/pavetherm-sentinel.env"
---

# PaveTherm Sentinel — 沥青路表高温监测预警

**类级定位**: 物理基础设施的实时环境监测预警 skill。原型是沥青路面高温监测,但架构通用 — 替换数据源/建模/阈值,可复制到桥梁结构监测、隧道通风、输电线路温度等同类场景。

## 适用场景

- 用户说"监测 XX 路段路表温度"、"路面高温风险"、"车辙预警"、"高温巡查任务"
- 用户添加一个坐标(精准或道路名),问当前/未来 N 天的路表温度
- 用户批量查"我在 XX 城市的所有点位"
- 用户配置定时巡查 cron,触发预警主动推飞书
- 用户要 CSV 导出 / 可视化 dashboard

## 🚀 首次安装 (复用者必读)

本 skill **不打包任何 API key**。所有 key 保存在 `~/.hermes/secrets/pavetherm-sentinel.env` (chmod 600), 跟随 skill 的代码完全独立。

### 必装 3 项 key (缺一不可)

| 字段 | 来源 | 说明 |
|------|------|------|
| `PAVETHERM_AMAP_KEY` | https://lbs.amap.com/dev/key/app | 高德 Web 服务 API key, 用于 geocoding |
| `PAVETHERM_FEISHU_CHAT_ID` | https://open.feishu.cn/document/server-docs/im-v1/chat-group/chat-id-introduction | 飞书机器人 chat_id (推送目标, 格式 `oc_xxxxxxx`) |
| `PAVETHERM_LARK_CLI` | `npm install -g @larksuite/cli` (默认 `~/.npm-global/bin/lark-cli`) | 飞书消息 CLI 工具 |

### 可选 1 项

| 字段 | 来源 | 说明 |
|------|------|------|
| `PAVETHERM_TAVILY_KEY` | https://tavily.com/ | 查道路建成/大修年份。**缺它时走默认 5 年中度老化兜底**, skill 仍可正常使用 |

### 一键引导

```bash
python3 scripts/install.py
```

按提示粘贴 4 项 key → 自动写入 `~/.hermes/secrets/pavetherm-sentinel.env` (chmod 600) → 跑 smoke test。

### 飞书机器人权限授权

1. 访问 https://open.feishu.cn/app → 找到你的应用 (或新建)
2. 「权限管理」→ 开通以下 scopes:
   - `im:message` (发送消息)
   - `im:message.group_at_msg` (群消息 @)
   - `im:chat:readonly` (读取会话列表)
3. 「事件订阅」→ 添加 `im.message.receive_v1` (接收消息, 可选)
4. 把机器人**加入目标 chat** (私聊直接发, 群需要先 add)
5. 复制 chat_id: 在 chat 详情页 URL 里, 形如 `oc_xxxxxxxx`

### 验证安装

```bash
python3 scripts/config_loader.py    # 配置自检
python3 scripts/monitor.py list      # 列出监测点 (空 = 首次无点)
python3 scripts/monitor.py add "成都天府广场" --pavement-color gray --age 5
python3 scripts/monitor.py batch --filter city=成都 --export feishu
```

详细说明书: `README_INSTALL.md`

## 核心架构 (4 段链路)

```
[1] 坐标解析      精准坐标 | 高德(国内 POI) | Nominatim(海外兜底)
       ↓
[2] 数据拉取      Open-Meteo 当前+14d (气温 / GTI 太阳辐射 / 云量 / 风速)
       ↓
[3] 路表建模      T_pav = T_air + α·GTI + α_color·I + Δ_age − α_wind·wind
       ↓
[4] 预警+渲染     4 级阈值(55/60/65) → 飞书卡片 / CSV / Dashboard
```

## 关键事实(均已实测验证,非推测)

### 网络可达性(容器内)
- ✅ **Open-Meteo** 通(1.1s,无需 key)
- ✅ **高德/百度/腾讯地图 API** 通(均需 key)
- ❌ **Nominatim (OSM)** **不可达** — `curl: (101) Network is unreachable`。**不要**把 Nominatim 当主路,只能海外兜底。
- ❌ **Tavily search/extract** 当前 401(凭证过期),搜索引擎需用其它路径

### Open-Meteo 字段重点
- `global_tilted_irradiance` (GTI, W/m²) 是建模关键,**不是** `shortwave_radiation`。GTI 是瓦片辐射,直接对应路面吸热。
- `temperature_2m` 是 2 米气温,非地表。
- `daily.temperature_2m_max/min` 是日极值,hourly 是逐小时。

### 高德地理编码响应格式
- 路径:`https://restapi.amap.com/v3/geocode/geo?key=XXX&address=XXX`
- 成功 `status="1"`,结果在 `geocodes[0].location` (格式 `"lng,lat"` 注意经度在前)
- `level` 字段是关键置信度信号:`道路名/交叉路口/桥/高速` → 高;`地名地址/兴趣点` → 中;`省/市/商圈` → 低
- 失败 `status="0"`,`info` 字段有原因(常用 `INVALID_USER_KEY` / `CUQPS_HAS_EXCEEDED_THE_LIMIT`)

### SHRP/LTPP 路表温度模型参数(已实施,🟡 ASSUMED 偏保守)
```
T_pavement = T_air
           + 0.035 × GTI                # 太阳辐射升温 (α_rad)
           + color_abs × (GTI/800) × 2.5 # 颜色吸收率 (black=0.92 / gray=0.80 / light_gray=0.65 / unknown=0.78)
           + age_correction(age_years)  # 老化年数 (0→0, 5→+0.5, 10→+1.0, 15→+1.2, 20→+1.0)
           − 0.5 × wind_speed           # 风冷
```
**风险预警偏置**: α_rad 取上限 0.035,SHRP 文献实测多在 0.025-0.030。**有意识地让模型偏激进**,因为黄警漏报比红警误报代价高。卡片 footer 需明示此设计。

### 飞书 Interactive Card v1 schema 注意事项
- `<text_tag color='xxx'>` 是内联标签,**必须在 markdown 元素字符串内**,不能单独 tag。
- `header.template` 颜色仅支持固定枚举: blue/green/yellow/orange/red/grey/purple/wathet。
- 卡片 JSON 通过 `msg_type: interactive` POST 到 webhook 或 chat API。
- 单卡片宽度限 8000 字符,内容超长要分页或拆分。
- **lark-cli `--content` 有 JSON 嵌套陷阱** (4 轮试错才解决) — 详见 `references/feishu-card-schema.md` 的"lark-cli 实战踩坑"章节。

## 命令行速查(完整在 scripts/monitor.py)

```bash
# 单点查询(默认飞书卡片 JSON)
python3 scripts/monitor.py query <point_id> --export {feishu|csv|console|dashboard}
python3 scripts/monitor.py query <point_id> --days N --export {feishu|console}  # 未来 N 天 (1-14)
python3 scripts/monitor.py query <point_id> --from-date YYYY-MM-DD --days N       # 起始日 + 天数
python3 scripts/monitor.py query <point_id> --days N --send-feishu                # 推飞书单点卡片

# 批量查询
python3 scripts/monitor.py batch --all
python3 scripts/monitor.py batch --filter city=成都
python3 scripts/monitor.py batch --filter city=成都 --days N --export feishu       # 批量 N 天窗口
python3 scripts/monitor.py batch --filter city=成都 --from-date YYYY-MM-DD --days N

# 增删
python3 scripts/monitor.py add "京港澳高速某段" --pavement-color gray --age 8
python3 scripts/monitor.py remove <point_id>
python3 scripts/monitor.py list

# 几何/天气/建模独立可调
python3 scripts/geocode.py "成都三环路成渝立交"
python3 scripts/fetch_weather.py 30.6344 104.1516
python3 scripts/model.py  # 单点参数敏感性测试
python3 scripts/alert.py  # 阈值分档测试

# 监测点元数据自动补全 (路面颜色/年份缺失时)
python3 scripts/enrich_point.py cd_chengyulukou_001          # dry-run, 不写 YAML
python3 scripts/enrich_point.py cd_chengyulukou_001 --apply  # 写入
python3 scripts/enrich_point.py                              # 全部缺失点批量补

# 推送到飞书 (2026-06-30 实测验证)
python3 scripts/feishu_push.py point --chat-id "oc_xxx" --point-id cd_chengyulukou_001
python3 scripts/feishu_push.py batch --chat-id "oc_xxx" --filter "city=成都"
python3 scripts/feishu_push.py point ... --markdown  # 降级到 markdown post

# 推送验证 (Bug 4 防御)
python3 scripts/verify_push_no_questionmark.py                # dry-run 验证字段映射
python3 scripts/verify_push_no_questionmark.py --send-feishu --chat-id "oc_xxx"  # 实际推 1 张验证
```

## 文件结构

```
pavetherm-sentinel/
├── SKILL.md                          # 本文件
├── config.yaml                       # API keys + 模型参数 (chmod 600, .gitignore)
├── data/
│   └── monitoring_points.yaml        # 监测点元数据(主存储)
├── scripts/
│   ├── geocode.py                    # 坐标解析
│   ├── fetch_weather.py              # Open-Meteo 拉取
│   ├── model.py                      # 路表温度建模
│   ├── alert.py                      # 4 级预警 + 飞书卡片 V1/V2 双渲染器
│   ├── feishu_push.py                # 飞书推送 (含 FEISHU_HOME_CHAT_ID 常量)
│   ├── render.py                     # markdown 渲染 (降级用)
│   ├── dashboard.py                  # HTML dashboard
│   ├── enrich_point.py               # 多源元数据补全
│   ├── monitor.py                    # CLI 主入口 (含 --days / --from-date)
│   └── verify_push_no_questionmark.py # 推送验证脚本
├── templates/
│   └── monitoring_points.template.yaml   # 新建点位时复制此模板
├── references/
│   ├── api-reference.md              # Open-Meteo / 高德 字段参考
│   ├── model-calibration.md          # SHRP/LTPP 参数选择依据 + 校准路径
│   ├── feishu-card-schema.md         # Interactive Card v1 写法规范 + lark-cli 踩坑 ⚠️
│   ├── feishu-push-bugs.md           # 推送链路 4 个 bug 修复记录
│   ├── card-layout-architecture.md   # V1/V2 双版本排版架构
│   ├── enrichment-pattern.md         # 监测点元数据 4 源补全模式
│   └── time-window-feature.md        # --days / --from-date 时间窗口能力 (2026-06-30 新增)
└── exports/                          # CSV / Dashboard 输出目录
```

## 配置即开即用(checklist)

1. `config.yaml` 填高德 key(无 key 走 Nominatim 海外兜底,国内命中率低)
2. 监测点先用本地 YAML,跑通再升级飞书多维表格(`lark_bitable.enabled=true`)
3. 路面颜色 `pavement_color` 必填,缺失按 `unknown` 兜底但模型置信度降为 LOW
4. 阈值 `warning_thresholds` 可逐点覆盖默认 55/60/65
5. **飞书 home chat_id** 默认从 `config.yaml` 的 `feishu_push.feishu_home_chat_id` 读, 否则从 env `PAVETHERM_FEISHU_CHAT_ID` 读, 兜底空串(必须由 install.py 引导用户填入, **禁止硬编码** 避免泄漏到公开仓库)

## Pitfalls(踩过的坑,新 session 别再踩)

1. **Nominatim 不可达**:不要把 OSM 当主路,容器环境经常被 GFW 拦。先高德。
2. **坐标顺序**: 高德 `location` 字段是 **"经度,纬度"**,不是 "纬度,经度"。直接 split 会反。
3. **GTI 不是太阳辐射总场**: 用 `global_tilted_irradiance`(瓦片辐射)而非 `shortwave_radiation`(水平辐射),前者对应路面真实吸热。
4. **当前无 GTI 数据**: `current_weather` 只给气温/风速/天气码,不包含辐射。建模当前温度时按 `is_day` 粗估 500 W/m²(白天)/ 0(夜晚),或查同小时 hourly 数组。
5. **模型偏激进是设计选择**: 不要"修正" α_rad 回到 0.025 而不告诉用户。风险预警场景宁误报不漏报。
6. **飞书卡片 JSON 中 markdown 元素内的 `<text_tag color>`**: 必须用单引号,不能转义。这是飞书渲染层的怪癖。
7. **API key 别入仓**: `config.yaml` 写 `chmod 600`,`.gitignore` 加 `config.yaml`。这是 SOUL.md 红线。
8. **CSV 用 UTF-8-BOM 编码**: Excel 中文不乱码(`utf-8-sig`)。
9. **lark-cli `--content` JSON 嵌套陷阱**: 不要预先把卡片 dict json.dumps 整个包成字符串再传(lark-cli 会再 stringify 一次导致三重嵌套, 200621)。**正确的 `content` 就是卡片 dict 自己的 JSON 字符串, 不加 `card` 包装**。详见 `references/feishu-card-schema.md` 末尾"lark-cli 实战踩坑"章节。
10. **改 API 前必用 `--dry-run`**: `lark-cli im +messages-send --dry-run ...` 看 body.content 是不是你期望的格式, 能省 3 轮试错。
11. **路面颜色/年份缺失别用 placeholder**: 走 `enrich_point.py` 4 源兜底 (OSM 周边路网 → 高德 POI 类型 → Tavily 搜建成/大修年份 → JTG F40 按路等级+年龄推断颜色)。**所有结果带置信度标签** (CONFIRMED/ASSUMED/SPECULATIVE), 模型用置信度决定权重。详见 `references/enrichment-pattern.md`。
12. **⚠️ 飞书批量推送链路 4 个已知 bug (2026-06-30 累计)**: 详见 `references/feishu-push-bugs.md`。
    - **Bug 1-3 (旧)**: `feishu_send` 名字不存在 / 参数错位 / `render_batch_markdown` 路径错。**临时修**了别名, 完整修走真 `push_batch(chat_id, results, use_card=True)` + `FEISHU_CHAT_ID` 兜底。
    - **Bug 4 (严重)**: `feishu_push.py push_batch()` 内部字典推导漏 5 个字段 → 卡片显示 `?` / `??:??` / `?°C`。**修法**: 字典推导补全 5 字段。**验证必跑**: `scripts/verify_push_no_questionmark.py`
    - **强约束**: 改 feishu_push.py 任何字段映射前, **必须**先跑验证脚本确认推送内容无 `?`。
13. **⚠️ 飞书卡片排版: 信息密度 vs 视觉舒适度 (2026-06-30 晚 新增)**: 默认 `render_multi_point_card` (V1) 把 6 个点塞进 2 列 `column_set` 网格, 信息堆一起视觉拥挤。用户反馈"挤一团,没有分割,看起来太累"。
    - **解法**: `render_multi_point_card_v2()` 采用**分块+全展开+分线**+ 树形字段 + 双等级显示 (当前+峰值)
    - **字符预算**: 6 点 V2 约 3166 字符, 飞书 markdown 上限 4096, **不要超过 3500**。
    - **切换方式**: `push_batch(chat_id, results, use_card=True, version="v2")` (默认 v2), `version="v1"` 切回旧版
    - **教训**: 卡片排版是 P0 设计问题, 不是 P2 装饰 — 用户拿到第一眼就拒。**改卡片布局前先发 V1 给用户看, 再加 V2 切换**。
14. **⚠️ patch 工具会"删除非空行" (2026-06-30 晚 发现)**: 用 `patch` 工具做 find-and-replace 时, 如果 `old_string` 只包含 `}\n    return ...` 边界附近的 1-2 行, 而 `new_string` 包含 1 个新插入块但没保留原有那行, 工具会**静默删掉**被替换位置的那行。
    - **真实案例**: 改 `feishu_push.py:238-243` 时, 工具**误删**了 `card = render_multi_point_card(pts_alerts)` 那行, 引发 `NameError: name 'card' is not defined`。
    - **防御**: 改完后**必跑 `python3 -c "import scripts.feishu_push"` 验证无语法/导入错误**。
    - **原则**: patch 工具的"删除"是合并到替换里的, 不是"独立删除"。**插入块必须显式包含边界**。
15. **⚠️ 时间窗口 (`--days` / `--from-date`)**: 高频问 "X 未来 7 天怎么样" / "成都所有点位 14 天" —— 这种**时间窗口**需求必须支持, 不能只给固定 14 天。
    - **接口**: `query_point(point_id, days=None, from_date=None)` / `query_batch(..., days, from_date)`
    - **CLI**: `python3 scripts/monitor.py query <id> --days N --send-feishu` / `batch --filter city=成都 --days N --export feishu`
    - **返回结构**: `daily_peaks` (全 14 天保留, API 兼容) + `daily_peaks_sliced` (截取后) + `slice_window` (窗口元数据)
    - **关键设计 (⚠️ 修正)**: V2 渲染器**必须显式收** `window_days` 参数, 因为卡片文案 ("14 天内峰值" / "14 天内全网最危险日" / "14 天峰值降序") 之前是写死的——只改字段不改文案, 用户会立刻质疑 (2026-06-30 晚 反馈)。修法: `feishu_push.r0_window_days(results)` + `render_multi_point_card_v2(pts_alerts, window_days=...)`, V2 内部按 `window_days` 动态选文案。详见 `references/time-window-feature.md` V2 渲染器必须收 window_days 节
    - **字符预算**: 6 点 V2 + 5 天窗口实测 3166 字符, 4096 上限; 加 `--include-14d-table` 会爆 (开关在 argparse, 实现待补)
    - **顺手修 2 个 bug**:
        - `query --send-feishu` 把 markdown 当 chat_id → 改 `push_markdown(FEISHU_HOME_CHAT_ID, md)`
        - `batch --export feishu` 直接传 results 给 V1 渲染器 (字段格式不匹配) → 改走 `feishu_push.push_batch(..., version="v2")`
    - **完整说明 + 验证记录**: 详见 `references/time-window-feature.md`
    - **后续 TODO**: 自然语言解析 ("成都三环路未来 7 天" 自动映射), `--include-14d-table` 真实现

17. **⚠️ 数据对 ≠ 文案对 (2026-06-30 晚 新增)**: 改字段计算逻辑后, **必须**同时检查**所有写死**的渲染文案/标题/footer。`peak_14d_max_date` 是窗口内了, 但 V2 渲染器内"14 天内峰值"/"14 天内全网最危险日"/"14 天峰值降序"还写死——用户拿到卡片立刻发现"明明 5 天窗口, 卡片说 14 天", 立刻质疑 (本次教训)。
    - **强约束**: 改任何**带窗口/参数维度**的字段后, grep `14 天` / `默认` / `硬编码` 看渲染器内是否还有写死文案
    - **测试方法**: 拿不同 `--days` (1/5/7/14) 各推一次卡片, 读 `feishu_card` 全文确认文案随窗口变
    - **反模式**: "数据计算逻辑改了, 文案渲染器不动"——**字段语义改了, 渲染器要相应具备这个上下文** (要么显式收参数, 要么用同一个上下文源)
    - **相关修改**: 见 `references/time-window-feature.md` 的"V2 渲染器必须收 window_days"节

## 排版架构(V1 / V2 双版本,2026-06-30 晚)

```
alert.py
├── render_multi_point_card()        # V1: 2 列 column_set 网格, 信息堆 (默认旧版, 备用)
└── render_multi_point_card_v2()     # V2: 分块 + 全展开 + 分线 (当前默认, 排版清新)

feishu_push.py
└── push_batch(chat_id, results, use_card=True, version="v2")
    ├── version="v1" → render_multi_point_card (旧)
    └── version="v2" → render_multi_point_card_v2 (新, 默认)
```

**V2 关键设计原则** (新加卡片前必读):
- **每点独立 div**, 不再用 column 网格 (column 适合 1-3 点对比, 不适合 6+ 点罗列)
- **每点内字段树形化**: `├` `└` 表示层级, 而非空格/换行
- **双等级显示**: 当前温度等级 + 14 天峰值等级都要显示 (避免"现在绿+未来红"被掩盖)
- **预算 3500 字符**: 超了拆卡, 不要裁剪字段

## 飞书推送函数选择 (4 种,各管各的)

| 函数 | 入参 | 用途 |
|---|---|---|
| `push_markdown(chat_id, md)` | 必填 chat_id | 最稳, 降级用, 避开 200621 |
| `push_card(chat_id, card_dict)` | 必填 chat_id | 真卡片 (interactive) |
| `push_point(chat_id, point_id, ...)` | 必填 chat_id | 单点 (内部渲染+推送) |
| `push_batch(chat_id, results, ...)` | 必填 chat_id | 批量 (内部转换格式+V2 渲染+推送) |

**所有 4 个函数** `chat_id` 都是**第 1 个位置参数**, **没有默认值**。如果调用方没传, 用 `FEISHU_HOME_CHAT_ID` 常量兜底 (从 config.yaml 读)。

**铁律**: **绝不要**调 `send(md, "markdown")` 这种 2 参 (把 md 当 chat_id) 的调用 — 这是 6/30 累计 2 个 bug 的根因。

## 升级路径(从 demo 到生产)

| 阶段 | 触发条件 | 动作 |
|---|---|---|
| **Demo → 单点生产** | 单点跑通 | 接飞书消息 API(webhook 或 tenant_access_token)实现推送闭环 |
| **单点 → 批量** | ≥3 个点位 | 接飞书多维表格当云端主存,本地 YAML 当 cache |
| **批量 → 定时** | 多点位常态化监测 | cron_setup.py 注册定时巡查,触发阈值主动推 |
| **定时 → Dashboard** | 用户要可视化 | dashboard.py 生成 HTML+Leaflet 瓦片地图,飞书云文档内嵌 |
| **Dashboard → 长期托管** | 跨设备访问需求 | GitHub Pages / Vercel 托管,Git 同步 |

## 后续可扩展方向(暂未实现)

- **同类垂直迁移**: 替换数据源和模型,这套架构可直接做桥梁伸缩缝监测、隧道 CO/VI 监测、输电线路温度监测。模板化后单类 ≤2 小时可出 demo。
- **多源融合**: 当前只用 Open-Meteo,可加和风天气、心知天气作为对比源(冗余降风险)。
- **实测校准**: 当积累用户提供的"实测路表温度"后,用线性回归校准 α_rad/color_abs/age_correction。
- **机器学习替代**: 收集 ≥1000 样本(气温/GTI/云/风/实测路表)后,XGBoost/LightGBM 通常能把 MAE 从 ±5°C 降到 ±2°C。
- **自然语言查询**: "成都三环路未来 7 天" → 自动解析 → `query --days 7 --send-feishu`
- **批量卡片 14 天表**: 实现 `--include-14d-table` (需拆卡或降级到 CSV 附件)

## 已知状态(2026-06-30)

- ✅ 端到端 demo 跑通:成渝立交 1 个真实监测点
- ✅ 6 个成都点位批量查询通过(成渝立交/春熙路/宽窄巷子/武侯祠/刃具立交/双流机场高速)
- ✅ 飞书真卡片推送通过 (header 颜色 + markdown 主体 + hr + note)
- ✅ 飞书 markdown 降级推送通过
- ✅ **V2 清新版卡片推送通过**: `render_multi_point_card_v2()` + `push_batch(..., version="v2")` 默认
- ✅ **未来 X 天生成卡片能力**: `--days` / `--from-date` 单点+批量+console 三路径, 3 个 message_id 实测验证
- ✅ **数据一致性自检测试套件**: `tests/test_query_consistency.py` (8 个测试类, ~30 个用例) — `python3 tests/test_query_consistency.py` 独立运行, 或 `pytest tests/test_query_consistency.py -v` 接 CI。强约束: 改 query/peak/card 任何代码前必跑。
- ⚠️ 路面颜色全部 unknown (你还没填, 模型置信度 LOW)
- ⚠️ 飞书多维表格云端同步未启用
- ⚠️ Cron 定时巡查未接
- ⚠️ Dashboard 未生成
- ⚠️ **Skill 还没完成对外可发布 (redistribution-ready) 封装** — 见下文"Distribution Packaging (pending)"
- ✅ Bug 4 (字段映射 `?` 显示) 已修
- ✅ Bug 5-6 (query/batch --send-feishu 参数错位) 已修
- ✅ 验证脚本 `scripts/verify_push_no_questionmark.py` 改 feishu_push.py 前必跑

## 相关 skill

- `weather`: 通用天气拉取,本 skill 在它基础上加了路表建模
- `maps`: 通用地图查询,本 skill 的 geocode 走的是更专业的国内 POI 路径
- `feishu-lark-cli`: 飞书消息推送,本 skill 卡片 JSON 可直接喂给它
- `stock-monitor`: 架构同源(监测点+阈值+预警),可参考其 cron 注册与告警去重模式
- `rigor-discipline`: 出方案/写 todo/大改前必读,本 skill 的 4 字真言由此而来

## 进一步阅读(本 skill 的 references/)

- `references/api-reference.md` — Open-Meteo / 高德 字段参考、容器内网络可达性实测表、字段命名不一致陷阱
- `references/model-calibration.md` — SHRP/LTPP 参数选择依据、已知偏差方向、用户实测数据进来后的 3 级校准路线图
- `references/feishu-card-schema.md` — Interactive Card v1 写法规范、text_tag 单引号陷阱、**lark-cli `--content` JSON 嵌套踩坑 6 条 + 降级路径** ⚠️ 新
- `references/feishu-push-bugs.md` — 2026-06-30 推送链路 **4 个 bug** (Bug 1-3 临时修 + Bug 4 字段映射) + 完整修法 + 验证脚本路径
- `references/card-layout-architecture.md` — V1/V2 双版本排版架构, V2 设计原则 (分块+树形+双等级), 字符预算表, 已知边界, 扩展方向
- `references/enrichment-pattern.md` — 监测点元数据 4 源补全模式 (OSM/高德/Tavily/JTG F40), 置信度标签设计
- `references/time-window-feature.md` — **2026-06-30 晚新增** `--days` / `--from-date` 时间窗口能力, 字段截取逻辑, 验证记录, 后续 TODO
- `references/user-visible-data-consistency-test.md` — **2026-06-30 新增** "用户看到的卡片/UI 字段 == 底层计算值" 的测试模式 (3 步流程 + 真实案例 + 5 个踩坑), 对应 `tests/test_query_consistency.py`, 是 rigor-discipline 7.6.1.1 的代码化版本
- `references/redistribution-secrets-pattern.md` — **2026-06-30 新增** 「Skill 凭证外部化 + 可复用封装」class-level 模式 (4 步落地法 / 文件位置约定 / 旧代码迁移 / 零回归 4 件验证 / 11 项 Checklist), 任何同类封装工作都适用, 这次 PaveTherm v0.4→v0.5 实战验证
- `references/bugs-discovered-2026-06-30.md` — **2026-06-30 session 收尾 实战发现的 4 个真 bug**: (1) 高德反查"康定东大街" → 错配上海康定东路  (2) feishu_push.py env 加载不稳 (靠 shell source) (3) add 点位不验证坐标合理性 (4) README/脚本含真 key 泄漏  → 教训: 街道级地名要全限定 / feishu_push.py 应自 load_dotenv / 报"封装完成"前必 grep final-check

## 起步模板

`templates/monitoring_points.template.yaml` — 包含完整字段注释、字段填写指引、cron 任务示例。复制后修改即可。
