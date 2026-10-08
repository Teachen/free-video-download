"""从本机浏览器导出 B 站 Cookie 为 cookies.txt（Netscape 格式，供 yt-dlp 使用）。

B 站对未登录请求会返回 412 或 "No video formats found"，
需要携带登录态 Cookie 才能正常解析和下载。本脚本提供三级方案：

    方案 A（推荐）：browser_cookie3 自动读取
    方案 B（兜底）：手工指定已导出的 cookies.txt 路径做校验/转换
    方案 C（终极）：浏览器扩展「Get cookies.txt LOCALLY」手动导出

用法：
    python export_cookies.py                     # 自动尝试 Chrome -> Edge -> Firefox
    python export_cookies.py --browser edge      # 指定浏览器
    python export_cookies.py --browser chrome --profile "Profile 1"
    python export_cookies.py --out cookies.txt   # 指定输出

前置依赖：
    pip install browser_cookie3

⚠️ 重要（Windows + 新版 Chrome）：
    自 Chrome 127 起引入了 App-Bound Encryption，即使管理员权限也常解密失败。
    本脚本会把数据库【先复制到临时目录】再解析（避开文件锁），
    若仍失败，请使用方案 C 手动导出，成功率最高。
"""

import argparse
import os
import shutil
import sys
import tempfile

OUTPUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")

# 关键登录态字段（缺一不可，否则 B 站仍会拒绝）
CRITICAL_FIELDS = ("SESSDATA", "bili_jct", "DedeUserID")


# --------------------------------------------------------------------------- #
# 权限与依赖检查
# --------------------------------------------------------------------------- #
def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def print_env_banner() -> None:
    """打印环境信息，方便排查。"""
    print("=" * 64)
    print(f"Python : {sys.version.split()[0]}  ({sys.executable})")
    print(f"管理员 : {'是' if is_admin() else '否'}")
    try:
        import browser_cookie3  # noqa: F401

        print("依赖   : browser_cookie3 已安装")
    except ImportError:
        print("依赖   : browser_cookie3 【未安装】")
    print("=" * 64)


# --------------------------------------------------------------------------- #
# 手工解析一个浏览器（先复制数据库避开文件锁）
# --------------------------------------------------------------------------- #
_BROWSER_SPECS = {
    "chrome": {
        "user_data": r"%LOCALAPPDATA%\Google\Chrome\User Data",
        "process": "chrome.exe",
    },
    "edge": {
        "user_data": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
        "process": "msedge.exe",
    },
}


def _expand(path: str) -> str:
    return os.path.expandvars(path)


def _copy_with_shared_read(src: str, dst: str) -> bool:
    """Windows 下以「共享读」方式复制被占用的文件。

    Chrome/Edge 运行时会以 FILE_SHARE_READ 打开 Cookies 数据库，
    普通 shutil.copy 会报 [WinError 32]。
    这里用 Win32 CreateFileW 显式声明共享标志，即可在浏览器运行时复制。
    非 Windows 平台回退到普通复制。
    """
    if os.name != "nt":
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            return False

    try:
        import ctypes
        from ctypes import wintypes

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        FILE_SHARE_DELETE = 0x00000004
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80

        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        handle = CreateFileW(
            src,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            return False
        try:
            chunks = []
            buf = ctypes.create_string_buffer(1024 * 256)
            bytes_read = wintypes.DWORD(0)
            ReadFile = ctypes.windll.kernel32.ReadFile
            while True:
                ok = ReadFile(handle, buf, len(buf), ctypes.byref(bytes_read), None)
                if not ok or bytes_read.value == 0:
                    break
                chunks.append(buf.raw[: bytes_read.value])
            with open(dst, "wb") as f:
                f.write(b"".join(chunks))
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception as e:
        print(f"[警告] 共享读复制失败：{e}")
        return False


def copy_cookies_db_to_temp(user_data_dir: str) -> str | None:
    """把 <user_data>/Default/Network/Cookies 复制到临时文件。

    Chrome/Edge 运行时会对该 SQLite 文件加锁，普通复制会 [WinError 32]。
    这里优先用共享读方式复制，可在浏览器【不关闭】的情况下完成。
    """
    candidates = [
        os.path.join(user_data_dir, "Default", "Network", "Cookies"),
        os.path.join(user_data_dir, "Default", "Cookies"),  # 老版本
    ]
    for src in candidates:
        if not os.path.exists(src):
            continue
        tmp = os.path.join(tempfile.gettempdir(), f"_cookies_copy_{os.getpid()}.db")
        if _copy_with_shared_read(src, tmp):
            return tmp
        # 兜底：普通复制
        try:
            shutil.copy2(src, tmp)
            return tmp
        except Exception as e:
            print(f"[警告] 复制 Cookie 数据库失败（浏览器可能正在占用）：{e}")
            return None
    return None


def collect_via_browser_cookie3(browser: str, profile: str | None):
    """用 browser_cookie3 加载指定浏览器的 B 站 Cookie，返回 cookie 列表。"""
    try:
        import browser_cookie3
    except ImportError:
        return None

    loaders = {
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
        "opera": browser_cookie3.opera,
    }
    loader = loaders.get(browser)
    if not loader:
        return None

    kwargs = {"domain_name": ".bilibili.com"}
    if profile:
        kwargs["profile"] = profile

    last_err = None
    for attempt in range(3):
        try:
            jar = loader(**kwargs)
            cookies = list(jar)
            if cookies:
                return cookies
            last_err = "返回 0 条 Cookie（可能是浏览器占用数据库或加密不受支持）"
        except Exception as e:
            last_err = str(e)[:120]
        # 只在无法读取时重试一次，避免无意义循环
        if attempt == 1 and last_err and "admin" in last_err.lower():
            break

    print(f"[跳过] {browser}: {last_err}")
    return None


def _scan_db_directly(user_data_dir: str):
    """直接解析 Cookie 数据库（不依赖 browser_cookie3 的解密）。

    仅用于诊断：告诉我们数据库里到底有多少条 bilibili Cookie、
    以及是否有 SESSDATA，从而区分「读不到」与「解密失败」。
    """
    import sqlite3

    db = copy_cookies_db_to_temp(user_data_dir)
    if not db:
        return None
    try:
        # 用只读模式 + URI，避免触发写入
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT host_key, name, length(encrypted_value) "
            "FROM cookies WHERE host_key LIKE '%bilibili%'"
        )
        rows = cur.fetchall()
        con.close()
        return rows
    except Exception as e:
        print(f"[诊断] 直接读取数据库失败：{e}")
        return None
    finally:
        try:
            os.remove(db)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 写出 Netscape cookies.txt
# --------------------------------------------------------------------------- #
def write_netscape(cookies, out_path: str) -> int:
    """cookies: 可迭代对象，元素需具备 domain/name/value/path/secure/expires。

    兼容 http.cookiejar.Cookie 与 browser_cookie3 返回的对象。
    """
    lines = ["# Netscape HTTP Cookie File", "# 由 export_cookies.py 从浏览器导出", ""]
    count = 0
    for c in cookies:
        domain = getattr(c, "domain", None) or ".bilibili.com"
        name = getattr(c, "name", "")
        value = getattr(c, "value", "")
        if not name:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = getattr(c, "path", None) or "/"
        secure = "TRUE" if getattr(c, "secure", False) else "FALSE"
        expires = getattr(c, "expires", None)
        expires = int(expires) if expires else 0
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        count += 1

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return count


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def export(browser: str, out_path: str, profile: str | None = None) -> int:
    print_env_banner()

    try:
        import browser_cookie3  # noqa: F401
    except ImportError:
        print("[错误] 缺少依赖，请先执行：pip install browser_cookie3")
        print("       或改用方案 C（浏览器扩展手动导出），见文末。")
        return 1

    browsers = [browser] if browser else ["chrome", "edge", "firefox"]
    cookies = None
    used = None

    for name in browsers:
        got = collect_via_browser_cookie3(name, profile)
        if got:
            cookies = got
            used = name
            break
        # bonus：Chrome/Edge 静默失败时给出诊断
        spec = _BROWSER_SPECS.get(name)
        if spec:
            rows = _scan_db_directly(_expand(spec["user_data"]))
            if rows:
                has_sess = any(r[1] == "SESSDATA" for r in rows)
                print(
                    f"       诊断：数据库中共有 {len(rows)} 条 bilibili Cookie"
                    f"{'（含 SESSDATA）' if has_sess else '（无 SESSDATA，可能未登录 B 站）'}，"
                    "说明是【解密失败】而非读不到。"
                )

    if not cookies:
        print_manual_guide()
        return 1

    names = {getattr(c, "name", "") for c in cookies}
    missing = [f for f in CRITICAL_FIELDS if f not in names]
    if missing:
        print(f"[警告] 缺少关键登录字段：{', '.join(missing)}")
        print("       通常意味着【未登录 B 站】。请先在浏览器登录 https://www.bilibili.com")
        print(f"       然后重新运行本脚本。仍要写入 {out_path}（可作备用）。")

    count = write_netscape(cookies, out_path)
    print(f"[成功] 从 {used} 导出 {count} 条 Cookie")
    print(f"       文件：{out_path}")
    if not missing:
        print("\n下一步：重启后端服务即可生效。")
        return 0
    print("\n注意：登录字段不全，解析可能仍失败。建议按上方提示先登录 B 站再导出。")
    return 0


def print_manual_guide() -> None:
    print()
    print("=" * 64)
    print("[失败] 未能自动读取到 B 站 Cookie。")
    print()
    print("请按顺序排查：")
    print("  1) 【完全退出浏览器】Chrome/Edge 必须彻底关闭（含后台进程、托盘图标）")
    print("     任务管理器结束所有 chrome.exe / msedge.exe 后重试")
    print("  2) 【以管理员身份】运行终端（当前管理员状态见上方 banner）")
    print("  3) 确认已在浏览器【登录过 B 站】https://www.bilibili.com")
    print("  4) 若仍失败 → 多为 Chrome 127+ App-Bound 加密不受支持，请用方案 C")
    print()
    print("─" * 64)
    print("方案 C（成功率最高）：浏览器扩展手动导出")
    print("  1. 浏览器安装扩展「Get cookies.txt LOCALLY」")
    print("     Chrome 商店 / Edge 商店直接搜该名字即可")
    print("  2. 打开并登录 https://www.bilibili.com")
    print("  3. 点击扩展图标 → Export → 保存文件为 cookies.txt")
    print(f"  4. 把文件放到：{OUTPUT_DEFAULT}")
    print("  5. 重启后端服务")
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 B 站 Cookie 为 cookies.txt")
    parser.add_argument("--browser", default="", help="chrome / edge / firefox / brave / opera")
    parser.add_argument("--profile", default=None, help='Chrome 多用户目录名，如 "Profile 1"')
    parser.add_argument("--out", default=OUTPUT_DEFAULT, help="输出文件路径")
    args = parser.parse_args()
    return export(args.browser, args.out, args.profile)


if __name__ == "__main__":
    sys.exit(main())
