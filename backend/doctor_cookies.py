"""一键自检：B 站 Cookie 导出环境诊断。

用法（在你的项目 backend 目录下）：
    python doctor_cookies.py

它会检查：
  1. Python 环境与依赖安装情况
  2. 管理员权限
  3. 浏览器进程是否占用 Cookie 数据库
  4. Cookie 数据库可读性 + 解密能力
  5. 给出针对性的下一步操作
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def line(t=""):
    print(t)


def check_python():
    line("=" * 66)
    line("[1] Python 环境")
    line(f"    解释器 : {sys.executable}")
    line(f"    版本   : {sys.version.split()[0]}")
    if "workbuddy" in sys.executable.lower():
        line("    ⚠️  当前使用的是 WorkBuddy 内置 Python，可能与后端服务不是同一个环境！")
    mods = ["browser_cookie3", "yt_dlp", "curl_cffi", "static_ffmpeg"]
    for m in mods:
        try:
            mod = __import__(m)
            ver = getattr(mod, "__version__", "?")
            line(f"    ✅ {m:18} {ver}")
        except ImportError:
            line(f"    ❌ {m:18} 未安装")


def check_admin():
    line()
    line("[2] 权限")
    if os.name != "nt":
        line("    非 Windows，跳过")
        return
    try:
        import ctypes

        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        line(f"    管理员 : {'是 ✅' if admin else '否 ❌ (读取加密 Cookie 需要)'}")
    except Exception as e:
        line(f"    检测失败: {e}")


def check_processes():
    line()
    line("[3] 浏览器进程占用")
    for exe in ("chrome.exe", "msedge.exe"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            running = exe.lower() in out.lower()
            line(f"    {exe:14} {'运行中 ⚠️ (会锁定 Cookie 数据库)' if running else '未运行 ✅'}")
        except Exception as e:
            line(f"    {exe:14} 查询失败: {e}")


def check_db():
    line()
    line("[4] Cookie 数据库可读性")
    specs = {
        "Chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data",
        "Edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
    }
    for name, raw in specs.items():
        ud = os.path.expandvars(raw)
        cands = [
            os.path.join(ud, "Default", "Network", "Cookies"),
            os.path.join(ud, "Default", "Cookies"),
        ]
        src = next((c for c in cands if os.path.exists(c)), None)
        if not src:
            line(f"    {name}: 未找到 Cookies 文件（未安装或未使用过？）")
            continue
        size = os.path.getsize(src)
        tmp = os.path.join(tempfile.gettempdir(), f"_doc_{name}.db")
        ok = False
        try:
            shutil.copy2(src, tmp)
            ok = True
        except Exception:
            # 尝试共享读
            ok = _shared_copy(src, tmp)
        if not ok:
            line(f"    {name}: ❌ 无法复制（【浏览器正在运行】独占此文件）→ 请完全退出 {name}")
            continue
        try:
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%bilibili%'")
            n = cur.fetchone()[0]
            cur.execute(
                "SELECT name FROM cookies WHERE host_key LIKE '%bilibili%'"
            )
            names = {r[0] for r in cur.fetchall()}
            con.close()
            has = [k for k in ("SESSDATA", "bili_jct", "DedeUserID") if k in names]
            line(f"    {name}: ✅ 可读，bilibili cookie {n} 条")
            line(f"        登录字段: {has if has else '❌ 无（说明未登录 B 站）'}")
        except Exception as e:
            line(f"    {name}: 数据库解析失败 {e}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _shared_copy(src, dst):
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        CreateFileW = ctypes.windll.kernel32.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        h = CreateFileW(src, 0x80000000, 7, None, 3, 0x80, None)
        if h == wintypes.HANDLE(-1).value:
            return False
        try:
            buf = ctypes.create_string_buffer(1024 * 128)
            read = wintypes.DWORD(0)
            chunks = []
            ReadFile = ctypes.windll.kernel32.ReadFile
            while True:
                if not ReadFile(h, buf, len(buf), ctypes.byref(read), None) or read.value == 0:
                    break
                chunks.append(buf.raw[: read.value])
            with open(dst, "wb") as f:
                f.write(b"".join(chunks))
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return False


def main():
    line()
    line("  B 站 Cookie 导出环境自检")
    line()
    check_python()
    check_admin()
    check_processes()
    check_db()
    line()
    line("=" * 66)
    line("结论与建议：")
    line("  · 若 [3] 显示浏览器运行中 → 完全退出浏览器后重跑 export_cookies.py")
    line("  · 若 [4] 显示可读且有 SESSDATA → 直接跑 export_cookies.py 即可成功")
    line("  · 若 [4] 显示可读但无登录字段 → 请先在浏览器登录 B 站")
    line("  · 若 [1] 提示用的是 WorkBuddy 内置 Python → 换成项目实际使用的 Python 再跑")
    line("  · 以上都正常仍失败 → 用浏览器扩展「Get cookies.txt LOCALLY」手动导出")
    line("=" * 66)


if __name__ == "__main__":
    main()
