"""Video downloading and timestamp trimming module using yt-dlp, FFmpeg, and MoviePy."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

from yt_processor import HighlightSegment
from utils import sanitize_filename, format_timestamp, format_bytes, strip_ansi


def get_ffmpeg_path() -> Optional[str]:
    """Find ffmpeg executable from PATH, standard system paths, or imageio_ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for loc in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        if os.path.exists(loc) and os.access(loc, os.X_OK):
            return loc
    try:
        import imageio_ffmpeg
        img_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if img_exe and os.path.exists(img_exe):
            return img_exe
    except Exception:
        pass
    return None


def ensure_ffmpeg() -> Optional[str]:
    """
    Ensure ffmpeg is available and callable.
    If ffmpeg is not already on PATH, creates a shim in a local .bin directory
    pointing to imageio_ffmpeg's binary and prepends it to os.environ['PATH'].
    Returns the resolved ffmpeg executable path, or None if unavailable.
    """
    exe = get_ffmpeg_path()
    if not exe:
        return None

    # If 'ffmpeg' is not directly executable by command name, create a shim
    if shutil.which("ffmpeg") is None:
        bin_dir = Path(__file__).resolve().parent / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim = bin_dir / "ffmpeg"
        if not shim.exists():
            try:
                os.symlink(exe, shim)
            except Exception:
                shutil.copy2(exe, shim)
                shim.chmod(0o755)
        cur_path = os.environ.get("PATH", "")
        if str(bin_dir) not in cur_path:
            os.environ["PATH"] = f"{bin_dir}:{cur_path}"
        return str(shim)
    return exe


# Initialize ffmpeg on import
ensure_ffmpeg()


def has_ffmpeg() -> bool:
    """Check if ffmpeg is installed and available on system PATH or via imageio-ffmpeg."""
    return ensure_ffmpeg() is not None


def download_full_video(
    url: str,
    output_dir: Path,
    max_height: int = 720,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Download the source YouTube video to `output_dir` at up to `max_height` resolution.
    Returns the Path to the downloaded video file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    def _hook(d: dict):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            pct = (downloaded / total) if total > 0 else 0.0
            speed_str = strip_ansi(d.get("_speed_str", "")).strip()
            speed_part = f" at {speed_str}" if speed_str else ""
            if total > 0:
                msg = f"Downloading video... {format_bytes(downloaded)} / {format_bytes(total)} ({int(pct * 100)}%{speed_part})"
            else:
                msg = f"Downloading video... {format_bytes(downloaded)}{speed_part}"
            progress_callback(min(0.99, pct), msg)
        elif progress_callback and d.get("status") == "finished":
            progress_callback(1.0, "Download finished, finalizing container...")

    ffmpeg_exe = ensure_ffmpeg()
    outtmpl = str(output_dir / "source_%(id)s.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "overwrites": False,         # Resume existing download if available
        "continuedl": True,          # Resume partial download
        "retries": 25,               # Auto-retry on connection drops
        "fragment_retries": 25,      # Auto-retry DASH fragments
        "file_access_retries": 5,
        "socket_timeout": 30,        # Don't hang on dropped sockets
        "http_chunk_size": 10485760, # 10 MB chunks to prevent YouTube CDN stream aborts
        "progress_hooks": [_hook],
    }

    if ffmpeg_exe:
        format_selector = (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best"
        )
        ydl_opts["format"] = format_selector
        ydl_opts["ffmpeg_location"] = ffmpeg_exe
        ydl_opts["merge_output_format"] = "mp4"
    else:
        # Fallback to single progressive stream to avoid requiring stream merging
        format_selector = (
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best[ext=mp4]/best"
        )
        ydl_opts["format"] = format_selector

    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "video")
        # Locate downloaded file
        expected_mp4 = output_dir / f"source_{video_id}.mp4"
        if expected_mp4.exists():
            return expected_mp4
        for candidate in output_dir.glob(f"source_{video_id}.*"):
            return candidate
        raise FileNotFoundError("Downloaded video file could not be found in output directory.")


def download_single_clip_section(
    url: str,
    output_path: Path,
    start_time: float,
    end_time: float,
    max_height: int = 720,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Download *only* the specific timestamp range [start_time, end_time] directly
    from YouTube using yt-dlp section downloading (byte-range streaming).
    Avoids downloading the full source video file, saving bandwidth and time.
    Returns the Path to the output clip.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = ensure_ffmpeg()

    def _hook(d: dict):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            pct = (downloaded / total) if total > 0 else 0.0
            speed_str = strip_ansi(d.get("_speed_str", "")).strip()
            speed_part = f" at {speed_str}" if speed_str else ""
            if total > 0:
                msg = f"Direct Stream-Trim: {format_bytes(downloaded)} / {format_bytes(total)} ({int(pct * 100)}%{speed_part})"
            else:
                msg = f"Direct Stream-Trim: {format_bytes(downloaded)}{speed_part}"
            progress_callback(min(0.99, pct), msg)
        elif progress_callback and d.get("status") == "finished":
            progress_callback(1.0, "Stream downloaded, finalizing MP4 clip...")

    import yt_dlp

    stem = output_path.stem
    outtmpl = str(output_path.parent / f"{stem}.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "overwrites": True,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
        "force_keyframes_at_cuts": True,
        "retries": 15,
        "fragment_retries": 15,
        "socket_timeout": 30,
        "progress_hooks": [_hook],
    }

    if ffmpeg_exe:
        ydl_opts["ffmpeg_location"] = ffmpeg_exe
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["format"] = (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best"
        )
    else:
        ydl_opts["format"] = (
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best[ext=mp4]/best"
        )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Check if exact output_path exists
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path

    # Check for any produced file with this stem (e.g. .mkv, .webm, or stem with tags)
    candidates = [
        f for f in output_path.parent.glob(f"{stem}*")
        if not f.name.endswith(".part") and f.is_file() and f.stat().st_size > 0
    ]
    if candidates:
        chosen = candidates[0]
        if chosen == output_path:
            return output_path
        if chosen.suffix.lower() == ".mp4":
            shutil.move(str(chosen), str(output_path))
            return output_path
        # If it has a different container like webm/mkv, remux to mp4
        if ffmpeg_exe:
            subprocess.run(
                [ffmpeg_exe, "-y", "-i", str(chosen), "-c", "copy", "-movflags", "+faststart", str(output_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if output_path.exists():
                try:
                    chosen.unlink()
                except Exception:
                    pass
                return output_path
        return chosen

    raise FileNotFoundError(f"Failed to produce stream-trimmed clip: {output_path.name}")


def download_and_trim_segments_direct(
    url: str,
    segments: List[HighlightSegment],
    clips_dir: Path,
    video_title: str = "video",
    max_height: int = 720,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[int, Path]:
    """
    Download each highlight segment directly from YouTube without downloading the full video.
    Returns mapping of segment.rank -> clip_path.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(video_title)
    results: Dict[int, Path] = {}
    total = len(segments)

    for idx, seg in enumerate(segments, start=1):
        start_tag = format_timestamp(seg.start_time).replace(":", "m") + "s"
        end_tag = format_timestamp(seg.end_time).replace(":", "m") + "s"
        clip_filename = f"{safe_title}_rank{seg.rank}_{start_tag}_to_{end_tag}.mp4"
        clip_path = clips_dir / clip_filename

        if progress_callback:
            progress_callback(
                idx,
                total,
                f"Directly streaming & trimming Clip #{seg.rank} ({seg.time_range_str}) [{idx}/{total}]...",
            )

        download_single_clip_section(
            url=url,
            output_path=clip_path,
            start_time=seg.start_time,
            end_time=seg.end_time,
            max_height=max_height,
        )
        results[seg.rank] = clip_path

    return results


def trim_clip_ffmpeg(
    source_video: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    fast_copy: bool = False,
) -> Path:
    """
    Trim a segment `[start_time, end_time]` from `source_video` into `output_path`.
    Uses FFmpeg if available (re-encoding with libx264/aac for frame-accurate cuts
    and browser compatibility, or stream copy if `fast_copy=True`), with a fallback
    to MoviePy if system ffmpeg CLI is unavailable.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, end_time - start_time)

    ffmpeg_exe = ensure_ffmpeg()
    if ffmpeg_exe:
        if fast_copy:
            cmd = [
                ffmpeg_exe, "-y",
                "-ss", f"{start_time:.3f}",
                "-i", str(source_video),
                "-t", f"{duration:.3f}",
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            cmd = [
                ffmpeg_exe, "-y",
                "-ss", f"{start_time:.3f}",
                "-i", str(source_video),
                "-t", f"{duration:.3f}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0 and output_path.exists():
            return output_path

    # Fallback to MoviePy if ffmpeg CLI fails (compatible with MoviePy 1.x and 2.x)
    try:
        from moviepy import VideoFileClip
    except ImportError:
        from moviepy.editor import VideoFileClip

    with VideoFileClip(str(source_video)) as clip:
        safe_end = min(clip.duration, end_time)
        if hasattr(clip, "subclipped"):
            subclip = clip.subclipped(start_time, safe_end)
        else:
            subclip = clip.subclip(start_time, safe_end)

        write_kwargs = {
            "codec": "libx264",
            "audio_codec": "aac",
            "temp_audiofile": str(output_path.with_suffix(".temp-audio.m4a")),
            "remove_temp": True,
            "logger": None,
        }
        import inspect
        if "verbose" in inspect.signature(subclip.write_videofile).parameters:
            write_kwargs["verbose"] = False

        subclip.write_videofile(str(output_path), **write_kwargs)
    return output_path


def trim_selected_segments(
    source_video: Path,
    segments: List[HighlightSegment],
    clips_dir: Path,
    video_title: str = "video",
    fast_copy: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[int, Path]:
    """
    Trim a list of `HighlightSegment` items from `source_video` and save each clip
    into `clips_dir`. Returns a dictionary mapping `segment.rank -> clip_path`.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(video_title)
    results: Dict[int, Path] = {}
    total = len(segments)

    for idx, seg in enumerate(segments, start=1):
        start_tag = format_timestamp(seg.start_time).replace(":", "m") + "s"
        end_tag = format_timestamp(seg.end_time).replace(":", "m") + "s"
        clip_filename = f"{safe_title}_rank{seg.rank}_{start_tag}_to_{end_tag}.mp4"
        clip_path = clips_dir / clip_filename

        if progress_callback:
            progress_callback(
                idx,
                total,
                f"Trimming Clip #{seg.rank} ({seg.time_range_str}) [{idx}/{total}]...",
            )

        trim_clip_ffmpeg(
            source_video=source_video,
            output_path=clip_path,
            start_time=seg.start_time,
            end_time=seg.end_time,
            fast_copy=fast_copy,
        )
        results[seg.rank] = clip_path

    return results


def create_clips_zip(clip_paths: Dict[int, Path], zip_output_path: Path) -> Path:
    """Bundle multiple trimmed `.mp4` clip files into a single `.zip` archive."""
    zip_output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rank, path in sorted(clip_paths.items()):
            if path and path.exists():
                zf.write(path, arcname=path.name)
    return zip_output_path
