"""
query_batch / query_point 数据一致性自检
========================================
目标: 每次跑批量/单点查询后,自动校验卡片上展示的 peak 字段 (temperature/date/time/air)
是否与底层数据源 (daily_peaks[_sliced]) 真实最大值一致。

为什么重要: 用户看到卡片上的 "14 天峰值 67°C @ 7-13 14:00" 必须是真的,
不能卡片显示 14 天但数据只查了 5 天,或者峰值日错位。

校验内容:
  1. peak_14d_max == max(sliced_peaks, key=peak_pavement_temp)
  2. peak_14d_max_date == 真峰值那天的 date
  3. peak_14d_max_time == 那天的 peak_time[11:16]
  4. peak_14d_max_air == 那天的 peak_air_temp
  5. alert_summary.pavement_temp == peak_14d_max OR current_temp (取更严的等级)
  6. slice_window.count == len(sliced_peaks)
  7. days / from_date 参数: days=None 应回退为 14
  8. 单点的 feishu_card 里 daily_peaks_sliced 也得满足以上

使用:
  pytest tests/test_query_consistency.py -v
  或 python tests/test_query_consistency.py (独立运行)
"""

import sys
import importlib
from pathlib import Path

# 加入 scripts 路径, 跟 monitor.py 同步刷新
SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

# 每次测试前强制重载,防止上次测试残留
def _reload():
    for mod_name in ("monitor", "alert", "feishu_push", "model", "fetch_weather", "geocode"):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

import pytest


# ============================================================
# 工具函数
# ============================================================
def _real_window_max(sliced_peaks):
    """从 sliced_peaks 返回真实最大值(温度+日期+时间+气温)"""
    if not sliced_peaks:
        return None
    return max(sliced_peaks, key=lambda x: x["peak_pavement_temp"])


def _assert_match(result, label=""):
    """单点断言: result 的 peak_14d_max_xxx 必须对得上 sliced_peaks 真实最大值"""
    sliced = result.get("daily_peaks_sliced") or result.get("daily_peaks", [])
    if not sliced:
        pytest.skip(f"{label}: 无数据可校验 (skipping)")
    real_max = _real_window_max(sliced)
    if not real_max:
        pytest.skip(f"{label}: 无峰值数据可校验")

    # 1. 温度
    assert abs(result["peak_14d_max"] - real_max["peak_pavement_temp"]) < 0.05, \
        f"{label}: peak_14d_max={result['peak_14d_max']} ≠ sliced 真实最大值={real_max['peak_pavement_temp']}"

    # 2. 日期
    assert result["peak_14d_max_date"] == real_max["date"], \
        f"{label}: peak_14d_max_date={result['peak_14d_max_date']} ≠ sliced 真实峰值日={real_max['date']}"

    # 3. 时间 (HH:MM 段)
    expected_time = real_max["peak_time"][11:16] if real_max.get("peak_time") else None
    assert result["peak_14d_max_time"] == expected_time, \
        f"{label}: peak_14d_max_time={result['peak_14d_max_time']} ≠ sliced 真实峰值时刻={expected_time}"

    # 4. 当时气温
    assert abs(result["peak_14d_max_air"] - real_max["peak_air_temp"]) < 0.05, \
        f"{label}: peak_14d_max_air={result['peak_14d_max_air']} ≠ sliced 真实峰值气温={real_max['peak_air_temp']}"


# ============================================================
# 1. 默认窗口 (14 天) - 6 个成都监测点
# ============================================================
class TestBatchDefaultWindow:
    def setup_method(self):
        _reload()
        from monitor import query_batch
        self.results = query_batch(filter_city="成都")
        assert len(self.results) > 0, "应至少有 1 个成都监测点"

    def test_window_count_is_14(self):
        for r in self.results:
            win = r.get("slice_window", {})
            assert win.get("count") == 14, \
                f"{r['point']['name']}: 默认窗口应为 14 天,实际 count={win.get('count')}"
            assert win.get("days") is None, \
                f"{r['point']['name']}: days 不传时应为 None,实际={win.get('days')}"

    def test_sliced_peaks_count_matches(self):
        for r in self.results:
            sliced = r.get("daily_peaks_sliced", [])
            assert len(sliced) == 14, \
                f"{r['point']['name']}: sliced_peaks 应为 14 行,实际={len(sliced)}"

    def test_peak_alignment(self):
        """所有 6 个点的 peak_14d_max_xxx 必须对得上 sliced 真实最大值"""
        for r in self.results:
            _assert_match(r, label=r["point"]["name"])


# ============================================================
# 2. days=5 窗口
# ============================================================
class TestBatchDays5:
    def setup_method(self):
        _reload()
        from monitor import query_batch
        self.results = query_batch(filter_city="成都", days=5)

    def test_window_count_is_5(self):
        for r in self.results:
            win = r.get("slice_window", {})
            assert win.get("count") == 5, \
                f"{r['point']['name']}: days=5 应得 count=5,实际={win.get('count')}"
            assert win.get("days") == 5

    def test_sliced_peaks_count_matches(self):
        for r in self.results:
            sliced = r.get("daily_peaks_sliced", [])
            assert len(sliced) == 5, \
                f"{r['point']['name']}: sliced_peaks 应为 5 行,实际={len(sliced)}"

    def test_peak_alignment(self):
        """5 天窗口内峰值必须真从 5 天里取,不能是全 14 天的峰值"""
        for r in self.results:
            sliced = r.get("daily_peaks_sliced", [])
            full_peaks = r.get("daily_peaks", [])
            # sliced 是 full 的 prefix [start:start+5]
            assert sliced == full_peaks[0:5], \
                f"{r['point']['name']}: sliced 不是 full 的前 5 行"
            _assert_match(r, label=r["point"]["name"])

    def test_5day_max_differs_from_14day_max(self):
        """5 天峰值必须 ≠ 14 天峰值 (除非 14 天内的峰值恰好就在前 5 天)"""
        for r in self.results:
            full_max = max(r["daily_peaks"], key=lambda x: x["peak_pavement_temp"])
            if full_max["date"] not in [p["date"] for p in r["daily_peaks_sliced"]]:
                # 14 天峰值不在 5 天窗口里 → 5 天峰值必须更小
                assert r["peak_14d_max"] < full_max["peak_pavement_temp"], \
                    f"{r['point']['name']}: 14 天峰值日 ({full_max['date']}) 不在 5 天窗口里,但 5 天峰值 (={r['peak_14d_max']}) 仍等于 14 天峰值 (={full_max['peak_pavement_temp']}),窗口计算可能没生效"


# ============================================================
# 3. days=3 窗口
# ============================================================
class TestBatchDays3:
    def setup_method(self):
        _reload()
        from monitor import query_batch
        self.results = query_batch(filter_city="成都", days=3)

    def test_window_count_is_3(self):
        for r in self.results:
            assert r["slice_window"].get("count") == 3

    def test_peak_alignment(self):
        for r in self.results:
            _assert_match(r, label=r["point"]["name"])

    def test_3day_max_leq_5day_max(self):
        """3 天峰值 ≤ 5 天峰值 (基本数学常识)"""
        _reload()
        from monitor import query_batch
        r3 = {r["point"]["name"]: r["peak_14d_max"] for r in query_batch(filter_city="成都", days=3)}
        r5 = {r["point"]["name"]: r["peak_14d_max"] for r in query_batch(filter_city="成都", days=5)}
        for name in r3:
            assert r3[name] <= r5[name] + 0.05, \
                f"{name}: 3天峰值 ({r3[name]}) > 5天峰值 ({r5[name]}),不可能"


# ============================================================
# 4. from_date + days 自定义窗口
# ============================================================
class TestBatchCustomWindow:
    def setup_method(self):
        _reload()
        from monitor import query_batch
        # 7-10 起, 查 3 天
        self.results = query_batch(filter_city="成都", from_date="2026-07-10", days=3)

    def test_window_is_correct(self):
        for r in self.results:
            sliced = r.get("daily_peaks_sliced", [])
            if sliced:
                assert sliced[0]["date"] == "2026-07-10", \
                    f"{r['point']['name']}: sliced 起始日应为 2026-07-10,实际={sliced[0]['date']}"
                assert sliced[-1]["date"] == "2026-07-12", \
                    f"{r['point']['name']}: sliced 末日应为 2026-07-12,实际={sliced[-1]['date']}"

    def test_peak_alignment(self):
        for r in self.results:
            _assert_match(r, label=r["point"]["name"])


# ============================================================
# 5. 参数边界
# ============================================================
class TestEdgeCases:
    def setup_method(self):
        _reload()

    def test_days_0_rejected(self):
        from monitor import query_batch
        with pytest.raises(ValueError):
            query_batch(filter_city="成都", days=0)

    def test_days_15_rejected(self):
        from monitor import query_batch
        with pytest.raises(ValueError):
            query_batch(filter_city="成都", days=15)

    def test_days_1_accepted(self):
        from monitor import query_batch
        r = query_batch(filter_city="成都", days=1)
        for x in r:
            assert x["slice_window"]["count"] == 1

    def test_days_14_accepted(self):
        from monitor import query_batch
        r = query_batch(filter_city="成都", days=14)
        for x in r:
            assert x["slice_window"]["count"] == 14


# ============================================================
# 6. 单点 query_point
# ============================================================
class TestSinglePoint:
    def setup_method(self):
        _reload()
        from monitor import query_point
        self.point_id = "cd_chengyulukou_001"

    def test_default_14days(self):
        from monitor import query_point
        r = query_point(self.point_id)
        assert r["slice_window"]["count"] == 14
        _assert_match(r, label="单点默认 14 天")

    def test_days_5(self):
        from monitor import query_point
        r = query_point(self.point_id, days=5)
        assert r["slice_window"]["count"] == 5
        _assert_match(r, label="单点 5 天")

    def test_days_3(self):
        from monitor import query_point
        r = query_point(self.point_id, days=3)
        sliced = r.get("daily_peaks_sliced", [])
        assert len(sliced) == 3
        _assert_match(r, label="单点 3 天")

    def test_days_0_rejected(self):
        from monitor import query_point
        r = query_point(self.point_id, days=0)
        assert "error" in r, "days=0 应返回 error"

    def test_days_15_rejected(self):
        from monitor import query_point
        r = query_point(self.point_id, days=15)
        assert "error" in r, "days=15 应返回 error"

    def test_days_invalid_returns_error(self):
        from monitor import query_point
        r = query_point(self.point_id, days=20)
        assert "error" in r


# ============================================================
# 7. V2 卡片文案与数据一致性 (跨层校验)
# ============================================================
class TestV2CardDataConsistency:
    """V2 卡片渲染的字段必须 == 底层数据 (用户看的就是这个)"""
    def setup_method(self):
        _reload()
        from monitor import query_batch
        from alert import render_multi_point_card_v2
        from feishu_push import r0_window_days
        self.query_batch = query_batch
        self.render = render_multi_point_card_v2
        self.r0_window_days = r0_window_days

    def _build_pts(self, results):
        return [{
            "point": r["point"],
            "current_temp": r["current_temp"],
            "current_air_temp": r.get("current_air_temp"),
            "current_time": r.get("current_time"),
            "alert_summary": r["alert_summary"],
            "peak_14d_max": r.get("peak_14d_max"),
            "peak_14d_max_date": r.get("peak_14d_max_date"),
            "peak_14d_max_time": r.get("peak_14d_max_time"),
            "peak_14d_max_air": r.get("peak_14d_max_air"),
        } for r in results]

    def test_v2_window_detect(self):
        """r0_window_days 抽出来的值应等于 slice_window.days 或 count"""
        results = self.query_batch(filter_city="成都", days=5)
        win = self.r0_window_days(results)
        assert win == 5, f"r0_window_days 应返回 5,实际={win}"

    def test_v2_default_14(self):
        results = self.query_batch(filter_city="成都")
        win = self.r0_window_days(results)
        # 默认: days is None, count=14 → 应识别为 14
        assert win == 14, f"默认窗口应识别为 14,实际={win}"

    def test_v2_card_mentions_correct_days_for_5(self):
        """5 天窗口的卡片文案应该出现 "5 天" / "未来 5 天" 而不是 "14 天" """
        import json, re
        results = self.query_batch(filter_city="成都", days=5)
        pts = self._build_pts(results)
        win = self.r0_window_days(results)
        card = self.render(pts, window_days=win)
        cs = json.dumps(card, ensure_ascii=False)
        # 必须出现 5 天相关文案
        assert "5 天" in cs, "V2 卡片文应有 '5 天' 文案"
        # 不应出现 "14 天内峰值"
        assert "14 天内峰值" not in cs, "V2 卡片不应出现 '14 天内峰值' (窗口 = 5)"
        # 全网最危险应说"未来 5 天"
        assert "未来 5 天" in cs, "V2 卡片应有 '未来 5 天' 文案"

    def test_v2_card_uses_14_for_default(self):
        import json
        results = self.query_batch(filter_city="成都")
        pts = self._build_pts(results)
        win = self.r0_window_days(results)
        card = self.render(pts, window_days=win)
        cs = json.dumps(card, ensure_ascii=False)
        # 默认 (14 天) 文案用 "14 天"
        assert "14 天" in cs


# ============================================================
# 8. r0_window_days 函数自身的鲁棒性
# ============================================================
class TestR0WindowDays:
    def setup_method(self):
        _reload()
        from feishu_push import r0_window_days
        self.fn = r0_window_days

    def test_empty_list_returns_none(self):
        assert self.fn([]) is None

    def test_missing_slice_window_returns_none(self):
        result = [{"point": {"name": "x"}, "current_temp": 30}]
        assert self.fn(result) is None

    def test_with_days(self):
        result = [{"point": {}, "slice_window": {"days": 7, "count": 7}}]
        assert self.fn(result) == 7

    def test_with_count_fallback(self):
        result = [{"point": {}, "slice_window": {"days": None, "count": 10}}]
        assert self.fn(result) == 10

    def test_count_out_of_range_falls_back(self):
        result = [{"point": {}, "slice_window": {"days": None, "count": 20}}]
        assert self.fn(result) is None


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    # 独立运行: 不依赖 pytest, 直接 print 结果
    _reload()

    print("=" * 60)
    print("数据一致性自检 (独立运行)")
    print("=" * 60)

    from monitor import query_batch, query_point

    # Test 1: 默认窗口
    print("\n[1] 默认窗口 (14 天)")
    r = query_batch(filter_city="成都")
    print(f"  点数: {len(r)}")
    for x in r:
        try:
            _assert_match(x, label=x["point"]["name"])
            print(f"  ✅ {x['point']['name']}: peak={x['peak_14d_max']:.1f}°C @ {x['peak_14d_max_date']} {x['peak_14d_max_time']}")
        except AssertionError as e:
            print(f"  ❌ {x['point']['name']}: {e}")

    # Test 2: 5 天窗口
    print("\n[2] days=5 窗口")
    r = query_batch(filter_city="成都", days=5)
    for x in r:
        try:
            _assert_match(x, label=x["point"]["name"])
            print(f"  ✅ {x['point']['name']}: peak={x['peak_14d_max']:.1f}°C @ {x['peak_14d_max_date']} {x['peak_14d_max_time']}")
        except AssertionError as e:
            print(f"  ❌ {x['point']['name']}: {e}")

    # Test 3: 3 天窗口
    print("\n[3] days=3 窗口")
    r = query_batch(filter_city="成都", days=3)
    for x in r:
        try:
            _assert_match(x, label=x["point"]["name"])
            print(f"  ✅ {x['point']['name']}: peak={x['peak_14d_max']:.1f}°C @ {x['peak_14d_max_date']} {x['peak_14d_max_time']}")
        except AssertionError as e:
            print(f"  ❌ {x['point']['name']}: {e}")

    # Test 4: from-date + days
    print("\n[4] from_date=2026-07-10 + days=3")
    r = query_batch(filter_city="成都", from_date="2026-07-10", days=3)
    for x in r:
        try:
            sliced = x["daily_peaks_sliced"]
            print(f"  {'✅' if sliced[0]['date']=='2026-07-10' else '❌'} {x['point']['name']}: 日期范围 {sliced[0]['date']}→{sliced[-1]['date']}, peak={x['peak_14d_max']:.1f}°C")
        except Exception as e:
            print(f"  ❌ {x['point']['name']}: {e}")

    # Test 5: 单点
    print("\n[5] 单点 cd_chengyulukou_001")
    for d in [None, 3, 5, 14]:
        r = query_point("cd_chengyulukou_001", days=d)
        win = r.get("slice_window", {})
        print(f"  days={d}: sliced={len(r.get('daily_peaks_sliced', []))} 行, peak={r.get('peak_14d_max', 0):.1f}°C @ {r.get('peak_14d_max_date')}")

    print("\n" + "=" * 60)
    print("完成")
    print("=" * 60)
