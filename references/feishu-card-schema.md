# 飞书 Interactive Card v1 — 写法规范

## 整体结构

```json
{
  "msg_type": "interactive",
  "card": {
    "config": {
      "wide_screen_mode": true        // 宽屏模式, 数据多时强烈建议开
    },
    "header": {
      "template": "red",              // 颜色 enum: blue/green/yellow/orange/red/grey/purple/wathet
      "title": {
        "tag": "plain_text",
        "content": "🌡️ 标题"
      }
    },
    "elements": [
      // markdown / hr / note / action / column / divider 等
    ]
  }
}
```

## Element 类型速查

| tag | 用途 | 关键字段 |
|---|---|---|
| `markdown` | 富文本正文 | `content`(字符串, 支持 **粗** *斜* `代码` [链接](url)) |
| `hr` | 分隔线 | - |
| `note` | 灰色脚注 | `elements: [{tag: "plain_text", content: "..."}]` |
| `text_tag` | **内联彩色标签**, 必须在 markdown 内使用 | `<text_tag color='red'>红</text_tag>` |

## markdown 内联标签用法(关键)

**不要**把 text_tag 当独立 element 写,而是嵌在 markdown 元素的 content 字符串里:

```json
{
  "tag": "markdown",
  "content": "预警等级: <text_tag color='red'>🔴 红-危险</text_tag>\n建议立即..."
}
```

⚠️ **必须用单引号**,不要用双引号(飞书渲染层会失败)。本 skill 的 alert.py 已自动按单引号生成。

## header.template 颜色映射

```python
LEVEL_TO_TEMPLATE = {
    "green": "green",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
}
```

`template` 字段控制 header 横幅的颜色, 给卡片一个一眼能看到的等级提示。

## 大小限制

- **单卡片 ~8000 字符**, 超过会被截断
- **markdown 内容 ~4000 字符**是舒适区, 超过建议分页
- **emoji 不算字符**,放心用

## 实战模板 — 本 skill 的单点卡片

见 `scripts/alert.py: render_feishu_card()`。

输出示例(精简版, 实际渲染效果见飞书):

```
┌─ 🟢 成都三环路-成渝立交 - 路表温度监测 ─────┐
│                                              │
│ 监测点: 成都三环路-成渝立交                 │
│ 位置: 四川省成都市成华区成渝立交 (...)       │
│ 路面: unknown · None · 老化 未知年          │
│                                              │
│ ────────────────────                         │
│                                              │
│ 📊 当前路表温度                              │
│ • 路表: 45.6°C / 114.1°F                     │
│ • 气温: 24.9°C / 76.8°F                      │
│ • 太阳辐射: 500 W/m²                         │
│ • 模型置信度: LOW                            │
│                                              │
│ ────────────────────                         │
│                                              │
│ ⚠️ 预警等级: <green>✅ 绿-正常</green>       │
│ 路表温度正常, 无需采取措施                   │
│                                              │
│ ────────────────────                         │
│                                              │
│ 📅 未来 14 天路表温度峰值                    │
│ 2026-06-30 🟢 44.3°C (峰值 16:00)            │
│ 2026-07-01 🟢 52.2°C (峰值 16:00)            │
│ ...                                          │
│                                              │
│ 🔥 高风险日 (橙/红预警):                    │
│   • 2026-07-13: 75.3°C / 167.5°F             │
│                                              │
│ ────────────────────                         │
│                                              │
│ 🕐 数据拉取: 2026-06-30T09:40:39             │
│ 🧮 模型: SHRP/LTPP v0.1 (参数偏保守)        │
│                                              │
│ PaveTherm Sentinel · 数据源: Open-Meteo     │
└──────────────────────────────────────────────┘
```

## 推送路径

飞书消息推送有 3 条路, **按优先级**:

### A. 自定义机器人 Webhook(最快)
```
POST https://open.feishu.cn/open-apis/bot/v2/hook/{token}
Content-Type: application/json
Body: <本 skill 输出的卡片 JSON>
```
- 优点: 0 鉴权复杂度, 5 分钟接入
- 缺点: 不能 @人, 不能回复, 群 webhook 需手动加机器人

### B. 飞书应用 API(用 tenant_access_token)
```
POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id
Header: Authorization: Bearer t-xxx
Body: {
  "receive_id": "oc_xxx",
  "msg_type": "interactive",
  "content": json.dumps(card)  // 卡片 JSON 序列化为字符串
}
```
- 优点: 可 @人, 可定向, 可回复
- 缺点: 需先调 /auth/v3/tenant_access_token/internal 拿 token(2h 过期)

### C. lark-cli 封装(本 skill 默认走这条)
```python
# lark-cli 的 +messages-send 封装了上面的 (B)
LARK_CLI = os.environ.get("PAVETHERM_LARK_CLI", "lark-cli")
subprocess.run([
    LARK_CLI, "im", "+messages-send",
    "--as", "bot",
    "--chat-id", "oc_xxx",
    "--msg-type", "interactive",
    "--content", json.dumps(card_inner, ensure_ascii=False),
])
```
- 优点: 走 bot 模式无需 user 登录, 适合 cron 后台跑
- 缺点: --content JSON 嵌套有坑 (见下)

**本 skill 当前状态**: scripts/feishu_push.py 已封装 B/C 两条路, 推卡片 + 推 markdown (降级)。

## 常见踩坑

1. ❌ `template` 写 "GREEN" 大写 → 渲染失败, **必须小写**
2. ❌ `text_tag color="red"` 用双引号 → 部分飞书版本渲染失败, **用单引号**
3. ❌ 卡片 JSON 里再嵌套 `msg_type` → 顶层有就够了, 内部不要再包
4. ❌ emoji 太多卡死渲染 → 控制 ≤20 个 emoji/卡片
5. ❌ markdown 不写换行 → 长字符串挤在一起, **用 \n 显式换行**
6. ❌ 时间用 UTC ISO → 用户看不懂, **用 timezone=Asia/Shanghai 的本地时间**

---

## ⚠️ lark-cli `--content` 卡片 JSON 嵌套陷阱 (2026-06-30 实战踩坑)

**症状**: 卡片 JSON 看起来完全合法, 但推送时飞书返回 `200621 parse card json err`。

**坑 1 (最容易踩)**: lark-cli 的 `--content` 参数**自己会再做一次 json.dumps**。

```bash
# 你的 python 脚本:
content_str = json.dumps(card_dict)  # 第一次 stringify
# lark-cli 收到 content_str (已是字符串), 又会把它当 dict 再 stringify 一次进 body.content
# 飞书收到的 body.content 是双重嵌套 → 200621 parse card json err
```

**坑 2**: 不要自己加 `card` 包装

```python
# ❌ 错误: 我以为 content 字段需要 {"card": {...}} 结构
payload = {"card": card_inner}
content_str = json.dumps(payload)  # 飞书解析时找不到顶层 header/elements, 200621

# ✅ 正确: content 字符串就是卡片 dict 自己
content_str = json.dumps(card_inner)  # {"header":..., "elements":[...]}
# lark-cli 再 stringify 一次进 body.content, 飞书收到正确结构
```

**坑 3**: 用 dry-run 看真实请求体再调, 别瞎试

```bash
# 必做第一步: 看 lark-cli 怎么构造请求, 避免凭直觉猜
lark-cli im +messages-send --dry-run --as bot --chat-id "oc_xxx" \
    --msg-type interactive --content '{"header":...,"elements":[...]}'
# 输出 body.content 字段就是飞书真正收到的字符串, 验证它是不是你期望的格式
# 我用这个方法才在 1 步内定位到"我不该加 card 包装"
```

**坑 4**: `as` 是 Python 关键字, 别当参数名

```python
# ❌ Python 语法错
def push_card(chat_id, card, as="bot"): pass
# SyntaxError: invalid syntax

# ✅ 改用 identity
def push_card(chat_id, card, identity="bot"): pass
```

**坑 5**: subprocess 不接受 dict 参数, 必须 stringify

```python
# ❌ subprocess.run 收到 list 里含 dict 会崩
subprocess.run([lark_cli, "im", "+messages-send", "--content", card_dict])

# ✅ 传字符串
subprocess.run([lark_cli, "im", "+messages-send", "--content", json.dumps(card_dict)])
```

**坑 6**: 历史遗留的 import 错误会引爆

PaveTherm 历史版本 monitor.py 有 `from feishu_push import send as feishu_send` 的旧 import,
新版本 feishu_push.py 没有 `send` 函数, 会 ImportError 把整个 skill 锁死。
**所有历史 import 必须用 try/except 包**, 缺失不致命, 后面代码照样能跑。

**实战验证 (PaveTherm, 2026-06-30)**:
- 单点真卡片: `om_x100b6b063365bca0c07bde1af127ed3` ✅
- 批量真卡片: `om_x100b6b0630545494c17afb3ec0f6201` ✅
- 修复耗时: 4 轮试错 → 1 行修复 (改 `json.dumps({"card":card_inner})` 为 `json.dumps(card_inner)`)
- 教训: 第一次改前必用 `--dry-run` 看真实请求体, 能省 3 轮试错

**推送失败的降级路径**:

如果 interactive 卡片怎么都推不出去 (200621 顽固), **降级到 markdown post 类型**,
lark-cli 的 `--markdown` 参数 100% 兼容, 视觉上接近卡片 (有颜色 + 表格 + emoji):

```python
# 降级推送 (飞书 post 类型)
r = _run_lark([
    LARK_CLI, "im", "+messages-send",
    "--as", identity,
    "--chat-id", chat_id,
    "--markdown", markdown_text,
])
# 实测: 推 2 条 markdown 全部成功
```

**scripts/feishu_push.py 已实现**:
- `push_card()` 推真卡片 (interactive)
- `push_markdown()` 推 markdown post 降级
- `push_point()` / `push_batch()` 默认真卡片, 加 `--markdown` 切降级
- `push_point(use_card=False)` 程序化切降级

## 飞书文档参考

- 卡片 JSON 构建器: https://open.larksuite.com/document/uAjLw4CM/uYjL24iN/intro
- 消息发送: https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create
- 内嵌 Webhook: https://open.larksuite.com/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
- Lark Agent 集成能力: https://open.larksuite.com/document/mcp_open_tools/overview-of-lark-agent-integration-capabilities
