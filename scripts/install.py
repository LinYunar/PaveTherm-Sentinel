#!/usr/bin/env python3
"""
PaveTherm Sentinel - 首次安装引导
=================================

功能:
  1. 检查 ~/.hermes/secrets/pavetherm-sentinel.env 是否就绪
  2. 缺哪项引导用户填 (高德 key / Tavily key / 飞书 chat_id / lark-cli 路径)
  3. 写入文件并 chmod 600
  4. 跑一次配置自检 + 一次最小 query 自检 (确保 skill 能跑)

支持的非交互模式:
  python3 install.py --non-interactive
  (不会主动问,而是把缺项列出来,适合 CI / 远程引导)
"""

import os
import sys
import stat
from pathlib import Path
from typing import Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = Path.home() / ".hermes" / "secrets" / "pavetherm-sentinel.env"
ENV_DIR = ENV_PATH.parent

# ====== 4 个用户必须自备的 key 链接 ======
KEY_HINTS = {
    "PAVETHERM_AMAP_KEY": {
        "label": "高德地图 Web API key",
        "url": "https://lbs.amap.com/dev/key/app",
        "free": True,
        "note": "注册高德开放平台 → 创建应用 → 添加 Key (Web 服务 API 类型)",
    },
    "PAVETHERM_TAVILY_KEY": {
        "label": "Tavily AI 搜索 key (可选)",
        "url": "https://tavily.com/",
        "free": True,
        "note": "用于自动查道路建成/大修年份。可跳过 (无 key 则用默认老化和兜底数据)",
    },
    "PAVETHERM_FEISHU_CHAT_ID": {
        "label": "飞书机器人 chat_id",
        "url": "https://open.feishu.cn/document/server-docs/im-v1/chat-group/chat-id-introduction",
        "free": True,
        "note": "推送目标。格式 oc_xxxxxxx (私聊) 或 oc_xxxxxxx (群)。需要先把飞书机器人加到目标会话",
    },
    "PAVETHERM_LARK_CLI": {
        "label": "lark-cli 路径",
        "url": "https://open.feishu.cn/document/server-docs/cli/introduction",
        "free": True,
        "note": "安装命令: npm install -g @larksuite/cli (默认路径 ~/.npm-global/bin/lark-cli)",
    },
}


def _read_existing_env() -> dict:
    """读现有 env 文件 (存在则读,不存在返回空 dict)"""
    if not ENV_PATH.exists():
        return {}
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _write_env(env: dict) -> None:
    """写 env 文件 + chmod 600"""
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PaveTherm Sentinel - 用户专属凭证",
        "# 所有者: " + os.environ.get("USER", "你") + " - 不要 commit,不要分享",
        "# 由 install.py 生成, chmod 600 保护",
        "",
    ]
    # 友好排版
    for key in ["PAVETHERM_AMAP_KEY", "PAVETHERM_TAVILY_KEY",
                "PAVETHERM_FEISHU_CHAT_ID", "PAVETHERM_LARK_CLI"]:
        hint = KEY_HINTS.get(key, {})
        lines.append(f"# --- {hint.get('label', key)} ---")
        lines.append(f"# 获取: {hint.get('url', '?')}")
        lines.append(f"{key}={env.get(key, '')}")
        lines.append("")
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)
    print(f"  ✅ 写入 {ENV_PATH} (chmod 600)")


def _prompt_for_key(key: str, current: str, non_interactive: bool) -> Optional[str]:
    """引导用户填一个 key, 返回最终值"""
    hint = KEY_HINTS.get(key, {})
    label = hint.get("label", key)
    url = hint.get("url", "")
    note = hint.get("note", "")

    if non_interactive:
        # 非交互模式: 不问,返回现有 (None 表示缺)
        return current or None

    print(f"\n{'='*60}")
    print(f"📝 {label} ({key})")
    print(f"{'='*60}")
    if note:
        print(f"   {note}")
    if url:
        print(f"   🔗 {url}")
    if current:
        print(f"\n   当前已有值: {current[:12]}...{current[-4:]} ({len(current)} 字符)")
        ans = input("   保留现有值? 回车=保留 / 输入新值覆盖: ").strip()
        if ans:
            return ans
        return current
    val = input(f"   请输入 (粘贴 key 后回车, 无 key 直接回车跳过): ").strip()
    return val if val else None


def _check_config_after(env: dict) -> list[str]:
    """写完 env 后, 用 config_loader 自检还缺什么"""
    try:
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        from config_loader import load_config
        cfg = load_config(strict=False)
        return cfg.get("_missing_keys", [])
    except Exception as e:
        return [f"config_loader 调用失败: {e}"]


def _smoke_test() -> bool:
    """最小 smoke test: 跑一次 query_point 看是否能拉到数据"""
    print("\n🧪 Smoke test: 拉一次成都三环路-成渝立交 验证链路")
    try:
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        from monitor import query_point
        r = query_point("cd_chengyulukou_001", days=1)
        if "error" in r:
            print(f"  ⚠️ query_point 失败: {r['error']}")
            return False
        cur = r.get("current", {})
        print(f"  ✅ 成功: 当前路表 {cur.get('pavement_temp')}°C / 气温 {cur.get('air_temp')}°C")
        return True
    except Exception as e:
        print(f"  ⚠️ 异常: {e}")
        return False


def main():
    print("🌡️  PaveTherm Sentinel - 首次安装引导")
    print("=" * 60)

    non_interactive = "--non-interactive" in sys.argv

    # 1) 读现有 env
    env = _read_existing_env()
    if env:
        print(f"✅ 检测到现有配置: {ENV_PATH}")
        print(f"   已有 {len(env)} 项 key")
    else:
        print(f"📂 准备写入新配置: {ENV_PATH}")

    # 2) 引导填 4 项 (按优先级: amap > feishu > lark > tavily)
    order = ["PAVETHERM_AMAP_KEY", "PAVETHERM_FEISHU_CHAT_ID",
             "PAVETHERM_LARK_CLI", "PAVETHERM_TAVILY_KEY"]
    if non_interactive:
        # 非交互: 只校验,不让用户输入
        missing_now = [k for k in order if not env.get(k)]
        if missing_now:
            print(f"\n⚠️ 非交互模式: {len(missing_now)} 项 key 缺失")
            for m in missing_now:
                print(f"  - {m} ({KEY_HINTS[m]['label']})")
                print(f"    获取: {KEY_HINTS[m]['url']}")
            print("\n请补全后重跑:")
            print(f"  python3 {SKILL_ROOT / 'scripts' / 'install.py'}")
            sys.exit(1)
        else:
            print("\n✅ 非交互模式: 4 项 key 全在")
    else:
        for key in order:
            val = _prompt_for_key(key, env.get(key, ""), non_interactive=False)
            if val is not None:
                env[key] = val

    # 3) 写入
    if not non_interactive:
        _write_env(env)

    # 4) 自检
    print("\n📋 配置自检:")
    missing = _check_config_after(env)
    if missing:
        print(f"  ⚠️ {len(missing)} 项缺失:")
        for m in missing:
            print(f"    - {m}")
    else:
        print("  ✅ 4 项 key 全部就绪")

    # 5) smoke test
    if not missing and not non_interactive:
        ok = _smoke_test()
        if not ok:
            print("\n⚠️ Smoke test 失败,但不影响 config 写入。请检查网络/firewall")
    elif missing:
        print("\n⏭️  跳过 smoke test (有缺失 key)")

    # 6) 收尾指引
    print("\n" + "=" * 60)
    print("🎉 安装引导完成")
    print("=" * 60)
    print(f"\n接下来你可以:")
    print(f"  # 单点查询 (推飞书)")
    print(f"  python3 {SKILL_ROOT}/scripts/monitor.py query cd_chengyulukou_001 --days 5 --send-feishu")
    print(f"")
    print(f"  # 批量查询 (成都 6 点)")
    print(f"  python3 {SKILL_ROOT}/scripts/monitor.py batch --filter city=成都 --export feishu")
    print(f"")
    print(f"  # 加新监测点")
    print(f"  python3 {SKILL_ROOT}/scripts/monitor.py add '成都天府广场' --pavement-color gray --age 5")
    print(f"")
    print(f"查看完整说明: cat {SKILL_ROOT}/README_INSTALL.md")


if __name__ == "__main__":
    main()
