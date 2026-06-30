# 飞书推送链路 4 个已知 bug (2026-06-30 累计)

## ⚠️ Bug 4 (2026-06-30 新增, **已修**) — 字段映射遗漏导致 `?` 显示

**这是最致命的 bug**, 因为它**不会报任何错**, 推送 `ok=true`, 但卡片内容全 `?`, 用户看到才反馈。

### 触发场景
`feishu_push.py push_batch(chat_id, results, use_card=True)` 任何调用

### 症状
卡片每个监测点 column 显示:
```
**当前** (??:??): 路表 41.5°C · 气温 ?°C
**14 天峰值** (? @ ??:??): 路表 67.0°C · 气温 ?°C
```

时间、气温、14 天峰值日期/时刻/气温 — **全是 `?` 或 `??:??`**。

### 根因
`scripts/feishu_push.py` 第 238-243 行 (修复前) 字典推导只塞了 4 个字段:
```python
pts_alerts = [{
    "point": r["point"],
    "current_temp": r["current_temp"],
    "alert_summary": r["alert_summary"],
    "peak_14d_max": r.get("peak_14d_max", r["current_temp"]),
} for r in results]
```

但 `scripts/alert.py` `render_multi_point_card` 第 357-361 行需要 5 个**额外**字段:
```python
cur_air = x.get("current_air_temp", "?")
cur_time_short = (x.get("current_time") or "")[11:16] if x.get("current_time") else "??:??"
peak_max_date = x.get("peak_14d_max_date", "?")
peak_max_time = x.get("peak_14d_max_time", "??:??")
peak_max_air = x.get("peak_14d_max_air", "?")
```

拿不到字段就 fallback 到 `?` / `??:??`。

### 修复 (2026-06-30 已应用)
`feishu_push.py:238-243` 补全 5 个字段:
```python
pts_alerts = [{
    "point": r["point"],
    "current_temp": r["current_temp"],
    "current_air_temp": r.get("current_air_temp"),
    "current_time": r.get("current_time"),
    "alert_summary": r["alert_summary"],
    "peak_14d_max": r.get("peak_14d_max", r["current_temp"]),
    "peak_14d_max_date": r.get("peak_14d_max_date"),
    "peak_14d_max_time": r.get("peak_14d_max_time"),
    "peak_14d_max_air": r.get("peak_14d_max_air"),
} for r in results]
```

### 修复后验证 (必须)
```bash
# Dry-run 验证, 不推飞书
python3 scripts/verify_push_no_questionmark.py

# 期望输出: ✅ 卡片无 `?` 字段 (Bug 4 已修)
# 退出码 0

# 实际推 1 张验证卡片
python3 scripts/verify_push_no_questionmark.py --send-feishu --chat-id "oc_xxx"
```

### 防御约束 (写给未来 session)
- **改 feishu_push.py 任何字段映射前, 必跑 `verify_push_no_questionmark.py`**
- **改 alert.py `render_multi_point_card` 后, 必跑同一个脚本**
- **新 session 第一件事**: 跑这个脚本确认当前代码状态无 `?`
- **`ok=true` 不代表成功** — 必须 grep 卡片内容确认字段映射完整

### 教训
- 静默 fallback 是反模式: `x.get("current_air_temp", "?")` 用 `"?"` 当默认值的写法让 bug **完全静默**, 没有 warn, 没有 raise, 没有 log。**改进**: 用 `None` 当默认值, 渲染时 `if x is None: warnings.warn(...)`
- 接口契约要明示: `render_multi_point_card` 的输入字段在 docstring 应该穷举, 而不是依赖 `x.get(key, default)` 隐式兜底
- 验证脚本要在新功能/重构后**第一件事**跑, 不是"想起来再跑"

---

## Bug 1-3 (2026-06-30 旧) — TypeError / NoneType

## 触发场景

```bash
python3 scripts/monitor.py batch --filter city=成都 --export send-feishu
```

预期: 推送 6 个成都监测点的批量卡片到当前飞书对话
实际: 报 `TypeError: 'NoneType' object is not callable` 然后退出

## Bug 1: `feishu_send` 名字不存在 (已临时修复)

**位置**: `scripts/monitor.py` line 46
**错误代码**:
```python
try:
    from feishu_push import send as feishu_send
except (ImportError, ModuleNotFoundError, AttributeError) as e:
    feishu_send = None
```

**根因**: `scripts/feishu_push.py` 里**没有** `send()` 函数, 只有:
- `push_markdown(chat_id, markdown, identity)`
- `push_text(chat_id, text, identity)`
- `push_card(chat_id, card, identity)`
- `push_point(chat_id, point_id, identity, use_card)`
- `push_batch(chat_id, results, identity, use_card)`

所以 `from feishu_push import send as feishu_send` 走 `AttributeError` 分支, `feishu_send = None`。

**临时修复** (已应用 2026-06-30): `feishu_push.py` line 92 加一行:
```python
LEVEL_ICONS = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}

# 兼容旧名: monitor.py 旧版用 `from feishu_push import send as feishu_send`
send = push_markdown
```

效果: 旧 `from feishu_push import send as feishu_send` 不再报 ImportError / AttributeError, `feishu_send` 不再是 None。

## Bug 2: `feishu_send(md, "markdown")` 参数错位 (未修)

**位置**: `scripts/monitor.py` line 335
**错误代码**:
```python
push_result = feishu_send(md, "markdown")
```

**根因**: `push_markdown` 签名是 `(chat_id, markdown, identity="bot")`, 第 1 参是 chat_id。monitor.py 把 markdown 字符串当 chat_id 传, "markdown" 字符串被当 identity。

**正确调用** (3 种修法):

**修法 A (推荐)**: monitor.py 加环境变量兜底, 改 `feishu_send` 调用
```python
# monitor.py line 333 附近
import os
chat_id = os.environ.get("FEISHU_CHAT_ID")
if not chat_id:
    print("❌ 推送需要 FEISHU_CHAT_ID 环境变量 (或传 --chat-id 参数)", file=sys.stderr)
    sys.exit(2)
push_result = push_markdown(chat_id, md)  # 直接用真函数
```

**修法 B (CLI 友好)**: monitor.py 加 `--chat-id` 参数
```python
# main() 里 sub parser
p_b.add_argument("--chat-id", help="飞书 chat_id (省略则用 FEISHU_CHAT_ID 环境变量)")
```

**修法 C (最小改动)**: feishu_push.py 加 chat_id 自动检测
```python
def send(markdown: str, identity: str = "bot") -> dict:
    """兼容 monitor.py 旧调用, chat_id 从环境变量读"""
    chat_id = os.environ.get("FEISHU_CHAT_ID")
    if not chat_id:
        return {"ok": False, "error": "FEISHU_CHAT_ID not set"}
    return push_markdown(chat_id, markdown, identity)
```

**当前临时别名** (`send = push_markdown`) 等价于"修法 C 的反例", 因为它把 `feishu_send(md, "markdown")` 路由到 `push_markdown(md, "markdown")` —— 把 md 当 chat_id, 仍然错。

## Bug 3: `render_batch_markdown` 可能不存在 (未验证)

**位置**: `scripts/monitor.py` line 41
**错误代码**:
```python
try:
    from render import render_single_point_markdown, render_batch_markdown
except (ImportError, ModuleNotFoundError, AttributeError) as e:
    render_single_point_markdown = None
    render_batch_markdown = None
```

**风险**: 如果 `render.py` 没有 `render_batch_markdown` 函数, `monitor.py batch --export send-feishu` 走到 `md = render_batch_markdown(results)` 时 `render_batch_markdown = None` → 同样 `TypeError: 'NoneType' object is not callable`。

**验证步骤**:
```bash
python3 -c "from scripts.render import render_batch_markdown; print('exists:', render_batch_markdown)"
```

**修法**: 如果不存在, 复制 `feishu_push.py` 里的 `render_batch_markdown` 到 `render.py` (因为 feishu_push.py 里有实现, 实际是 monitor.py 导入路径错了)。

## 完整修复路径 (3 步, 推荐顺序)

1. **验证 Bug 3** (5 秒): 跑上面验证命令, 确认 `render_batch_markdown` 是否真存在
2. **修 Bug 2** (5 分钟): 用修法 A 加 `FEISHU_CHAT_ID` 环境变量兜底, 改 monitor.py 用真 `push_markdown`
3. **保留 Bug 1 临时别名** (不动): 一行兼容无害

## 验证脚本 (完整端到端)

```bash
# Step 1: 验证 Bug 3
cd ~/.hermes/skills/pavetherm-sentinel
python3 -c "
import sys
sys.path.insert(0, 'scripts')
from render import render_batch_markdown
print('render_batch_markdown exists:', render_batch_markdown is not None)
" 2>&1 | tail -5

# Step 2: 设置 FEISHU_CHAT_ID (从飞书群 URL 拿 oc_xxx)
export FEISHU_CHAT_ID="oc_你的群ID"

# Step 3: 修复后 (Bug 2 修法 A 应用) 重跑
python3 scripts/monitor.py batch --filter city=成都 --export send-feishu 2>&1 | tail -5
# 预期: ✅ 已推送到飞书: message_id=om_xxx
```

## 教训 (写给未来 session)

- **接口名不一致是定时炸弹**: `push_markdown` / `send` / `feishu_send` 3 个名字指同一件事 = 迟早出错。统一成 `push_*` 前缀
- **签名要严格**: `(chat_id, content)` 顺序不可换, 别用 `("markdown", md)` 这种"看起来对"的调用
- **环境变量兜底要早加**: CLI 工具频繁用 `FEISHU_*` 是行业惯例, 一开始就该 `--chat-id` 参数 + 环境变量双兜底
- **import 失败兜底要告警**: 上面 try/except 把 ImportError / AttributeError 静默成 `= None`, 不打印任何警告, 调试时不知道哪里失败。**改进**: 改成 print warning
- **本 session 反思**: 早上 session 修了 Bug 1-3 临时别名, 跑了端到端推**返回成功**就以为完成, 但**没验证推送内容**。**当晚 user 反馈 "为什么是问号?"** 才暴露 Bug 4。**正确做法**: 任何推送修复后, **必跑 `verify_push_no_questionmark.py`** + 实际推 1 张验证卡片, 然后才交付

## 跨 session 教训 (2026-06-30 → 当晚)

- **fallback 默认值会掩盖字段映射 bug** (Bug 4 根源): `x.get(key, "?")` 把"缺字段"和"值是问号"混淆。**改进**: 用 `None` 默认值 + 渲染时显式处理
- **静默成功比显式失败更危险** (Bug 4 + 早上 session): 推送 `ok=true` ≠ 推送内容正确。**改进**: 推送后必读回响应内容 grep 关键字段
- **skill 复用必须做验证脚本**: 一个 skill 有 4 个 bug, 1 个临时修 3 个未修, 加 1 个验证脚本 (`verify_push_no_questionmark.py`) 是最低成本防御
- **规则化推送流程**: 任何飞书推送修复 → 跑 `verify_push_no_questionmark.py` → 实际推 1 张 → 用户确认 → 才视为完成
