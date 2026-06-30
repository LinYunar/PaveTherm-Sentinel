# 多源元数据补全模式 (Enrichment Multi-Source Pattern)

适用: 任何"实体数据有缺失字段, 不能等用户提供" 的 skill。  
本 skill 的典型场景: 监测点加进来时 `pavement_color` / `pavement_age_years` 缺失, 模型置信度直接掉到 LOW, 没法给出可信预测。

## 4 源兜底 (按信号强度降序)

| # | 数据源 | 适用字段 | 信号强度 | 失败兜底 |
|---|---|---|---|---|
| 1 | **OSM Overpass API** | 路面类型 (surface=asphalt), 周边 landuse | 强 (有 surface tag 几乎 100% 准) | 无 tag → 跳到 #2 |
| 2 | **高德 POI** | POI 类型 (桥/立交/高速), 确认是"道路" | 中 (类型对就 90% 准) | 返回地铁站/无关 POI → 跳到 #3 |
| 3 | **Tavily Search** | 建成/大修年份 | 弱-中 (要找对关键词) | 无明确年份 → 跳到 #4 |
| 4 | **行业标准推断** (JTG F40 / GB 50092 等) | 路面规格 + 颜色按"路等级+年龄" | 兜底 (覆盖 80% 场景, 但置信度低) | 仍缺 → 标 unknown + SPECULATIVE |

**关键**: 每个来源返回结果时, **必须带置信度标签**:
- `CONFIRMED` (用户提供 or OSM surface tag 直接命中)
- `ASSUMED` (Tavily 找到明确年份, 但解释依赖搜索结果)
- `SPECULATIVE` (标准推断, 或 Tavily 模糊匹配)
- `UNKNOWN` (全部 4 源都失败)

**模型层如何使用**: `model.py` 的 confidence 输出用 `CONFIRMED/ASSUMED/SPECULATIVE` 而非 `HIGH/MEDIUM/LOW` 三档, 区分"输入数据"和"模型精度"。

## 实现要点 (基于本 skill 的 `enrich_point.py`)

### 1. 单点函数, 多源串行调用

```python
def enrich_point(point: dict) -> dict:
    result = {"color": None, "age_years": None, ...}
    # 串行调用, 任何一源成功填入就跳过下一个
    osm = query_overpass(point["lat"], point["lon"])  # 1
    if osm.get("surface"):
        result["pavement_type"] = "AC 沥青 (OSM 验证)"
    poi = query_amap_poi(point["name"], AMAP_KEY)  # 2
    if "桥" in poi.get("type", ""):
        result["pavement_type"] = result.get("pavement_type") or "AC 沥青 (高德 POI 确认道路)"
    age = query_tavily_for_age(point["name"], TAVILY_KEY)  # 3
    if age is not None:
        result["age_years"] = age
        result["age_confidence"] = "ASSUMED"
    if result["pavement_type"] is None:  # 4
        result["pavement_type"] = infer_pavement_type(...)
    return result
```

### 2. dry-run 模式 (关键 UX)

```bash
# 默认 dry-run, 只打印推断结果不写 YAML
python3 enrich_point.py cd_chengyulukou_001
# → 打印 4 源逐一结果 + 最终推断
# → 提示 "加 --apply 才会写入"

# 用户确认后再写
python3 enrich_point.py cd_chengyulukou_001 --apply
```

**为什么要 dry-run 默认**: 元数据是用户半人工维护的, 自动覆盖代价高, dry-run 让用户先看结果再 apply。

### 3. Tavily 关键词策略 (老出错的地方)

- 第一轮: `<地点名> 建成年份 OR 通车时间 OR 大修时间` (3 选 1, OR 让 Tavily 自由组合)
- 第二轮 (失败再试): `<地点名> 沥青路面 修筑` (放宽到"修筑"而非"大修")
- **不要**: 用"大修"单一关键词 → 找不到就放弃, 实际上可能搜的是"建成"

### 4. 年份推断陷阱

- **不要用道路建成年份** 直接当路面年龄! `成都三环路 2002 年通车`, 但 2026 年的路表是 2018 年大修铺的, 实际路面年龄是 8 年不是 24 年。
- 但当 Tavily 找不到大修年份时, 道路建成年份是**最后一个** fallback, 且必须标 `SPECULATIVE`。
- 解析年份的正则要覆盖中文多种表达: `(\d{4})\s*年[通车建成使用开放]`, `(\d{4})\s*年[大修翻新重建改建扩建]`, `建于\s*(\d{4})`。

### 5. 颜色推断的年龄分段

```python
# JTG F40 经验: 沥青路面颜色随老化变化
if age_years is None:       return "unknown"     # 兜底
elif age_years <= 2:        return "black"       # 新铺, 接近黑体
elif age_years <= 7:        return "gray"        # 中度老化, 深灰
else:                       return "light_gray"  # 严重老化, 泛白
```

## 复用到其他 skill

这个模式通用到几乎所有"现实实体" 类的 skill:

- **桥梁监测**: 缺桥型/建成年份 → OSM 桥 tag + 高德 POI + Tavily + JTG D60 规范推断
- **输电线路**: 缺电压等级/投运时间 → OSM power=line + 国网公开数据 + Tavily + GB 50061 规范
- **楼宇能耗**: 缺建造年代/暖通类型 → OSM building:year + 物业公开档案 + Tavily + GB 50189 规范
- **道路病害**: 缺路面结构/交通量 → OSM surface + 高德路况 + 交通委公开年报 + JTG D40 规范

**共通的 4 层结构**: 开放地图 → 国内 POI → AI 搜索 → 行业标准规范。

## Pitfalls

1. **4 源不是并行的**: 不要写 `asyncio.gather` 并发调, 因为后面的源依赖前面失败才触发, 并发浪费配额且语义混乱。
2. **Tavily dev key 有月配额**: 实测 dev key (tvly-dev-...) 1000 次/月, 单点 enrich 不要在循环里反复调, 跑完一次就 cache 到 YAML。
3. **不要把 OSM `surface=asphalt` 误当"路面是 AC-13"**: OSM 只告诉你"是沥青", 具体配合比要靠 JTG F40 推断。
4. **JTG F40 是中国国标**: 海外场景用 AASHTO / ASTM 替换推断规则, 别硬套。
5. **dry-run 必须默认**: 不要让一个不熟悉的脚本自动覆盖用户的 YAML, 哪怕看起来 "100% 准"。
