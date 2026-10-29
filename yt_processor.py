"""YouTube metadata & Most-Replayed (heatmap) timestamp extraction module."""

import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional
import re

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils import format_timestamp, get_yt_cookies_file


@dataclass
class HighlightSegment:
    """Represents an extracted high-engagement timestamp segment."""
    rank: int
    start_time: float
    end_time: float
    peak_time: float
    score: float  # 0.0 to 1.0 (normalized replay intensity)
    source: str   # 'heatmap', 'chapters', 'comments_description', or 'fallback'
    label: str = ""

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end_time - self.start_time), 2)

    @property
    def time_range_str(self) -> str:
        return f"{format_timestamp(self.start_time)} - {format_timestamp(self.end_time)}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["duration"] = self.duration
        d["time_range_str"] = self.time_range_str
        return d


def fetch_video_metadata(url: str, cookies_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch video metadata (including heatmap and chapters) without downloading the video.
    Tries multiple client profiles (with and without cookies) so heatmap data is
    retrieved reliably on Streamlit Cloud / datacenter IPs.
    """
    import yt_dlp

    # Always obtain a fresh working cookie copy if available
    active_cookies = cookies_file or get_yt_cookies_file()

    # Note: 'web' and 'mweb' return YouTube's 'heatmap' (Most Replayed) array.
    # Try cookie-authenticated web/mweb first, then cookieless web/visionos/android_vr,
    # then tv/embedded fallback.
    attempts = []
    if active_cookies and Path(active_cookies).exists():
        attempts.append({
            "use_cookies": True,
            "clients": ["web", "mweb", "tv"],
        })
    attempts.extend([
        {
            "use_cookies": False,
            "clients": ["web", "mweb", "visionos"],
        },
        {
            "use_cookies": False,
            "clients": ["android_vr", "ios", "tv_embedded"],
        },
        {
            "use_cookies": True if (active_cookies and Path(active_cookies).exists()) else False,
            "clients": ["tv_embedded", "web_creator", "mediaconnect"],
        },
    ])

    last_exc: Optional[Exception] = None
    for attempt in attempts:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "ignore_no_formats_error": True,
            "cachedir": False,
            "socket_timeout": 25,
            "extractor_args": {
                "youtube": {
                    "player_client": attempt["clients"],
                }
            },
        }
        if attempt["use_cookies"]:
            fresh_cf = cookies_file or get_yt_cookies_file()
            if fresh_cf and Path(fresh_cf).exists():
                ydl_opts["cookiefile"] = str(fresh_cf)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info and isinstance(info, dict) and (info.get("duration") or info.get("title")):
                    return info
        except Exception as exc:
            last_exc = exc
            continue

    # Final fallback: try pytubefix metadata if yt-dlp is completely blocked
    try:
        from pytubefix import YouTube
        yt = YouTube(url, client="ANDROID_VR")
        chapters_list = []
        if getattr(yt, "chapters", None):
            for ch in yt.chapters:
                st_sec = float(getattr(ch, "start_seconds", 0.0))
                dur_sec = float(getattr(ch, "duration", 20.0))
                chapters_list.append({
                    "start_time": st_sec,
                    "end_time": st_sec + dur_sec,
                    "title": getattr(ch, "title", "Chapter"),
                })
        return {
            "id": yt.video_id,
            "title": yt.title or "YouTube Video",
            "uploader": yt.author or "YouTube Channel",
            "duration": float(yt.length or 0.0),
            "view_count": int(yt.views or 0),
            "thumbnail": yt.thumbnail_url,
            "description": yt.description or "",
            "chapters": chapters_list,
            "heatmap": None,
        }
    except Exception:
        pass

    if last_exc:
        raise last_exc
    raise RuntimeError("Unable to fetch YouTube video metadata.")


def estimate_video_size(info: Optional[Dict[str, Any]], max_height: int = 720) -> int:
    """
    Estimate the file size (in bytes) of a video for a given resolution height before downloading.
    Inspects yt-dlp formats metadata (filesize, filesize_approx, tbr/vbr/abr bitrates)
    with adaptive bitrate fallbacks based on duration and target resolution.
    """
    if not info or not isinstance(info, dict):
        return 0

    duration = float(info.get("duration") or 0.0)

    # 1. Check formats list from yt-dlp
    formats = info.get("formats")
    if isinstance(formats, list) and len(formats) > 0:
        def _get_format_size(f: dict) -> Optional[int]:
            if f.get("filesize") and f["filesize"] > 0:
                return int(f["filesize"])
            if f.get("filesize_approx") and f["filesize_approx"] > 0:
                return int(f["filesize_approx"])
            tbr = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0))
            if tbr and tbr > 0 and duration > 0:
                return int((tbr * 1000 / 8) * duration)
            return None

        video_formats = [
            f for f in formats
            if f.get("vcodec") != "none" and (f.get("height") or 0) <= max_height
        ]
        audio_formats = [
            f for f in formats
            if f.get("acodec") != "none" and f.get("vcodec") == "none"
        ]
        progressive_formats = [
            f for f in formats
            if f.get("vcodec") != "none" and f.get("acodec") != "none" and (f.get("height") or 0) <= max_height
        ]

        if video_formats and audio_formats:
            def _v_rank(f: dict):
                is_mp4 = 1 if f.get("ext") == "mp4" else 0
                h = f.get("height") or 0
                sz = _get_format_size(f) or 0
                return (h, is_mp4, sz)

            def _a_rank(f: dict):
                is_m4a = 1 if f.get("ext") in ("m4a", "mp4") else 0
                abr = f.get("abr") or f.get("tbr") or 0
                sz = _get_format_size(f) or 0
                return (is_m4a, abr, sz)

            best_v = max(video_formats, key=_v_rank)
            best_a = max(audio_formats, key=_a_rank)

            v_sz = _get_format_size(best_v)
            a_sz = _get_format_size(best_a)

            if v_sz and a_sz:
                return v_sz + a_sz
            elif v_sz:
                audio_est = int((128 * 1000 / 8) * duration) if duration > 0 else 0
                return v_sz + audio_est

        if progressive_formats:
            def _p_rank(f: dict):
                is_mp4 = 1 if f.get("ext") == "mp4" else 0
                h = f.get("height") or 0
                sz = _get_format_size(f) or 0
                return (h, is_mp4, sz)

            best_p = max(progressive_formats, key=_p_rank)
            p_sz = _get_format_size(best_p)
            if p_sz:
                return p_sz

    if info.get("filesize") and info["filesize"] > 0:
        return int(info["filesize"])
    if info.get("filesize_approx") and info["filesize_approx"] > 0:
        return int(info["filesize_approx"])

    if duration > 0:
        bitrate_map = {
            360: 750_000,
            480: 1_250_000,
            720: 2_600_000,
            1080: 4_800_000,
        }
        bps = bitrate_map.get(max_height, int(max_height * 3600))
        return int((bps / 8) * duration)

    return 0


def estimate_clip_size(clip_duration: float, total_duration: float, total_size_bytes: int) -> int:
    """
    Estimate clip file size proportionally based on segment duration and full video size.
    """
    if total_duration <= 0 or total_size_bytes <= 0:
        return int((2_600_000 / 8) * max(0.0, clip_duration))
    ratio = max(0.0, clip_duration) / total_duration
    return int(total_size_bytes * ratio)


def extract_most_viewed_segments(
    heatmap: List[Dict[str, float]],
    video_duration: float,
    top_k: int = 5,
    min_score_threshold: float = 0.40,
    min_clip_duration: float = 10.0,
    max_clip_duration: float = 60.0,
    padding_seconds: float = 3.0,
    merge_gap_seconds: float = 5.0,
) -> List[HighlightSegment]:
    """
    Analyze YouTube's 'Most Replayed' heatmap array to extract the top K most-viewed
    timestamp intervals.

    Algorithm:
    1. Filter heatmap bins whose normalized intensity `value` >= `min_score_threshold`
       (or adaptively lower threshold if fewer than `top_k` peaks exist).
    2. Identify local maxima and contiguous high-engagement regions around each peak.
    3. Expand/clamp each region to `[min_clip_duration, max_clip_duration]` with optional padding.
    4. Merge overlapping or tightly adjacent intervals (`merge_gap_seconds`).
    5. Rank by peak replay score descending and return the top `top_k` segments.
    """
    if not heatmap or video_duration <= 0:
        return []

    bins = sorted(heatmap, key=lambda b: float(b.get("start_time", 0.0)))
    n = len(bins)

    candidate_indices = []
    for i in range(n):
        val = float(bins[i].get("value", 0.0))
        left_val = float(bins[i - 1].get("value", 0.0)) if i > 0 else -1.0
        right_val = float(bins[i + 1].get("value", 0.0)) if i < n - 1 else -1.0
        if val >= left_val and val >= right_val:
            candidate_indices.append(i)

    if not candidate_indices:
        candidate_indices = list(range(n))

    candidate_indices.sort(key=lambda idx: float(bins[idx].get("value", 0.0)), reverse=True)

    raw_intervals = []
    used_bins = set()

    for idx in candidate_indices:
        if idx in used_bins:
            continue
        peak_val = float(bins[idx].get("value", 0.0))
        if peak_val < min_score_threshold and len(raw_intervals) >= top_k:
            continue

        local_cutoff = max(min_score_threshold * 0.85, peak_val * 0.65)
        left = idx
        while left > 0 and float(bins[left - 1].get("value", 0.0)) >= local_cutoff:
            left -= 1
        right = idx
        while right < n - 1 and float(bins[right + 1].get("value", 0.0)) >= local_cutoff:
            right += 1

        for b_idx in range(left, right + 1):
            used_bins.add(b_idx)

        start_t = max(0.0, float(bins[left].get("start_time", 0.0)) - padding_seconds)
        end_t = min(video_duration, float(bins[right].get("end_time", video_duration)) + padding_seconds)
        peak_t = (float(bins[idx].get("start_time", 0.0)) + float(bins[idx].get("end_time", 0.0))) / 2.0

        cur_dur = end_t - start_t
        if cur_dur < min_clip_duration:
            deficit = min_clip_duration - cur_dur
            start_t = max(0.0, start_t - deficit / 2.0)
            end_t = min(video_duration, start_t + min_clip_duration)
            if end_t - start_t < min_clip_duration:
                start_t = max(0.0, end_t - min_clip_duration)
        elif cur_dur > max_clip_duration:
            half = max_clip_duration / 2.0
            start_t = max(0.0, peak_t - half)
            end_t = min(video_duration, start_t + max_clip_duration)
            if end_t - start_t < max_clip_duration:
                start_t = max(0.0, end_t - max_clip_duration)

        raw_intervals.append({
            "start_time": round(start_t, 2),
            "end_time": round(end_t, 2),
            "peak_time": round(peak_t, 2),
            "score": round(peak_val, 4),
        })

        if len(raw_intervals) >= top_k * 2:
            break

    raw_intervals.sort(key=lambda x: x["start_time"])
    merged: List[Dict[str, float]] = []
    for item in raw_intervals:
        if not merged:
            merged.append(item)
            continue
        prev = merged[-1]
        if item["start_time"] <= prev["end_time"] + merge_gap_seconds:
            combined_dur = max(prev["end_time"], item["end_time"]) - prev["start_time"]
            if combined_dur <= max_clip_duration:
                prev["end_time"] = max(prev["end_time"], item["end_time"])
                if item["score"] > prev["score"]:
                    prev["score"] = item["score"]
                    prev["peak_time"] = item["peak_time"]
            else:
                if item["score"] > prev["score"]:
                    merged[-1] = item
        else:
            merged.append(item)

    merged.sort(key=lambda x: x["score"], reverse=True)
    top_merged = merged[:top_k]

    segments: List[HighlightSegment] = []
    for rank_idx, seg in enumerate(top_merged, start=1):
        segments.append(
            HighlightSegment(
                rank=rank_idx,
                start_time=seg["start_time"],
                end_time=seg["end_time"],
                peak_time=seg["peak_time"],
                score=seg["score"],
                source="heatmap",
                label=f"Most Viewed #{rank_idx} ({int(round(seg['score'] * 100))}% intensity)",
            )
        )
    return segments


def extract_fallback_segments(
    info: Dict[str, Any],
    top_k: int = 5,
    clip_duration: float = 20.0,
) -> List[HighlightSegment]:
    """
    Fallback when YouTube does not provide `heatmap` (Most Replayed) metadata for a video:
    1. Try video chapters (`info.get('chapters')`).
    2. Try parsing timestamps in video description (`01:23`, `12:45`, etc.).
    3. Fallback to evenly spaced highlight windows across the video duration.
    """
    duration = float(info.get("duration") or 0.0)
    if duration <= 0:
        return []

    chapters = info.get("chapters")
    if chapters and isinstance(chapters, list):
        segments = []
        for idx, ch in enumerate(chapters[:top_k], start=1):
            st = float(ch.get("start_time", 0.0))
            et = min(duration, float(ch.get("end_time", st + clip_duration)))
            if et - st > clip_duration * 2:
                et = st + clip_duration
            title = ch.get("title") or f"Chapter {idx}"
            segments.append(
                HighlightSegment(
                    rank=idx,
                    start_time=round(st, 2),
                    end_time=round(et, 2),
                    peak_time=round((st + et) / 2.0, 2),
                    score=round(max(0.5, 1.0 - (idx - 1) * 0.1), 2),
                    source="chapters",
                    label=f"Chapter: {title}",
                )
            )
        if segments:
            return segments

    description = info.get("description") or ""
    ts_matches = re.findall(r"(?:^|\s)(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n]{3,60})", description)
    if ts_matches:
        segments = []
        for idx, (ts_raw, label_raw) in enumerate(ts_matches[:top_k], start=1):
            parts = [int(p) for p in ts_raw.split(":")]
            st = float(parts[0] * 60 + parts[1]) if len(parts) == 2 else float(parts[0] * 3600 + parts[1] * 60 + parts[2])
            if st >= duration:
                continue
            et = min(duration, st + clip_duration)
            segments.append(
                HighlightSegment(
                    rank=idx,
                    start_time=round(st, 2),
                    end_time=round(et, 2),
                    peak_time=round((st + et) / 2.0, 2),
                    score=round(max(0.4, 0.95 - (idx - 1) * 0.1), 2),
                    source="description_timestamps",
                    label=label_raw.strip(" -–—:•"),
                )
            )
        if segments:
            return segments

    segments = []
    step = duration / (top_k + 1)
    for idx in range(1, top_k + 1):
        center = step * idx
        st = max(0.0, center - clip_duration / 2.0)
        et = min(duration, st + clip_duration)
        segments.append(
            HighlightSegment(
                rank=idx,
                start_time=round(st, 2),
                end_time=round(et, 2),
                peak_time=round(center, 2),
                score=round(max(0.3, 0.85 - (idx - 1) * 0.1), 2),
                source="uniform_fallback",
                label=f"Segment #{idx} (Uniform Fallback)",
            )
        )
    return segments
