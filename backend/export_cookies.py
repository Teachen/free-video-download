"""从本机浏览器导出 B 站 Cookie 为 cookies.txt。

B 站对未登录请求会返回 412 或 "No video formats found"，
需要携带登录态 Cookie 才能正常解析和下载。

用法：
    python export_cookies.py                # 自动尝试 Chrome -> Edge
    python export_cookies.py --browser edge # 指定浏览器
    python export_cookies.py --out cookies.txt

前置依赖：
    pip install browser_cookie3
"""

import argparse
import os
import sys

OUTPUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def _preflight_check() -> None:
    """提前检测管理员权限，给出可操作的提示"""
    if os.name != "nt":
        return
    try:
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return
    if not is_admin:
        print()
        print("=" * 60)
        print("提示：当前终端【不是管理员权限】")
        print("     Windows 下读取浏览器加密 Cookie 需要管理员权限，")
        print("     否则会报 'This operation requires admin'。")
        print()
        print("     请右键终端 → 以管理员身份运行，再执行本脚本。")
        print("=" * 60)
        print()


def export(browser: str, out_path: str) -> int:
    try:
        import browser_cookie3
    except ImportError:
        print("[错误] 缺少依赖，请先执行：pip install browser_cookie3")
        return 1

    # 提前检测权限/浏览器占用，给出明确指引而不是直接失败
    _preflight_check()

    loaders = {
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
        "opera": browser_cookie3.opera,
    }

    browsers = [browser] if browser else ["chrome", "edge", "firefox"]
    jar = None
    used = None

    for name in browsers:
        loader = loaders.get(name)
        if not loader:
            continue
        try:
            jar = loader(domain_name=".bilibili.com")
            if len(jar) > 0:
                used = name
                break
        except Exception as e:
            print(f"[跳过] {name}: {str(e)[:80]}")

    if jar is None or len(jar) == 0:
        print()
        print("[失败] 未从浏览器读取到 B 站 Cookie。常见原因与解决办法：")
        print("  1. 权限不足 → 以【管理员身份】重新打开终端再运行本脚本")
        print("  2. 浏览器正在运行占用 Cookies 数据库 → 完全退出 Chrome / Edge 后重试")
        print("  3. 未登录 B 站 → 先在浏览器登录 https://www.bilibili.com")
        print()
        print("  备选方案：手动导出")
        print("  ─ 浏览器安装扩展「Get cookies.txt LOCALLY」")
        print("  ─ 登录 B 站后导出，保存为 backend/cookies.txt")
        return 1

    # 检查关键登录态字段
    names = {c.name for c in jar}
    critical = {"SESSDATA", "bili_jct", "DedeUserID"}
    missing = critical - names
    if missing:
        print(f"[警告] 缺少关键登录字段：{', '.join(missing)}")
        print("       可能未登录 B 站，解析仍可能失败。")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# 由 export_cookies.py 从浏览器导出\n\n")
        for c in jar:
            domain = c.domain or ".bilibili.com"
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if c.secure else "FALSE"
            expires = int(c.expires) if c.expires else 0
            f.write(
                f"{domain}\t{flag}\t{c.path or '/'}\t{secure}\t"
                f"{expires}\t{c.name}\t{c.value}\n"
            )

    print(f"[成功] 从 {used} 导出 {len(jar)} 条 Cookie")
    print(f"       文件：{out_path}")
    print("\n下一步：重启后端服务即可生效。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 B 站 Cookie 为 cookies.txt")
    parser.add_argument("--browser", default="", help="chrome / edge / firefox")
    parser.add_argument("--out", default=OUTPUT_DEFAULT, help="输出文件路径")
    args = parser.parse_args()
    return export(args.browser, args.out)


if __name__ == "__main__":
    sys.exit(main())
