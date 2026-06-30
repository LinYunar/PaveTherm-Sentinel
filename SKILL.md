---
name: pavetherm-sentinel
description: 沥青路表高温监测预警 skill。基于 Open-Meteo 天气 + 高德地理编码 + SHRP/LTPP 经验公式,对路表温度建模并触发 4 级预警(绿/黄/橙/红),输出飞书卡片/CSV/HTML dashboard。覆盖:监测点 CRUD、单点/批量查询、未来 N 天窗口、定时巡查、阈值告警、IM 推送闭环。触发词:"路表温度"、"沥青监测"、"路面高温"、"pavetherm"、"车辙风险"、"高温巡查"、"巡查一下"。⚠️ 复用此 skill 时用户需自行配置 4 个 key (高德/Tavily/飞书 chat_id/lark-cli), 不随 skill 打包。
metadata:
  openclaw:
    emoji: "🌡️"
    version: "0.6"
    last_updated: "2026-06-30 (末)"
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
- ✅ **Tavily search/extract** 通(2026-06-30 末 实测 curl 返回 200,`api.tavily.com/search` POST 正常,response_time < 1s) — **校正**之前"Pitfall 24 网络可达性表"里写的"❌ 401 凭证过期",那条已过期,实际 key 能用。**但**搜中文小地名仍会返回同名无关实体 (Pitfall 23) — 见 `references/tavily-curl-verify.md` 验证命令

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

18. **Config 字段必须同步实现 reader**: `config.yaml` 中定义的配置项（如 `output.temperature_unit`、`output.prefer_both`）必须在代码中有对应的读取逻辑，否则配置不生效。
    - **约束**: 写配置字段时必须同步实现 reader（或在 docstring 中明确标注 "TODO: reader not implemented"）
    - **推荐模式**: 引入 `output_helper.py` 集中格式化（如 `format_temp(c)`），所有渲染调用方统一通过 helper 读取配置
    - **验证**: `rg "9/5|°F|华氏|fahrenheit" scripts/` 残留检查应只命中 helper 内部
    - **详见**: `references/temperature-display-and-card-content.md`

19. **修改卡片字段后必须执行端到端三步验证**: 渲染逻辑分布在多个文件中，修改字段时容易遗漏。
    - **强制三步**:
        1. `query_point(<id>)` 检查卡片 JSON 包含新字段
        2. 推送一张真实飞书卡片目视确认
        3. 运行 `verify_push_no_questionmark.py` 验证无 `?` 显示
    - **多渲染器同步**: 修改表头时 `alert.py` 和 `feishu_push.py` 两个表头必须一起改（一个 markdown tag 一个 inline 字符串）
    - **详见**: `references/temperature-display-and-card-content.md`

20. **卡片类型与查询需求匹配**: 单点查询和批量查询输出不同，需根据场景选择。
    - **决策矩阵**:
        - 单点 14 天明细 → `push_point(chat_id, point_id)` 单点卡片（含完整 14 天表）
        - 批量现状总览 → `push_batch(chat_id, results, version="v2")`（不含逐日，仅高风险日清单）
        - 10 点全量 14 天数据 → CSV / Markdown 附件，不走飞书卡片
    - **字符预算**: 飞书 markdown 上限 4096 字符，10 点 × 14 天明细超出预算
    - **详见**: `references/temperature-display-and-card-content.md`

21. **时间窗口硬限制**: Open-Meteo 免费 API 最长支持 16 天预报，本 skill 默认 14 天。
    - **规则**: 当查询需求超过 14 天时，必须直接告知无法预测并提供 14 天数据，不假装能跑、不静默降级
    - **详见**: `references/temperature-display-and-card-content.md`

22. **enrich 不得覆盖 CONFIRMED 数据**: `enrich_point.py --apply` 默认会覆盖所有字段，包括用户手工标记为 CONFIRMED 的字段。
    - **已实现防御** (`scripts/enrich_point.py:230-252` 顶部守卫): 函数开头检查已有 `*_confidence == "CONFIRMED"` 的字段（age/color/type），直接保留现有值并跳过 Tavily 推断
    - **强约束**:
        1. `--apply` 前必跑 dry-run，人工确认结果再 apply
        2. 已有 CONFIRMED 字段自动跳过（代码已实现）
        3. Tavily 关键词不足时易搜到同名无关实体，建议使用 `<路名> 沥青 大修 OR 翻修 OR 铣刨重铺` 类语义化关键词
    - **回滚模式**: 修改前 `cp data/monitoring_points.yaml data/monitoring_points.yaml.bak-<日期>`，错配时从备份恢复
    - **详见**: `references/enrichment-pattern.md` + `references/data-verdict-display.md`

23. **Tavily 搜索路名可能返回同名无关实体**: 地名搜索容易匹配到地铁站、公交站、商圈等同名词条。
    - **强约束**: Tavily 关键词必须包含路面/施工语义，例如 `"<路名> 沥青路面 大修 OR 翻修 OR 铣刨重铺"` 加 `"<路名> 道路工程 路面"` 双关键词
    - **可选防御**: 搜索结果回包后用 LLM 二次过滤，确认是"路"而非"站/商圈/楼盘"
    - **置信度标注**: 即使 Tavily 返回结果，ASSUMED/SPECULATIVE 仍可能是误匹配，卡片需显示 `⚠️ 估计` 标签
    - **详见**: `references/enrichment-pattern.md` 第 3 节

24. **数据真实度铁律**: 监测点元数据（color/type/age）必须满足以下要求：
    1. **必须走 Tavily 搜索真实数据**——不得手工填值就完事
    2. **Tavily 搜不到时使用 DEFAULT 兜底**——`pavement_age_years: 5`、`pavement_age_source: "default (Tavily 未找到)"`、`pavement_age_confidence: "DEFAULT"`
    3. **卡片/UI 必须明确标识数据来源**——四档 verdict：
        - `✓ 真实`（CONFIRMED，user 来源）
        - `⚠️ 估计`（ASSUMED/SPECULATIVE，行业推断或 Tavily）
        - `❓ 默认`（DEFAULT，Tavily 搜不到兜底）
        - `?未知`（字段无 confidence 标注，缺数据）
    - **失效条件**: 新加点位不走 `enrich_point.py --apply` 则字段缺失 `*_source` / `*_confidence`，卡片显示 `?未知`
    - **详见**: `references/data-verdict-display.md`

25. **enrich --apply 必须同时写值、source、confidence 三个字段**: 任何 enrich 写入路径缺一不可。
    - **要求**: 三个一组共 6 字段（age + color + type 各一组：值、source、confidence）
    - **验证命令**:
      ```bash
      python3 -c "import yaml; d=yaml.safe_load(open('data/monitoring_points.yaml')); \
        [print(p['id'], 'age_conf:', p.get('age_confidence'), 'color_conf:', p.get('color_confidence'), 'type_conf:', p.get('type_confidence')) \
         for p in d['monitoring_points']]"
      ```
      输出应全部为 CONFIRMED / ASSUMED / DEFAULT，不应有 None
    - **详见**: `references/data-verdict-display.md`

26. **JTG F40 颜色推断分段**: 基于路面使用年数推断沥青表面颜色。
    - **分段**: `≤5 年 → black` / `5-10 年 → gray` / `>10 年 → light_gray`
    - **依据**: 沥青路面 5 年内表面氧化层薄，仍接近黑色；5-10 年中度老化变深灰；10+ 年表层剥落泛白
    - **副作用**: 之前推断为 gray 的点重跑可能变 black；CONFIRMED 字段不受影响（Pitfall 22 守卫）

27. **数据可追溯性要求**: 每个自动填的字段必须有可追溯链路。
    - **格式**: 字段值 + `_source`（数据来源） + `_confidence`（可信度）
    - **回答模板**: "X 字段，来源 Y（user/Tavily/JTG F40/OSM/高德），置信度 Z（CONFIRMED/ASSUMED/DEFAULT/SPECULATIVE）"
    - **不允许**: "大概是默认吧" / "之前填的" / "enrich 推断的" 等含糊回答
    - **会话搜索兜底**: 代码查不到时用 `session_search query="<关键字段>"` 翻历史

28. **批量操作前必须先验证数据存在**: 不得基于口述或假设的数据执行批量操作。
    - **强约束**:
        1. 批量操作前必须 `python3 -c "import yaml; print(len(...))"` 数实际条目
        2. 推送 N 张卡片后必须抽样验证 1-2 张
        3. "全部成功" 表述前必须有实物证据（`wc -l` / `ls | wc -l` / `git log --oneline | wc -l` / curl exit code）
    - **诚实表述**:
        - ✅ "yaml 现有 N 个点，推送 N 张卡片，实测全部 ok=true"
        - ❌ "应该推了 N 张" / "我记得 N 个点" / "前面跑过 N 张"（无实物证据）
    - **回滚预案**: 批量操作前必须有备份（`cp data/monitoring_points.yaml data/monitoring_points.yaml.bak-<日期>`）

29. **工作流原则: 快速且准确**:
    - **原则 1**: 不在执行过程中插入"等等"等打断性表述；如需验证，表述为"读取 X 文件"而非"等等让我先验证"
    - **原则 2**: 一次到位——`read_file` 读完直接 patch，不分步解释
    - **原则 3**: 用户拍板后立即执行，不重复确认"要不要先 X"
    - **原则 4**: 结果先于过程——改完后先告诉用户"完成/失败"，再解释细节
    - **环境限制**: session 上下文压缩导致丢失前文是已知问题，解决方案是使用工具查找（`grep` / `session_search`），而非用"等等"拖延

30. **enrich 常见故障根因**:
    1. **Tavily 搜中文小地名识别度低**——返回地铁规划/JICA 报告等无关实体。外部 API 限制，关键词需包含路面/施工语义；找不到时老实标 DEFAULT
    2. **enrich 兜底逻辑异常**——旧版 `max(0, road_age-8)` 把年龄改 0。已修复：使用 `min(road_age, 5)` 默认 5 年
    3. **CONFIRMED 守卫缺失**——即使手填真值也会被覆盖。已修复：`enrich_point.py:230-252` 顶部守卫
    - **回答模板**: 1) Tavily 外部限制 → 关键词改进 + DEFAULT 兜底  2) enrich 兜底 bug → 已修复  3) CONFIRMED 守卫缺失 → 已修复

31. **渲染器分家陷阱**: `feishu_push.py:render_point_markdown()` 是独立渲染器，不调用 `alert.py`，改 `alert.py` 不会自动同步到 markdown 降级路径。
    - **渲染器分家现状**:
        - `alert.py:render_feishu_card()` → 真卡片（interactive card JSON，走 `push_card`）
        - `alert.py:render_multi_point_card_v2()` → 批量真卡片（走 `push_batch(..., version="v2")`）
        - `feishu_push.py:render_point_markdown()` → 单点 markdown 降级
        - `feishu_push.py:render_batch_markdown()` → 批量 markdown 降级
    - **强约束**: 修改任何字段显示（温度单位/列名/14 天表 header）时必须 grep 全部 4 个渲染器并全部同步
    - **验证命令**:
      ```bash
      rg "f\".*°C.*°F\"|temp_to_dual|table_header|峰值" scripts/alert.py scripts/feishu_push.py scripts/render.py
      ```
    - **详见**: `references/feishu-render-paths.md`

32. **Tavily 调用成功不等于搜索结果正确**: 即使 HTTP 200 返回结果数组，结果可能匹配到同名无关实体。
    - **强约束**:
        1. 不基于 SKILL.md 旧记录推断工具状态——以实时实测为准
        2. 怀疑工具不可用时，先用 curl 实测验证
        3. Tavily 搜错实体也会返回 200，卡片 verdict 4 档会显示 SPECULATIVE/DEFAULT
    - **Tavily 实测命令**:
      ```bash
      curl -s --max-time 8 -X POST https://api.tavily.com/search \
        -H "Content-Type: application/json" \
        -d "{\"api_key\":\"$(grep PAVETHERM_TAVILY_KEY ~/.hermes/secrets/pavetherm-sentinel.env | cut -d= -f2)\",\"query\":\"<路名> 沥青 建成\",\"max_results\":2}"
      ```
      200 + results 数组 = 通；401 = key 错；403 = 配额；empty results = 通但未搜到
    - **详见**: `references/tavily-curl-verify.md`

33. **文档自我宣称的修复须可验证**: SKILL.md 中标注"已修复"或"已实现"前必须实际验证代码状态。
    - **核心原则**: 改完代码 → 跑自检命令 → 看到实物 → 才回 SKILL.md 改状态。顺序不可颠倒
    - **文档状态自检 5 问**:
        1. 文档宣称"X 已修复" → `rg -c "<X 关键词>" scripts/` 验证代码存在
        2. 文档宣称"X 通过" → 上次实测命令的 exit code / message_id 仍可查证
        3. 文档写"Pitfall N 已修复" → `cat -n scripts/X.py | sed -n 'A,Bp'` 验证代码位置
        4. 文档写"X 数据" → `python3 -c "..."` 实测数对得上
        5. 文档写"3 条强约束" → 实际是否按 3 条执行
    - **触发条件**: 用户质疑"是不是真的修了 / 跑了 / 调了"时必须触发全盘审计
    - **详见**: `references/skill-md-self-claim-audit.md`

34. **路表温度公式物理特性**: 模型公式 `T_pav = T_air + 0.035×GTI + 颜色吸收项 + 老化 − 0.5×风速` 中各变量贡献不同。
    - **关键**: 风速是唯一降温项，辐射/颜色/老化均为升温项
    - **含义**: "路表峰值日" ≠ "气温峰值日"，两者可能错位 1-2 天（路表跟随辐射，气温跟随大气环流）
    - **诊断方法**: 看到 Δ 差值反常（气温低但路表高或反之）时，先查当日 5 个变量的实际值，逐项对账找出主导项
    - **反常场景**: 高温低路表 / 异常高（同条件差 10°C+） / 异常低 / 模型超 65°C
    - **卡片优化**: 14 天表建议增加风速列以便自查 Δ 反常
    - **详见**: `references/model-formula-physics-explained.md`

35. **Tavily 兜底逻辑须保留搜索证据**: enrich 内部存在多次 Tavily 调用（主关键词 + 兜底关键词），失败时应保留证据而非直接跳到 DEFAULT。
    - **当前问题**: enrich 内部搜"大修时间"失败 → 兜底搜"建成年份" → 结果被错判无关 → 直接走 DEFAULT 5 年，丢失兜底搜索的真实数据
    - **改进方向**: enrich 应记录每次 Tavily 调用的 URL、score、来源标题，失败时保留证据而非直接跳档
    - **优先级链**: 第 1 次 Tavily（路面/施工语义）→ 第 2 次 Tavily（建成年份）→ 用户手填 → DEFAULT 5 年；每次失败保留证据
    - **手填优先**: 手填字段永远覆盖自动推断（Pitfall 22 守卫已实现）

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

- ✅ 端到端 demo 跑通：成渝立交 1 个真实监测点
- ✅ 6 个成都点位批量查询通过（成渝立交/春熙路/宽窄巷子/武侯祠/刃具立交/双流机场高速）
- ✅ 飞书真卡片推送通过 (header 颜色 + markdown 主体 + hr + note)
- ✅ 飞书 markdown 降级推送通过
- ✅ **V2 清新版卡片推送通过**：`render_multi_point_card_v2()` + `push_batch(..., version="v2")` 默认
- ✅ **未来 X 天生成卡片能力**：`--days` / `--from-date` 单点+批量+console 三路径
- ✅ **数据一致性自检测试套件**：`tests/test_query_consistency.py`（8 个测试类，~30 个用例）— `python3 tests/test_query_consistency.py` 独立运行，或 `pytest tests/test_query_consistency.py -v` 接 CI。强约束：改 query/peak/card 任何代码前必跑
- ⚠️ 路面颜色全部 unknown 时模型置信度 LOW
- ⚠️ 飞书多维表格云端同步未启用
- ⚠️ Cron 定时巡查未接
- ⚠️ Dashboard 未生成
- ⚠️ Skill 还没完成对外可发布封装，见"Distribution Packaging (pending)"
- ✅ Bug 4（字段映射 `?` 显示）已修
- ✅ Bug 5-6（query/batch --send-feishu 参数错位）已修
- ✅ 验证脚本 `scripts/verify_push_no_questionmark.py`，改 feishu_push.py 前必跑
- ✅ **温度单位走 config (Pitfall 18)** — output_helper.py 集中，5 文件全替换
- ✅ **14 天表加气温/Δ列 (Pitfall 19+20)** — alert.py + feishu_push.py 双表头同步
- ✅ **时间窗口 > 14 天硬限制 (Pitfall 21)** — 超过 14 天的查询必须告知无法预测
- ⚠️ **enrich 覆盖 CONFIRMED (Pitfall 22)**：已修复 — `enrich_point.py:230-252` 顶部守卫
- ⚠️ **enrich apply 写 6 字段 (Pitfall 25)**：部分修复 — age 字段组已写，color/type 字段组仍可能 None。必跑自检命令
- ✅ **JTG F40 颜色分段 (Pitfall 26)**：已修正 — `≤5 black / ≤10 gray / >10 light_gray`
- ✅ **数据真实度铁律 (Pitfall 24)**：已实现 — 卡片 verdict 4 档 + Tavily 默认 5 年兜底
- ⚠️ **Pitfall 31 渲染器分家**：改字段必跑全 4 渲染器 grep 同步
- ✅ **Pitfall 32 Tavily 实测**：已校正 — Tavily 实测能通，之前 SKILL.md 写的"401 凭证过期"已过期
- ⚠️ **Pitfall 33 文档自查**：改完代码必跑自检命令看到实物再回 SKILL.md 改状态

## 相关 skill

- `weather`：通用天气拉取，本 skill 在它基础上加了路表建模
- `maps`：通用地图查询，本 skill 的 geocode 走的是更专业的国内 POI 路径
- `feishu-lark-cli`：飞书消息推送，本 skill 卡片 JSON 可直接喂给它
- `stock-monitor`：架构同源（监测点+阈值+预警），可参考其 cron 注册与告警去重模式
- `rigor-discipline`：出方案/写 todo/大改前必读

## 进一步阅读(本 skill 的 references/)

- `references/api-reference.md` — Open-Meteo / 高德 字段参考、容器内网络可达性实测表、字段命名不一致陷阱
- `references/model-calibration.md` — SHRP/LTPP 参数选择依据、已知偏差方向、用户实测数据进来后的 3 级校准路线图
- `references/feishu-card-schema.md` — Interactive Card v1 写法规范、text_tag 单引号陷阱、lark-cli `--content` JSON 嵌套踩坑 + 降级路径
- `references/feishu-push-bugs.md` — 推送链路 4 个 bug (Bug 1-3 临时修 + Bug 4 字段映射) + 完整修法 + 验证脚本路径
- `references/card-layout-architecture.md` — V1/V2 双版本排版架构，V2 设计原则 (分块+树形+双等级)，字符预算表
- `references/enrichment-pattern.md` — 监测点元数据 4 源补全模式 (OSM/高德/Tavily/JTG F40)，置信度标签设计
- `references/time-window-feature.md` — `--days` / `--from-date` 时间窗口能力，字段截取逻辑，后续 TODO
- `references/user-visible-data-consistency-test.md` — 用户看到的卡片/UI 字段 == 底层计算值的测试模式 (3 步流程 + 真实案例 + 5 个踩坑)，对应 `tests/test_query_consistency.py`
- `references/redistribution-secrets-pattern.md` — Skill 凭证外部化 + 可复用封装模式 (4 步落地法 / 文件位置约定 / 旧代码迁移 / 零回归 4 件验证 / 11 项 Checklist)
- `references/bugs-discovered-2026-06-30.md` — 实战发现的 4 个真 bug：(1) 高德反查"康定东大街"→错配上海康定东路 (2) feishu_push.py env 加载不稳 (3) add 点位不验证坐标合理性 (4) README/脚本含真 key 泄漏
- `references/temperature-display-and-card-content.md` — 温度单位统一走 config (output_helper.py 模式) + 5 文件完整链路 + 14 天表加气温/Δ差值列 + 卡片类型内容对齐 + 30 天不可预测硬限制 + Pitfall 18-21
- `references/data-verdict-display.md` — 数据真实度铁律：Tavily 必走 + 搜不到默认 5 年 + 卡片 4 档 verdict (✓真实/⚠️估计/❓默认/?未知) + CONFIRMED 守卫实现 + apply 写 6 字段 + JTG F40 颜色分段修正 + Pitfall 22-26
- `references/feishu-render-paths.md` — 4 个渲染器分家 + 字段流图 + 全链路同步 grep 命令 (Pitfall 31)
- `references/tavily-curl-verify.md` — Tavily 1 行 curl 实测命令 + 常见响应码解读 + "调了 ≠ 调对了" 判别 (Pitfall 32)
- `references/skill-md-self-claim-audit.md` — SKILL.md 自查 5 问 + 翻车案例库 (Pitfall 33)
- `references/model-formula-physics-explained.md` — 路表温度公式物理解释 (Pitfall 34) — 5 个变量各自贡献 / 4 个反常场景诊断表 / 4 类回答模板 / 卡片改进 TODO

## 起步模板

- `templates/monitoring_points.template.yaml` — 包含完整字段注释、字段填写指引、cron 任务示例。复制后修改即可。
