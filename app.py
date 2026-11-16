"""
Streamlit Frontend Application:
YouTube Most-Viewed Timestamp Extractor, Video Trimmer & Aspect-Ratio Cropper
"""

import os
import sys
from pathlib import Path

# Streamlit Cloud deployment safeguard: ensure app directory is at the head of sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    os.chdir(PROJECT_ROOT)
except Exception:
    pass

import shutil
import tempfile
import inspect
from typing import Dict, List, Optional

import plotly.graph_objects as go
import streamlit as st

from utils import (
    format_timestamp,
    parse_timestamp,
    is_valid_youtube_url,
    sanitize_filename,
    format_bytes,
    strip_ansi,
    save_yt_cookies,
    clear_yt_cookies,
    get_cookie_stats,
    load_persisted_cookies,
    is_single_user_mode,
    escape_html,
    safe_css_url,
    sanitize_environment,
)

# Strip oversized environment variables immediately on app start
sanitize_environment()
from yt_processor import (
    HighlightSegment,
    fetch_video_metadata,
    extract_most_viewed_segments,
    extract_fallback_segments,
    estimate_video_size,
    estimate_clip_size,
)
try:
    from video_trimmer import (
        CROP_PRESETS,
        CropConfig,
        compute_crop_box_normalized,
        get_video_dimensions,
        has_ffmpeg,
        ensure_ffmpeg,
        download_full_video,
        download_single_clip_section,
        trim_clip_ffmpeg,
        trim_selected_segments,
        create_clips_zip,
        YouTubeBlockedError,
    )
except ImportError:
    import importlib
    if "video_trimmer" in sys.modules:
        importlib.reload(sys.modules["video_trimmer"])
    from video_trimmer import (
        CROP_PRESETS,
        CropConfig,
        compute_crop_box_normalized,
        get_video_dimensions,
        has_ffmpeg,
        ensure_ffmpeg,
        download_full_video,
        download_single_clip_section,
        trim_clip_ffmpeg,
        trim_selected_segments,
        create_clips_zip,
        YouTubeBlockedError,
    )

# Streamlit >= 1.40 compatibility: replace deprecated use_container_width=True with width="stretch"
STRETCH_WIDTH = (
    {"width": "stretch"}
    if "width" in inspect.signature(st.button).parameters
    else {"use_container_width": True}
)


# ------------------------------------------------------------------------------
# Page Configuration & Styling
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="YT Highlight Trimmer",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .clip-card {
        background-color: #1E232E;
        border: 1px solid #2E3646;
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .clip-title {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 1.02rem;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .clip-meta {
        font-size: 0.85rem;
        color: #B0BAC9;
    }
    .score-badge {
        background-color: #FF4B4B;
        color: white;
        padding: 2px 9px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 0.78rem;
    }
    .crop-badge {
        background-color: #0F5132;
        color: #75B798;
        padding: 2px 9px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 0.75rem;
    }
    .crop-pending {
        color: #F59E0B;
        font-size: 0.8rem;
    }
    /* Keep vertical (9:16) clips from stretching the page */
    video {
        max-height: 420px;
        background: #000;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------------------
# Session State Initialization
# ------------------------------------------------------------------------------
def init_session_state():
    defaults = {
        "video_info": None,
        "video_url": "",
        "segments": [],
        "source_video_path": None,
        "trimmed_clips": {},       # rank -> Path
        "clip_crops": {},          # rank -> {"mode", "tag"} actually encoded into that clip
        "raw_clips": {},           # rank -> uncropped Path (for instant re-cropping)
        "all_clips_zip_path": None,
        "workspace_dir": tempfile.mkdtemp(prefix="yt_trimmer_"),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if "youtube_cookies_text" not in st.session_state:
        # Only this visitor's own cookies ever go in session state. Server cookies
        # are never pre-filled; disk restore happens only in opt-in single-user mode.
        st.session_state["youtube_cookies_text"] = load_persisted_cookies()

    if "_pending_cookie_box_val" in st.session_state:
        st.session_state["cookie_textarea_box"] = st.session_state.pop("_pending_cookie_box_val")
    elif "cookie_textarea_box" not in st.session_state:
        st.session_state["cookie_textarea_box"] = st.session_state["youtube_cookies_text"]


init_session_state()


# ------------------------------------------------------------------------------
# Helper: Interactive Heatmap Chart & Visual Crop Preview
# ------------------------------------------------------------------------------
def build_heatmap_figure(heatmap: List[Dict[str, float]], segments: List[HighlightSegment]) -> go.Figure:
    """Create a Plotly area chart showing the YouTube Most Replayed curve and extracted peak windows."""
    fig = go.Figure()

    if heatmap:
        x_times = [(float(b.get("start_time", 0.0)) + float(b.get("end_time", 0.0))) / 2.0 for b in heatmap]
        x_labels = [format_timestamp(t) for t in x_times]
        y_vals = [round(float(b.get("value", 0.0)) * 100, 1) for b in heatmap]

        fig.add_trace(
            go.Scatter(
                x=x_times,
                y=y_vals,
                mode="lines",
                fill="tozeroy",
                name="Replay Intensity (%)",
                line=dict(color="#FF4B4B", width=2.5),
                fillcolor="rgba(255, 75, 75, 0.22)",
                text=x_labels,
                hovertemplate="<b>Time:</b> %{text}<br><b>Replay Score:</b> %{y:.1f}%<extra></extra>",
            )
        )

    for idx, seg in enumerate(segments):
        fig.add_vrect(
            x0=seg.start_time,
            x1=seg.end_time,
            fillcolor="rgba(0, 212, 170, 0.18)",
            layer="below",
            line_width=0,
            annotation_text=f"#{seg.rank}",
            annotation_position="top left",
        )

    fig.update_layout(
        xaxis_title="Time (s)",
        yaxis_title="Replay %",
        yaxis=dict(range=[0, 108]),
        template="plotly_dark",
        height=280,
        margin=dict(l=10, r=10, t=10, b=30),
        hovermode="x unified",
        showlegend=False,
    )
    return fig


def render_crop_framing_preview(crop_cfg: CropConfig, thumb_url: Optional[str] = None):
    """Render a live CSS visual framing overlay showing the active crop region on a 16:9 canvas."""
    if not crop_cfg.is_active:
        return

    if crop_cfg.mode == "9:16_blur":
        st.caption("Full 16:9 frame centered on a 9:16 canvas with a blurred background.")
        return

    x0, y0, x1, y1 = compute_crop_box_normalized(crop_cfg, source_aspect=16.0 / 9.0)
    left_pct = round(x0 * 100, 1)
    top_pct = round(y0 * 100, 1)
    width_pct = round((x1 - x0) * 100, 1)
    height_pct = round((y1 - y0) * 100, 1)

    safe_thumb = safe_css_url(thumb_url)
    bg_style = (
        f"background-image: url('{safe_thumb}'); background-size: cover; background-position: center;"
        if safe_thumb
        else "background: linear-gradient(135deg, #1E293B 0%, #334155 100%);"
    )

    preview_html = f"""
    <div style="position:relative; width:100%; aspect-ratio:16/9; border-radius:8px; overflow:hidden; border:1px solid #334155; margin-bottom:6px; {bg_style}">
        <div style="
            position:absolute;
            left:{left_pct}%;
            top:{top_pct}%;
            width:{width_pct}%;
            height:{height_pct}%;
            border:2px solid #00D4AA;
            box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6);
        "></div>
    </div>
    """
    st.markdown(preview_html, unsafe_allow_html=True)
    st.caption(f"Crop preview: {escape_html(CROP_PRESETS.get(crop_cfg.mode, crop_cfg.mode))}")


# ------------------------------------------------------------------------------
# Sidebar Controls
# ------------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")

    top_k = st.slider(
        "Number of clips",
        min_value=1,
        max_value=10,
        value=5,
        help="How many of the highest-replayed moments to extract.",
    )
    min_score_pct = st.slider(
        "Minimum replay score (%)",
        min_value=10,
        max_value=90,
        value=40,
        step=5,
        help="Minimum heatmap intensity (0-100%) required for a peak to be considered.",
    )
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        min_clip_dur = st.number_input(
            "Min clip (s)",
            min_value=3,
            max_value=120,
            value=12,
            step=1,
        )
    with col_d2:
        max_clip_dur = st.number_input(
            "Max clip (s)",
            min_value=5,
            max_value=300,
            value=45,
            step=5,
        )

    padding_sec = st.slider(
        "Padding around peak (s)",
        min_value=0,
        max_value=15,
        value=3,
        help="Extra lead-in and lead-out seconds added around each detected peak.",
    )

    # --------------------------------------------------------------------------
    # Video Cropping & Framing
    # --------------------------------------------------------------------------
    st.divider()
    st.subheader("Crop")

    crop_mode_key = st.selectbox(
        "Aspect ratio",
        options=list(CROP_PRESETS.keys()),
        index=0,
        format_func=lambda k: CROP_PRESETS[k],
        help="Crop 16:9 videos to 9:16 (Shorts/Reels/TikTok), 1:1, 4:5, 4:3, or custom margins.",
    )

    pan_x_val = 0.5
    pan_y_val = 0.5
    custom_left = 0.0
    custom_right = 0.0
    custom_top = 0.0
    custom_bottom = 0.0

    if crop_mode_key in ("9:16", "1:1", "4:5", "4:3"):
        focus_choice = st.selectbox(
            "Subject position",
            options=["Center", "Left", "Right", "Custom"],
            index=0,
            help="Which part of the widescreen frame to keep inside the cropped video.",
        )
        if focus_choice == "Left":
            pan_x_val = 0.20
        elif focus_choice == "Right":
            pan_x_val = 0.80
        elif focus_choice == "Custom":
            pan_x_pct = st.slider("Horizontal pan (%)", 0, 100, 50, 1)
            pan_y_pct = st.slider("Vertical pan (%)", 0, 100, 50, 1)
            pan_x_val = pan_x_pct / 100.0
            pan_y_val = pan_y_pct / 100.0

    elif crop_mode_key == "custom":
        st.caption("Percentage to crop off each edge (0–45%):")
        cc1, cc2 = st.columns(2)
        with cc1:
            custom_left = float(st.slider("Left (%)", 0, 45, 15, 1))
            custom_top = float(st.slider("Top (%)", 0, 45, 0, 1))
        with cc2:
            custom_right = float(st.slider("Right (%)", 0, 45, 15, 1))
            custom_bottom = float(st.slider("Bottom (%)", 0, 45, 0, 1))

    active_crop_config = CropConfig(
        mode=crop_mode_key,
        pan_x=pan_x_val,
        pan_y=pan_y_val,
        left_pct=custom_left,
        right_pct=custom_right,
        top_pct=custom_top,
        bottom_pct=custom_bottom,
    )

    info_loaded = st.session_state.get("video_info")

    # --------------------------------------------------------------------------
    # Video Output Settings
    # --------------------------------------------------------------------------
    st.divider()
    st.subheader("Output")

    def _res_label(r: int) -> str:
        if info_loaded:
            sz = estimate_video_size(info_loaded, r)
            return f"{r}p (~{format_bytes(sz)} full video)"
        return f"{r}p"

    max_resolution = st.selectbox(
        "Max resolution",
        options=[360, 480, 720, 1080],
        index=2,
        format_func=_res_label,
        # Stable key: the option labels change once a video loads (size estimates),
        # which would otherwise give the widget a new identity and reset it to 720p.
        key="max_resolution_select",
    )

    trim_strategy = st.radio(
        "Download method",
        options=["Clip only (fast)", "Full video first"],
        index=0,
        help="'Clip only' streams just the needed section (~10-15 MB per clip). "
             "'Full video first' downloads the whole video, then trims locally.",
    )
    is_direct_stream_trim = trim_strategy.startswith("Clip only")

    fast_copy_mode = st.checkbox(
        "Fast stream copy (no re-encode)",
        value=False,
        disabled=active_crop_config.is_active,
        help="Uses FFmpeg `-c copy` for instant trimming. Unavailable while cropping, which requires re-encoding.",
    )

    cookie_stats = get_cookie_stats()
    has_active_cookies = cookie_stats.get("active", False)
    yt_count = cookie_stats.get("yt_count", 0)
    has_auth = cookie_stats.get("has_auth", False)
    cookie_source = cookie_stats.get("source")
    badge = " ✅" if has_active_cookies else ""
    with st.expander(f"YouTube cookies{badge}", expanded=False):
        st.caption("Only needed for age-restricted, private, or bot-blocked videos.")
        cookie_text = st.text_area(
            "Paste cookies.txt (Netscape, JSON, or DevTools)",
            placeholder="# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...",
            height=110,
            key="cookie_textarea_box",
            help="Export cookies while logged into YouTube with an extension such as 'Get cookies.txt LOCALLY'.",
        )

        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            if st.button("Save", key="save_sidebar_cookie_btn", **STRETCH_WIDTH):
                if cookie_text.strip():
                    sanitized = save_yt_cookies(cookie_text.strip())
                    st.session_state["youtube_cookies_text"] = sanitized
                    st.session_state["_pending_cookie_box_val"] = sanitized
                    st.rerun()
        with col_c2:
            if cookie_source == "session" and st.button("Clear", key="clear_sidebar_cookie_btn", **STRETCH_WIDTH):
                clear_yt_cookies(st.session_state.get("youtube_cookies_text"))
                st.session_state["youtube_cookies_text"] = ""
                st.session_state["_pending_cookie_box_val"] = ""
                st.rerun()

        if has_active_cookies:
            status = "logged-in session" if has_auth else "no login session found"
            if cookie_source == "server":
                where = "provided by the app operator"
            elif is_single_user_mode():
                where = "saved on this machine"
            else:
                where = "kept in this browser session only"
            st.caption(f"{yt_count} cookies, {status}, {where}.")
        elif cookie_stats.get("error"):
            st.error(f"Could not parse cookies: {cookie_stats['error']}")

    active_ffmpeg = ensure_ffmpeg()
    if not active_ffmpeg:
        st.error("FFmpeg not found. Trimming will not work until it is installed.")

    st.divider()
    if st.button("Reset workspace", **STRETCH_WIDTH, help="Delete downloaded and trimmed files and start over."):
        ws = Path(st.session_state["workspace_dir"])
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        st.session_state["workspace_dir"] = tempfile.mkdtemp(prefix="yt_trimmer_")
        st.session_state["video_info"] = None
        st.session_state["segments"] = []
        st.session_state["source_video_path"] = None
        st.session_state["trimmed_clips"] = {}
        st.session_state["clip_crops"] = {}
        st.session_state["raw_clips"] = {}
        st.session_state["all_clips_zip_path"] = None
        st.rerun()


# ------------------------------------------------------------------------------
# Main Header & URL Input
# ------------------------------------------------------------------------------
st.title("🎬 YouTube Highlight Trimmer")
st.caption("Find a video's most-replayed moments, crop them for Shorts or Reels, and download the clips.")

url_col, btn_col, demo_col = st.columns([6, 1.4, 1.2])
with url_col:
    yt_url = st.text_input(
        "YouTube Video URL",
        value=st.session_state["video_url"],
        placeholder="Paste a YouTube link: https://www.youtube.com/watch?v=...",
        label_visibility="collapsed",
    )
with btn_col:
    analyze_clicked = st.button("Analyze", type="primary", **STRETCH_WIDTH)
with demo_col:
    demo_clicked = st.button("Try demo", **STRETCH_WIDTH)


# ------------------------------------------------------------------------------
# Handle Demo Mode or Live URL Extraction
# ------------------------------------------------------------------------------
if demo_clicked:
    import math
    demo_duration = 300.0
    demo_heatmap = []
    for i in range(100):
        t0 = i * 3.0
        t1 = (i + 1) * 3.0
        val = (
            0.15
            + 0.83 * math.exp(-((t0 - 42.0) ** 2) / 120.0)
            + 0.72 * math.exp(-((t0 - 115.0) ** 2) / 160.0)
            + 0.91 * math.exp(-((t0 - 195.0) ** 2) / 140.0)
            + 0.64 * math.exp(-((t0 - 255.0) ** 2) / 100.0)
        )
        demo_heatmap.append({"start_time": t0, "end_time": t1, "value": min(1.0, round(val, 4))})

    demo_info = {
        "id": "demo_sample_01",
        "title": "Sample Demo Video",
        "uploader": "Demo Channel",
        "duration": demo_duration,
        "view_count": 1_420_000,
        "thumbnail": "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        "heatmap": demo_heatmap,
        "is_demo": True,
    }
    st.session_state["video_info"] = demo_info
    st.session_state["video_url"] = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    st.session_state["segments"] = extract_most_viewed_segments(
        heatmap=demo_heatmap,
        video_duration=demo_duration,
        top_k=top_k,
        min_score_threshold=min_score_pct / 100.0,
        min_clip_duration=float(min_clip_dur),
        max_clip_duration=float(max_clip_dur),
        padding_seconds=float(padding_sec),
    )
    st.session_state["trimmed_clips"] = {}
    st.session_state["clip_crops"] = {}
    st.session_state["raw_clips"] = {}
    st.session_state["all_clips_zip_path"] = None

if analyze_clicked:
    if not is_valid_youtube_url(yt_url):
        st.error("Please enter a valid YouTube video URL (e.g. `https://www.youtube.com/watch?v=...` or `https://youtu.be/...`).")
    else:
        st.session_state["video_url"] = yt_url.strip()
        with st.spinner("Fetching video details..."):
            try:
                info = fetch_video_metadata(st.session_state["video_url"])
                st.session_state["video_info"] = info
                st.session_state["source_video_path"] = None
                st.session_state["trimmed_clips"] = {}
                st.session_state["clip_crops"] = {}
                st.session_state["raw_clips"] = {}
                st.session_state["all_clips_zip_path"] = None

                heatmap = info.get("heatmap")
                duration = float(info.get("duration") or 0.0)

                if heatmap and isinstance(heatmap, list):
                    segs = extract_most_viewed_segments(
                        heatmap=heatmap,
                        video_duration=duration,
                        top_k=top_k,
                        min_score_threshold=min_score_pct / 100.0,
                        min_clip_duration=float(min_clip_dur),
                        max_clip_duration=float(max_clip_dur),
                        padding_seconds=float(padding_sec),
                    )
                else:
                    segs = []

                if not segs:
                    segs = extract_fallback_segments(
                        info=info,
                        top_k=top_k,
                        clip_duration=float(min(max_clip_dur, max(min_clip_dur, 20))),
                    )
                st.session_state["segments"] = segs
            except Exception as exc:
                err_text = str(exc)
                st.error(f"Failed to load the video: {err_text}")
                if "bot" in err_text.lower() or "sign in" in err_text.lower() or "cookie" in err_text.lower():
                    st.caption(
                        "YouTube is blocking this request. Paste your YouTube cookies under "
                        "**YouTube cookies** in the sidebar, or click **Try demo** to test the app."
                    )


# ------------------------------------------------------------------------------
# Display Extracted Metadata, Heatmap & Trimming Controls
# ------------------------------------------------------------------------------
def record_trimmed_clips(trimmed_map: Dict[int, Path], crop_cfg: CropConfig) -> None:
    """Store trimmed clip paths along with the crop actually encoded into each file."""
    st.session_state["trimmed_clips"].update(trimmed_map)
    encoded = {
        "mode": crop_cfg.mode if crop_cfg.is_active else "none",
        "tag": crop_cfg.file_tag if crop_cfg.is_active else "",
    }
    crops = st.session_state.setdefault("clip_crops", {})
    for rank in trimmed_map:
        crops[rank] = dict(encoded)


def get_valid_source_path() -> Optional[Path]:
    """Downloaded source video, unless it was fetched at a different max resolution."""
    existing = st.session_state.get("source_video_path")
    if not existing or not Path(existing).exists():
        return None
    if (st.session_state.get("video_info") or {}).get("is_demo"):
        return Path(existing)
    if st.session_state.get("source_video_height") != max_resolution:
        return None
    return Path(existing)


def raw_clip_cache_key(seg: HighlightSegment, height: int) -> str:
    """Raw (uncropped) clip cache key: same segment window AND same download resolution."""
    return f"{seg.rank}|{seg.start_time:.2f}|{seg.end_time:.2f}|{height}p"


info = st.session_state.get("video_info")
segments: List[HighlightSegment] = st.session_state.get("segments", [])

if info:
    st.divider()
    meta_col1, meta_col2 = st.columns([1.3, 2.7])

    duration = float(info.get("duration") or 0.0)
    src_path_existing = get_valid_source_path()
    is_src_downloaded = src_path_existing is not None
    if is_src_downloaded:
        source_size_bytes = Path(src_path_existing).stat().st_size
    else:
        source_size_bytes = estimate_video_size(info, max_resolution)
    has_hm = bool(info.get("heatmap"))

    with meta_col1:
        if active_crop_config.is_active:
            render_crop_framing_preview(active_crop_config, thumb_url=info.get("thumbnail"))
        elif st.session_state.get("video_url"):
            st.video(st.session_state["video_url"])

    with meta_col2:
        st.subheader(info.get("title", "Untitled YouTube Video"))
        views = info.get("view_count")
        meta_bits = [
            escape_html(info.get("uploader") or "Unknown channel"),
            format_timestamp(duration),
            f"{views:,} views" if isinstance(views, int) else None,
            (
                f"{format_bytes(source_size_bytes)} downloaded"
                if is_src_downloaded
                else f"~{format_bytes(source_size_bytes)} at {max_resolution}p"
            ),
        ]
        st.caption(" · ".join(b for b in meta_bits if b))

        if has_hm:
            st.plotly_chart(
                build_heatmap_figure(info.get("heatmap") or [], segments),
                config={"displayModeBar": False},
                **STRETCH_WIDTH,
            )
        else:
            st.info(
                "This video has no 'Most Replayed' data yet, so clips were picked "
                "from its chapters or description timestamps instead."
            )

    # --------------------------------------------------------------------------
    # Highlight clips
    # --------------------------------------------------------------------------
    def render_custom_clip_expander():
        """Custom range trimmer. Adds the segment and queues it to be trimmed on the next run."""
        with st.expander("Add a custom clip", expanded=not segments):
            c_col1, c_col2, c_col3 = st.columns([2, 2, 2], vertical_alignment="bottom")
            with c_col1:
                custom_start_str = st.text_input("Start (MM:SS)", value="00:15")
            with c_col2:
                custom_end_str = st.text_input("End (MM:SS)", value="00:35")
            with c_col3:
                custom_trim_clicked = st.button("Add & trim", **STRETCH_WIDTH)

            if custom_trim_clicked:
                try:
                    c_start = parse_timestamp(custom_start_str)
                    c_end = parse_timestamp(custom_end_str)
                    max_dur = float(info.get("duration") or 0.0)
                    if c_end <= c_start or c_start < 0 or (max_dur > 0 and c_end > max_dur):
                        st.warning("Ensure `0 <= Start < End <= Video Duration`.")
                    else:
                        new_rank = max([s.rank for s in segments], default=0) + 1
                        custom_seg = HighlightSegment(
                            rank=new_rank,
                            start_time=round(c_start, 2),
                            end_time=round(c_end, 2),
                            peak_time=round((c_start + c_end) / 2.0, 2),
                            score=1.0,
                            source="custom",
                            label=f"Custom Clip #{new_rank}",
                        )
                        st.session_state["segments"].append(custom_seg)
                        st.session_state["_pending_custom_trim_rank"] = new_rank
                        st.rerun()
                except ValueError as err:
                    st.error(str(err))

    if segments:
        st.subheader("Highlights")

        total_clips_est = sum(
            estimate_clip_size(seg.duration, duration, source_size_bytes)
            for seg in segments
        )
        trim_all_clicked = st.button(
            f"✂️ Trim all {len(segments)} clips (~{format_bytes(total_clips_est)})",
            type="primary",
        )

        render_custom_clip_expander()

        # Helper to ensure source video is downloaded (or generated in demo mode)
        def ensure_source_video() -> Path:
            ws = Path(st.session_state["workspace_dir"])
            existing = get_valid_source_path()
            if existing:
                return existing

            if info.get("is_demo"):
                demo_mp4 = ws / "demo_source.mp4"
                if not demo_mp4.exists() and has_ffmpeg():
                    import subprocess
                    ffmpeg_bin = ensure_ffmpeg() or "ffmpeg"
                    subprocess.run(
                        [
                            ffmpeg_bin, "-y",
                            "-f", "lavfi", "-i", "testsrc=duration=300:size=640x360:rate=24",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=300",
                            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest",
                            str(demo_mp4),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                st.session_state["source_video_path"] = demo_mp4
                return demo_mp4

            prog_bar = st.progress(0.0, text="Starting YouTube video download...")
            dl_path = download_full_video(
                url=st.session_state["video_url"],
                output_dir=ws / "downloads",
                max_height=max_resolution,
                progress_callback=lambda p, msg: prog_bar.progress(p, text=msg),
            )
            prog_bar.empty()
            st.session_state["source_video_path"] = dl_path
            st.session_state["source_video_height"] = max_resolution
            return dl_path

        def show_trim_error(prefix: str, exc: Exception):
            clean_msg = strip_ansi(str(exc))
            st.error(f"{prefix}: {clean_msg}")
            if isinstance(exc, YouTubeBlockedError):
                cookie_source = get_cookie_stats().get("source")
                if cookie_source:
                    st.caption(
                        "YouTube's bot check rejected this server even with the "
                        f"{'cookies you pasted' if cookie_source == 'session' else 'server cookies'}. "
                        "They have probably expired or been rotated. Export fresh ones from a private/incognito "
                        "window (then close it) and paste them under **YouTube cookies** in the sidebar."
                    )
                else:
                    st.caption(
                        "YouTube blocks most cloud servers that don't present cookies from a signed-in account. "
                        "Paste your YouTube cookies under **YouTube cookies** in the sidebar, or, if you run this "
                        "app, add them as the `YOUTUBE_COOKIES` secret in your Streamlit Cloud app settings. "
                        "Running the app on your own computer also avoids this."
                    )
                with st.expander("Why each download attempt failed"):
                    wrap_kw = {"wrap_lines": True} if "wrap_lines" in inspect.signature(st.code).parameters else {}
                    st.code("\n".join(dict.fromkeys(exc.reasons)) or "(no details)", language=None, **wrap_kw)
            elif "403" in clean_msg or "bot" in clean_msg.lower() or "sign in" in clean_msg.lower():
                st.caption(
                    "YouTube blocked this download. If the video is age-restricted or private, "
                    "paste fresh cookies under **YouTube cookies** in the sidebar."
                )

        def rebuild_zip_bundle():
            ws = Path(st.session_state["workspace_dir"])
            zip_path = ws / f"{sanitize_filename(info.get('title', 'video'))}_most_viewed_clips.zip"
            create_clips_zip(st.session_state["trimmed_clips"], zip_path)
            st.session_state["all_clips_zip_path"] = zip_path

        def trim_single_segment_smart(target_seg: HighlightSegment, rebuild_zip: bool = True):
            ws = Path(st.session_state["workspace_dir"])
            clips_dir = ws / "trimmed_clips"
            raw_dir = ws / "raw_clips_cache"
            clips_dir.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)

            has_src = get_valid_source_path() is not None
            raw_key = raw_clip_cache_key(target_seg, max_resolution)
            cached_raw = st.session_state.setdefault("raw_clips", {}).get(raw_key)
            has_cached_raw = bool(cached_raw and Path(cached_raw).exists() and Path(cached_raw).stat().st_size > 0)

            safe_title = sanitize_filename(info.get("title", "video"))
            start_tag = format_timestamp(target_seg.start_time).replace(":", "m") + "s"
            end_tag = format_timestamp(target_seg.end_time).replace(":", "m") + "s"
            crop_tag = active_crop_config.file_tag if active_crop_config else ""
            clip_filename = f"{safe_title}_rank{target_seg.rank}_{start_tag}_to_{end_tag}{crop_tag}.mp4"
            clip_path = clips_dir / clip_filename

            # 1. If we already have the uncropped raw clip cached locally, crop/copy from it in <1s without network!
            if has_cached_raw and not has_src:
                with st.spinner(f"Applying crop ({active_crop_config.mode}) to cached Clip #{target_seg.rank}..."):
                    trim_clip_ffmpeg(
                        source_video=Path(cached_raw),
                        output_path=clip_path,
                        start_time=0.0,
                        end_time=target_seg.duration,
                        fast_copy=not active_crop_config.is_active,
                        crop_config=active_crop_config,
                    )
                    trimmed_map = {target_seg.rank: clip_path}
            elif has_src or info.get("is_demo") or not is_direct_stream_trim:
                src_path = ensure_source_video()
                with st.spinner(f"Trimming Clip #{target_seg.rank} ({target_seg.time_range_str})..."):
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=[target_seg],
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                        crop_config=active_crop_config,
                    )
            else:
                raw_clip_path = raw_dir / f"{safe_title}_rank{target_seg.rank}_{start_tag}_to_{end_tag}_{max_resolution}p_raw.mp4"
                prog_bar = st.progress(0.0, text=f"Downloading clip #{target_seg.rank} ({target_seg.time_range_str})...")
                try:
                    download_single_clip_section(
                        url=st.session_state["video_url"],
                        output_path=raw_clip_path,
                        start_time=target_seg.start_time,
                        end_time=target_seg.end_time,
                        max_height=max_resolution,
                        crop_config=None,
                        progress_callback=lambda p, msg: prog_bar.progress(max(0.0, min(1.0, float(p))), text=msg),
                    )
                    prog_bar.empty()
                    st.session_state["raw_clips"][raw_key] = raw_clip_path
                    if active_crop_config.is_active:
                        with st.spinner(f"Applying {active_crop_config.mode} crop to Clip #{target_seg.rank}..."):
                            trim_clip_ffmpeg(
                                source_video=raw_clip_path,
                                output_path=clip_path,
                                start_time=0.0,
                                end_time=target_seg.duration,
                                fast_copy=False,
                                crop_config=active_crop_config,
                            )
                    else:
                        shutil.copy2(raw_clip_path, clip_path)
                    trimmed_map = {target_seg.rank: clip_path}
                except YouTubeBlockedError:
                    # The full download uses the same clients, so it would only spend
                    # minutes reaching the same block. Report it straight away.
                    prog_bar.empty()
                    raise
                except Exception:
                    prog_bar.empty()
                    st.toast("Clip-only download failed, downloading the full video instead...")
                    src_path = ensure_source_video()
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=[target_seg],
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                        crop_config=active_crop_config,
                    )

            record_trimmed_clips(trimmed_map, active_crop_config)
            if rebuild_zip:
                rebuild_zip_bundle()
            return trimmed_map

        # Execute Trim All
        if trim_all_clicked:
            try:
                ws = Path(st.session_state["workspace_dir"])
                clips_dir = ws / "trimmed_clips"
                has_src = get_valid_source_path() is not None

                if is_direct_stream_trim and not has_src and not info.get("is_demo"):
                    # Per-clip smart path: caches each uncropped raw clip so later
                    # re-crops are local, and falls back to a full download if needed.
                    prog = st.progress(0.0, text="Downloading highlight clips...")
                    trimmed_map = {}
                    for clip_idx, seg_item in enumerate(segments, start=1):
                        prog.progress(
                            (clip_idx - 1) / max(1, len(segments)),
                            text=f"Clip #{seg_item.rank} ({seg_item.time_range_str}) [{clip_idx}/{len(segments)}]...",
                        )
                        trimmed_map.update(trim_single_segment_smart(seg_item, rebuild_zip=False))
                    prog.empty()
                else:
                    src_path = ensure_source_video()
                    prog = st.progress(0.0, text="Trimming highlight clips...")
                    def _on_trim_prog(idx: int, total: int, msg: str):
                        prog.progress(max(0.0, min(1.0, idx / max(1, total))), text=msg)
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=segments,
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                        crop_config=active_crop_config,
                        progress_callback=_on_trim_prog,
                    )
                    prog.empty()
                    record_trimmed_clips(trimmed_map, active_crop_config)

                rebuild_zip_bundle()
                st.toast(f"Trimmed {len(trimmed_map)} clips")
            except Exception as exc:
                show_trim_error("Error while trimming clips", exc)

        # Trim a custom clip queued by "Add & trim" on the previous run
        pending_custom_rank = st.session_state.pop("_pending_custom_trim_rank", None)
        if pending_custom_rank is not None:
            pending_seg = next((s for s in segments if s.rank == pending_custom_rank), None)
            if pending_seg:
                try:
                    trim_single_segment_smart(pending_seg)
                    st.toast(f"Trimmed custom clip #{pending_seg.rank}")
                except Exception as exc:
                    show_trim_error(f"Error trimming custom clip #{pending_seg.rank}", exc)

        # ----------------------------------------------------------------------
        # Clip cards: preview, trim and download
        # ----------------------------------------------------------------------
        trimmed_clips: Dict[int, Path] = st.session_state.get("trimmed_clips", {})
        zip_bundle = st.session_state.get("all_clips_zip_path")

        if zip_bundle and Path(zip_bundle).exists() and len(trimmed_clips) > 1:
            zip_sz = Path(zip_bundle).stat().st_size
            with open(zip_bundle, "rb") as zf_bytes:
                st.download_button(
                    label=f"📦 Download all {len(trimmed_clips)} clips as ZIP ({format_bytes(zip_sz)})",
                    data=zf_bytes.read(),
                    file_name=Path(zip_bundle).name,
                    mime="application/zip",
                    type="primary",
                )

        grid_cols = st.columns(2)
        sidebar_tag = active_crop_config.file_tag if active_crop_config.is_active else ""

        for idx, seg in enumerate(segments):
            col = grid_cols[idx % 2]
            with col:
                clip_file = trimmed_clips.get(seg.rank)
                is_trimmed = bool(clip_file and Path(clip_file).exists())
                if is_trimmed:
                    size_str = format_bytes(Path(clip_file).stat().st_size)
                else:
                    size_str = f"~{format_bytes(estimate_clip_size(seg.duration, duration, source_size_bytes))}"

                crop_pill = ""
                if is_trimmed:
                    # Badge shows the crop encoded into the saved file, not the sidebar selection
                    dims = get_video_dimensions(Path(clip_file))
                    dims_str = f" {dims[0]}×{dims[1]}" if dims else ""
                    encoded_crop = st.session_state.get("clip_crops", {}).get(seg.rank, {"mode": "none", "tag": ""})
                    encoded_label = encoded_crop["mode"] if encoded_crop["mode"] != "none" else "original"
                    crop_pill = f" · <span class='crop-badge'>{escape_html(encoded_label)}{dims_str}</span>"
                    if encoded_crop.get("tag", "") != sidebar_tag:
                        pending_label = active_crop_config.mode if active_crop_config.is_active else "original"
                        crop_pill += (
                            f" <span class='crop-pending'>(re-trim to apply {escape_html(pending_label)})</span>"
                        )

                score_html = (
                    f'<span class="score-badge">🔥 {int(round(seg.score * 100))}%</span>'
                    if seg.source != "custom"
                    else '<span class="score-badge">custom</span>'
                )
                card_html = (
                    f'<div class="clip-card">'
                    f'<div class="clip-title"><span>#{escape_html(seg.rank)} · {escape_html(seg.time_range_str)}</span>{score_html}</div>'
                    f'<div class="clip-meta">{seg.duration:.0f}s · {size_str}{crop_pill}</div>'
                    f'</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)

                if is_trimmed:
                    st.video(str(clip_file))
                    dl_col, recrop_col = st.columns([2, 1])
                    with dl_col:
                        with open(clip_file, "rb") as f_mp4:
                            st.download_button(
                                label=f"⬇️ Download ({size_str})",
                                data=f_mp4.read(),
                                file_name=Path(clip_file).name,
                                mime="video/mp4",
                                key=f"dl_btn_{seg.rank}",
                                **STRETCH_WIDTH,
                            )
                    with recrop_col:
                        if st.button(
                            "Re-trim",
                            key=f"recrop_btn_{seg.rank}",
                            help="Trim this clip again with the current sidebar crop settings.",
                            **STRETCH_WIDTH,
                        ):
                            try:
                                trim_single_segment_smart(seg)
                                st.rerun()
                            except Exception as exc:
                                show_trim_error(f"Error re-cropping Clip #{seg.rank}", exc)
                else:
                    if st.button(
                        "✂️ Trim",
                        key=f"inline_trim_{seg.rank}",
                        **STRETCH_WIDTH,
                    ):
                        try:
                            trim_single_segment_smart(seg)
                            st.rerun()
                        except Exception as exc:
                            show_trim_error(f"Error trimming Clip #{seg.rank}", exc)
    else:
        st.info("No highlights were found for this video. Add a custom clip below to trim it manually.")
        render_custom_clip_expander()
