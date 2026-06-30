# Changelog - PaveTherm Sentinel

所有"为什么改"的上下文都在这里，不是只记一行 commit。
时间用 UTC+8 日期。

---

## [v0.1.1] - 2026-06-30 · 安全重建 + 性能优化 + Bug 修复

### 🔥 重大变更 (Breaking-ish)

#### 1. **GitHub 仓库完全重建 (数据安全)**

**触发原因**: v0.1.0 把 `data/monitoring_points.yaml`（含你的 6 个成都 + 1 个康定监测点数据）传到了公开仓库。这些数据包含坐标/路面配置等个人/商业信息。

**这次怎么修**:
- 删除 GitHub 仓库 `LinYunar/PaveTherm-Sentinel` (HTTP 204)
- 本地重置 `.git` 目录，丢弃所有 commit
- 用全新历史重建仓库，tag v0.1.1

**对用户的影响**:
- ❌ 旧 GitHub URL `https://github.com/LinYunar/PaveTherm-Sentinel` 已 404
- ✅ 新仓库（同名同 owner）从空仓库重建，历史干净
- ⚠️ 旧 `v0.1.0` tag 消失（commit hash 不同）

#### 2. **`data/monitoring_points.yaml` 进 `.gitignore`**

**触发原因**: 个人路面监测数据应跟 key 同等级保护。

**改动**:
```gitignore
# .gitignore 新增:
data/monitoring_points.yaml
```

**对 clone 用户的影响**:
- ✅ clone 后是空数据文件（带 schema 注释），用 `monitor.py add` 加自己的点
- ✅ 想看完整字段示例，参考 `templates/monitoring_points.template.yaml`

---

### 🐛 Bug 修复 (4 个实战发现)

#### Bug #1 - 高德地名歧义

**现象**:
```bash
$ python3 scripts/monitor.py add "康定东大街"
✅ 已添加: 坐标 (31.24N, 121.46E)  ← 上海静安区康定东路!
```

**根因**: 高德把"康定东大街"解析为"上海市静安区康定东路"（同名混淆）。

**修复**: 加 `--city` 参数 + 智能拼接：
```bash
$ python3 scripts/monitor.py add "东大街" --city 康定
💡 加入 --city hint, 查询用: 康定市东大街
✅ 已添加: 坐标 (30.05N, 101.96E)  ← 真正的四川甘孜康定
```

**智能拼接规则**:
- 中文 city 末尾无"市/县/区"自动补"市" → `--city 康定` → "康定市"
- 非中文 city 直接空格拼 → `--city Chengdu` → "Chengdu ..."

**回归验证**: 5 个测例全过（康定/成都/宁夏石嘴山/英文 city/city hint 不匹配）

#### Bug #2 - 飞书推送必须手动 source env

**现象**:
```bash
$ unset PAVETHERM_LARK_CLI PAVETHERM_FEISHU_CHAT_ID
$ python3 scripts/monitor.py batch --filter city=成都 --export send-feishu
FileNotFoundError: 'lark-cli'  ← 找不到 lark-cli
```

**根因**: `feishu_push.py` 模块加载时只读 `os.environ`，没从 `~/.hermes/secrets/` 读。

**修复**: `feishu_push.py` 启动时自动 load env：
```python
def _load_secrets_env():
    env_path = Path.home() / ".hermes" / "secrets" / "pavetherm-sentinel.env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        # KEY=VALUE 解析 + setdefault (不覆盖用户 shell 已有值)
```

**回归验证**: 不 source env 也能推飞书（实测 3 次 push 都成功）

#### Bug #3 - 加点位无坐标合理性验证

**现象**: 上一轮加了 2 个错误点位（宁夏石嘴山 / 上海康定东路）都没拦截。

**修复**: 双重验证：
1. **bbox 验证** - 中国陆地范围 lat 18-54, lon 73-135，超出则 exit 3
2. **city hint 验证** - 用户传 `--city` 时，验证高德返回的 display_name 是否含该 city，不含则 exit 4
3. **`--yes` 跳过** - 高级用户可加 `--yes` 跳过 city hint 验证

**回归验证**: monkey-patch 模拟境外坐标 + 模拟 city 不匹配 → 退出码正确

#### Bug #4 - 批量查询速度慢

**现象**: 7 个点 batch 查询约 10 秒（Open-Meteo 串行调用）。

**实测速度**:
| 版本 | 7 点 batch | 加速 |
|---|---|---|
| 旧 (串行) | 9539ms | 1x |
| 新 (8 worker 并行) | 1674ms | **5.7x** |

**修复**: `monitor.py` 用 `concurrent.futures.ThreadPoolExecutor` 并行：
- 阶段 1：geocode 并行（未 geocode 的点）
- 阶段 2：Open-Meteo 并行（所有点）
- 阶段 3：CPU 计算串行（不重要）

---

### ✨ 新增能力

| 命令 | 用途 |
|---|---|
| `monitor.py add "东大街" --city 康定` | 防高德歧义 |
| `monitor.py add "某路" --city 北京 --yes` | 跳过 city hint 确认 |
| `monitor.py batch --filter city=成都 --days 7 --export send-feishu` | 7 点 batch 5.7x 加速 |

---

### 📊 性能基准

```
单点 query:  ~1.7s (Open-Meteo 1130ms 主导)
7 点 batch:   1.7-3.3s (并行后 ~瓶颈在 slowest 单点)
环境启动:    ~50ms (无额外开销)
```

---

### ✅ 回归测试

| 测试 | 结果 |
|---|---|
| `monitor.py add "东大街" --city 康定` | ✅ 加到四川甘孜 (30.05N, 101.96E) |
| `monitor.py batch --filter city=成都 --days 7 --export console` | ✅ 6 个成都点数据全 |
| `monitor.py batch --filter city=成都 --days 5 --export send-feishu` (不 source env) | ✅ 推送成功 |
| bbox 拦截 (模拟境外) | ✅ exit 3 |
| city hint 不匹配 | ✅ exit 4 |
| `monitor.py query pt_ba0d4569 --days 14` (康定) | ✅ 14 天峰值全 |

---

### 📝 文件变更清单

```
新增:
  (无)

修改:
  .gitignore                                     +data/monitoring_points.yaml 强保护
  README.md                                      +"📍 监测点数据" 章节
  CHANGELOG.md                                   (本文)
  data/monitoring_points.yaml                    清空 + schema 注释 (从 8 个点 → 0 个)
  scripts/feishu_push.py                         +_load_secrets_env() 自动 load env
  scripts/monitor.py                             +--city / --yes 参数; query_batch 并行化
  scripts/monitor.py                             +bbox 验证 + city hint 验证

删除:
  .git/                                          重置历史
  data/monitoring_points.yaml.bak-*              备份残留
  references/bugs-discovered-2026-06-30.md       curator 自动生成, 不属正式内容

外部:
  GitHub LinYunar/PaveTherm-Sentinel             删除 (HTTP 204) → 重建
```

---

### 🎓 P0 经验沉淀 (这次踩的坑)

1. **git add 之前必须 final-check** —— `git status` 看哪些文件会被 stage, 别凭印象
2. **个人数据 (.yaml/.json 含坐标/年龄/...) 应该跟 key 同等级保护**
3. **"我以为 .gitignore 已经 ignore 了" ≠ "实际 ignore 了"** —— 跑 `git ls-files` 验证
4. **删仓库重建是终极方案** —— 简单粗暴但 100% 干净。filter-branch 也能用但容易留尾巴
5. **测试加监测点时一定用 `--city`** —— 高德地名歧义很常见（康定东大街 = 上海康定东路）
6. **monitor.py 加完点位必须看 display_name 确认是预期地点** —— bbox + city hint 是兜底

---

## [v0.1.0] - 2026-06-30 (历史) · 已废弃

v0.1.0 仓库已删除。该版本曾包含：
- 完整封装 + key 隔离 + install 引导 + README + CHANGELOG
- ⚠️ **意外泄漏** monitoring_points.yaml 到公开仓库 (含个人数据)

v0.1.1 取代 v0.1.0，commit hash 不同。**请使用 v0.1.1**。