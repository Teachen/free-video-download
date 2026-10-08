"""直接从浏览器 Cookie 数据库导出 B 站 cookies.txt（不依赖 browser_cookie3）。

相比 export_cookies.py，本脚本：
  1. 用 Win32 共享读方式复制数据库 → **不用关闭浏览器**（若仍失败会提示你关）
  2. 用 Windows DPAPI（CryptUnprotectData）自行解密 cookie 值
  3. 自动从 Local State 读取加密密钥（支持 v10 / v11 及 Chrome 的 App-Bound v20 降级尝试）
  4. 支持 Chrome / Edge，自动挑有 B 站登录态的那个

用法（在 backend 目录下，建议管理员终端）：
    python export_cookies_direct.py                # 自动 Chrome -> Edge
    python export_cookies_direct.py --browser edge
    python export_cookies_direct.py --out cookies.txt

依赖：仅需标准库 + pycryptodome（用于 AES-GCM 解密）
    pip install pycryptodome
"""

import argparse
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes

OUTPUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
CRITICAL_FIELDS = ("SESSDATA", "bili_jct", "DedeUserID")

BROWSERS = {
    "chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data",
    "edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
    "brave": r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",
}


# --------------------------------------------------------------------------- #
# 1. 共享读复制（浏览器运行时也能复制）
# --------------------------------------------------------------------------- #
def copy_shared(src: str, dst: str) -> bool:
    if os.name != "nt":
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            return False
    try:
        k = ctypes.windll.kernel32
        CreateFileW = k.CreateFileW
        CreateFileW.restype = wintypes.HANDLE
        CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        INVALID = wintypes.HANDLE(-1).value
        h = CreateFileW(src, 0x80000000, 0x7, None, 3, 0x80, None)
        if h == INVALID:
            return False
        try:
            buf = ctypes.create_string_buffer(1024 * 256)
            read = wintypes.DWORD(0)
            chunks = []
            ReadFile = k.ReadFile
            while True:
                if not ReadFile(h, buf, len(buf), ctypes.byref(read), None) or read.value == 0:
                    break
                chunks.append(buf.raw[: read.value])
            with open(dst, "wb") as f:
                f.write(b"".join(chunks))
            return True
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 2. DPAPI 解密
# --------------------------------------------------------------------------- #
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data: bytes) -> bytes | None:
    """用 CryptUnprotectData 解密（对应 Chrome 的 DPAPI 加密层）。"""
    try:
        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                                   ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def get_master_key(user_data: str) -> bytes | None:
    """从 Local State 读取并解密 AES 主密钥（v10/v11 有效）。"""
    local_state = os.path.join(user_data, "Local State")
    if not os.path.exists(local_state):
        return None
    tmp = local_state + f".tmp{os.getpid()}"
    if not copy_shared(local_state, tmp):
        try:
            shutil.copy2(local_state, tmp)
        except Exception:
            return None
    try:
        with open(tmp, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    enc_key_b64 = state.get("os_crypt", {}).get("encrypted_key")
    if not enc_key_b64:
        return None
    try:
        enc_key = base64.b64decode(enc_key_b64)
    except Exception:
        return None
    if enc_key.startswith(b"DPAPI"):
        enc_key = enc_key[5:]
    return dpapi_unprotect(enc_key)


def decrypt_value(enc: bytes, master_key: bytes | None) -> str | None:
    """解密单条 cookie 值。支持 v10/v11(AES-GCM) 与旧版 DPAPI。"""
    if not enc:
        return None
    # 旧版：直接 DPAPI
    if not enc.startswith(b"v1") and not enc.startswith(b"v2"):
        out = dpapi_unprotect(enc)
        return out.decode("utf-8", "ignore") if out else None

    version = enc[:3]
    if version in (b"v10", b"v11"):
        if not master_key:
            return None
        try:
            from Crypto.Cipher import AES  # pycryptodome
        except ImportError:
            print("  [提示] 需要 pycryptodome 解密：pip install pycryptodome")
            return None
        nonce = enc[3:15]
        ciphertext = enc[15:-16]
        tag = enc[-16:]
        try:
            cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8", "ignore")
        except Exception:
            return None

    # v20 = App-Bound Encryption，标准 DPAPI 无法解密
    if version == b"v20":
        return None
    return None


# --------------------------------------------------------------------------- #
# 3. 读取并导出
# --------------------------------------------------------------------------- #
def extract(browser: str, out_path: str) -> int:
    print("=" * 66)
    print(f"浏览器 : {browser}")
    user_data = os.path.expandvars(BROWSERS[browser])
    if not os.path.isdir(user_data):
        print(f"[错误] 未找到目录：{user_data}")
        return 1
    print(f"数据目录: {user_data}")

    db_candidates = [
        os.path.join(user_data, "Default", "Network", "Cookies"),
        os.path.join(user_data, "Default", "Cookies"),
    ]
    src = next((c for c in db_candidates if os.path.exists(c)), None)
    if not src:
        print("[错误] 未找到 Cookies 数据库")
        return 1

    tmp = os.path.join(tempfile.gettempdir(), f"_direct_{browser}.db")
    if not copy_shared(src, tmp):
        print("[错误] 无法复制数据库。请【完全退出浏览器】后重试：")
        print(f"       任务管理器结束所有 {browser}.exe 进程")
        return 1
    print("数据库 : 已复制（共享读）✅")

    master_key = get_master_key(user_data)
    print(f"主密钥 : {'已解密 ✅' if master_key else '未获取（v20 App-Bound 或权限不足）⚠️'}")

    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT host_key, name, value, encrypted_value, path, "
            "is_secure, expires_utc FROM cookies WHERE host_key LIKE '%bilibili%'"
        )
        rows = cur.fetchall()
        con.close()
    except Exception as e:
        print(f"[错误] 读取数据库失败：{e}")
        return 1
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if not rows:
        print("[错误] 数据库中没有 bilibili 相关 Cookie → 请先在浏览器登录 B 站")
        return 1
    print(f"记录数 : 找到 {len(rows)} 条 bilibili Cookie")

    lines = ["# Netscape HTTP Cookie File", "# export_cookies_direct.py", ""]
    got = {}
    failed = 0
    for host, name, value, enc, path, secure, expires in rows:
        val = value or ""
        if not val and enc:
            val = decrypt_value(bytes(enc), master_key) or ""
        if not val:
            failed += 1
            continue
        domain = host or ".bilibili.com"
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        sec = "TRUE" if secure else "FALSE"
        exp = 0
        if expires:
            # Chrome 存的是 1601-01-01 起的微秒
            exp = int(expires / 1_000_000 - 11644473600) if expires > 10_000_000_000 else int(expires)
        lines.append(f"{domain}\t{flag}\t{path or '/'}\t{sec}\t{exp}\t{name}\t{val}")
        got[name] = val

    if not got:
        print(f"[错误] {failed} 条记录全部解密失败。")
        print("       原因多为 Chrome 127+ 的 App-Bound Encryption(v20)。")
        print("       解决办法：改用浏览器扩展「Get cookies.txt LOCALLY」手动导出。")
        return 1

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"成功   : 写出 {len(got)} 条（失败 {failed} 条）→ {out_path}")
    have = [k for k in CRITICAL_FIELDS if k in got]
    missing = [k for k in CRITICAL_FIELDS if k not in got]
    print(f"登录字段: {have if have else '无'}")
    if missing:
        print(f"[警告] 缺少 {missing}，可能未登录 B 站或这些字段解密失败。")
        return 1
    print("\n✅ 导出成功！重启后端服务即可生效。")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="直接从浏览器数据库导出 B 站 Cookie")
    p.add_argument("--browser", default="", help="chrome / edge / brave")
    p.add_argument("--out", default=OUTPUT_DEFAULT)
    a = p.parse_args()

    targets = [a.browser] if a.browser else ["chrome", "edge"]
    for b in targets:
        if b not in BROWSERS:
            print(f"[跳过] 不支持的浏览器：{b}")
            continue
        try:
            code = extract(b, a.out)
        except Exception as e:
            print(f"[异常] {b}: {e}")
            code = 1
        if code == 0:
            return 0
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
