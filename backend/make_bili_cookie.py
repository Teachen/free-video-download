"""从 cookies.txt 提取 B 站登录三要素，生成微信云托管控制台可直接粘贴的单行值。

适用场景：
    你已经用浏览器扩展「Get cookies.txt LOCALLY」导出了 cookies.txt，
    但不确定该往微信云托管的 BILI_COOKIE 里填什么。

用法（在 backend 目录下）：
    python make_bili_cookie.py                       # 默认读取 ./cookies.txt
    python make_bili_cookie.py --file D:/xxx.txt     # 指定文件
    python make_bili_cookie.py --raw "SESSDATA=x; bili_jct=y; DedeUserID=1"
"""

import argparse
import os
import sys

CRITICAL_FIELDS = ("SESSDATA", "bili_jct", "DedeUserID")
DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def parse_netscape(text: str) -> dict:
    """解析 Netscape 格式，返回 {名称: 值}（只保留 bilibili 相关）"""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            # 兼容 "#HttpOnly_.bilibili.com" 这种行
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, name, value = parts[0], parts[5], parts[6]
        if "bilibili" in domain.lower():
            values[name] = value
    return values


def parse_raw(text: str) -> dict:
    """解析 "k=v; k2=v2" 形式"""
    values = {}
    for pair in text.replace("\n", ";").split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, _, value = pair.partition("=")
            values[name.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="提取 B 站 Cookie 三要素")
    parser.add_argument("--file", default=DEFAULT_FILE, help="cookies.txt 路径")
    parser.add_argument("--raw", default="", help='直接传入 "k=v; k2=v2" 字符串')
    args = parser.parse_args()

    # 取值
    if args.raw:
        values = parse_raw(args.raw)
        source = "--raw 参数"
    else:
        if not os.path.exists(args.file):
            print(f"[错误] 找不到文件：{args.file}")
            print()
            print("请先导出 cookies.txt：")
            print("  · 浏览器扩展「Get cookies.txt LOCALLY」登录 B 站后导出")
            print("  · 或运行 python export_cookies_direct.py")
            return 1
        with open(args.file, encoding="utf-8", errors="ignore") as f:
            values = parse_netscape(f.read())
        source = args.file

    print("=" * 66)
    print(f"来源：{source}")
    print(f"共解析到 {len(values)} 个 bilibili Cookie 字段")
    print("=" * 66)

    missing = [k for k in CRITICAL_FIELDS if not values.get(k)]

    # 逐项展示
    for key in CRITICAL_FIELDS:
        val = values.get(key)
        if val:
            masked = val[:6] + "..." + val[-4:] if len(val) > 14 else val
            print(f"  ✅ {key:12} = {masked}")
        else:
            print(f"  ❌ {key:12} 缺失")

    if missing:
        print()
        print(f"[失败] 缺少关键字段：{', '.join(missing)}")
        print("       最常见原因：没有登录 B 站，或导出的不是 bilibili.com 的 Cookie。")
        print("       请登录 https://www.bilibili.com 后重新导出。")
        return 1

    line = "; ".join(f"{k}={values[k]}" for k in CRITICAL_FIELDS)

    print()
    print("=" * 66)
    print("【微信云托管】复制下面这一整行，粘贴到 BILI_COOKIE 的值里：")
    print("=" * 66)
    print()
    print(line)
    print()
    print("=" * 66)
    print("提示：")
    print("  · SESSDATA 通常 30 天过期，失效后需重新导出并更新此变量")
    print("  · 粘贴时注意不要带首尾空格或换行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
