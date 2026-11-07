"""FFmpeg 合并（Phase 2/3）：音视频分离流合并为单一媒体文件。

- 使用系统 PATH 中的 ffmpeg（不自动安装、不改系统配置）。
- 输出到临时文件，成功后才替换最终文件；失败绝不触碰已下载的流文件。
- 安全说明：子进程以参数列表 + shell=False 方式执行（无 shell 解释面）；
  输入文件执行前校验存在；输出写入固定临时名。
"""

from __future__ import annotations

import os
import shutil
import subprocess


class MergeError(Exception):
    pass


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def ffmpeg_version() -> str:
    exe = ffmpeg_path()
    if not exe:
        return "未安装"
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                             timeout=10, shell=False)
        return (out.stdout or "").splitlines()[0][:80]
    except Exception:  # noqa: BLE001
        return "不可用"


def mux_streams(video_path: str, audio_path: str, out_path: str, *, timeout: float = 600.0) -> str:
    """将视频流与音频流无损合并（-c copy）。返回输出文件路径。"""
    exe = ffmpeg_path()
    if not exe:
        raise MergeError("未找到 ffmpeg：请安装并加入 PATH（本项目不自动安装）")
    if not os.path.isfile(video_path) or not os.path.isfile(audio_path):
        raise MergeError("待合并的流文件不存在")
    tmp_out = out_path + ".clipharbor.merge.tmp"
    try:
        os.remove(tmp_out)
    except OSError:
        pass
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y",
             "-i", video_path, "-i", audio_path,
             "-c", "copy", "-movflags", "+faststart",
             "-f", "mp4",  # 临时输出名不含 mp4 扩展名，必须显式指定封装器
             tmp_out],
            capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MergeError(f"ffmpeg 合并超时（>{timeout:.0f}s）") from exc
    if proc.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
        detail = (proc.stderr or "").strip().splitlines()[-3:]
        raise MergeError("ffmpeg 合并失败: " + (" | ".join(detail) or f"exit={proc.returncode}"))
    os.replace(tmp_out, out_path)
    return out_path
