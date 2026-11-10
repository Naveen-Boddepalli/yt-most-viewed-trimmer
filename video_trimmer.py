"""Video downloading, timestamp trimming, and aspect-ratio/custom cropping module."""

import os
import re
import sys
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from yt_processor import HighlightSegment
from utils import (
    sanitize_filename,
    format_timestamp,
    format_bytes,
    strip_ansi,
    get_yt_cookies_file,
    sanitize_environment,
    get_clean_env,
    get_js_runtimes,
)

# Strip oversized environment variables that cause Linux execve E2BIG [Errno 7]
sanitize_environment()
# Enable Node.js and Deno JS runtimes for yt-dlp 2025/2026 signature/n-challenge solving
os.environ.setdefault("YTDLP_JS_RUNTIMES", "node,deno")


# ------------------------------------------------------------------------------
# Video Cropping Configuration & FFmpeg Filter Builder
# ------------------------------------------------------------------------------
CROP_PRESETS: Dict[str, str] = {
    "none": "🖥️ Original (No Crop — 16:9)",
    "9:16": "📱 9:16 Vertical Crop (Shorts / Reels / TikTok)",
    "9:16_blur": "✨ 9:16 Vertical + Blurred Background (Full Frame)",
    "1:1": "🟦 1:1 Square Crop (Instagram / Feed)",
    "4:5": "🖼️ 4:5 Portrait Crop (Social Feed)",
    "4:3": "📺 4:3 Classic Crop",
    "custom": "📐 Custom Crop Margins (%)",
}


@dataclass
class CropConfig:
    """Configuration for aspect-ratio or custom margin video cropping."""
    mode: str = "none"          # 'none', '9:16', '9:16_blur', '1:1', '4:5', '4:3', 'custom'
    pan_x: float = 0.5          # 0.0 (left) to 1.0 (right), 0.5 = center
    pan_y: float = 0.5          # 0.0 (top) to 1.0 (bottom), 0.5 = center
    left_pct: float = 0.0       # Custom left crop % (0-45)
    right_pct: float = 0.0      # Custom right crop % (0-45)
    top_pct: float = 0.0        # Custom top crop % (0-45)
    bottom_pct: float = 0.0     # Custom bottom crop % (0-45)

    @property
    def is_active(self) -> bool:
        if not self.mode or self.mode == "none":
            return False
        if self.mode == "custom":
            return (self.left_pct + self.right_pct + self.top_pct + self.bottom_pct) > 0.1
        return True

    @property
    def file_tag(self) -> str:
        if not self.is_active:
            return ""
        clean_mode = self.mode.replace(":", "x")
        if self.mode == "custom":
            return f"_crop_custom_{int(self.left_pct)}L_{int(self.right_pct)}R_{int(self.top_pct)}T_{int(self.bottom_pct)}B"
        px_pct = int(round(self.pan_x * 100))
        if px_pct != 50 and self.mode in ("9:16", "1:1", "4:5", "4:3"):
            return f"_crop_{clean_mode}_pan{px_pct}"
        return f"_crop_{clean_mode}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_crop_vf_filter(crop_config: Optional[CropConfig]) -> Optional[str]:
    """
    Build an FFmpeg `-vf` video filter string for the given `CropConfig`.
    Ensures output width and height are always even integers (`trunc(.../2)*2`)
    required by H.264 (`yuv420p`).
    """
    if not crop_config or not crop_config.is_active:
        return None

    mode = crop_config.mode
    px = max(0.0, min(1.0, float(crop_config.pan_x)))
    py = max(0.0, min(1.0, float(crop_config.pan_y)))

    aspect_map: Dict[str, Tuple[int, int]] = {
        "9:16": (9, 16),
        "1:1": (1, 1),
        "4:5": (4, 5),
        "4:3": (4, 3),
    }

    if mode in aspect_map:
        num, den = aspect_map[mode]
        w_expr = f"trunc(min(iw,ih*{num}/{den})/2)*2"
        h_expr = f"trunc(min(ih,iw*{den}/{num})/2)*2"
        x_expr = f"trunc((iw-({w_expr}))*{px:.4f})"
        y_expr = f"trunc((ih-({h_expr}))*{py:.4f})"
        return f"crop=w='{w_expr}':h='{h_expr}':x='{x_expr}':y='{y_expr}'"

    if mode == "9:16_blur":
        return (
            "split[original][copy];"
            "[copy]scale='trunc(ih*9/16/2)*2':ih:force_original_aspect_ratio=increase,"
            "crop='trunc(ih*9/16/2)*2':ih,boxblur=20:5,eq=brightness=-0.12[blurred];"
            "[original]scale='trunc(ih*9/16/2)*2':-2:force_original_aspect_ratio=decrease[fg];"
            "[blurred][fg]overlay=(W-w)/2:(H-h)/2"
        )

    if mode == "custom":
        l_frac = max(0.0, min(0.45, float(crop_config.left_pct) / 100.0))
        r_frac = max(0.0, min(0.45, float(crop_config.right_pct) / 100.0))
        t_frac = max(0.0, min(0.45, float(crop_config.top_pct) / 100.0))
        b_frac = max(0.0, min(0.45, float(crop_config.bottom_pct) / 100.0))

        w_frac = max(0.10, 1.0 - l_frac - r_frac)
        h_frac = max(0.10, 1.0 - t_frac - b_frac)

        w_expr = f"trunc(iw*{w_frac:.4f}/2)*2"
        h_expr = f"trunc(ih*{h_frac:.4f}/2)*2"
        x_expr = f"trunc(iw*{l_frac:.4f})"
        y_expr = f"trunc(ih*{t_frac:.4f})"
        return f"crop=w='{w_expr}':h='{h_expr}':x='{x_expr}':y='{y_expr}'"

    return None


def compute_crop_box_normalized(
    crop_config: Optional[CropConfig],
    source_aspect: float = 16.0 / 9.0,
) -> Tuple[float, float, float, float]:
    """
    Compute normalized `(x0, y0, x1, y1)` in `[0.0, 1.0]` representing the crop window
    on a source video of aspect ratio `source_aspect` (default 16:9).
    """
    if not crop_config or not crop_config.is_active or crop_config.mode == "9:16_blur":
        return (0.0, 0.0, 1.0, 1.0)

    mode = crop_config.mode
    px = max(0.0, min(1.0, float(crop_config.pan_x)))
    py = max(0.0, min(1.0, float(crop_config.pan_y)))

    aspect_map = {
        "9:16": 9.0 / 16.0,
        "1:1": 1.0,
        "4:5": 4.0 / 5.0,
        "4:3": 4.0 / 3.0,
    }
    if mode in aspect_map:
        target_aspect = aspect_map[mode]
        if target_aspect <= source_aspect:
            w_norm = target_aspect / source_aspect
            h_norm = 1.0
        else:
            w_norm = 1.0
            h_norm = source_aspect / target_aspect
        x0 = (1.0 - w_norm) * px
        y0 = (1.0 - h_norm) * py
        return (round(x0, 4), round(y0, 4), round(x0 + w_norm, 4), round(y0 + h_norm, 4))

    if mode == "custom":
        x0 = max(0.0, min(0.45, float(crop_config.left_pct) / 100.0))
        x1 = max(x0 + 0.1, 1.0 - max(0.0, min(0.45, float(crop_config.right_pct) / 100.0)))
        y0 = max(0.0, min(0.45, float(crop_config.top_pct) / 100.0))
        y1 = max(y0 + 0.1, 1.0 - max(0.0, min(0.45, float(crop_config.bottom_pct) / 100.0)))
        return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))

    return (0.0, 0.0, 1.0, 1.0)


def get_video_dimensions(video_path: Path) -> Optional[Tuple[int, int]]:
    """Inspect a local video file with ffmpeg/ffprobe and return `(width, height)` in pixels."""
    if not video_path or not Path(video_path).exists():
        return None
    ffmpeg_exe = ensure_ffmpeg()
    if not ffmpeg_exe:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", str(video_path)],
            env=get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        m = re.search(r"Stream #\d+:\d+.*Video:.*?,\s*(\d{2,5})x(\d{2,5})(?:\s|\[|,)", proc.stderr)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    return None


# ------------------------------------------------------------------------------
# FFmpeg Discovery & Diagnostics
# ------------------------------------------------------------------------------
def is_ffmpeg_executable(path: Optional[str]) -> bool:
    """Verify that a path points to an executable and runnable ffmpeg binary."""
    if not path:
        return False
    resolved = shutil.which(path) if (os.sep not in path and (os.altsep is None or os.altsep not in path)) else path
    target = Path(resolved or path)
    try:
        if not (target.is_file() and os.access(str(target), os.X_OK)):
            return False
    except Exception:
        return False

    try:
        proc = subprocess.run(
            [str(target), "-version"],
            env=get_clean_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except OSError as e:
        if getattr(e, "errno", None) == 7:
            return True
        return False
    except Exception:
        return False


def get_ffmpeg_path() -> Optional[str]:
    """Find a verified working ffmpeg executable from system, imageio-ffmpeg, or static-ffmpeg."""
    candidates = [
        shutil.which("ffmpeg"),
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/app/.bin/ffmpeg",
        "/home/appuser/.local/bin/ffmpeg",
        "/home/adminuser/.local/bin/ffmpeg",
    ]
    for loc in candidates:
        if loc and os.path.exists(loc) and is_ffmpeg_executable(loc):
            return loc

    try:
        import imageio_ffmpeg
        bin_dir = Path(imageio_ffmpeg.__file__).resolve().parent / "binaries"
        target = Path(tempfile.gettempdir()) / "ffmpeg_bin" / "ffmpeg"
        target.parent.mkdir(parents=True, exist_ok=True)

        if bin_dir.exists():
            for f in bin_dir.glob("ffmpeg*"):
                if f.is_file():
                    try:
                        shutil.copy2(f, target)
                        target.chmod(0o755)
                        if is_ffmpeg_executable(str(target)):
                            return str(target)
                    except Exception:
                        pass

        try:
            img_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if img_exe and os.path.exists(img_exe):
                shutil.copy2(img_exe, target)
                target.chmod(0o755)
                if is_ffmpeg_executable(str(target)):
                    return str(target)
        except Exception:
            pass
    except Exception:
        pass

    try:
        import static_ffmpeg
        sdir = Path(static_ffmpeg.__file__).resolve().parent / "bin"
        if sdir.exists():
            target = Path(tempfile.gettempdir()) / "ffmpeg_bin" / "ffmpeg"
            target.parent.mkdir(parents=True, exist_ok=True)
            for f in sdir.glob("**/ffmpeg*"):
                if f.is_file() and not f.name.endswith(".zip"):
                    try:
                        shutil.copy2(f, target)
                        target.chmod(0o755)
                        if is_ffmpeg_executable(str(target)):
                            return str(target)
                    except Exception:
                        pass
        static_ffmpeg.add_paths(weak=True)
        exe = shutil.which("ffmpeg")
        if exe and is_ffmpeg_executable(exe):
            return exe
    except Exception:
        pass

    return None


def ensure_ffmpeg() -> Optional[str]:
    """
    Ensure ffmpeg is available, verified runnable, on os.environ['PATH'],
    and accessible via a standard 'ffmpeg' binary shim in tempdir and PATH.
    """
    exe = get_ffmpeg_path()
    if not exe:
        return None

    exe_dir = str(Path(exe).resolve().parent)
    cur_path = os.environ.get("PATH", "")
    if exe_dir not in cur_path.split(os.pathsep):
        os.environ["PATH"] = f"{exe_dir}{os.pathsep}{cur_path}"

    if Path(exe).name == "ffmpeg":
        return exe

    tmp_bin = Path(tempfile.gettempdir()) / "ffmpeg_bin"
    tmp_bin.mkdir(parents=True, exist_ok=True)
    shim = tmp_bin / "ffmpeg"
    if not shim.exists() or not is_ffmpeg_executable(str(shim)):
        try:
            if shim.exists() or shim.is_symlink():
                shim.unlink()
            shutil.copy2(exe, shim)
            shim.chmod(0o755)
        except Exception:
            try:
                os.symlink(exe, shim)
            except Exception:
                pass

    if str(tmp_bin) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = f"{tmp_bin}{os.pathsep}{os.environ.get('PATH', '')}"

    if shim.exists() and is_ffmpeg_executable(str(shim)):
        return str(shim)

    return exe


def get_ffmpeg_diagnostics() -> Dict[str, Any]:
    """Return comprehensive diagnostics about FFmpeg and JS runtime discovery."""
    diag: Dict[str, Any] = {}
    diag["python"] = sys.version.split()[0]
    diag["shutil_which_ffmpeg"] = shutil.which("ffmpeg")
    diag["shutil_which_node"] = shutil.which("node") or shutil.which("nodejs")
    diag["PATH"] = os.environ.get("PATH", "")
    diag["env_keys_count"] = len(os.environ)
    diag["env_total_bytes"] = sum(len(k) + len(v) for k, v in os.environ.items())

    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]:
        exists = os.path.exists(p)
        diag[f"exists_{p}"] = exists
        if exists:
            try:
                proc = subprocess.run([p, "-version"], env=get_clean_env(), capture_output=True, text=True, timeout=3)
                diag[f"run_{p}"] = f"code={proc.returncode}, out={proc.stdout[:40]}"
            except Exception as e:
                diag[f"run_{p}_err"] = str(e)

    diag["js_runtimes"] = get_js_runtimes()
    try:
        import yt_dlp
        diag["yt_dlp_version"] = yt_dlp.version.__version__
    except Exception:
        pass
    diag["ensure_ffmpeg_result"] = ensure_ffmpeg()
    return diag


# Initialize ffmpeg on import
ensure_ffmpeg()


def has_ffmpeg() -> bool:
    """Check if ffmpeg is verified installed and runnable."""
    exe = ensure_ffmpeg()
    return bool(exe and is_ffmpeg_executable(exe))


# ------------------------------------------------------------------------------
# Multi-Client Cloud-Resilient YouTube Download Profiles
# ------------------------------------------------------------------------------
def _build_client_profiles() -> List[Dict[str, Any]]:
    """
    Build prioritized, cloud-resilient player client profiles for yt-dlp.

    Why this ordering fixes Streamlit Cloud bot detection & HTTP 403:
    1. `visionos` (Apple Vision Pro client) runs FIRST (cookieless):
       - Bypasses YouTube browser bot detection on datacenter IPs without a PO Token or JS challenge.
       - Provides direct HLS & DASH streams up to 1080p.
    2. `android` (Android mobile client) runs next (cookieless):
       - Direct progressive MP4 streams (360p).
    3. Authenticated cookie-supporting profiles (`default`, `tv_samsung`, `web_safari`)
       run next if valid cookies exist (note: `visionos`, `android`, `android_vr`, and `ios`
       do not support cookies in yt-dlp, so cookies are never passed to them).
    4. Cookieless fallbacks (`default`, `android_vr`, `ios`, `tv_samsung`, `web_safari`).
    """
    has_cookies = bool(get_yt_cookies_file())
    profiles: List[Dict[str, Any]] = [
        {
            "name": "visionos_direct",
            "use_cookies": False,
            "clients": ["visionos"],
            "prefer_hls_or_prog": False,
        },
        {
            "name": "android_direct",
            "use_cookies": False,
            "clients": ["android"],
            "prefer_hls_or_prog": True,
        },
    ]

    if has_cookies:
        profiles.extend([
            {
                "name": "default_authenticated",
                "use_cookies": True,
                "clients": None,
                "prefer_hls_or_prog": False,
            },
            {
                "name": "tv_samsung_authenticated",
                "use_cookies": True,
                "clients": ["tv_samsung"],
                "prefer_hls_or_prog": False,
            },
            {
                "name": "web_safari_authenticated",
                "use_cookies": True,
                "clients": ["web_safari"],
                "prefer_hls_or_prog": True,
            },
        ])

    profiles.extend([
        {
            "name": "default_direct",
            "use_cookies": False,
            "clients": None,
            "prefer_hls_or_prog": False,
        },
        {
            "name": "android_vr_direct",
            "use_cookies": False,
            "clients": ["android_vr"],
            "prefer_hls_or_prog": False,
        },
        {
            "name": "ios_hls_direct",
            "use_cookies": False,
            "clients": ["ios"],
            "prefer_hls_or_prog": True,
        },
        {
            "name": "tv_samsung_direct",
            "use_cookies": False,
            "clients": ["tv_samsung"],
            "prefer_hls_or_prog": False,
        },
        {
            "name": "web_safari_direct",
            "use_cookies": False,
            "clients": ["web_safari"],
            "prefer_hls_or_prog": True,
        },
    ])
    return profiles


# ------------------------------------------------------------------------------
# Python-Native HLS Segment Slicer (Zero FFmpeg Network Calls -> Zero Code 8!)
# ------------------------------------------------------------------------------
def slice_hls_playlist_to_file(
    m3u8_url: str,
    output_ts_path: Path,
    start_time: float,
    end_time: float,
    headers: Optional[Dict[str, str]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Tuple[Path, float]:
    """
    Fetch an HLS `.m3u8` media playlist in Python, identify ONLY the `.ts` / fMP4
    segments overlapping `[start_time, end_time]`, download those segments via
    Python's `urllib.request` (never invoking FFmpeg over the network), and
    concatenate them into `output_ts_path`.

    Returns `(output_ts_path, local_trim_start_offset)`.
    """
    req_headers = dict(headers or {})
    if "User-Agent" not in req_headers:
        req_headers["User-Agent"] = "com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1 like Mac OS X;)"

    def _fetch_bytes(target_url: str, byte_range: Optional[str] = None) -> bytes:
        hdrs = dict(req_headers)
        if byte_range:
            hdrs["Range"] = f"bytes={byte_range}"
        req = urllib.request.Request(target_url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()

    raw_m3u8 = _fetch_bytes(m3u8_url).decode("utf-8", errors="replace")
    lines = [l.strip() for l in raw_m3u8.splitlines() if l.strip()]

    # If this is a master playlist (#EXT-X-STREAM-INF), resolve a media playlist URL first
    for idx, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for nxt in lines[idx + 1:]:
                if not nxt.startswith("#"):
                    sub_url = urllib.parse.urljoin(m3u8_url, nxt)
                    raw_m3u8 = _fetch_bytes(sub_url).decode("utf-8", errors="replace")
                    m3u8_url = sub_url
                    lines = [l.strip() for l in raw_m3u8.splitlines() if l.strip()]
                    break
            break

    init_map_url: Optional[str] = None
    segments_meta: List[Tuple[float, float, str, Optional[str]]] = []  # (start, end, url, range)
    cur_t = 0.0
    pending_dur: Optional[float] = None
    pending_range: Optional[str] = None
    next_range_offset = 0

    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            m_uri = re.search(r'URI="([^"]+)"', line)
            if m_uri:
                init_map_url = urllib.parse.urljoin(m3u8_url, m_uri.group(1))
        elif line.startswith("#EXT-X-BYTERANGE:"):
            br_spec = line[len("#EXT-X-BYTERANGE:"):].strip()
            if "@" in br_spec:
                length_s, off_s = br_spec.split("@", 1)
                length_i, off_i = int(length_s), int(off_s)
            else:
                length_i, off_i = int(br_spec), next_range_offset
            pending_range = f"{off_i}-{off_i + length_i - 1}"
            next_range_offset = off_i + length_i
        elif line.startswith("#EXTINF:"):
            dur_str = line[len("#EXTINF:"):].split(",")[0].strip()
            try:
                pending_dur = float(dur_str)
            except ValueError:
                pending_dur = 5.0
        elif not line.startswith("#"):
            seg_dur = pending_dur if pending_dur is not None else 5.0
            seg_url = urllib.parse.urljoin(m3u8_url, line)
            segments_meta.append((cur_t, cur_t + seg_dur, seg_url, pending_range))
            cur_t += seg_dur
            pending_dur = None
            pending_range = None

    if not segments_meta:
        raise ValueError("HLS playlist contained no media segments.")

    # Select segments overlapping [max(0, start_time - 1.0), end_time + 1.0]
    pad_start = max(0.0, start_time - 1.0)
    pad_end = end_time + 1.0
    overlapping = [
        (s0, s1, u, rng) for (s0, s1, u, rng) in segments_meta
        if s1 > pad_start and s0 < pad_end
    ]
    if not overlapping:
        overlapping = segments_meta[:3]

    first_seg_start = overlapping[0][0]
    local_offset = max(0.0, start_time - first_seg_start)

    output_ts_path.parent.mkdir(parents=True, exist_ok=True)
    total_segs = len(overlapping)
    downloaded_bytes = 0

    with open(output_ts_path, "wb") as out_f:
        if init_map_url:
            init_data = _fetch_bytes(init_map_url)
            out_f.write(init_data)
            downloaded_bytes += len(init_data)

        for idx, (_, _, seg_url, seg_rng) in enumerate(overlapping, start=1):
            chunk = _fetch_bytes(seg_url, byte_range=seg_rng)
            out_f.write(chunk)
            downloaded_bytes += len(chunk)
            if progress_callback:
                pct = max(0.0, min(0.95, idx / max(1, total_segs)))
                progress_callback(
                    pct,
                    f"Direct HLS Slice: {format_bytes(downloaded_bytes)} (segment {idx}/{total_segs})",
                )

    return output_ts_path, local_offset


def _is_hls_format(f: Dict[str, Any]) -> bool:
    url_str = str(f.get("url") or "")
    proto_str = str(f.get("protocol") or "")
    return bool(url_str) and ("m3u8" in proto_str or ".m3u8" in url_str)


def _select_hls_formats(
    formats: List[Dict[str, Any]],
    max_height: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Pick either a muxed HLS format OR a (video_hls, audio_hls) pair within `max_height`.
    Prefers H.264 (`avc1`) video for broad browser playback and fast FFmpeg decoding.
    """
    muxed = [
        f for f in formats
        if _is_hls_format(f)
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    muxed_capped = [f for f in muxed if (f.get("height") or 0) <= max_height] or muxed
    if muxed_capped:
        muxed_capped.sort(
            key=lambda f: (
                (f.get("height") or 0) <= max_height,
                f.get("height") or 0,
                1 if str(f.get("vcodec", "")).startswith("avc") else 0,
                f.get("tbr") or 0,
            ),
            reverse=True,
        )
        return muxed_capped[0], None, None

    v_hls = [
        f for f in formats
        if _is_hls_format(f)
        and f.get("vcodec") not in (None, "none")
        and (f.get("height") or 0) > 0
    ]
    a_hls = [
        f for f in formats
        if _is_hls_format(f)
        and f.get("vcodec") == "none"
        and (
            f.get("acodec") not in (None, "none")
            or str(f.get("format_id", "")) in ("233", "234")
            or "audio" in str(f.get("format_note", "")).lower()
        )
    ]
    if not v_hls or not a_hls:
        return None, None, None

    v_capped = [f for f in v_hls if (f.get("height") or 0) <= max_height] or v_hls
    v_capped.sort(
        key=lambda f: (
            (f.get("height") or 0) <= max_height,
            f.get("height") or 0,
            1 if str(f.get("vcodec", "")).startswith("avc") else 0,
            f.get("tbr") or 0,
        ),
        reverse=True,
    )
    a_hls.sort(
        key=lambda f: (
            1 if str(f.get("format_id", "")) == "234" else 0,
            f.get("abr") or f.get("tbr") or 0,
        ),
        reverse=True,
    )
    return None, v_capped[0], a_hls[0]


def _try_python_hls_direct_slice(
    url: str,
    output_path: Path,
    start_time: float,
    end_time: float,
    max_height: int = 720,
    crop_config: Optional[CropConfig] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Optional[Path]:
    """
    Attempt to extract HLS `.m3u8` stream URLs via `yt-dlp` (`visionos`, `default`,
    `web_safari`), download ONLY the overlapping segments in Python (supports both
    muxed HLS and separate video+audio HLS), and trim/mux/crop locally with FFmpeg.
    """
    import yt_dlp

    has_cookies = bool(get_yt_cookies_file())
    hls_profiles: List[Dict[str, Any]] = [
        {"use_cookies": False, "clients": ["visionos"]},
        {"use_cookies": False, "clients": None},
    ]
    if has_cookies:
        hls_profiles.extend([
            {"use_cookies": True, "clients": ["web_safari"]},
            {"use_cookies": True, "clients": None},
        ])
    hls_profiles.append({"use_cookies": False, "clients": ["web_safari"]})

    js_runtimes = get_js_runtimes()
    ffmpeg_exe = ensure_ffmpeg()

    for prof in hls_profiles:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cachedir": False,
            "nocheckcertificate": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 20,
            "js_runtimes": js_runtimes,
            "remote_components": ["ejs:github"],
        }
        if prof.get("clients"):
            ydl_opts["extractor_args"] = {
                "youtube": {
                    "player_client": prof["clients"],
                }
            }
        if prof["use_cookies"]:
            cf = get_yt_cookies_file()
            if cf and Path(cf).exists():
                ydl_opts["cookiefile"] = str(cf)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info or not isinstance(info.get("formats"), list):
                continue

            muxed_fmt, v_fmt, a_fmt = _select_hls_formats(info["formats"], max_height)
            clip_dur = max(0.5, end_time - start_time)

            if muxed_fmt:
                fmt_headers = muxed_fmt.get("http_headers") or info.get("http_headers") or {}
                temp_ts = output_path.with_name(f"_hls_slice_{output_path.stem}.ts")
                try:
                    _, local_offset = slice_hls_playlist_to_file(
                        m3u8_url=muxed_fmt["url"],
                        output_ts_path=temp_ts,
                        start_time=start_time,
                        end_time=end_time,
                        headers=fmt_headers,
                        progress_callback=progress_callback,
                    )
                    if temp_ts.exists() and temp_ts.stat().st_size > 0:
                        trim_clip_ffmpeg(
                            source_video=temp_ts,
                            output_path=output_path,
                            start_time=local_offset,
                            end_time=local_offset + clip_dur,
                            fast_copy=False,
                            crop_config=crop_config,
                        )
                        if output_path.exists() and output_path.stat().st_size > 0:
                            return output_path
                finally:
                    try:
                        if temp_ts.exists():
                            temp_ts.unlink()
                    except Exception:
                        pass

            elif v_fmt and a_fmt and ffmpeg_exe:
                v_headers = v_fmt.get("http_headers") or info.get("http_headers") or {}
                a_headers = a_fmt.get("http_headers") or info.get("http_headers") or {}
                temp_v = output_path.with_name(f"_hls_v_{output_path.stem}.ts")
                temp_a = output_path.with_name(f"_hls_a_{output_path.stem}.ts")
                try:
                    _, v_off = slice_hls_playlist_to_file(
                        m3u8_url=v_fmt["url"],
                        output_ts_path=temp_v,
                        start_time=start_time,
                        end_time=end_time,
                        headers=v_headers,
                        progress_callback=progress_callback,
                    )
                    _, a_off = slice_hls_playlist_to_file(
                        m3u8_url=a_fmt["url"],
                        output_ts_path=temp_a,
                        start_time=start_time,
                        end_time=end_time,
                        headers=a_headers,
                    )
                    if (
                        temp_v.exists() and temp_v.stat().st_size > 0
                        and temp_a.exists() and temp_a.stat().st_size > 0
                    ):
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        vf_filter = build_crop_vf_filter(crop_config)
                        cmd = [
                            ffmpeg_exe, "-y",
                            "-ss", f"{v_off:.3f}", "-i", str(temp_v),
                            "-ss", f"{a_off:.3f}", "-i", str(temp_a),
                            "-t", f"{clip_dur:.3f}",
                        ]
                        if vf_filter:
                            cmd.extend(["-vf", vf_filter])
                        cmd.extend([
                            "-c:v", "libx264",
                            "-preset", "veryfast",
                            "-crf", "23",
                            "-pix_fmt", "yuv420p",
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-shortest",
                            "-movflags", "+faststart",
                            str(output_path),
                        ])
                        proc = subprocess.run(
                            cmd,
                            env=get_clean_env(),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                            return output_path
                finally:
                    for tmp_f in (temp_v, temp_a):
                        try:
                            if tmp_f.exists():
                                tmp_f.unlink()
                        except Exception:
                            pass
        except Exception:
            continue

    return None


def _download_with_pytubefix(
    url: str,
    output_dir: Path,
    max_height: int = 720,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Optional[Path]:
    """
    Secondary fallback engine using `pytubefix` across multiple innerTube clients
    (`ANDROID_VR`, `IOS`, `TV`, `MWEB`, `WEB`) if `yt-dlp` hits 403 on all profiles.
    """
    try:
        from pytubefix import YouTube
    except ImportError:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = ensure_ffmpeg()

    for client_name in ["ANDROID_VR", "IOS", "TV", "MWEB", "WEB"]:
        try:
            def _on_prog(stream, chunk, bytes_remaining):
                if progress_callback and stream.filesize:
                    done = stream.filesize - bytes_remaining
                    pct = max(0.0, min(0.99, done / stream.filesize))
                    progress_callback(pct, f"Downloading ({client_name})... {format_bytes(done)} / {format_bytes(stream.filesize)}")

            yt = YouTube(url, client=client_name, on_progress_callback=_on_prog)
            vid_id = yt.video_id or "video"
            final_mp4 = output_dir / f"source_{vid_id}.mp4"

            prog_streams = (
                yt.streams.filter(progressive=True, file_extension="mp4")
                .order_by("resolution")
                .desc()
            )
            chosen_prog = None
            for s in prog_streams:
                res_int = int((s.resolution or "0p").rstrip("p") or 0)
                if res_int <= max_height:
                    chosen_prog = s
                    break
            if not chosen_prog and prog_streams:
                chosen_prog = prog_streams.first()

            if chosen_prog:
                dl_file = chosen_prog.download(output_path=str(output_dir), filename=f"source_{vid_id}.mp4")
                if dl_file and Path(dl_file).exists() and Path(dl_file).stat().st_size > 0:
                    return Path(dl_file)

            if ffmpeg_exe:
                v_streams = (
                    yt.streams.filter(adaptive=True, only_video=True, file_extension="mp4")
                    .order_by("resolution")
                    .desc()
                )
                chosen_v = None
                for s in v_streams:
                    res_int = int((s.resolution or "0p").rstrip("p") or 0)
                    if res_int <= max_height:
                        chosen_v = s
                        break
                if not chosen_v and v_streams:
                    chosen_v = v_streams.first()

                a_stream = yt.streams.filter(adaptive=True, only_audio=True).order_by("abr").desc().first()
                if chosen_v and a_stream:
                    v_path = Path(chosen_v.download(output_path=str(output_dir), filename=f"temp_v_{vid_id}.mp4"))
                    a_path = Path(a_stream.download(output_path=str(output_dir), filename=f"temp_a_{vid_id}.m4a"))
                    subprocess.run(
                        [
                            ffmpeg_exe, "-y",
                            "-i", str(v_path),
                            "-i", str(a_path),
                            "-c:v", "copy",
                            "-c:a", "aac",
                            "-movflags", "+faststart",
                            str(final_mp4),
                        ],
                        env=get_clean_env(),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    for tmp_f in (v_path, a_path):
                        try:
                            tmp_f.unlink()
                        except Exception:
                            pass
                    if final_mp4.exists() and final_mp4.stat().st_size > 0:
                        return final_mp4
        except Exception:
            continue

    return None


def _build_ydl_base_opts(
    prof: Dict[str, Any],
    max_height: int,
    ffmpeg_exe: Optional[str],
    progress_hook: Optional[Callable[[dict], None]] = None,
) -> Dict[str, Any]:
    """Build yt-dlp options for a player client profile with JS runtime and format prefs."""
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "overwrites": True,
        "source_address": "0.0.0.0",
        "cachedir": False,
        "nocheckcertificate": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "socket_timeout": 25,
        "downloader_args": {
            "ffmpeg_i": ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        },
        "js_runtimes": get_js_runtimes(),
        "remote_components": ["ejs:github"],
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    if prof.get("clients"):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": prof["clients"],
            }
        }

    if prof.get("use_cookies"):
        fresh_cookies = get_yt_cookies_file()
        if fresh_cookies and Path(fresh_cookies).exists():
            opts["cookiefile"] = str(fresh_cookies)

    if ffmpeg_exe and is_ffmpeg_executable(ffmpeg_exe):
        if prof.get("prefer_hls_or_prog"):
            format_selector = (
                f"best[height<={max_height}][ext=mp4]/"
                f"best[height<={max_height}]/"
                f"bestvideo[height<={max_height}][vcodec^=avc]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={max_height}]+bestaudio/best"
            )
        else:
            format_selector = (
                f"bestvideo[height<={max_height}][vcodec^=avc]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={max_height}][ext=mp4]/"
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]/best"
            )
        opts["format"] = format_selector
        opts["ffmpeg_location"] = str(ffmpeg_exe)
        opts["merge_output_format"] = "mp4"
    else:
        opts["format"] = (
            f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best[ext=mp4]/best"
        )
    return opts


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
            progress_callback(max(0.0, min(0.99, pct)), msg)
        elif progress_callback and d.get("status") == "finished":
            progress_callback(1.0, "Download finished, finalizing container...")

    ffmpeg_exe = ensure_ffmpeg()
    outtmpl = str(output_dir / "source_%(id)s.%(ext)s")

    import yt_dlp

    profiles = _build_client_profiles()
    last_exc: Optional[Exception] = None

    for prof in profiles:
        for part_f in output_dir.glob("source_*.part*"):
            try:
                part_f.unlink()
            except Exception:
                pass

        ydl_opts = _build_ydl_base_opts(prof, max_height, ffmpeg_exe, progress_hook=_hook)
        ydl_opts.update({
            "outtmpl": outtmpl,
            "continuedl": True,
            "http_chunk_size": 10485760,
        })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            video_id = info.get("id", "video") if info else "video"
            expected_mp4 = output_dir / f"source_{video_id}.mp4"
            if expected_mp4.exists() and expected_mp4.stat().st_size > 0:
                return expected_mp4
            for candidate in output_dir.glob(f"source_{video_id}.*"):
                if candidate.is_file() and candidate.stat().st_size > 0 and not candidate.name.endswith(".part"):
                    return candidate
            for f in output_dir.glob("source_*.*"):
                if f.is_file() and f.stat().st_size > 0 and not f.name.endswith(".part"):
                    return f
        except Exception as exc:
            last_exc = exc
            continue

    pytube_res = _download_with_pytubefix(
        url=url,
        output_dir=output_dir,
        max_height=max_height,
        progress_callback=progress_callback,
    )
    if pytube_res and pytube_res.exists() and pytube_res.stat().st_size > 0:
        return pytube_res

    if last_exc:
        raise last_exc
    raise FileNotFoundError("Downloaded video file could not be found in output directory.")


def download_single_clip_section(
    url: str,
    output_path: Path,
    start_time: float,
    end_time: float,
    max_height: int = 720,
    crop_config: Optional[CropConfig] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Download *only* the specific timestamp range [start_time, end_time] directly
    from YouTube, then optionally apply local FFmpeg cropping (`crop_config`).

    3-Tier Cloud-Resilient Architecture:
    1. Tier 1 (`_try_python_hls_direct_slice`): Fetches HLS `.m3u8` playlists in Python,
       downloads ONLY overlapping segments in Python (never spawning FFmpeg over HTTP),
       and runs FFmpeg purely locally to mux, trim & crop. Eliminates `ffmpeg exited with code 8`!
    2. Tier 2 (`yt-dlp` section download with isolated client profiles & JS challenge solver).
    3. Tier 3 (`pytubefix` progressive/adaptive fallback + local FFmpeg trim & crop).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = ensure_ffmpeg()

    # Tier 1: Python-native HLS segment slicer (0 FFmpeg network requests -> impossible to get code 8)
    hls_sliced = _try_python_hls_direct_slice(
        url=url,
        output_path=output_path,
        start_time=start_time,
        end_time=end_time,
        max_height=max_height,
        crop_config=crop_config,
        progress_callback=progress_callback,
    )
    if hls_sliced and hls_sliced.exists() and hls_sliced.stat().st_size > 0:
        return hls_sliced

    # Tier 2: yt-dlp section download with isolated profiles
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
            progress_callback(max(0.0, min(0.99, pct)), msg)
        elif progress_callback and d.get("status") == "finished":
            progress_callback(1.0, "Stream downloaded, finalizing MP4 clip...")

    import yt_dlp

    needs_crop = bool(crop_config and crop_config.is_active)
    raw_target = output_path.with_name(f"_raw_{output_path.name}") if needs_crop else output_path
    stem = raw_target.stem
    outtmpl = str(raw_target.parent / f"{stem}.%(ext)s")

    profiles = _build_client_profiles()
    last_exc: Optional[Exception] = None

    for prof in profiles:
        for old_f in raw_target.parent.glob(f"{stem}*"):
            try:
                old_f.unlink()
            except Exception:
                pass

        ydl_opts = _build_ydl_base_opts(prof, max_height, ffmpeg_exe, progress_hook=_hook)
        ydl_opts.update({
            "outtmpl": outtmpl,
            "download_ranges": yt_dlp.utils.download_range_func(None, [(start_time, end_time)]),
            "force_keyframes_at_cuts": False,
            "retries": 4,
            "fragment_retries": 4,
            "socket_timeout": 20,
        })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            produced_file: Optional[Path] = None
            if raw_target.exists() and raw_target.stat().st_size > 0:
                produced_file = raw_target
            else:
                candidates = [
                    f for f in raw_target.parent.glob(f"{stem}*")
                    if not f.name.endswith(".part") and f.is_file() and f.stat().st_size > 0
                ]
                if candidates:
                    produced_file = candidates[0]

            if produced_file and produced_file.exists():
                if needs_crop:
                    clip_dur = max(0.5, end_time - start_time)
                    trim_clip_ffmpeg(
                        source_video=produced_file,
                        output_path=output_path,
                        start_time=0.0,
                        end_time=clip_dur + 5.0,
                        fast_copy=False,
                        crop_config=crop_config,
                    )
                    try:
                        produced_file.unlink()
                    except Exception:
                        pass
                    if output_path.exists() and output_path.stat().st_size > 0:
                        return output_path
                else:
                    if produced_file == output_path:
                        return output_path
                    if produced_file.suffix.lower() == ".mp4":
                        shutil.move(str(produced_file), str(output_path))
                        return output_path
                    if ffmpeg_exe:
                        subprocess.run(
                            [ffmpeg_exe, "-y", "-i", str(produced_file), "-c", "copy", "-movflags", "+faststart", str(output_path)],
                            env=get_clean_env(),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        if output_path.exists() and output_path.stat().st_size > 0:
                            try:
                                produced_file.unlink()
                            except Exception:
                                pass
                            return output_path
                    shutil.move(str(produced_file), str(output_path))
                    return output_path
        except Exception as exc:
            last_exc = exc
            continue

    # Tier 3: pytubefix fallback + local FFmpeg trim/crop
    try:
        with tempfile.TemporaryDirectory() as pt_tmp:
            pt_path = _download_with_pytubefix(
                url=url,
                output_dir=Path(pt_tmp),
                max_height=max_height,
                progress_callback=progress_callback,
            )
            if pt_path and pt_path.exists() and pt_path.stat().st_size > 0:
                trim_clip_ffmpeg(
                    source_video=pt_path,
                    output_path=output_path,
                    start_time=start_time,
                    end_time=end_time,
                    fast_copy=False,
                    crop_config=crop_config,
                )
                if output_path.exists() and output_path.stat().st_size > 0:
                    return output_path
    except Exception:
        pass

    if last_exc:
        raise last_exc
    raise FileNotFoundError(f"Failed to produce stream-trimmed clip: {output_path.name}")


def download_and_trim_segments_direct(
    url: str,
    segments: List[HighlightSegment],
    clips_dir: Path,
    video_title: str = "video",
    max_height: int = 720,
    crop_config: Optional[CropConfig] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[int, Path]:
    """
    Download each highlight segment directly from YouTube without downloading the full video,
    applying optional `crop_config`.
    Returns mapping of segment.rank -> clip_path.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(video_title)
    crop_tag = crop_config.file_tag if crop_config else ""
    results: Dict[int, Path] = {}
    total = len(segments)

    for idx, seg in enumerate(segments, start=1):
        start_tag = format_timestamp(seg.start_time).replace(":", "m") + "s"
        end_tag = format_timestamp(seg.end_time).replace(":", "m") + "s"
        clip_filename = f"{safe_title}_rank{seg.rank}_{start_tag}_to_{end_tag}{crop_tag}.mp4"
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
            crop_config=crop_config,
        )
        results[seg.rank] = clip_path

    return results


def trim_clip_ffmpeg(
    source_video: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    fast_copy: bool = False,
    crop_config: Optional[CropConfig] = None,
) -> Path:
    """
    Trim a segment `[start_time, end_time]` from `source_video` into `output_path`,
    applying optional `crop_config` (aspect ratio or custom margins).
    Note: When `crop_config` is active, video filter re-encoding (`libx264`) is
    automatically used because stream copy (`-c copy`) cannot apply `-vf` filters.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, end_time - start_time)
    vf_filter = build_crop_vf_filter(crop_config)

    ffmpeg_exe = ensure_ffmpeg()
    if ffmpeg_exe:
        if fast_copy and not vf_filter:
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
            ]
            if vf_filter:
                cmd.extend(["-vf", vf_filter])
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ])
        proc = subprocess.run(cmd, env=get_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
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

        if crop_config and crop_config.is_active and crop_config.mode != "9:16_blur":
            w, h = subclip.size
            src_aspect = float(w) / float(max(1, h))
            x0_n, y0_n, x1_n, y1_n = compute_crop_box_normalized(crop_config, src_aspect)
            x1_px = int(round(x0_n * w)) // 2 * 2
            y1_px = int(round(y0_n * h)) // 2 * 2
            x2_px = max(x1_px + 2, int(round(x1_n * w)) // 2 * 2)
            y2_px = max(y1_px + 2, int(round(y1_n * h)) // 2 * 2)
            if hasattr(subclip, "cropped"):
                subclip = subclip.cropped(x1=x1_px, y1=y1_px, x2=x2_px, y2=y2_px)
            elif hasattr(subclip, "crop"):
                subclip = subclip.crop(x1=x1_px, y1=y1_px, x2=x2_px, y2=y2_px)

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
    crop_config: Optional[CropConfig] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[int, Path]:
    """
    Trim a list of `HighlightSegment` items from `source_video` and save each clip
    into `clips_dir`, applying optional `crop_config`.
    Returns a dictionary mapping `segment.rank -> clip_path`.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(video_title)
    crop_tag = crop_config.file_tag if crop_config else ""
    results: Dict[int, Path] = {}
    total = len(segments)

    for idx, seg in enumerate(segments, start=1):
        start_tag = format_timestamp(seg.start_time).replace(":", "m") + "s"
        end_tag = format_timestamp(seg.end_time).replace(":", "m") + "s"
        clip_filename = f"{safe_title}_rank{seg.rank}_{start_tag}_to_{end_tag}{crop_tag}.mp4"
        clip_path = clips_dir / clip_filename

        if progress_callback:
            crop_note = f" [{crop_config.mode} crop]" if (crop_config and crop_config.is_active) else ""
            progress_callback(
                idx,
                total,
                f"Trimming Clip #{seg.rank} ({seg.time_range_str}){crop_note} [{idx}/{total}]...",
            )

        trim_clip_ffmpeg(
            source_video=source_video,
            output_path=clip_path,
            start_time=seg.start_time,
            end_time=seg.end_time,
            fast_copy=fast_copy,
            crop_config=crop_config,
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
