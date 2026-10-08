import os
import re
import shutil
import yt_dlp
from typing import Optional

try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
except ImportError:  # 兼容旧版 yt-dlp
    ImpersonateTarget = None

# 浏览器 UA，用于规避视频网站的防盗链 / 风控校验
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 各平台的 Referer，防盗链校验的关键字段
_REFERER_MAP = {
    "bilibili": "https://www.bilibili.com/",
    "youtube": "https://www.youtube.com/",
    "douyin": "https://www.douyin.com/",
}


def _find_ffmpeg_path() -> Optional[str]:
    """查找 ffmpeg 可执行文件路径"""
    if shutil.which("ffmpeg"):
        return os.path.dirname(shutil.which("ffmpeg"))
    try:
        import static_ffmpeg
        paths = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
        return os.path.dirname(paths[0])
    except Exception:
        return None


def _has_impersonate() -> bool:
    """检测是否可用的 TLS 指纹伪装能力（需要新版 yt-dlp + curl_cffi）"""
    if ImpersonateTarget is None:
        return False
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


# 需要 TLS 指纹伪装才能绕过风控的平台（普通请求头无效）
_IMPERSONATE_TARGET = "chrome"

# B 站登录态凭证文件（Netscape 格式 cookies.txt），存在时自动加载
_BILIBILI_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")

# 需要登录态才能获取视频流的平台
_LOGIN_REQUIRED_PLATFORMS = ("bilibili", "douyin")


def _resolve_cookiefile(url: str) -> Optional[str]:
    """查找适用于该 URL 的 Cookie 文件。

    B 站等平台对未登录用户的请求会返回 412 或 "No video formats found"，
    若项目目录下存在 cookies.txt 则自动使用。
    """
    if os.path.exists(_BILIBILI_COOKIE_FILE):
        return _BILIBILI_COOKIE_FILE
    return None


def build_ydl_opts(url: str = "", **overrides) -> dict:
    """构建带防盗链规避能力的 yt-dlp 配置。

    视频网站（尤其 B 站）存在多重风控，逐层绕过：
    1. 校验 Referer / 浏览器 UA —— 通过 http_headers 注入
    2. 校验 TLS/JA3 指纹 —— 需 curl_cffi 做 impersonate 伪装（普通请求头无效）
    3. 校验登录态 —— 需 cookies.txt，否则返回 412 / No video formats found

    统一在这里注入，避免各调用点重复配置。
    """
    url_lower = url.lower()
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    for key, referer in _REFERER_MAP.items():
        if key in url_lower:
            headers["Referer"] = referer
            break
    else:
        headers["Referer"] = url or "https://www.bilibili.com/"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": headers,
    }

    # 关键 1：TLS 指纹伪装，破解风控拦截（412 Precondition Failed）
    if _has_impersonate():
        opts["impersonate"] = ImpersonateTarget(_IMPERSONATE_TARGET)

    # 关键 2：登录态 Cookie，破解 "No video formats found"
    if any(p in url_lower for p in _LOGIN_REQUIRED_PLATFORMS):
        cookie_file = _resolve_cookiefile(url)
        if cookie_file:
            opts["cookiefile"] = cookie_file

    opts.update(overrides)
    return opts


class VideoDownloader:
    """yt-dlp 封装层，提供视频解析、下载、直链获取能力"""

    DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

    def __init__(self):
        os.makedirs(self.DOWNLOAD_DIR, exist_ok=True)
        self.ffmpeg_path = _find_ffmpeg_path()
        self.has_ffmpeg = self.ffmpeg_path is not None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name)

    @staticmethod
    def _format_filesize(size: Optional[int]) -> str:
        if not size:
            return "未知大小"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f}KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        return f"{size / (1024 * 1024 * 1024):.2f}GB"

    @staticmethod
    def _format_duration(seconds: Optional[int]) -> str:
        if not seconds:
            return "00:00"
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def parse_video(self, url: str) -> dict:
        """解析视频信息，不下载文件"""
        ydl_opts = build_ydl_opts(url, extract_flat=False)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise ValueError("无法解析该链接")

        formats = self._extract_formats(info)
        platform = info.get("extractor", info.get("extractor_key", "Unknown"))

        return {
            "id": info.get("id", ""),
            "title": info.get("title", "未知标题"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "duration_string": self._format_duration(info.get("duration")),
            "uploader": info.get("uploader", info.get("channel", "未知")),
            "platform": platform,
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date", ""),
            "description": (info.get("description") or "")[:200],
            "formats": formats,
            "subtitles": list(info.get("subtitles", {}).keys()),
            "automatic_captions": list(info.get("automatic_captions", {}).keys())[:5],
        }

    def _extract_formats(self, info: dict) -> list:
        """从 yt-dlp info 中提取并整理可用格式"""
        raw_formats = info.get("formats", [])
        if not raw_formats:
            return []

        seen = set()
        results = []

        for f in raw_formats:
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            height = f.get("height")
            ext = f.get("ext", "mp4")

            has_video = vcodec and vcodec != "none"
            has_audio = acodec and acodec != "none"

            if not has_video:
                continue

            resolution = f"{f.get('width', '?')}x{height}" if height else "未知"
            filesize = f.get("filesize") or f.get("filesize_approx")
            size_label = self._format_filesize(filesize)

            if has_audio:
                label = f"{height}p {ext.upper()} ({size_label})"
                key = (height, ext, "av")
            else:
                label = f"{height}p {ext.upper()} (仅视频, {size_label})"
                key = (height, ext, "v")

            if key in seen:
                continue
            seen.add(key)

            results.append({
                "format_id": f.get("format_id", ""),
                "ext": ext,
                "resolution": resolution,
                "height": height or 0,
                "filesize": filesize,
                "filesize_approx": filesize,
                "vcodec": vcodec,
                "acodec": acodec if has_audio else None,
                "has_audio": has_audio,
                "label": label,
            })

        results.sort(key=lambda x: x["height"], reverse=True)

        # 若平台只提供「音视频分离」的流（如 B 站），追加一个合并选项。
        # 注意：合并需要 ffmpeg；无 ffmpeg 时使用 yt-dlp 选择器兜底语法，
        # 保证在缺少 ffmpeg 的机器上也能下载到「带音频的单文件」。
        if not any(r["has_audio"] for r in results) and results:
            best_video = results[0]
            if self.has_ffmpeg:
                merged_id = "bestvideo+bestaudio/best"
                merged_label = f"{best_video['height']}p 最佳 (视频+音频合并)"
            else:
                merged_id = (
                    "bestvideo+bestaudio/best[acodec!=none]/best"
                )
                merged_label = f"{best_video['height']}p 最佳 (无法合并，自动降级)"
            merged = {
                **best_video,
                "format_id": merged_id,
                "label": merged_label,
                "has_audio": True,
                "acodec": "merged",
            }
            results.insert(0, merged)

        return results[:15]

    @staticmethod
    def _fallback_format(format_id: str) -> str:
        """无 ffmpeg 时，把「需要合并」的 format_id 降级为「含音频的单文件」格式。

        原实现直接改写为 "best"：
          - 部分平台（B 站）没有 best 单文件 → Requested format is not available
        简单加 "/best[acodec!=none]" 也会失败：
          - B 站根本不提供带音频的单文件流，选择器仍会选中分离流 → 报错要求 ffmpeg

        正确做法：显式要求「同时含音视频的单文件」（带 + 号即分离流，必须先排除），
        用 acodec!=none & vcodec!=none 双重约束，再兜底到任意可用格式。
        """
        # 单文件约束：视频和音频同在一个文件里
        single_file = "best[vcodec!=none][acodec!=none]"

        if "+" not in format_id:
            return f"{format_id}/{single_file}/best"

        # 有 "+" 说明是分离流组合，取视频部分并按画质偏好选择单文件
        video_part = format_id.split("+")[0]
        height = None
        if video_part.isdigit():
            # 形如 30080，无法从 id 推画质，退回到「最佳单文件」
            return f"{single_file}/best"

        # 形如 bestvideo / worstvideo 等选择器
        return f"{format_id}/{single_file}/best"

    def download_video(self, url: str, format_id: str) -> dict:
        """下载视频到服务器临时目录，返回文件路径和元数据"""
        if not self.has_ffmpeg and ("+" in format_id or "bestvideo" in format_id):
            format_id = self._fallback_format(format_id)

        ydl_opts = build_ydl_opts(
            url,
            format=format_id,
            outtmpl=os.path.join(self.DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        )
        # 无 ffmpeg 时禁止合并，否则 yt-dlp 会尝试调用不存在的 ffmpeg
        if self.has_ffmpeg:
            ydl_opts["ffmpeg_location"] = self.ffmpeg_path
            ydl_opts["merge_output_format"] = "mp4"
        else:
            ydl_opts["format_sort"] = ["res", "ext:mp4:m4a"]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        if not info:
            raise ValueError("下载失败")

        title = self._sanitize_filename(info.get("title", "video"))
        ext = info.get("ext", "mp4")
        filename = f"{title}.{ext}"
        filepath = os.path.join(self.DOWNLOAD_DIR, filename)

        if not os.path.exists(filepath):
            prepared = ydl.prepare_filename(info)
            if os.path.exists(prepared):
                filepath = prepared
                filename = os.path.basename(prepared)
            else:
                for f in os.listdir(self.DOWNLOAD_DIR):
                    if title in f:
                        filepath = os.path.join(self.DOWNLOAD_DIR, f)
                        filename = f
                        break

        return {
            "filepath": filepath,
            "filename": filename,
            "title": info.get("title", "video"),
            "ext": ext,
        }

    def get_direct_url(self, url: str, format_id: str) -> dict:
        """获取视频直链"""
        ydl_opts = build_ydl_opts(url, format=format_id)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise ValueError("无法获取直链")

        direct_url = info.get("url")
        if not direct_url:
            requested = info.get("requested_formats")
            if requested and len(requested) > 0:
                direct_url = requested[0].get("url")

        if not direct_url:
            raise ValueError("该视频不支持直链下载，请使用服务端下载模式")

        return {
            "direct_url": direct_url,
            "ext": info.get("ext", "mp4"),
            "filesize": info.get("filesize") or info.get("filesize_approx"),
            "title": info.get("title", "video"),
        }
