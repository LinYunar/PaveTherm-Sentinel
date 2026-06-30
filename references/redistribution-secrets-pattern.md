# Skill 凭证外部化 + 可复用封装模式

> 来源: 2026-06-30 PaveTherm Sentinel v0.4 → v0.5 封装实战
> 适用: 任何"我有 1 个本地项目, 想封装成可分发 + 凭证安全的 skill"的工作

---

## 1. 问题陈述

**默认状态 (反模式)**:
- `config.yaml` 里直接写明文 API key
- skill 提交/分享/打包时 key 一起跟着走
- 别人 clone 下来用了你的 key, 你的额度被消耗 / 你的服务被滥用
- 你想给别人用, 必须先手动 `sed` 掉所有 key, 极容易漏

**目标状态**:
- skill 代码不含任何 key
- 别人 clone 后跑 1 条命令, 引导他填自己的 key
- key 存在用户主目录, chmod 600, 不污染 skill 目录
- 自带 smoke test, 装完就能验证跑通

---

## 2. 4 步落地法 (实战验证)

### Step 1: 备份真 key (留退路!)

```bash
cp config.yaml config.yaml.user
chmod 600 config.yaml.user
```

**为什么必须先做**: 重构中途改坏时, 1 秒钟能恢复。不备份要 1 小时重申请 key。

### Step 2: 写统一 config_loader.py

**单一职责**: 加载 yaml + 解析 `${env:XXX}` 占位符 + 注入 key + 校验缺失。

最小代码骨架:
```python
import os, sys, yaml
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.yaml"
ENV_PATH = Path.home() / ".hermes" / "secrets" / "<skill-name>.env"

def _load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def _resolve(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        key = v[2:-1] if not v.startswith("${env:") else v[6:-1]
        return os.environ.get(key, v)
    if isinstance(v, dict): return {k: _resolve(x) for k, x in v.items()}
    if isinstance(v, list): return [_resolve(x) for x in v]
    return v

def load_config(strict: bool = False) -> dict:
    _load_env()
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    cfg = _resolve(cfg)
    if strict and "xxx" in str(cfg):  # 简单校验: 还存在 ${...} 占位符 → 缺 key
        print("❌ 配置缺失, 跑 scripts/install.py", file=sys.stderr)
        sys.exit(2)
    return cfg
```

**关键设计**:
- `strict=True` 启动时硬阻塞
- `strict=False` 让脚本能跑通到具体失败点, 给清晰提示
- `setdefault` 不覆盖既有 env, 允许用户用 `export` 临时覆盖

### Step 3: config.yaml 改用占位符

```yaml
# 改前
geocoding:
  amap_key: "YOUR_AMAP_KEY_HERE"   # 真 key, 危险!

# 改后
geocoding:
  amap_key: ${PAVETHERM_AMAP_KEY}                  # 占位符, 加载时替换
```

**⚠️ 坑**: 数值表 (如 `age_aging_correction: {0: 0, 5: 0.5}`) 的 key 类型必须跟 model lookup 保持一致 (int vs str)。改完实测 model 不报错。

### Step 4: 引导脚本 install.py

**两种模式**:
- 交互模式 (新装者): 主动问, 引导填, 给 URL
- `--non-interactive` (CI/有经验用户): 只校验, 不输入

最小骨架:
```python
def _prompt_for_key(key, hint):
    print(f"\n📝 {hint['label']}")
    print(f"   获取: {hint['url']}")
    return input(f"   粘贴 key 后回车 (跳过直接回车): ").strip() or None

def _write_env(env):
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in env.items()))
    os.chmod(ENV_PATH, 0o600)  # chmod 600 是关键!

def _smoke_test():
    """跑一次最小查询, 验证链路"""
    from monitor import query_point
    r = query_point("已有点位_id", days=1)
    return "error" not in r
```

**强约束**:
- chmod 600 不能省 (用户专属, 不能被同机其他用户读)
- 引导脚本要给"获取 key 的官方 URL", 不要替用户申请
- 写完跑一次 smoke test, 立刻反馈成功/失败

---

## 3. 文件位置约定 (Hermes 标准)

```
~/.hermes/secrets/<skill-name>.env      # 用户专属, chmod 600
~/.hermes/skills/<skill-name>/           # skill 代码
  ├── SKILL.md                            # 有"🚀 首次安装"章节
  ├── README_INSTALL.md                   # 5 章节中文详细说明书
  ├── config.yaml                         # 占位符, 无 key
  ├── .gitignore                          # 保护 secrets/ + .user 备份
  └── scripts/
      ├── config_loader.py                 # 统一加载器
      └── install.py                      # 引导脚本
```

**为什么不放在 skill 自己的 secrets/ 目录**:
- skill 目录 git push 时会包含, 容易泄漏
- Hermes `~/.hermes/secrets/` 是标准 plugin 凭证目录, 不会被打包
- 多 skill 可共用同 1 个 env 文件 (虽然不推荐)

---

## 4. .gitignore 必含条目

```gitignore
# 用户备份: 含真 key, 绝对不能 commit
config.yaml.user

# 用户专属 key 文件 (在 ~/.hermes/secrets/, 兜底)
.env
*.key
*.secret
secrets/

# Python 缓存
__pycache__/
*.pyc

# 测试临时
.pytest_cache/
tests/__pycache__/

# 导出文件
exports/
*.tmp

# ❗ 不 ignore config.yaml — 它现在是占位符版, 应被 commit
```

**⚠️ 关键**: 改完 `.gitignore` **不要无脑 ignore `config.yaml`**! 重构后它不含敏感 key, 反而是别人 clone 后"必装"配置。

---

## 5. 旧代码迁移 (不破坏性重构)

**反模式**: 一刀切改所有 `load_config()` 调用方, 容易漏引坏。

**正确路径**: 委托模式 + try/except 兜底:

```python
# 旧 load_config 改成委托给 config_loader
try:
    from config_loader import load_config as _load_config
except ImportError:
    _load_config = None  # 旧代码 standalone 跑还能 fallback

def load_config() -> dict:
    if _load_config is not None:
        return _load_config(strict=False)
    # 兜底: 旧 yaml 加载 (key 仍是 ${...} 占位符, 跑时会失败但不影响测试)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)
```

**应用范围**: 6 个脚本 (alert.py / geocode.py / fetch_weather.py / model.py / enrich_point.py / feishu_push.py) 各自的 `load_config` 全改成这种模式。

---

## 6. 零回归验证 (4 件必跑)

封装完**必须**真跑这 4 个, 不能只 commit:

```bash
# 1. config_loader 自检
python3 scripts/config_loader.py
# 期望: 4 项 key 全部 ✅, 阈值正常列出

# 2. 数据一致性自检 (如果原本就有)
python3 tests/test_query_consistency.py
# 期望: 5 类 case 全过

# 3. install.py --non-interactive (不主动输入,只校验)
python3 scripts/install.py --non-interactive
# 期望: 4 项 key 全在, 提示装好

# 4. 推 1 张真卡片
python3 scripts/monitor.py batch --filter city=xxx --days 7 --export feishu
# 期望: 拿到 message_id, 飞书能收到
```

**真实回归案例** (本次 2026-06-30):
- 改完 Step 3 跑 config_loader, 6/6 点数据一致性测试都过 ✅
- 推 1 张卡片 `om_x100b6b07f73ab0a0c1cc4c204e6169f` 飞书真的收到 ✅
- 4 件都过才算封装完成

---

## 7. 飞书机器人权限 (反复踩坑点)

**封装时必给的 scope** (P0, 不给就 11310 permission denied):
- `im:message` (发送消息, 必需)
- `im:message.group_at_msg` (群消息)
- `im:chat:readonly` (读会话列表)

**chat_id 格式** (P1, 错了 invalid chat ID format):
- 必须以 `oc_` 开头
- 私聊: 在机器人 chat 详情 URL 找
- 群: 拉机器人进群后 URL 找

**installer 里加 pre-flight 校验**:
```python
if not chat_id.startswith("oc_"):
    print(f"❌ PAVETHERM_FEISHU_CHAT_ID 格式错误, 应以 'oc_' 开头, 当前: {chat_id[:8]}")
```

---

## 8. README_INSTALL.md 5 章节模板

```
1. 快速开始         5 分钟安装, 4 步
2. 配置详解         4 项 key 来源 URL + 飞书机器人权限步骤
3. 使用方式         CLI / 自然语言 / 定时 cron
4. 故障排除         4 大类: 配置 / 数据 / 飞书推送 / 测试验证
5. 参考资料         数据源 / 模型公式 / 版本历史 / 文件清单
```

**写 5 章节的好处**:
- 别人搜问题直接定位到对应章节
- 维护时容易补, 知道在哪节加
- 跟其他 skill 说明书风格统一 (看 hermes-agent 那种)

---

## 9. 适用场景判断

**用这个模式** when:
- ✅ skill 用了 1+ 个第三方 API key
- ✅ skill 可能分享给同事/朋友
- ✅ key 消耗真金白银 (按量计费) 或安全敏感 (能写资源)

**不一定要用** when:
- ❌ skill 只用公开 API (无 key), 比如 Open-Meteo, OSM
- ❌ skill 永远不分享 (本地一次性脚本)
- ❌ skill 还没跑通 (先把 demo 做出来再考虑封装)

---

## 10. 复盘 (实战踩过的坑)

1. **patch 工具会静默删除行** (SKILL.md 写 front-matter 时少打一行 `---` 导致 YAML 解析错)
2. **`int key` vs `string key`** 一旦不一致, model 查询会 KeyError, 改时实测跑通
3. **不 import os 就用 os.environ** 导致 NameError, 改 feishu_push.py 时遇到过
4. **.gitignore 无脑 ignore config.yaml** 导致别人 clone 拿不到占位符版 config
5. **没真跑过就声称"封装完成"** 是最危险的, 必须 4 件验证全过

---

## 11. 未来同类工作 Checklist

复用一个 skill 前, copy 这个清单:

- [ ] 备份当前 config.yaml 到 *.user (chmod 600)
- [ ] 写 config_loader.py (含 ${env:...} 解析 + strict 模式)
- [ ] 改 config.yaml 把明文 key → 占位符
- [ ] 改所有 scripts/*.py 的 load_config 委托给 config_loader
- [ ] 写 install.py (交互 + --non-interactive)
- [ ] SKILL.md 顶上加 "🚀 首次安装" 章节
- [ ] 写 README_INSTALL.md 5 章节
- [ ] 改 .gitignore (保护 *.user + secrets/)
- [ ] 跑 4 件验证 (config_loader / 数据自检 / install --non-interactive / 推 1 张)
- [ ] 真实推送拿到 message_id 才算完

