---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
<!-- refreshed: 2026-11-11 -->
# Architecture

**Analysis Date:** 2026-11-11

## System Overview

```text
+-------------------------------------------------------------------+
|                   Streamlit UI & Orchestration                    |
|         `streamlit_app.py` (Cloud wrapper) -> `app.py`            |
+-----------------------+--------------------------+----------------+
                        |                          |
                        v                          v
+-------------------------------+  +--------------------------------+
|  YouTube Metadata & Segments  |  | Video Download, Trim & Cropper |
|       `yt_processor.py`       |  |       `video_trimmer.py`       |
+---------------+---------------+  +---------------+----------------+
                |                                  |
                +----------------+-----------------+
                                 |
                                 v
+-------------------------------------------------------------------+
|                        Shared Utilities                           |
|   `utils.py` (cookies, env sanitization, timestamps, JS runtimes) |
+-------------------------------------------------------------------+
                                 |
                                 v
+-------------------------------------------------------------------+
|           External Services & Local System Tools                  |
|  YouTube / googlevideo (`yt-dlp`, `pytubefix`), `ffmpeg`, `node`  |
+-------------------------------------------------------------------+
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Cloud Entrypoint | Ensures `sys.path`/`cwd`, sanitizes environment, executes `app.py` | `streamlit_app.py` |
| Web UI & Orchestrator | Sidebar settings, heatmap Plotly chart, crop preview, clip cards, batch/single trim orchestration, downloads | `app.py` |
| Metadata & Peak Extractor | Multi-client YouTube metadata fetch, heatmap peak extraction, chapter/description/uniform fallbacks, size estimators | `yt_processor.py` |
| Stream Trimmer & Cropper | 4-tier cloud-resilient stream trimming, HLS slicer, HTTP Range relay, FFmpeg/MoviePy cutting & cropping, ZIP bundling | `video_trimmer.py` |
| Shared Utilities | Timestamp & byte formatting, YouTube URL validation, multi-format cookie sanitization/isolation, env pruning, JS runtime lookup | `utils.py` |

## Pattern Overview

**Overall:** Modular Monolith (single-process Streamlit application with clear separation between UI orchestration, metadata analysis, media processing, and utilities)

**Key Characteristics:**
- Stateless server script rerun model backed by per-session `st.session_state` and a per-session temporary directory (`workspace_dir`)
- Multi-tier resilience cascade for YouTube access (isolated single-client profiles separating cookieless clients `visionos`/`android` from cookie-authenticated clients `web`/`web_embedded`/`tv_samsung`, plus `pytubefix` fallback)
- Zero-network FFmpeg execution model in Tiers 1 & 2 (`slice_hls_playlist_to_file` and `run_http_range_relay_trim`), keeping external HTTP requests inside Python (`ydl.urlopen`) so `ffmpeg` never encounters remote HTTP 403 / exit code 8 failures

## Layers

**Presentation & Orchestration Layer:**
- Purpose: Renders interactive controls, engagement charts, crop overlay previews, and executes user-triggered trim/crop workflows
- Location: `app.py`, `streamlit_app.py`
- Contains: Streamlit page setup, CSS styles, `init_session_state()`, `build_heatmap_figure()`, `render_crop_framing_preview()`, sidebar & main UI forms, `trim_single_segment_smart()`, `ensure_source_video()`
- Depends on: `yt_processor.py`, `video_trimmer.py`, `utils.py`, `streamlit`, `plotly`, `pandas`
- Used by: End users via browser

**Extraction & Analysis Layer:**
- Purpose: Queries YouTube metadata and computes ranked highlight time windows and file size estimates
- Location: `yt_processor.py`
- Contains: `HighlightSegment` dataclass, `fetch_video_metadata()`, `extract_most_viewed_segments()`, `extract_fallback_segments()`, `estimate_video_size()`, `estimate_clip_size()`
- Depends on: `utils.py`, `yt_dlp`, `pytubefix`
- Used by: `app.py`, `video_trimmer.py` (imports `HighlightSegment`)

**Media Streaming & Processing Layer:**
- Purpose: Downloads full videos or byte/segment slices, executes FFmpeg/MoviePy trimming and aspect-ratio cropping, and bundles `.zip` archives
- Location: `video_trimmer.py`
- Contains: `CropConfig`, `CROP_PRESETS`, `build_crop_vf_filter()`, `compute_crop_box_normalized()`, `ensure_ffmpeg()`, `slice_hls_playlist_to_file()`, `run_http_range_relay_trim()`, `download_single_clip_section()`, `download_full_video()`, `trim_clip_ffmpeg()`, `trim_selected_segments()`, `create_clips_zip()`
- Depends on: `yt_processor.py`, `utils.py`, `yt_dlp`, `pytubefix`, `ffmpeg` CLI, `imageio_ffmpeg`/`static_ffmpeg`, `moviepy`
- Used by: `app.py`

**Utility Layer:**
- Purpose: Cross-cutting environment safety, cookie normalization, runtime discovery, and formatting helpers
- Location: `utils.py`
- Contains: `sanitize_environment()`, `get_clean_env()`, `get_js_runtimes()`, `get_ytdlp_cache_dir()`, `YdlCaptureLogger`, `sanitize_cookie_content()`, `save_yt_cookies()`, `clear_yt_cookies()`, `get_yt_cookies_file()`, `get_cookie_stats()`, `format_timestamp()`, `parse_timestamp()`, `format_bytes()`, `is_valid_youtube_url()`, `sanitize_filename()`, `strip_ansi()`
- Depends on: Python stdlib (`http.cookiejar`, `tempfile`, `pathlib`, `json`, `re`, `shutil`), optional `streamlit` and `deno`
- Used by: `app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`

## Data Flow

### Primary Request Path (Analyze & Extract Timestamps)

1. User submits a YouTube URL and clicks `🔍 Load & Extract Timestamps` (`app.py:540-549`, `app.py:621-624`)
2. `fetch_video_metadata()` probes YouTube across prioritized client profiles (`web`/`mweb` with cookies if present, then cookieless `visionos`, `web`/`mweb`, `web_embedded`/`tv_samsung`, `android`/`tv_simply`), falling back to `pytubefix` (`yt_processor.py:49-168`)
3. If `info["heatmap"]` exists, `extract_most_viewed_segments()` finds local maxima, expands contiguous high-replay windows, clamps to `[min_clip_duration, max_clip_duration]`, merges adjacent intervals, and returns top $K$ `HighlightSegment` items (`yt_processor.py:273-399`)
4. If no heatmap is present, `extract_fallback_segments()` extracts segments from chapters, description timestamps, or uniform spacing (`yt_processor.py:402-481`)
5. UI stores `video_info` and `segments` in `st.session_state`, renders Plotly heatmap (`build_heatmap_figure()`), metrics, and `pd.DataFrame` table (`app.py:628-756`)

### Video Trimming & Cropping Flow (4-Tier Direct Stream-Trim vs Full Video)

1. User triggers `Trim Only Segment #K`, inline `Trim & Prepare Clip #K`, `Re-Trim / Apply Crop`, or `Trim All` (`app.py:785-815`, `app.py:900-1056`, `app.py:1131-1155`)
2. For single clips in Direct Stream-Trim mode (`app.py:900-987`):
   - If `raw_clips[rank]` is cached on disk and no full source exists, `trim_clip_ffmpeg()` re-crops locally in <1s without network calls
   - Otherwise `download_single_clip_section()` (`video_trimmer.py:1616-1800`) fetches an uncropped raw clip through 4 tiers:
     1. **Tier 1 (`_try_python_hls_direct_slice`)**: Downloads overlapping HLS `.m3u8` segments in Python via `ydl.urlopen`, then runs local FFmpeg trim/mux (`video_trimmer.py:874-1027`)
     2. **Tier 2 (`_try_python_http_range_slice`)**: Starts an ephemeral `127.0.0.1` HTTP Range relay (`run_http_range_relay_trim()`) backed by `ydl.urlopen` and points local FFmpeg at localhost (`video_trimmer.py:1115-1365`)
     3. **Tier 3 (`yt-dlp` section download)**: Calls `yt_dlp.YoutubeDL.download()` with `download_ranges` across filtered profiles (`video_trimmer.py:1691-1774`)
     4. **Tier 4 (`_download_with_pytubefix`)**: Downloads via `pytubefix` and trims locally (`video_trimmer.py:1776-1796`)
   - Stores raw clip in `st.session_state["raw_clips"]`, optionally applies `CropConfig` via `trim_clip_ffmpeg()`, updates `trimmed_clips`, and regenerates `.zip` via `create_clips_zip()`
3. If Direct Stream-Trim fails (or `Download Full Video First` / Demo mode is active), `ensure_source_video()` downloads the full video (`download_full_video()` or generates a synthetic `demo_source.mp4` with FFmpeg `testsrc`), and `trim_selected_segments()` slices & crops locally (`app.py:852-888`, `app.py:970-1023`)

**State Management:**
- `st.session_state` (`app.py:151-178`) holds `video_info`, `video_url`, `segments`, `source_video_path`, `trimmed_clips`, `raw_clips`, `all_clips_zip_path`, `workspace_dir`, `youtube_cookies_text`, and `_pending_cookie_box_val` (used to avoid Streamlit widget-key mutation errors when syncing the sidebar cookie textarea)

## Key Abstractions

**`HighlightSegment` (`yt_processor.py:23-46`):**
- Purpose: Uniform representation of a candidate highlight clip regardless of extraction source (`heatmap`, `chapters`, `description_timestamps`, `uniform_fallback`, `custom`)
- Pattern: `@dataclass` with computed `@property` helpers (`duration`, `time_range_str`) and `to_dict()`

**`CropConfig` (`video_trimmer.py:61-93`):**
- Purpose: Encapsulates aspect-ratio preset (`none`, `9:16`, `9:16_blur`, `1:1`, `4:5`, `4:3`, `custom`), subject focus panning (`pan_x`, `pan_y`), and edge crop percentages
- Pattern: `@dataclass` paired with pure filter-generation (`build_crop_vf_filter()`) and normalized preview geometry (`compute_crop_box_normalized()`)

**`YdlCaptureLogger` (`utils.py:22-41`):**
- Purpose: Suppresses noisy `yt-dlp` console output while capturing warnings and errors

## Entry Points

**Local & Container Entrypoint (`app.py`):**
- Location: `app.py`
- Triggers: `streamlit run app.py` (local CLI and `Dockerfile` `CMD`)
- Responsibilities: Fixes `sys.path`/`cwd`, sanitizes environment, initializes session state, and renders full Streamlit UI

**Streamlit Cloud Wrapper (`streamlit_app.py`):**
- Location: `streamlit_app.py`
- Triggers: Streamlit Community Cloud default main file lookup (`streamlit_app.py`)
- Responsibilities: Sets `ROOT_DIR` on `sys.path`, calls `sanitize_environment()`, and executes `app.py` via `runpy.run_path(..., run_name="__main__")`

## Architectural Constraints

- **Threading:** Streamlit executes top-to-bottom per user interaction on a script runner thread. `run_http_range_relay_trim()` (`video_trimmer.py:1206-1270`) spawns a daemon `ThreadingMixIn` HTTP server on `127.0.0.1:0` and serializes `ydl.urlopen()` setup with `threading.Lock()`, while FFmpeg runs in a child subprocess.
- **Global state & import side effects:** `utils.py:133` calls `sanitize_environment()` at import time; `video_trimmer.py:42-44,392` mutates `os.environ` (`YTDLP_JS_RUNTIMES`, `PATH`) and discovers/copies the FFmpeg shim at import time; `app.py:14-17` and `streamlit_app.py:9-12` call `os.chdir(PROJECT_ROOT)` at import time.
- **IPv4 enforcement:** `_create_ipv4_connection()` and `_IPV4_OPENER` (`video_trimmer.py:491-543`) force `socket.AF_INET` alongside `source_address: "0.0.0.0"` in `yt-dlp` options so `googlevideo.com` signed IP-bound URLs match across Python and subprocesses.
- **Even dimensions for H.264:** `build_crop_vf_filter()` (`video_trimmer.py:96-148`) wraps width/height expressions in `trunc(.../2)*2` because `libx264` with `yuv420p` rejects odd dimensions.

## Anti-Patterns

### Import-Time Side Effects & Reload Workaround

**What happens:** `utils.py:133` and `video_trimmer.py:392` mutate global environment state on import, and `app.py:71-90` catches `ImportError` to run `importlib.reload(sys.modules["video_trimmer"])`.
**Why it's wrong:** Import order alters `os.environ` globally and makes module initialization harder to test or isolate.
**Do this instead:** Initialize FFmpeg shims and environment sanitization explicitly inside application startup (`init_session_state()` or dedicated `bootstrap()` helper) rather than at module top level.

### Deeply Nested Orchestration Closures in UI Script

**What happens:** `ensure_source_video()`, `show_trim_error()`, and `trim_single_segment_smart()` are defined as nested closures inside the `if segments:` block of `app.py:852-987`, duplicating fallback/ZIP logic with the `trim_all_clicked` branch (`app.py:989-1046`).
**Why it's wrong:** Couples business workflow logic directly to Streamlit widget state and makes the orchestration untestable outside Streamlit.
**Do this instead:** Extract trimming/caching orchestration into a standalone service function or module accepting explicit paths and callbacks, keeping `app.py` strictly for UI state binding.

## Error Handling

**Strategy:** Multi-tier fallback cascades with user-friendly Streamlit error alerts and bot-bypass guidance.

**Patterns:**
- Client/tier retry loops (`yt_processor.py:96-129`, `video_trimmer.py:905-1025, 1311-1363, 1697-1773`) catch `Exception`, store `last_exc`, and silently try the next client profile or tier.
- `app.py:890-898` strips ANSI sequences (`strip_ansi()`) from exceptions before displaying `st.error()`, adding contextual cookie/bot-bypass warnings when `403`, `bot`, or `sign in` appears.

## Cross-Cutting Concerns

**Logging:** `YdlCaptureLogger` (`utils.py:22-41`) suppresses `yt-dlp` stdout/stderr noise; no structured `logging` module logger is configured across application modules.
**Validation:** URL format checked via regex in `is_valid_youtube_url()` (`utils.py:436-443`); custom timestamps validated via `parse_timestamp()` and duration bounds (`app.py:828-849`); filenames sanitized via `sanitize_filename()` (`utils.py:446-450`).
**Authentication:** Isolated per-PID Netscape cookie jar files created by `get_yt_cookies_file()` (`utils.py:303-359`) to protect persisted cookies from `yt-dlp` mutation.

---

*Architecture analysis: 2026-11-11*
