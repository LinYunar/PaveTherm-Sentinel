# PaveTherm Sentinel · 安装与使用说明书

> 🌡️ 沥青路表高温监测预警 skill · v0.5
> 基于 Open-Meteo 天气 + 高德地理编码 + SHRP/LTPP 经验公式，4 级预警 (绿/黄/橙/红) 实时推送飞书。

---

## 目录

1. [快速开始](#1-快速开始) — 5 分钟安装
2. [配置详解](#2-配置详解) — 4 项 key + 飞书机器人权限授权
3. [使用方式](#3-使用方式) — 命令行 / 自然语言 / 定时巡查
4. [故障排除](#4-故障排除) — 常见问题 + 排错流程
5. [参考资料](#5-参考资料) — 数据源、模型公式、版本历史

---

## 1. 快速开始

### 1.1 系统要求

- Linux / macOS (WSL 也行)
- Python 3.9+
- 网络可访问: Open-Meteo + 高德 API + Tavily API + Lark API
- lark-cli 工具 (npm 包, 详见 §2.3)
- 飞书账号 + 自有应用 (或机器人)

### 1.2 一键安装 (5 步)

```bash
# Step 1: 复制本 skill 到 Hermes 目录 (本仓库位置已是标准位置)
ls ~/.hermes/skills/pavetherm-sentinel/

# Step 2: 安装 lark-cli (若已有则跳过)
npm install -g @larksuite/cli
which lark-cli   # 应输出 ~/.npm-global/bin/lark-cli

# Step 3: 跑引导脚本 (按提示填 4 项 key)
python3 ~/.hermes/skills/pavetherm-sentinel/scripts/install.py

# Step 4: 配置自检
python3 ~/.hermes/skills/pavetherm-sentinel/scripts/config_loader.py

# Step 5: 加第 1 个监测点 + 推送
python3 ~/.hermes/skills/pavetherm-sentinel/scripts/monitor.py add "成都天府广场" \
    --pavement-color gray --age 5
python3 ~/.hermes/skills/pavetherm-sentinel/scripts/monitor.py query \
    $(python3 ~/.hermes/skills/pavetherm-sentinel/scripts/monitor.py list | head -1) \
    --days 5 --send-feishu
```

**如果你没有高德/Tavily 账号**：详见 §2 配置详解。
**如果没看到飞书卡片**：详见 §4 故障排除 §4.3。

### 1.3 验证清单

安装完成应满足：

- [x] `config_loader.py` 自检输出 ✅ 全部 4 项 key
- [x] `monitor.py list` 能列出监测点（首次空表正常）
- [x] `monitor.py add` 能新增 + 自动 geocode（需要高德 key）
- [x] `monitor.py batch --export feishu` 能推送卡片到飞书

---

## 2. 配置详解

### 2.1 4 项 key (env 文件位置)

| Key | 必装 | 来源 | 说明 |
|---|---|---|---|
| `PAVETHERM_AMAP_KEY` | ✅ | https://lbs.amap.com/dev/key/app | 高德 Web 服务 API |
| `PAVETHERM_FEISHU_CHAT_ID` | ✅ | https://open.feishu.cn/document/server-docs/im-v1/chat-group/chat-id-introduction | 飞书机器人 chat_id |
| `PAVETHERM_LARK_CLI` | ✅ | `npm install -g @larksuite/cli` | 飞书消息 CLI 路径 |
| `PAVETHERM_TAVILY_KEY` | ⚠️ 可选 | https://tavily.com/ | 查道路建成/大修年份 |

**存储位置**：`~/.hermes/secrets/pavetherm-sentinel.env` (chmod 600，仅当前用户可读)

**文件格式示例**（请用你自己的真实 key 替换 `xxx` 占位符）：

```
PAVETHERM_AMAP_KEY=替换成你的高德key
PAVETHERM_TAVILY_KEY=替换成你的Tavilykey_或留空
PAVETHERM_FEISHU_CHAT_ID=替换成你的飞书chat_id
PAVETHERM_LARK_CLI=/home/yourname/.npm-global/bin/lark-cli
```

⚠️ **绝不要**：
- 提交 `.env` 文件到 git
- 通过 IM / 邮件发送明文 key
- 把 skill 复制给同事时连同 `.env` 一起

### 2.2 高德 key 申请 (3 分钟)

1. 访问 https://lbs.amap.com/dev/key/app
2. 注册/登录 → 控制台 → 应用管理 → 创建新应用
3. 给应用加 Key → 类型选 **「Web 服务 API」**（不是 Web 端 JS API）
4. 提交后立即拿到 key，立即生效
5. 粘贴到 install.py 提示处

**测试 key 是否有效**：
```bash
curl "https://restapi.amap.com/v3/geocode/geo?address=成都天府广场&key=$PAVETHERM_AMAP_KEY&output=JSON"
# 应返回 status:1, location 经纬度
```

### 2.3 飞书 chat_id 申请 (10 分钟)

#### 步骤 1: 创建应用

1. 访问 https://open.feishu.cn/app
2. 企业自建应用 → 创建应用 → 填名称 + 描述
3. 进应用详情页，记下 **App ID** 和 **App Secret**

#### 步骤 2: 配置权限

「权限管理」→ 开通：
- `im:message` (发送消息 - 必需)
- `im:message.group_at_msg` (群消息)
- `im:chat:readonly` (读取会话列表)

#### 步骤 3: 安装 lark-cli 并登录

```bash
# 安装 lark-cli
npm install -g @larksuite/cli
which lark-cli

# 登录 (关联到上一步的应用)
lark-cli login --app-id <你的App_ID> --app-secret <你的App_Secret>
```

#### 步骤 4: 获取 chat_id

- **私聊**：在飞书给机器人发任意消息，看 lark-cli 收到的 chat_id
- **群**：把机器人拉进群 → 群详情 → URL `oc_xxxxxxxx` 那段就是 chat_id
- 或用：
  ```bash
  lark-cli im +chat-list --as bot
  # 找到想要的 chat_id
  ```

#### 步骤 5: 给机器人发首条消息

用 `chat_id` 推送测试：
```bash
lark-cli im +messages-send --as bot --chat-id $CHAT_ID --text "hello from PaveTherm"
```

成功看到 hello 就对了。

### 2.4 Tavily key 申请 (可选, 1 分钟)

1. 访问 https://tavily.com/
2. 注册 → dashboard → API Keys → Copy default
3. 粘贴到 install.py 提示

**没有 Tavily 会怎样**：
- `enrich_point.py` 会跳过自动查建成/大修年份
- 加监测点时路面年龄走默认 5 年中度老化
- skill **仍可正常使用**，只是预测精度稍降

### 2.5 阈值的 4 级 (无需 key, config.yaml 直接改)

```yaml
# config.yaml 默认阈值
alert:
  levels:
    green:  {max: 55, label: "✅ 绿-正常"}
    yellow: {min: 55, max: 60, label: "⚠️ 黄-注意"}
    orange: {min: 60, max: 65, label: "🟠 橙-警戒"}
    red:    {min: 65, label: "🔴 红-危险"}
```

可针对单个监测点单独覆盖：
```bash
python3 scripts/monitor.py add "天府广场" \
    --pavement-color gray --age 5 \
    --yellow 58 --orange 63 --red 68    # 该点更严的阈值
```

---

## 3. 使用方式

### 3.1 命令行 (CLI)

skill 入口是 `scripts/monitor.py`，子命令：

#### 加监测点

```bash
# 精准坐标
python3 scripts/monitor.py add "30.6586,104.0648" \
    --name "成都春熙路" --pavement-color gray --age 5

# 模糊道路名 (自动 geocode, 失败时回退手动输经纬度)
python3 scripts/monitor.py add "成都武侯祠大街" \
    --pavement-color black --age 8 --pavement-type SMA-13

# 单独阈值覆盖
python3 scripts/monitor.py add "成渝高速" \
    --pavement-color gray --age 6 --yellow 58 --orange 63 --red 68
```

#### 查询

```bash
# 单点 (推飞书)
python3 scripts/monitor.py query cd_chengyulukou_001 --days 5 --send-feishu

# 单点 (终端预览, 不推)
python3 scripts/monitor.py query cd_chengyulukou_001 --days 7 --export console

# 单点 (输出 CSV)
python3 scripts/monitor.py query cd_chengyulukou_001 --days 14 --export csv

# 批量
python3 scripts/monitor.py batch --filter city=成都 --export feishu
python3 scripts/monitor.py batch --all --days 7 --export feishu

# 自定义起始日
python3 scripts/monitor.py batch --filter city=成都 \
    --from-date 2026-07-10 --days 5 --export feishu
```

#### 列表 / 删除

```bash
python3 scripts/monitor.py list
python3 scripts/monitor.py remove cd_chengyulukou_001
```

### 3.2 自然语言 (IM 对话)

在飞书/Telegram 等 IM 给 assistant 发：

| 自然语言 | skill 行为 |
|---|---|
| "成都三环路-成渝立交未来 7 天" | 推单点 7 天卡片 |
| "巡查一下" / "今日巡查" | 批量所有点 + 推卡片 |
| "加一个监测点：成都天府广场" | 引导填字段 + 自动 geocode |
| "成都春熙路 14 天 CSV" | 导 CSV 文件 |
| "PaveTherm 配置自检" | 跑 `config_loader.py` 报告状态 |

**当前限制**：触发后**需要 clarification** 确认几个关键字段（哪个点？几天？推哪里？）。后续 cron 自动化可省掉这一步。

### 3.3 定时巡查 (cron)

**手动 cron 示例** (每天 14:00 跑)：

```bash
# 编辑 crontab
crontab -e

# 添加这一行 (每天 14:00 巡查 + 主动推飞书)
0 14 * * * cd ~/.hermes/skills/pavetherm-sentinel && \
    python3 scripts/monitor.py batch --all --days 1 --export feishu >> ~/.hermes/cron.log 2>&1
```

> 注：当前版本**不内置** cron 定时推送；如需「自动巡查 + 只在高温时推」逻辑，请提 issue 或自开发 wrapper。

---

## 4. 故障排除

### 4.1 配置相关

**Q: config_loader.py 报缺 key**

```
❌ PaveTherm Sentinel 配置缺失:
   - geocoding.amap_key
   ...
```

✅ 重跑 `python3 scripts/install.py` 补全。

**Q: env 文件权限不对 (太开放)**

```
chmod 600 ~/.hermes/secrets/pavetherm-sentinel.env
```

**Q: env 文件被误删**

✅ 重新跑 `install.py`，env 文件可重建（会保留现有 key 或引导新填）。

### 4.2 数据相关

**Q: 监测点 geocode 失败 (报错 `amap key 无效` 或 quota 超限)**

- 访问 https://lbs.amap.com/dev/key/app 看 quota
- 免费版每日 5000 次，通常够用
- 检查 key 是否填错（32 位 hex）

**Q: Open-Meteo 拉数据失败**

- 检查 `https://api.open-meteo.com/v1/forecast?latitude=30.65&longitude=104.07&hourly=temperature_2m` 是否能 curl
- 国内访问 Open-Meteo 偶有超时，重试即可

**Q: 路表温度模型和路面实际温度不符**

- LTPP/SHRP 经验公式有 ±3°C 系统误差
- 路面颜色 / 老化系数若不准确会偏
- 可在 `config.yaml` `pavement_model:` 调 `radiation_coefficient`

### 4.3 飞书推送相关

**Q: `monitor.py batch --export feishu` 报 `invalid chat ID format`**

✅ chat_id 必须以 `oc_` 开头。检查 `PAVETHERM_FEISHU_CHAT_ID`。

**Q: 报 `lark-cli not found`**

```bash
which lark-cli
# 若空:
npm install -g @larksuite/cli
export PAVETHERM_LARK_CLI=$(which lark-cli)
echo $PAVETHERM_LARK_CLI >> ~/.bashrc  # 或 .zshrc
```

**Q: 报 `permission denied` (飞书) 或 `unauthorized`**

✅ 检查 §2.3 的权限步骤：
1. `im:message` scope 开没开
2. 机器人进 chat 了没（私聊是直接发，群需要 add）
3. App Secret 是否过期

**Q: 卡片显示 `?` 占位符 (不是真数据)**

✅ 这是已知老 bug，已在 v0.4+ 修复。升级到最新版或跑：
```bash
python3 scripts/config_loader.py    # 应输出 4 项 ✅
python3 tests/test_query_consistency.py    # 数据一致性自检
```

### 4.4 测试 & 验证

```bash
# 数据一致性自检 (30s 跑完)
python3 tests/test_query_consistency.py

# pytest 集成 (如已装)
pip install pytest
pytest tests/ -v
```

---

## 5. 参考资料

### 5.1 数据源

- **Open-Meteo** (天气): https://open-meteo.com/ — 免费, 无需 key, 含 GTI 太阳辐射
- **高德 (Amap)** (geocode): https://lbs.amap.com/ — 国内精准, 免费版每日 5000 次
- **Nominatim** (geocode 兜底): https://nominatim.openstreetmap.org/ — 海外 fallback
- **Tavily** (建成/大修年份): https://tavily.com/ — 可选
- **飞书 IM** (推送目标): https://open.feishu.cn/document/server-docs/im-v1/overview

### 5.2 模型公式 (SHRP/LTPP)

```
T_pavement = T_air + α_rad · GTI + α_color · I + Δ_age − α_wind · wind

其中:
  T_air       气温 (°C)
  α_rad       0.030 (LTPP 实验经验值)
  GTI         Global Tilted Irradiance (W/m², 11-16 点窗口)
  α_color     路面颜色吸收率: black 0.92 / gray 0.80 / light_gray 0.65
  Δ_age       老化系数: 0=0  5=0.5  10=1.0  15=1.2  20=1.0
  α_wind      0.5 (风速冷却经验值)
  wind        10m 风速 (m/s)
```

详见 `references/model-calibration.md` 和 `references/enrichment-pattern.md`。

### 5.3 4 级预警阈值 (默认)

| 等级 | 阈值 (°C) | 应对建议 |
|---|---|---|
| 🟢 绿-正常 | < 55 | 正常巡查 |
| ⚠️ 黄-注意 | 55-60 | 加密巡查, 关注车辙变化 |
| 🟠 橙-警戒 | 60-65 | 洒水降温, 限制重车通行 |
| 🔴 红-危险 | ≥ 65 | 应急预案, 封闭高温时段 |

参考: JTG F40-2017《公路沥青路面施工技术规范》、JTG H20-2007《公路桥梁技术状况评定标准》。

### 5.4 版本历史

- **v0.5** (2026-06-30) - 安全封装: key 移出 skill, install.py + README
- **v0.4** (2026-06-30) - 卡片 V2 排版重整 + 数据一致性自检 + days/N 参数化
- **v0.3** (2026-06) - 飞书卡片 + CSV 导出 + dashboard
- **v0.2** (2026-06) - 单点 + 批量 + 4 级预警
- **v0.1** (2026-06) - 最小端到端 demo

### 5.5 文件清单

```
~/.hermes/skills/pavetherm-sentinel/
├── SKILL.md                   # skill 元数据 + 触发词
├── README_INSTALL.md          # 本说明书 (你正在读的)
├── config.yaml                # 公开参数 (阈值/端点, 无 key)
├── config.yaml.user           # 你之前的明文 key 备份 (chmod 600)
├── .gitignore                 # 保护 secrets/config.yaml.user
├── data/
│   └── monitoring_points.yaml # 监测点数据库
├── scripts/
│   ├── config_loader.py       # ⚠️ 统一配置入口 (key 注入)
│   ├── install.py             # 🚀 一键引导 (新装者跑这个)
│   ├── monitor.py             # 入口 CLI
│   ├── geocode.py             # 坐标解析 (高德 + Nominatim)
│   ├── fetch_weather.py       # Open-Meteo 拉取
│   ├── model.py               # 路表温度建模
│   ├── alert.py               # 4 级预警判定 + 卡片渲染
│   ├── render.py              # markdown 渲染
│   ├── feishu_push.py         # 飞书推送
│   ├── dashboard.py           # HTML dashboard
│   └── enrich_point.py        # 监测点元数据补全 (Tavily/高德 POI)
├── tests/
│   └── test_query_consistency.py  # 数据一致性自检 ⭐
├── references/
│   ├── model-calibration.md
│   ├── api-reference.md
│   ├── feishu-card-schema.md
│   └── enrichment-pattern.md
├── dashboards/
│   ├── index.html
│   └── PaveTherm_Sentinel_Dashboard.html
├── exports/                   # CSV 导出目录
└── templates/
    └── monitoring_points.template.yaml

~/.hermes/secrets/
└── pavetherm-sentinel.env     # 🔒 用户专属 key (chmod 600, 不打包)
```

### 5.6 反馈与贡献

- issue: 请附 `python3 scripts/config_loader.py` 输出 + 完整错误 stack
- 改进建议: 直接 PR 进 references/ 或 scripts/

---

📅 最后更新: 2026-06-30 · 维护: 开发者
