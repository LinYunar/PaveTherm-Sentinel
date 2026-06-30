# 时间窗口能力 (`--days` / `--from-date`) — 2026-06-30 增

## 能力定位

让用户能问"**未来 X 天**"或"**从 Y 日期起未来 N 天**"——单点/批量/Dashboard 都能用。

## 命令形式

```bash
# 单点 - 未来 N 天 (1-14)
python3 scripts/monitor.py query <point_id> --days N --export {feishu|console|csv}
python3 scripts/monitor.py query <point_id> --days N --send-feishu          # 推飞书

# 单点 - 自定义起始日
python3 scripts/monitor.py query <point_id> --from-date 2026-07-10 --days 3

# 批量 - 影响卡片峰值字段
python3 scripts/monitor.py batch --filter city=成都 --days 5 --export feishu
python3 scripts/monitor.py batch --filter city=成都 --from-date 2026-07-10 --days 3

# 批量 - 不指定 days 默认 14
python3 scripts/monitor.py batch --filter city=成都 --export feishu
```

## 关键实现细节

### `query_point(point_id, days=None, from_date=None)` 返回结构

| 字段 | 用途 |
|---|---|
| `daily_peaks` | **全 14 天**保留 (API 兼容) |
| `daily_peaks_sliced` | 截取后 (卡片渲染用) |
| `slice_window` | `{"days": N, "from_date": "YYYY-MM-DD", "count": M}` |

### `query_batch(filter_city, point_ids, days=None, from_date=None)` 返回结构

| 字段 | 用途 |
|---|---|
| `daily_peaks` | 全 14 天 |
| `daily_peaks_sliced` | 截取后 |
| `peak_14d_max` 等 | **窗口内**最高 (不再是全 14 天) |
| `slice_window` | 窗口元数据 |

### 窗口逻辑

```python
if from_date:
    start_idx = next(i for i, pk in enumerate(peaks) if pk["date"] >= from_date)
else:
    start_idx = 0
if days is not None:
    sliced_peaks = peaks[start_idx:start_idx + days]
elif from_date:
    sliced_peaks = peaks[start_idx:]
else:
    sliced_peaks = peaks
```

**降级**: `from_date` 找不到 (`>=` 全部不满足) → `start_idx = 0` (回退到今天起)

## 卡片标题自适应 (alert.py render_feishu_card)

```python
if len(daily_peaks) >= 14:
    title = "**📅 未来 14 天路表温度峰值** (按日 11:00-16:00 窗口)"
else:
    first = daily_peaks[0]["date"]
    last = daily_peaks[-1]["date"]
    title = f"**📅 未来 {len(daily_peaks)} 天路表温度峰值** ({first} 至 {last}, ...)"
```

**单点卡片**: 7 天表 5 行实际数据 + "未来 7 天" 标题 (不误导)

## ⚠️ V2 批量卡片必须显式传 window_days (2026-06-30 晚 补正)

**早期错误判断**: 之前文档写"V2 渲染器不需要传 days, 靠 peak_14d_max_xxx 字段是窗口内最高就行"——**这结论是错的**。

- **数据正确** (peak 数字 + 日期是窗口内的)
- **但 V2 卡片文案写死「14 天内峰值 / 14 天内全网最危险日 / 14 天峰值降序」**
- 用户 `--days 5` 拿到卡片, 看到「14 天内全网最危险日」+ 实际数据是 7-04 而不是 7-13, 立刻质疑

**正确做法**:
1. `render_multi_point_card_v2(points_alerts, window_days=None)` 显式收窗口参数
2. `feishu_push.r0_window_days(results)` 从 `results[0]["slice_window"]` 自动抽 days
3. `push_batch` 调 `render_fn(pts_alerts, window_days=r0_window_days(results))`
4. V2 内部根据 `window_days` 动态选文案 (本次修了 4 处):
   - `None or >= 14`: "14 天内全网最危险日" / "14 天内峰值" / "14 天峰值降序" / footer "查询窗口: 14 天"
   - `< 14`: "未来 N 天全网最危险日" / "N 天内峰值" / "N 天峰值降序" / footer "查询窗口: 未来 N 天"
5. 修了 4 处文案:
   - 「14 天内全网最危险日」(区块 2 标题)
   - 「14 天内峰值」(每个点内字段标题)
   - 「14 天峰值降序」(区块 3 排序说明)
   - footer 加 "查询窗口: 未来 N 天"

**强约束 (新 pitfall)**:
- **改 V2 渲染器任何文案前, 必须 grep `14 天` 看是不是还有写死的地方**

**反模式 (别再犯)**:
- ❌ "数据是窗口内就行, 文案写死 14 天没关系"——**数据对 ≠ 用户看到对**
- ❌ "之前没改 V2 渲染器, 那就是不需要改"——**是因为没人查过文案, 不是它该这样**

**为什么 V2 渲染器要显式收 days, 而不只靠字段数据**:
字段数据 (`peak_14d_max` / `peak_14d_max_date`) 数字本身正确, 但**渲染时无法判断这个数字是窗口内还是全 14 天** (除非查 `slice_window`)。让 V2 渲染器显式收 `window_days` 是**让渲染器具备这个上下文**, 而不是把判断逻辑塞到 feishu_push.py。

**留退路**: daily_peaks 全 14 天保留, 未来若要"卡片同时显示 5 天短期 + 14 天长期"也容易扩展。

## 验证记录 (2026-06-30 实测)

| 命令 | 推 message_id | 备注 |
|---|---|---|
| `batch --days 5 --export feishu` (V2 14天硬编码版) | `om_x100b6b07056b80a0c3179281e9efd44` | 数据对, 文案错 (重发修正) |
| `batch --from-date 2026-07-10 --days 3 --export feishu` | `om_x100b6b07020be09cc3f1334d6dcc038` | 窗口内 3 天峰值 |
| `query cd_chengyulukou_001 --days 7 --send-feishu` | `om_x100b6b071ccd18a4c231e09c500a2c4` | 单点 7 天卡片 |
| `batch --days 5 --export feishu` (V2 修正版) | `om_x100b6b072cb3a4e0c38a5d38a303e29` | 文案改 "未来 5 天" |
| `batch --days 3 --export feishu` (V2 修正版) | `om_x100b6b072dbb08a8c4e64d8d6fffaa2` | 文案改 "未来 3 天" |
| `batch --export feishu` (默认 14 天, V2 修正版) | `om_x100b6b072a4f3890c42e124cfced021` | 文案保持 "14 天" |

## 这次顺手修的 2 个 bug

1. **`query --send-feishu` 把 markdown 当 chat_id**: `feishu_send(md, "markdown")` → 改为 `push_markdown(FEISHU_HOME_CHAT_ID, md)` (参数错位)
2. **`batch --export feishu` 直接传 `results` 给 `render_multi_point_card`**: 字段格式不匹配会 TypeError → 改为走 `feishu_push.push_batch(chat_id, results, use_card=True, version="v2")`

## Pitfalls

1. **days 范围 1-14** — Open-Meteo 最多给 14 天预报, 超出会 raise ValueError
2. **from_date 格式** — 必须是 `YYYY-MM-DD`, 否则 `peaks["date"]` 字符串比较会出错
3. **from_date 找不到** — 静默回退到 start_idx=0 (不报错), 这可能导致你以为从 7-10 开始, 实际从今天开始 → 调试时打印 `slice_window` 确认
4. **批量卡片字符预算** — 6 点 V2 约 3166 字符, 加 `--include-14d-table` 会爆 4096 字符上限, 这开关**还没实现** (在 argparse 里, 没真用)
5. **⚠️ 数据对 ≠ 文案对**: 改字段计算逻辑后**必须**同时改文案。`peak_14d_max_date` 是窗口内了, 但写死的"14 天内"会立刻让用户怀疑。本次教训。
6. **⚠️ "改 V2 渲染器" 不要嫌麻烦** — V1 时代"靠字段数据就行"是因为字段没分窗口; 既然分了窗口, 渲染器就要**显式接窗口参数**。**字段的语义和渲染器的语义是同一层, 别把它们拆开**。

## 后续 TODO

- **`--include-14d-table` 实现**: 批量卡片里附加每点 14 天表 (84 行 markdown), 需要拆卡或降级到 CSV 附件
- **自然语言解析**: 收到"成都三环路未来 7 天"自动 → `query --days 7 --send-feishu`
- **主动询问**: 信息不全时用 clarify 工具问 (不在 IM 渠道, IM 渠道直接问)
- **V2 卡片动态化测试脚本**: 类似 `verify_push_no_questionmark.py`, 验证 `--days 1/5/7/14` 各窗口下文案都是动态的
