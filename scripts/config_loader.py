"""
统一配置加载器: 解决 skill 复用时 key 不被硬编码的问题
====================================================

设计原则:
  1. skill 自带 config.yaml 不含任何 key (只有 endpoint、阈值等公开参数)
  2. key 全部从 ~/.hermes/secrets/pavetherm-sentinel.env 读 (Hermes 标准)
  3. load_config() 一站式: 加载 config.yaml + 注入 env 里的 key + 校验
  4. 缺 key 时给清晰的报错 (告诉用户运行 install.py)

使用:
  from config_loader import load_config
  cfg = load_config()  # 自动读 env
  amap_key = cfg["geocoding"]["amap_key"]
"""

import os
import sys
import yaml
from pathlib import Path
from typing import Optional

# ====== 路径常量 ======
SKILL_ROOT = Path(__file__).resolve().parent.parent  # ~/.hermes/skills/pavetherm-sentinel/
CONFIG_PATH = SKILL_ROOT / "config.yaml"
ENV_PATH = Path.home() / ".hermes" / "secrets" / "pavetherm-sentinel.env"


# ====== env 加载 ======
def _load_env(env_path: Path = ENV_PATH) -> None:
    """加载 ~/.hermes/secrets/pavetherm-sentinel.env 到 os.environ
    不存在则跳过 (让上层报清晰的 '未配置' 错误)
    """
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # 不覆盖既有 env (允许用户用 export 临时覆盖)
        os.environ.setdefault(k, v)


# ====== 占位符替换 ======
def _resolve_placeholders(cfg: dict) -> dict:
    """把 cfg 里的 ${env:XXX} / ${XXX} 占位符替换成 os.environ[XXX]
    找不到 key 时原样保留 (上层会校验)
    """
    def resolve(v):
        if isinstance(v, str):
            # 支持 ${env:XXX} 和 ${XXX} 两种语法
            if v.startswith("${env:") and v.endswith("}"):
                key = v[6:-1]
                return os.environ.get(key, v)  # 找不到保留原值
            if v.startswith("${") and v.endswith("}"):
                key = v[2:-1]
                return os.environ.get(key, v)
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [resolve(x) for x in v]
        return v
    return resolve(cfg)


# ====== 校验 ======
def _validate(cfg: dict) -> list[str]:
    """返回缺失 key 的列表 (空列表表示全部就绪)"""
    missing = []
    # 检查关键 key 是否真的拿到了 (而不是占位符 ${...})
    checks = [
        ("geocoding.amap_key", cfg.get("geocoding", {}).get("amap_key", "")),
        ("search.tavily_key", cfg.get("search", {}).get("tavily_key", "")),
        ("alert_delivery.feishu_home_chat_id",
         cfg.get("alert_delivery", {}).get("feishu_home_chat_id", "")),
        ("feishu_push.lark_cli", cfg.get("feishu_push", {}).get("lark_cli", "")),
    ]
    for name, val in checks:
        if not val or (isinstance(val, str) and val.startswith("${")):
            missing.append(name)
    return missing


# ====== 主入口 ======
def load_config(strict: bool = False) -> dict:
    """
    加载完整配置 (yaml 内容 + env 注入的 key)

    Args:
        strict: True → 缺 key 时直接抛 SystemExit
                False → 返回 cfg + 缺失列表 (让 caller 决定怎么处理)

    Returns:
        dict: 完整 cfg (key 已注入)
    Raises:
        FileNotFoundError: 找不到 config.yaml
        SystemExit: strict=True 且缺 key
    """
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.yaml 不存在: {CONFIG_PATH}\n"
            f"请确认 PaveTherm Sentinel skill 已完整安装"
        )

    # 1) 加载 env
    _load_env()

    # 2) 加载 yaml + 解析占位符
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = _resolve_placeholders(cfg)

    # 3) 派生 feishu_push 配置 (lark_cli + chat_id) 给上层用
    cfg.setdefault("feishu_push", {})
    cfg["feishu_push"]["lark_cli"] = os.environ.get(
        "PAVETHERM_LARK_CLI", cfg.get("feishu_push", {}).get("lark_cli", "")
    )
    cfg["feishu_push"]["feishu_home_chat_id"] = os.environ.get(
        "PAVETHERM_FEISHU_CHAT_ID",
        cfg.get("alert_delivery", {}).get("feishu_home_chat_id", "")
    )

    # 4) 校验
    missing = _validate(cfg)
    if missing and strict:
        print("❌ PaveTherm Sentinel 配置缺失:", file=sys.stderr)
        for m in missing:
            print(f"   - {m}", file=sys.stderr)
        print(f"\n请运行引导脚本补全配置:", file=sys.stderr)
        print(f"   python3 {SKILL_ROOT}/scripts/install.py", file=sys.stderr)
        sys.exit(2)

    cfg["_missing_keys"] = missing
    return cfg


# ====== 兼容旧代码 ======
def load_alert_config_compat() -> dict:
    """兼容 alert.py 旧版的 load_alert_config 接口 (签名不变)"""
    cfg = load_config(strict=False)
    return cfg.get("alert", {})


if __name__ == "__main__":
    # CLI 自检: 跑一下看看 config 是否齐全
    print("🔍 PaveTherm Sentinel 配置自检\n")
    print(f"config.yaml: {CONFIG_PATH}")
    print(f"env file:    {ENV_PATH} ({'存在' if ENV_PATH.exists() else '不存在'})\n")

    cfg = load_config(strict=False)
    missing = cfg.get("_missing_keys", [])

    print("配置状态:")
    print(f"  geocoding.amap_key:           {'✅' if cfg['geocoding']['amap_key'] else '❌ 缺失'}")
    print(f"  search.tavily_key:            {'✅' if cfg['search']['tavily_key'] else '❌ 缺失 (可选)'}")
    print(f"  alert_delivery.feishu_chat:   {'✅' if cfg['alert_delivery'].get('feishu_home_chat_id') else '❌ 缺失'}")
    print(f"  feishu_push.lark_cli:         {'✅' if cfg['feishu_push'].get('lark_cli') else '❌ 缺失'}")

    print("\n阈值 (来自 config.yaml,无需 key):")
    for lvl, info in cfg.get("alert", {}).get("levels", {}).items():
        rng = f"≤ {info.get('max', info.get('min', '?'))}°C"
        print(f"  {info.get('label', lvl):15s} {rng}")

    if missing:
        print(f"\n⚠️ {len(missing)} 项缺失, 部分功能可能受限")
        print(f"运行: python3 scripts/install.py 完成首次配置")
    else:
        print("\n✅ 所有 key 已配置, skill 可正常使用")
