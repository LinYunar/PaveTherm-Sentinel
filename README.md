# PaveTherm Sentinel

> 🌡️ 沥青路面高温监测与预警 · Open-Meteo + 路表温度模型 + 飞书卡片推送

![Version](https://img.shields.io/badge/version-v0.2.0-blue)
![Last Updated](https://img.shields.io/badge/last_updated-2026--06--30-green)
![Data Safety](https://img.shields.io/badge/data--safety-.gitignore--protected-orange)

全球任意坐标的沥青路面监测点管理 + 未来 14 天路表温度预测 + 超阈值高温预警。

## 📋 版本更新

| 版本 | 日期 | 主题 | 详情 |
|---|---|---|---|
| **v0.2.0** | 2026-06-30 | 数据一致性 + 渲染修复 + 35 条 Pitfall 体系化 | [CHANGELOG](CHANGELOG.md#v020---2026-06-30--数据一致性--渲染修复--35-条-pitfall-体系化) |
| v0.1.1 | 2026-06-30 | 安全重建 + 性能优化 + Bug 修复 | [CHANGELOG](CHANGELOG.md#v011---2026-06-30--安全重建--性能优化--bug-修复) |
| v0.1.0 | 2026-06-30 | ~~已废弃~~ 数据泄露, 删库重建 | [CHANGELOG](CHANGELOG.md#v010---2026-06-30-历史--已废弃) |

### v0.2.0 主要修复 (用户反馈驱动)

1. **14 天表加 "路表峰值 / 当天气温 / Δ差值" 三列** — 用户问"气温低路表高"无法自查
2. **删 °F 双单位** — config `prefer_both=false` 没生效, 4 渲染器 hardcode
3. **feishu_push.py 渲染器分家陷阱修复** — 改 alert.py 没用, 必须同步改 4 个渲染器
4. **.gitignore 数据保护升级** — monitoring_points.yaml.bak-* + user-real-points.yaml 加入 ignore
5. **SKILL.md 补全 Pitfall 18-35** (18 条新) — 之前 v0.1.1 只到 17, 18-35 从没 push

详见 [CHANGELOG.md](CHANGELOG.md) 完整 v0.2.0 章节.

---

## 🚀 快速开始

```bash
# 1. 安装依赖 (Python 3.11+)
pip install requests pyyaml

# 2. 首次安装 - 一键引导填 4 个 key
python3 scripts/install.py

# 3. 加监测点
python3 scripts/monitor.py add "成都三环路-成渝立交" --pavement-color black --age 5

# 4. 查单个点 (推送飞书)
python3 scripts/monitor.py query cd_chengyulukou_001 --days 7 --send-feishu

# 5. 批量查询所有成都点
python3 scripts/monitor.py batch --filter city=成都 --export feishu
```

**完整使用文档**: [README_INSTALL.md](README_INSTALL.md) (5 章节, 含配置详解 / 故障排除)

---

## 📦 核心功能

| 功能 | 说明 |
|---|---|
| **多源坐标** | 精确坐标 (lat/lon) 或道路名 (自动高德反查) |
| **实时天气** | Open-Meteo 免费 API, 含 GTI 太阳辐射 / 气温 / 风向 |
| **路表模型** | SHRP/LTPP 算法 + 路面颜色吸收率 + 老化修正 |
| **14 天预测** | 每天路表温度峰值 (11:00-16:00 窗口) |
| **三级预警** | 🟡 黄 (55°C) / 🟠 橙 (60°C) / 🔴 红 (65°C), 可逐点覆盖 |
| **飞书卡片** | V2 多列布局, 单点/批量两种, 直推目标 chat |
| **可视大屏** | 自带 HTML 仪表盘 (`dashboards/index.html`) |

---

## 🔐 凭证安全 (重要)

**本 skill 不打包任何用户 key**。你需要自己提供 4 项配置:

| Key | 用途 | 获取 |
|---|---|---|
| `PAVETHERM_AMAP_KEY` | 高德反查坐标 (可选, 但强烈推荐) | https://lbs.amap.com/dev/key/app |
| `PAVETHERM_TAVILY_KEY` | AI 搜索路面建成/大修年份 (可选) | https://tavily.com/ |
| `PAVETHERM_FEISHU_CHAT_ID` | 飞书推送目标 chat | 飞书开发者后台 |
| `PAVETHERM_LARK_CLI` | lark-cli 路径 | `npm install -g @larksuite/cli` |

安装脚本会把这些写到 `~/.hermes/secrets/pavetherm-sentinel.env` (chmod 600)。

---

## 📍 监测点数据 (本地, 不上传)

**你的 `data/monitoring_points.yaml` 是个人数据** — 包含你的路面/坐标/位置信息。

⚠️ **已被 `.gitignore` 排除，永远不会被 git 跟踪**。

clone 后用 `monitor.py add` 添加自己的监测点；想参考 schema 看 `templates/monitoring_points.template.yaml`。

---

## 🛣️ 适用场景

- 公路养护单位: SMA / AC-13 / AC-16 路面高温期车辙预防
- 城市道路: 桥梁 / 隧道口 / 公交港湾重载路段
- 高速 / 一级公路: 长下坡 + 重车密集段

---

## 📊 数据来源

- **天气**: [Open-Meteo](https://open-meteo.com/) (CC-BY 4.0, 免费, 无 key)
- **坐标反查**: 高德开放平台 (Web API)
- **路面元数据**: Tavily AI 搜索 + 本地 YAML 兜底

---

## 📄 License

MIT License. 详见 [LICENSE](LICENSE).

---

## 🙏 致谢

- Open-Meteo 提供免费天气 API
- SHRP/LTPP 研究提供的路表温度建模方法
- 飞书 / Lark 开放平台