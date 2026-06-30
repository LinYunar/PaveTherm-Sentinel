# User-Visible Data Consistency Test Pattern

> **TL;DR**: 凡是用户看到的卡片/表格/UI 上展示的计算值,都必须有一个测试类断言"展示值 == 底层计算值",中间层(V2 渲染器 / 模板字符串 / 字段映射)不能分裂。

---

## 触发场景

适用所有"计算值 → 渲染到用户能看到的表面" 的场景:

- 飞书 / Slack / 钉钉 卡片 (interactive message)
- HTML dashboard / ECharts / Chart.js 数据绑定
- CLI 输出表格 (尤其有 `--days N` / `--from-date` / `--level` 等参数化时)
- 邮件模板 (SMTP 渲染)
- PDF 报告 (ReportLab / WeasyPrint)
- 任何 markdown / HTML 模板字符串拼的展示

---

## 核心模式 (3 步)

### Step 1: 识别"用户看到什么字段"

不只数据层字段, 还有**渲染层字段** (用户直接看到) — 两个集合可能不一样。

```python
# 例: 卡片显示 "14 天内峰值 67.0°C @ 7-13 14:00"
# 数据层字段: peak_14d_max, peak_14d_max_date, peak_14d_max_time, peak_14d_max_air
# 渲染层字段: "14 天内" / "67.0°C" / "7-13" / "14:00"  ← 4 个独立字符串
# 这 4 个字符串里任何一个魔改/拼错, 用户一眼看出来, 你的数据对也没用
```

### Step 2: 写一个 `_assert_visible_match(rendered, underlying)` 工具函数

```python
def _assert_match(r, label=""):
    """断言: 渲染层字段 == 底层 sliced_peaks 真实最大值"""
    sliced = r.get("daily_peaks_sliced") or r.get("daily_peaks", [])
    real_max = max(sliced, key=lambda x: x["peak_pavement_temp"])
    
    # 1. 温度 (允许 0.05 浮点误差)
    assert abs(r["peak_14d_max"] - real_max["peak_pavement_temp"]) < 0.05, \
        f"{label}: 卡片显示 {r['peak_14d_max']} ≠ 真实最大 {real_max['peak_pavement_temp']}"
    
    # 2. 日期 (字符串相等, 不模糊)
    assert r["peak_14d_max_date"] == real_max["date"], \
        f"{label}: 卡片日期 {r['peak_14d_max_date']} ≠ 真实峰值日 {real_max['date']}"
    
    # 3. 时间 (HH:MM 段)
    expected_time = real_max["peak_time"][11:16]
    assert r["peak_14d_max_time"] == expected_time, ...
    
    # 4. 当时气温
    assert abs(r["peak_14d_max_air"] - real_max["peak_air_temp"]) < 0.05, ...
```

### Step 3: 写 4 类测试用例覆盖"字段被参数化"的所有路径

| 测试类 | 覆盖什么 |
|---|---|
| **默认参数** (None / 不传) | 卡片字段 == 全量数据真实值 |
| **参数边界** (--days 1 / 5 / 7 / 14) | 卡片字段 == 窗口内真实值, 文案反映窗口 ("未来 5 天" 不写死 "14 天") |
| **参数越界** (--days 0 / 15 / 20) | 报错明确, 不静默降级 |
| **复合参数** (--from-date X + --days Y) | 切片起始日正确, 文案正确 |

每个测试类**不 mock**, 直接 `query_point(...)` / `query_batch(...)` 跑实数据。**唯一能省的 mock**: 网络层 (怕网络抖动), 用 monkeypatch 喂假 Open-Meteo 响应。

---

## 真实案例 (PaveTherm Sentinel `tests/test_query_consistency.py`)

8 个测试类覆盖:

1. `TestBatchDefaultWindow` — 6 个成都点 × 14 天默认窗口
2. `TestBatchDays5` — 5 天窗口 + 边界 (3 天峰值 ≤ 5 天峰值)
3. `TestBatchDays3`
4. `TestBatchCustomWindow` — from-date 偏移 + 切片对齐
5. `TestEdgeCases` — days 0/15 拒绝, 1/14 接受
6. `TestSinglePoint` — 单点 query_point 同套校验
7. `TestV2CardDataConsistency` — 渲染器输出**字面量**反映参数 (grep "14 天" 不应在 5 天窗口卡片里出现)
8. `TestR0WindowDays` — 工具函数自身鲁棒性 (空列表 / 缺字段 / 越界)

**总 ~30 个测试用例**, 一次跑约 14 秒 (因为实拉 Open-Meteo 6 次 × 1.1s)。

---

## 跟 rigor-discipline 7.6.1.1 的关系

**7.6.1.1** (`rigor-discipline/SKILL.md`) 是**口头铁律**:

> 加完参数化功能 → grep 渲染函数 magic string + 拉真卡片内容 grep 关键文案 → 才算做完

**这个 pattern** 是把口头铁律**代码化** — 写成 pytest 类, 每次改代码前 `pytest tests/test_*consistency*.py -v` 跑一次, 改完再跑一次。**口头铁律靠记忆, 代码化铁律靠 CI**。

**关系**:
- 7.6.1.1 = 改完**这次**别忘看
- `tests/test_*consistency*.py` = 下次**别忘**, 也别让人**别忘** (CI 红即报警)

---

## 写测试时的 5 个常见踩坑

1. **mock 网络 → mock 错** — 假数据算出来的 max 当然对, 但跟真数据不符。**不 mock**, 跑实拉, 14 秒不算慢。
2. **只看 happy path** — `--days 5` 通过就当完事。**必须**也测 0/14/越界 (raise / error) 路径, 否则下游会 crash。
3. **只测数据层不测渲染层** — `_assert_match` 只能验 `r["peak_14d_max"]` 字段。**还得**单独跑 `render_multi_point_card_v2(...)` 然后 `json.dumps(card)` grep 关键文案 ("14 天" 不该在 5 天窗口里出现)。`TestV2CardDataConsistency.test_v2_card_mentions_correct_days_for_5` 就是这个用例。
4. **不测工具函数鲁棒性** — `r0_window_days([])` 返回什么? 缺字段呢? 越界 count 呢? `TestR0WindowDays` 5 个用例全覆盖。
5. **测试耦合实现** — 别测 `r["slice_window"]["count"]` 应该 == 14 这种**实现细节**, 测"如果用户传 days=5, sliced_peaks 应该 5 行**且**peak_14d_max 是这 5 行里最大的" 这种**行为约束**。

---

## 何时该升级这个 pattern

- 字段从 4 个 → 8 个 → 12 个, 用例数线性增长, 测试运行时间 < 1 分钟, 还扛得住
- 字段开始依赖**外部配置** (阈值 / 模型版本 / 边界值从 YAML 读) → 加 fixture 注入测试, 别硬编码
- 出现 3 个 skill 都需要这种"用户看到值 == 真实值"测试 → 抽到独立 skill (`data-fidelity-test` / `rendering-fidelity-test` / `visual-data-integrity`), 三个 skill 都引用

---

## 相关引用

- `tests/test_query_consistency.py` (PaveTherm Sentinel 实例)
- `rigor-discipline/SKILL.md` 7.6.1.1 改 input 参数 → 验 rendered output 反映参数 (口头铁律)
- `rigor-discipline/SKILL.md` 7.6.4 返回成功 ≠ 功能正确 (更一般规则)
- `test-driven-development/SKILL.md` TDD 完整流程 (这个 pattern 是 TDD 在 user-facing rendering 场景的具体应用)
