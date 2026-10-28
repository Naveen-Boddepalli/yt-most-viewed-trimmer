"""
Streamlit Frontend Application:
YouTube Most-Viewed Timestamp Extractor & Video Trimmer
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
from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import (
    format_timestamp,
    parse_timestamp,
    is_valid_youtube_url,
    sanitize_filename,
    format_bytes,
    strip_ansi,
    get_yt_cookies_file,
    sanitize_cookie_content,
)
from yt_processor import (
    HighlightSegment,
    fetch_video_metadata,
    extract_most_viewed_segments,
    extract_fallback_segments,
    estimate_video_size,
    estimate_clip_size,
)
from video_trimmer import (
    has_ffmpeg,
    ensure_ffmpeg,
    download_full_video,
    download_single_clip_section,
    download_and_trim_segments_direct,
    trim_clip_ffmpeg,
    trim_selected_segments,
    create_clips_zip,
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
    page_title="YT Most-Viewed Timestamp Trimmer",
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
        padding: 16px;
        margin-bottom: 14px;
    }
    .score-badge {
        background-color: #FF4B4B;
        color: white;
        padding: 3px 10px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 0.82rem;
    }
    .source-badge {
        background-color: #2B3548;
        color: #A6B4D0;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 0.78rem;
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
        "all_clips_zip_path": None,
        "workspace_dir": tempfile.mkdtemp(prefix="yt_trimmer_"),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if "youtube_cookies_text" not in st.session_state:
        saved_cf = Path("user_cookies.txt")
        if saved_cf.exists() and saved_cf.stat().st_size > 0:
            st.session_state["youtube_cookies_text"] = saved_cf.read_text(encoding="utf-8")
        else:
            st.session_state["youtube_cookies_text"] = ""


init_session_state()


# ------------------------------------------------------------------------------
# Helper: Interactive Heatmap Chart
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

    # Highlight extracted segments as shaded vertical rectangles
    colors = [
        "rgba(0, 212, 170, 0.28)",
        "rgba(255, 193, 7, 0.25)",
        "rgba(59, 130, 246, 0.25)",
        "rgba(168, 85, 247, 0.25)",
        "rgba(236, 72, 153, 0.25)",
    ]
    for idx, seg in enumerate(segments):
        c = colors[idx % len(colors)]
        fig.add_vrect(
            x0=seg.start_time,
            x1=seg.end_time,
            fillcolor=c,
            opacity=0.8,
            layer="below",
            line_width=1.5,
            line_color="#00D4AA" if idx == 0 else "#94A3B8",
            annotation_text=f"#{seg.rank}",
            annotation_position="top left",
        )

    fig.update_layout(
        title="YouTube 'Most Replayed' Engagement Curve & Extracted Highlight Windows",
        xaxis_title="Video Timeline (Seconds)",
        yaxis_title="Relative Replay Intensity (%)",
        yaxis=dict(range=[0, 108]),
        template="plotly_dark",
        height=340,
        margin=dict(l=30, r=20, t=45, b=35),
        hovermode="x unified",
    )
    return fig


# ------------------------------------------------------------------------------
# Sidebar Controls
# ------------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Extraction & Trim Settings")

    top_k = st.slider(
        "Number of Top Segments (K)",
        min_value=1,
        max_value=10,
        value=5,
        help="How many of the highest-viewed timestamp peaks to extract.",
    )
    min_score_pct = st.slider(
        "Minimum Replay Score Threshold (%)",
        min_value=10,
        max_value=90,
        value=40,
        step=5,
        help="Minimum heatmap intensity (0-100%) required for a peak to be considered.",
    )
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        min_clip_dur = st.number_input(
            "Min Clip (sec)",
            min_value=3,
            max_value=120,
            value=12,
            step=1,
        )
    with col_d2:
        max_clip_dur = st.number_input(
            "Max Clip (sec)",
            min_value=5,
            max_value=300,
            value=45,
            step=5,
        )

    padding_sec = st.slider(
        "Context Padding Around Peak (sec)",
        min_value=0,
        max_value=15,
        value=3,
        help="Extra lead-in and lead-out seconds added around each detected peak.",
    )

    st.divider()
    st.subheader("🎞️ Video Output Settings")

    info_loaded = st.session_state.get("video_info")

    def _res_label(r: int) -> str:
        if info_loaded:
            sz = estimate_video_size(info_loaded, r)
            return f"{r}p MP4 (~{format_bytes(sz)})"
        return f"{r}p MP4"

    max_resolution = st.selectbox(
        "Max Download Resolution",
        options=[360, 480, 720, 1080],
        index=2,
        format_func=_res_label,
    )

    if info_loaded:
        src_path_val = st.session_state.get("source_video_path")
        if src_path_val and Path(src_path_val).exists():
            actual_sz = Path(src_path_val).stat().st_size
            st.caption(f"💾 **Source video downloaded:** {format_bytes(actual_sz)}")
        else:
            est_sz = estimate_video_size(info_loaded, max_resolution)
            st.caption(f"📦 **Est. full video download:** ~{format_bytes(est_sz)} ({max_resolution}p)")

    fast_copy_mode = st.checkbox(
        "Fast Stream Copy (No Re-encode)",
        value=False,
        help="Uses FFmpeg `-c copy` for instant trimming. Uncheck for frame-accurate H.264 re-encoding.",
    )

    trim_strategy = st.radio(
        "Trimming Strategy",
        options=["⚡ Direct Stream-Trim (Only ~10-15 MB)", "📦 Download Full Video First"],
        index=0,
        help="Direct Stream-Trim downloads ONLY the needed clip segment without downloading the full multi-GB video. Highly recommended for cloud/online hosting!",
    )
    is_direct_stream_trim = "Direct Stream-Trim" in trim_strategy

    has_active_cookies = bool(get_yt_cookies_file())
    with st.expander(f"🍪 YouTube Cookies / Bot Bypass {'(Active ✅)' if has_active_cookies else ''}", expanded=not has_active_cookies):
        st.caption(
            "Cloud servers (Streamlit Cloud, AWS) are flagged by YouTube's bot detection. "
            "Pasting your `cookies.txt` here saves it persistently on this server so you never have to paste again."
        )
        cookie_text = st.text_area(
            "Paste cookies.txt content",
            value=st.session_state.get("youtube_cookies_text", ""),
            placeholder="# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...\t...",
            height=120,
            key="cookie_textarea_box",
            help="Export cookies from your browser using an extension like 'Get cookies.txt LOCALLY'.",
        )
        if cookie_text.strip() and cookie_text.strip() != st.session_state.get("youtube_cookies_text", ""):
            Path("user_cookies.txt").write_text(cookie_text.strip(), encoding="utf-8")
            st.session_state["youtube_cookies_text"] = cookie_text.strip()
            st.rerun()

        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            if has_active_cookies:
                st.success("✅ Cookies active & saved!")
        with col_c2:
            if has_active_cookies and st.button("🗑️ Clear Cookies", **STRETCH_WIDTH):
                if Path("user_cookies.txt").exists():
                    Path("user_cookies.txt").unlink()
                st.session_state["youtube_cookies_text"] = ""
                st.rerun()

    st.divider()
    active_ffmpeg = ensure_ffmpeg()
    if active_ffmpeg:
        st.success(f"✅ FFmpeg active: `{Path(active_ffmpeg).name}`")
    else:
        st.warning("⚠️ FFmpeg binary not found")

    if st.button("🗑️ Clear Temporary Workspace", **STRETCH_WIDTH):
        ws = Path(st.session_state["workspace_dir"])
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        st.session_state["workspace_dir"] = tempfile.mkdtemp(prefix="yt_trimmer_")
        st.session_state["video_info"] = None
        st.session_state["segments"] = []
        st.session_state["source_video_path"] = None
        st.session_state["trimmed_clips"] = {}
        st.session_state["all_clips_zip_path"] = None
        st.rerun()


# ------------------------------------------------------------------------------
# Main Header & URL Input
# ------------------------------------------------------------------------------
st.title("🎬 YouTube Most-Viewed Timestamp Extractor & Video Trimmer")
st.caption(
    "Load any YouTube video link, automatically detect its highest-replayed timestamp peaks "
    "(via YouTube's heatmap telemetry, with automatic fallback to chapters/timestamps), "
    "and trim either all highlights or any particular segment for instant preview and download."
)

url_col, btn_col, demo_col = st.columns([6, 2, 2])
with url_col:
    yt_url = st.text_input(
        "YouTube Video URL",
        value=st.session_state["video_url"],
        placeholder="https://www.youtube.com/watch?v=...",
        label_visibility="collapsed",
    )
with btn_col:
    analyze_clicked = st.button("🔍 Load & Extract Timestamps", type="primary", **STRETCH_WIDTH)
with demo_col:
    demo_clicked = st.button("🧪 Load Sample Heatmap Demo", **STRETCH_WIDTH)

active_cookie_file = get_yt_cookies_file()
if not active_cookie_file:
    with st.expander("🔑 YouTube Authentication (Required for Cloud Hosting)", expanded=True):
        st.warning(
            "⚠️ **No YouTube cookies detected**: YouTube blocks cloud servers (Streamlit Cloud, AWS) from streaming video formats without authentication.\n\n"
            "**To enable video trimming:**\n"
            "1. Export your YouTube cookies using the free Chrome extension **Get cookies.txt LOCALLY**.\n"
            "2. Paste the cookie text below and click **Save & Authenticate** (or add `YOUTUBE_COOKIES` in Streamlit Cloud Secrets)."
        )
        main_cookie_text = st.text_area(
            "Paste cookies.txt content here",
            placeholder="# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t...\t...",
            height=110,
            key="main_cookie_box",
        )
        if st.button("💾 Save & Authenticate Cookies", type="primary", key="save_main_cookie_btn"):
            if main_cookie_text.strip():
                sanitized = sanitize_cookie_content(main_cookie_text)
                Path("user_cookies.txt").write_text(sanitized, encoding="utf-8")
                st.session_state["youtube_cookies_text"] = sanitized
                st.success("✅ Cookies saved and activated! You can now trim videos.")
                st.rerun()
else:
    st.caption("✅ **YouTube Authenticated** (Cookies active on server)")


# ------------------------------------------------------------------------------
# Handle Demo Mode or Live URL Extraction
# ------------------------------------------------------------------------------
if demo_clicked:
    # Populate a synthetic demo video with realistic heatmap data so users can test UI immediately
    import math
    demo_duration = 300.0
    demo_heatmap = []
    for i in range(100):
        t0 = i * 3.0
        t1 = (i + 1) * 3.0
        # Create 4 pronounced peaks at 42s, 115s, 195s, 255s
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
        "title": "Sample Demo Video — Synthetic Most-Replayed Heatmap Analysis",
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
    st.session_state["all_clips_zip_path"] = None

if analyze_clicked:
    if not is_valid_youtube_url(yt_url):
        st.error("Please enter a valid YouTube video URL (e.g., `https://www.youtube.com/watch?v=...` or `https://youtu.be/...`).")
    else:
        st.session_state["video_url"] = yt_url.strip()
        with st.spinner("Fetching video metadata and 'Most Replayed' heatmap from YouTube..."):
            try:
                info = fetch_video_metadata(st.session_state["video_url"])
                st.session_state["video_info"] = info
                st.session_state["source_video_path"] = None
                st.session_state["trimmed_clips"] = {}
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
                st.error(f"Failed to load YouTube video metadata: {err_text}")
                if "bot" in err_text.lower() or "sign in" in err_text.lower() or "cookie" in err_text.lower():
                    st.warning(
                        "🔒 **YouTube Cloud Bot-Check**: Cloud datacenters (like AWS/Streamlit Cloud) are frequently flagged by YouTube's bot wall.\n\n"
                        "**3 Ways to resolve this:**\n"
                        "1. **Paste Cookies (Instant)**: Open **'🍪 YouTube Cookies / Bot Bypass'** in the left sidebar and paste your exported YouTube `cookies.txt`.\n"
                        "2. **Streamlit Secrets**: In your Streamlit Cloud Dashboard, go to **App Settings → Secrets** and add:\n"
                        "   ```toml\n"
                        "   YOUTUBE_COOKIES = '''<paste cookies.txt content here>'''\n"
                        "   ```\n"
                        "3. **Try Demo Mode**: Click **'🧪 Load Sample Heatmap Demo'** in the top-right to test video trimming & interactive charts immediately."
                    )


# ------------------------------------------------------------------------------
# Display Extracted Metadata, Heatmap & Trimming Controls
# ------------------------------------------------------------------------------
info = st.session_state.get("video_info")
segments: List[HighlightSegment] = st.session_state.get("segments", [])

if info:
    st.divider()
    meta_col1, meta_col2 = st.columns([1.3, 2.7])

    with meta_col1:
        thumb_url = info.get("thumbnail")
        if thumb_url:
            st.image(thumb_url, **STRETCH_WIDTH)
        if st.session_state.get("video_url"):
            st.video(st.session_state["video_url"])

    with meta_col2:
        st.subheader(info.get("title", "Untitled YouTube Video"))
        duration = float(info.get("duration") or 0.0)
        src_path_existing = st.session_state.get("source_video_path")
        is_src_downloaded = bool(src_path_existing and Path(src_path_existing).exists())

        if is_src_downloaded:
            source_size_bytes = Path(src_path_existing).stat().st_size
            size_metric_label = "Source Video Size"
            size_metric_val = format_bytes(source_size_bytes)
            size_metric_help = "Full source video is already downloaded & cached in workspace."
        else:
            source_size_bytes = estimate_video_size(info, max_resolution)
            size_metric_label = "Est. Download Size"
            size_metric_val = f"~{format_bytes(source_size_bytes)}"
            size_metric_help = f"Estimated download size for {max_resolution}p MP4 before trimming."

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Channel", info.get("uploader", "Unknown"))
        m2.metric("Duration", format_timestamp(duration))
        views = info.get("view_count")
        m3.metric("Views", f"{views:,}" if isinstance(views, int) else "N/A")
        m4.metric(size_metric_label, size_metric_val, help=size_metric_help)
        has_hm = bool(info.get("heatmap"))
        m5.metric("Data Source", "Heatmap" if has_hm else "Chapters / Fallback")

        if not has_hm:
            st.info(
                "ℹ️ YouTube has not published a 'Most Replayed' heatmap for this video yet. "
                "Segments below were extracted using video chapters / description timestamps fallback."
            )

        # Render interactive Plotly curve
        fig = build_heatmap_figure(info.get("heatmap") or [], segments)
        st.plotly_chart(fig, **STRETCH_WIDTH)

    # --------------------------------------------------------------------------
    # Extracted Timestamps Table & Custom Timestamp Adjustment
    # --------------------------------------------------------------------------
    st.subheader("🔥 Extracted Most-Viewed Timestamp Segments")

    if segments:
        trimmed_clips_map: Dict[int, Path] = st.session_state.get("trimmed_clips", {})
        df_rows = []
        for seg in segments:
            clip_file = trimmed_clips_map.get(seg.rank)
            if clip_file and Path(clip_file).exists():
                c_sz_str = f"✅ {format_bytes(Path(clip_file).stat().st_size)}"
            else:
                c_est = estimate_clip_size(seg.duration, duration, source_size_bytes)
                c_sz_str = f"~{format_bytes(c_est)}"

            df_rows.append({
                "Rank": f"#{seg.rank}",
                "Label": seg.label,
                "Start Time": format_timestamp(seg.start_time),
                "End Time": format_timestamp(seg.end_time),
                "Peak Moment": format_timestamp(seg.peak_time),
                "Duration (s)": f"{seg.duration:.1f}s",
                "Est. Clip Size": c_sz_str,
                "Replay Intensity": f"{int(round(seg.score * 100))}%",
                "Source": seg.source,
            })
        st.dataframe(pd.DataFrame(df_rows), hide_index=True, **STRETCH_WIDTH)

        # Action toolbar: Trim All vs Trim Particular Segment
        st.markdown("### ✂️ Trim & Download Video Segments")

        total_clips_est = sum(
            estimate_clip_size(seg.duration, duration, source_size_bytes)
            for seg in segments
        )
        if is_src_downloaded:
            st.info(
                f"💾 **Source video cached:** `{format_bytes(source_size_bytes)}` &nbsp;|&nbsp; "
                f"🎬 **Estimated highlights total:** `~{format_bytes(total_clips_est)}` ({len(segments)} clips)"
            )
        else:
            st.info(
                f"📥 **Estimated full video download:** `~{format_bytes(source_size_bytes)}` ({max_resolution}p) &nbsp;|&nbsp; "
                f"🎬 **Estimated highlights total:** `~{format_bytes(total_clips_est)}` ({len(segments)} clips)"
            )

        act_col1, act_col2, act_col3 = st.columns([2.5, 2.5, 2.2])

        with act_col1:
            trim_all_clicked = st.button(
                f"⚡ Trim All {len(segments)} Highlight Clips (~{format_bytes(total_clips_est)})",
                type="primary",
                **STRETCH_WIDTH,
            )

        with act_col2:
            selected_rank = st.selectbox(
                "Select a Particular Segment to Trim",
                options=[seg.rank for seg in segments],
                format_func=lambda r: next(
                    (
                        f"Rank #{s.rank}: {s.time_range_str} (~{format_bytes(estimate_clip_size(s.duration, duration, source_size_bytes))})"
                        for s in segments if s.rank == r
                    ),
                    f"Rank #{r}",
                ),
                label_visibility="collapsed",
            )

        with act_col3:
            target_seg_preview = next((s for s in segments if s.rank == selected_rank), None)
            target_est_str = (
                f" (~{format_bytes(estimate_clip_size(target_seg_preview.duration, duration, source_size_bytes))})"
                if target_seg_preview else ""
            )
            trim_single_clicked = st.button(
                f"🎯 Trim Only Segment #{selected_rank}{target_est_str}",
                **STRETCH_WIDTH,
            )

        # Optional custom manual timestamp trimmer expander
        with st.expander("➕ Or Trim a Custom Timestamp Range Manually"):
            c_col1, c_col2, c_col3 = st.columns([2, 2, 2])
            with c_col1:
                custom_start_str = st.text_input("Start Timestamp (MM:SS)", value="00:15")
            with c_col2:
                custom_end_str = st.text_input("End Timestamp (MM:SS)", value="00:35")
            with c_col3:
                st.write("")
                custom_trim_clicked = st.button("✂️ Add & Trim Custom Clip", **STRETCH_WIDTH)

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
                        st.rerun()
                except ValueError as err:
                    st.error(str(err))

        # Helper to ensure source video is downloaded (or generated in demo mode)
        def ensure_source_video() -> Path:
            ws = Path(st.session_state["workspace_dir"])
            existing = st.session_state.get("source_video_path")
            if existing and Path(existing).exists():
                return Path(existing)

            if info.get("is_demo"):
                # Generate a synthetic demo MP4 with FFmpeg testsrc so Demo Mode works offline too
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
            return dl_path

        def show_trim_error(prefix: str, exc: Exception):
            clean_msg = strip_ansi(str(exc))
            st.error(f"{prefix}: {clean_msg}")
            if any(term in clean_msg.lower() for term in ["bytes read", "nodename nor servname", "connection", "timeout", "incomplete"]):
                st.warning(
                    "💡 **Connection Interrupted**: YouTube dropped the streaming connection before the video finished downloading. "
                    "Resuming is enabled — **click Trim again to continue from where it stopped**, or select **480p / 360p** in the sidebar for a much faster download."
                )

        def trim_single_segment_smart(target_seg: HighlightSegment):
            ws = Path(st.session_state["workspace_dir"])
            clips_dir = ws / "trimmed_clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            existing_src = st.session_state.get("source_video_path")
            has_src = bool(existing_src and Path(existing_src).exists())

            if has_src or info.get("is_demo") or not is_direct_stream_trim:
                src_path = ensure_source_video()
                with st.spinner(f"Trimming Clip #{target_seg.rank} ({target_seg.time_range_str})..."):
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=[target_seg],
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                    )
            else:
                # Direct Stream-Trim: download ONLY the ~10-15 MB clip from YouTube
                safe_title = sanitize_filename(info.get("title", "video"))
                start_tag = format_timestamp(target_seg.start_time).replace(":", "m") + "s"
                end_tag = format_timestamp(target_seg.end_time).replace(":", "m") + "s"
                clip_filename = f"{safe_title}_rank{target_seg.rank}_{start_tag}_to_{end_tag}.mp4"
                clip_path = clips_dir / clip_filename
                prog_bar = st.progress(0.0, text=f"Direct streaming clip #{target_seg.rank} ({target_seg.time_range_str})...")

                try:
                    download_single_clip_section(
                        url=st.session_state["video_url"],
                        output_path=clip_path,
                        start_time=target_seg.start_time,
                        end_time=target_seg.end_time,
                        max_height=max_resolution,
                        progress_callback=lambda p, msg: prog_bar.progress(p, text=msg),
                    )
                    prog_bar.empty()
                    trimmed_map = {target_seg.rank: clip_path}
                except Exception as exc:
                    prog_bar.empty()
                    st.warning(f"ℹ️ Direct stream note: {strip_ansi(str(exc))[:220]}")
                    # Fallback to full download if stream slice fails
                    st.info("Direct stream slice fallback: downloading source video...")
                    src_path = ensure_source_video()
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=[target_seg],
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                    )

            st.session_state["trimmed_clips"].update(trimmed_map)
            zip_path = ws / f"{sanitize_filename(info.get('title', 'video'))}_most_viewed_clips.zip"
            create_clips_zip(st.session_state["trimmed_clips"], zip_path)
            st.session_state["all_clips_zip_path"] = zip_path

        # Execute Trim All
        if trim_all_clicked:
            try:
                ws = Path(st.session_state["workspace_dir"])
                clips_dir = ws / "trimmed_clips"
                existing_src = st.session_state.get("source_video_path")
                has_src = bool(existing_src and Path(existing_src).exists())

                if is_direct_stream_trim and not has_src and not info.get("is_demo"):
                    prog = st.progress(0.0, text="Direct streaming highlight clips (~10-15 MB each)...")
                    def _on_trim_prog(idx: int, total: int, msg: str):
                        prog.progress(idx / total, text=msg)
                    try:
                        trimmed_map = download_and_trim_segments_direct(
                            url=st.session_state["video_url"],
                            segments=segments,
                            clips_dir=clips_dir,
                            video_title=info.get("title", "video"),
                            max_height=max_resolution,
                            progress_callback=_on_trim_prog,
                        )
                        prog.empty()
                    except Exception as exc:
                        prog.empty()
                        st.info("Direct stream fallback: downloading source video...")
                        src_path = ensure_source_video()
                        trimmed_map = trim_selected_segments(
                            source_video=src_path,
                            segments=segments,
                            clips_dir=clips_dir,
                            video_title=info.get("title", "video"),
                            fast_copy=fast_copy_mode,
                            progress_callback=_on_trim_prog,
                        )
                else:
                    src_path = ensure_source_video()
                    prog = st.progress(0.0, text="Trimming highlight clips...")
                    def _on_trim_prog(idx: int, total: int, msg: str):
                        prog.progress(idx / total, text=msg)
                    trimmed_map = trim_selected_segments(
                        source_video=src_path,
                        segments=segments,
                        clips_dir=clips_dir,
                        video_title=info.get("title", "video"),
                        fast_copy=fast_copy_mode,
                        progress_callback=_on_trim_prog,
                    )
                    prog.empty()

                st.session_state["trimmed_clips"].update(trimmed_map)
                zip_path = ws / f"{sanitize_filename(info.get('title', 'video'))}_most_viewed_clips.zip"
                create_clips_zip(st.session_state["trimmed_clips"], zip_path)
                st.session_state["all_clips_zip_path"] = zip_path
                st.success(f"Successfully trimmed all {len(trimmed_map)} most-viewed clips!")
            except Exception as exc:
                show_trim_error("Error while trimming clips", exc)

        # Execute Trim Single Particular Clip
        if trim_single_clicked:
            target_seg = next((s for s in segments if s.rank == selected_rank), None)
            if target_seg:
                try:
                    trim_single_segment_smart(target_seg)
                    st.success(f"Trimmed Segment #{target_seg.rank} ({target_seg.time_range_str})!")
                except Exception as exc:
                    show_trim_error(f"Error trimming segment #{target_seg.rank}", exc)

        # ----------------------------------------------------------------------
        # Display Individual Segment Cards, Video Previews & Download Buttons
        # ----------------------------------------------------------------------
        trimmed_clips: Dict[int, Path] = st.session_state.get("trimmed_clips", {})
        zip_bundle = st.session_state.get("all_clips_zip_path")

        if zip_bundle and Path(zip_bundle).exists() and len(trimmed_clips) > 1:
            zip_sz = Path(zip_bundle).stat().st_size
            with open(zip_bundle, "rb") as zf_bytes:
                st.download_button(
                    label=f"📦 Download All Trimmed Clips ({len(trimmed_clips)} MP4s in .ZIP — {format_bytes(zip_sz)})",
                    data=zf_bytes.read(),
                    file_name=Path(zip_bundle).name,
                    mime="application/zip",
                    type="primary",
                    **STRETCH_WIDTH,
                )

        st.markdown("#### 🎞️ Individual Highlight Segments (Preview & Download Particular Clip)")
        grid_cols = st.columns(2)

        for idx, seg in enumerate(segments):
            col = grid_cols[idx % 2]
            with col:
                clip_file = trimmed_clips.get(seg.rank)
                is_trimmed = bool(clip_file and Path(clip_file).exists())
                if is_trimmed:
                    actual_clip_sz = Path(clip_file).stat().st_size
                    size_badge_html = f"<b>File Size:</b> <span style='color:#00D4AA; font-weight:600;'>{format_bytes(actual_clip_sz)}</span>"
                else:
                    est_clip_sz = estimate_clip_size(seg.duration, duration, source_size_bytes)
                    size_badge_html = f"<b>Est. Size:</b> ~{format_bytes(est_clip_sz)}"

                st.markdown(
                    f"""
                    <div class="clip-card">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                            <span style="font-size:1.08rem; font-weight:700;">#{seg.rank} — {seg.time_range_str}</span>
                            <span class="score-badge">🔥 {int(round(seg.score * 100))}% Replay Score</span>
                        </div>
                        <div style="font-size:0.88rem; color:#B0BAC9; margin-bottom:6px;">
                            <b>Peak Moment:</b> {format_timestamp(seg.peak_time)} &nbsp;|&nbsp;
                            <b>Duration:</b> {seg.duration:.1f}s &nbsp;|&nbsp;
                            {size_badge_html} &nbsp;|&nbsp;
                            <span class="source-badge">{seg.source}</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if is_trimmed:
                    st.video(str(clip_file))
                    actual_sz_str = format_bytes(Path(clip_file).stat().st_size)
                    with open(clip_file, "rb") as f_mp4:
                        st.download_button(
                            label=f"⬇️ Download Clip #{seg.rank} ({seg.time_range_str} — {actual_sz_str})",
                            data=f_mp4.read(),
                            file_name=Path(clip_file).name,
                            mime="video/mp4",
                            key=f"dl_btn_{seg.rank}",
                            **STRETCH_WIDTH,
                        )
                else:
                    est_clip_sz = estimate_clip_size(seg.duration, duration, source_size_bytes)
                    est_sz_str = format_bytes(est_clip_sz)
                    if st.button(
                        f"✂️ Trim & Prepare Clip #{seg.rank} ({seg.time_range_str} — ~{est_sz_str})",
                        key=f"inline_trim_{seg.rank}",
                        **STRETCH_WIDTH,
                    ):
                        try:
                            trim_single_segment_smart(seg)
                            st.rerun()
                        except Exception as exc:
                            show_trim_error(f"Error trimming Clip #{seg.rank}", exc)
