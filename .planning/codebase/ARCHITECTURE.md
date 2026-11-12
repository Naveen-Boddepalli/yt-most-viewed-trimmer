---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
<!-- refreshed: 2026-11-12 -->
# Architecture

**Analysis Date:** 2026-11-12

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                Streamlit Web UI & Orchestration (`app.py`)              │
│  ┌─────────────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │  Sidebar Controls   │  │ Plotly Heatmap &     │  │ Clip Cards,    │  │
│  │  & Crop Preview     │  │ Segment Table        │  │ Preview & ZIP  │  │
│  └──────────┬──────────┘  └──────────┬───────────┘  └───────┬────────┘  │
└─────────────┼────────────────────────┼──────────────────────┼───────────┘
              │                        │                      │
              ▼                        ▼                      ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────┐
│  Metadata & Peak Extraction      │   │  Stream Acquisition & FFmpeg     │
│  (`yt_processor.py`)             │   │  Trimming/Cropping               │
│  - Multi-client yt-dlp probe     │   │  (`video_trimmer.py`)            │
│  - pytubefix metadata fallback   │   │  - Tier 1: Python HLS slicer     │
│  - Heatmap peak / chapter parser │   │  - Tier 2: 127.0.0.1 Range relay │
│  - Video & clip size estimators  │   │  - Tier 3: yt-dlp range download │
└──────────────┬───────────────────┘   │  - Tier 4: pytubefix + FFmpeg    │
               │                       │  - Local FFmpeg/MoviePy + ZIP    │
               │                       └──────────────┬───────────────────┘
               │                                      │
               ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             Environment, Cookie & Formatting Utilities (`utils.py`)     │
│  - Env sanitization (`MAX_ARG_STRLEN` guard) & `get_clean_env()`        │
│  - Multi-format cookie parser & isolated temp cookie jar (`/tmp`)       │
│  - JS runtime discovery (`node` / `deno`) & `YdlCaptureLogger`          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Cloud Entrypoint Wrapper | Normalizes `sys.path`/`cwd`, sanitizes environment, and executes `app.py` via `runpy` for Streamlit Community Cloud | `streamlit_app.py` |
| Web UI & Session Orchestrator | Renders Streamlit sidebar/main layout, Plotly heatmap chart, CSS crop preview, manages `st.session_state`, coordinates trim/crop/ZIP workflows | `app.py` |
| Metadata & Engagement Extractor | Probes YouTube metadata across client profiles (`yt-dlp` + `pytubefix` fallback), extracts heatmap peaks or chapter/description/uniform fallbacks, estimates file sizes | `yt_processor.py` |
| Stream Slicer, Trimmer & Cropper | Discovers/shims FFmpeg, implements 4-tier cloud-resilient stream trimming, runs ephemeral localhost HTTP Range relay, builds FFmpeg `-vf` crop filters, bundles `.zip` archives | `video_trimmer.py` |
| Environment, Cookies & Helpers | Strips oversized env vars, parses 5 cookie formats into Netscape working copies, locates Node/Deno JS runtimes, formats timestamps/bytes/filenames | `utils.py` |
| Automated Test Suite | Unit and local FFmpeg integration tests for peak extraction, crop math/filters, cookie parsing, HLS slicing, and HTTP Range relay | `tests/test_processor.py` |

## Pattern Overview

**Overall:** Layered Monolith with Multi-Tier Fallback Pipelines (Streamlit Reactive Script Architecture)

**Key Characteristics:**
- **Reactive top-to-bottom script execution:** `app.py` executes on every Streamlit UI interaction, holding all durable state (metadata, extracted `HighlightSegment` list, workspace path, cached raw/trimmed clips) in `st.session_state`.
- **Cloud/datacenter resilience via layered fallbacks:** Both metadata extraction (`yt_processor.py`) and media acquisition (`video_trimmer.py`) cycle through ordered cookieless and cookie-authenticated YouTube client profiles (`visionos`, `web_embedded`, `tv_samsung`, `web`, `mweb`, `android`, `ios`) before falling back to `pytubefix`.
- **Python-owned networking to prevent FFmpeg exit code 8:** For direct stream trimming, FFmpeg never connects to external HTTPS URLs; Python either slices HLS `.m3u8` segments directly (`slice_hls_playlist_to_file`) or proxies byte ranges over an ephemeral `127.0.0.1` HTTP server (`run_http_range_relay_trim`) using IPv4 sockets and `ydl.urlopen`.
- **Two-stage raw + cropped clip caching:** Single-clip direct trimming saves an uncropped clip under `raw_clips_cache/` (`st.session_state["raw_clips"]`) so subsequent crop preset changes re-encode locally in under a second without re-downloading.

## Layers

**Presentation & Orchestration Layer:**
- Purpose: Renders interactive controls, engagement visualizations, crop overlays, clip cards, video players, and download buttons; dispatches extraction and trimming actions.
- Location: `app.py`, `streamlit_app.py`
- Contains: Streamlit page setup, custom CSS badges, Plotly figure builder (`build_heatmap_figure`), CSS framing preview (`render_crop_framing_preview`), sidebar/main UI, `init_session_state()`, `ensure_source_video()`, `trim_single_segment_smart()`.
- Depends on: `yt_processor.py`, `video_trimmer.py`, `utils.py`, `streamlit`, `plotly`, `pandas`.
- Used by: End users via browser (`streamlit run app.py` or `streamlit_app.py`).

**Domain Extraction & Estimation Layer:**
- Purpose: Retrieves YouTube video telemetry and identifies highlight intervals and estimated media sizes.
- Location: `yt_processor.py`
- Contains: `HighlightSegment` dataclass, `fetch_video_metadata()`, `extract_most_viewed_segments()`, `extract_fallback_segments()`, `estimate_video_size()`, `estimate_clip_size()`.
- Depends on: `utils.py`, `yt-dlp`, `pytubefix` (fallback).
- Used by: `app.py`, `video_trimmer.py` (imports `HighlightSegment`), `tests/test_processor.py`.

**Media Acquisition & Processing Layer:**
- Purpose: Acquires full or byte-range-sliced streams from YouTube and produces trimmed, cropped, H.264/AAC `.mp4` clips and `.zip` bundles.
- Location: `video_trimmer.py`
- Contains: `CropConfig` dataclass, `CROP_PRESETS`, `build_crop_vf_filter()`, `compute_crop_box_normalized()`, FFmpeg discovery/shimming (`get_ffmpeg_path()`, `ensure_ffmpeg()`), IPv4 urllib opener (`_IPV4_OPENER`), HLS slicer (`slice_hls_playlist_to_file()`), localhost HTTP Range relay (`run_http_range_relay_trim()`), 4-tier `download_single_clip_section()`, `download_full_video()`, `trim_clip_ffmpeg()`, `trim_selected_segments()`, `create_clips_zip()`.
- Depends on: `yt_processor.HighlightSegment`, `utils.py`, `yt-dlp`, `pytubefix`, `ffmpeg` CLI (`subprocess`), `moviepy` (fallback).
- Used by: `app.py`, `tests/test_processor.py`.

**Infrastructure & Utility Layer:**
- Purpose: Cross-cutting environment safety, cookie jar isolation, JS solver discovery, and string/time/size parsing.
- Location: `utils.py`
- Contains: `sanitize_environment()`, `get_clean_env()`, `get_js_runtimes()`, `get_ytdlp_cache_dir()`, `YdlCaptureLogger`, `sanitize_cookie_content()`, `get_yt_cookies_file()`, `save_yt_cookies()`, `clear_yt_cookies()`, `get_cookie_stats()`, `format_bytes()`, `format_timestamp()`, `parse_timestamp()`, `is_valid_youtube_url()`, `sanitize_filename()`, `strip_ansi()`.
- Depends on: Python standard library (`os`, `re`, `json`, `tempfile`, `http.cookiejar`, `shutil`, `pathlib`), optional `streamlit` (for `st.secrets` / `st.session_state`), optional `deno`.
- Used by: `app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`, `tests/test_processor.py`.

## Data Flow

### Primary Request Path (Extract & Direct Stream-Trim)

1. User submits a YouTube URL in `app.py` (`analyze_clicked`, `app.py:621`), validated via `is_valid_youtube_url()` (`utils.py:436`).
2. `fetch_video_metadata()` (`yt_processor.py:49`) loads isolated cookies via `get_yt_cookies_file()` (`utils.py:303`) and JS runtimes via `get_js_runtimes()` (`utils.py:44`), then queries `yt-dlp` across ordered client profiles (`web`/`mweb` with cookies, `visionos`, `web`/`mweb`, `web_embedded`/`tv_samsung`, `android`/`tv_simply`), falling back to `pytubefix` (`yt_processor.py:132`).
3. If `info["heatmap"]` is present, `extract_most_viewed_segments()` (`yt_processor.py:273`) detects local maxima, expands contiguous intensity windows, clamps to `[min_clip_dur, max_clip_dur]`, merges adjacent windows, and ranks top `K` `HighlightSegment` objects. Otherwise, `extract_fallback_segments()` (`yt_processor.py:402`) falls back through chapters, description timestamps, or uniform windows.
4. `app.py` plots the heatmap and shaded segments (`build_heatmap_figure`, `app.py:186`), displays pre-download size estimates (`estimate_video_size`, `estimate_clip_size`), and awaits trim actions.
5. On single-clip or batch direct stream-trim, `download_single_clip_section()` (`video_trimmer.py:1616`) executes 4 tiers in order:
   - Tier 1 (`_try_python_hls_direct_slice`, `video_trimmer.py:874`): Downloads only overlapping `.m3u8` media segments in Python (`slice_hls_playlist_to_file`) and trims/crops locally with FFmpeg.
   - Tier 2 (`_try_python_http_range_slice`, `video_trimmer.py:1273`): Probes 1 KB via `ydl.urlopen`, starts an ephemeral `127.0.0.1` HTTP Range server (`run_http_range_relay_trim`, `video_trimmer.py:1115`), and points local FFmpeg `-ss ... -i http://127.0.0.1:<port>/...` at the relay.
   - Tier 3 (`video_trimmer.py:1684`): Uses `yt-dlp` with `download_ranges`.
   - Tier 4 (`video_trimmer.py:1775`): Downloads full video via `_download_with_pytubefix()` into a temp directory and trims locally.
6. Output `.mp4` paths are stored in `st.session_state["trimmed_clips"]` (and uncropped clips in `st.session_state["raw_clips"]` for single-clip flows), bundled into a `.zip` via `create_clips_zip()` (`video_trimmer.py:1995`), and served through `st.video` and `st.download_button`.

### Secondary Flow: Full Video Download, Demo Mode & Re-Cropping

1. If "Download Full Video First" is selected, direct stream-trim fails, or Demo Mode is active (`demo_clicked`, `app.py:580`), `ensure_source_video()` (`app.py:852`) either generates a synthetic 300s `demo_source.mp4` via FFmpeg `lavfi` or calls `download_full_video()` (`video_trimmer.py:1536`).
2. `trim_selected_segments()` (`video_trimmer.py:1948`) calls `trim_clip_ffmpeg()` (`video_trimmer.py:1854`) for each segment, applying `build_crop_vf_filter(crop_config)` (`video_trimmer.py:96`) with `libx264` encoding when cropping is active (or `-c copy` when `fast_copy=True` and uncropped), falling back to `moviepy` (`video_trimmer.py:1908`) if the FFmpeg CLI fails.
3. On "Re-Trim / Apply Crop" (`app.py:1131`), `trim_single_segment_smart()` (`app.py:900`) re-uses `st.session_state["raw_clips"][rank]` or `st.session_state["source_video_path"]` to re-crop locally with zero network calls.

**State Management:**
- Ephemeral per-session workspace created via `tempfile.mkdtemp(prefix="yt_trimmer_")` stored in `st.session_state["workspace_dir"]` (`app.py:160`), containing `downloads/`, `trimmed_clips/`, `raw_clips_cache/`, and `*_most_viewed_clips.zip`.
- Cookie persistence uses `user_cookies.txt` (CWD or tempdir), `/tmp/env_cookies.txt`, `st.secrets["YOUTUBE_COOKIES"]`, or `st.session_state["youtube_cookies_text"]`, copied per process to `/tmp/yt_working_cookies_<pid>.txt` (`utils.py:266`) so `yt-dlp` cookie mutations do not corrupt master cookies.

## Key Abstractions

**`HighlightSegment`:**
- Purpose: Canonical representation of an extracted highlight interval (`rank`, `start_time`, `end_time`, `peak_time`, `score`, `source`, `label`) with computed `duration` and `time_range_str` properties.
- Examples: `yt_processor.py:23-46`, consumed across `app.py` and `video_trimmer.py`.
- Pattern: `@dataclass` value object with `to_dict()` serialization.

**`CropConfig` + Filter Builder:**
- Purpose: Encapsulates aspect-ratio preset (`none`, `9:16`, `9:16_blur`, `1:1`, `4:5`, `4:3`, `custom`), horizontal/vertical pan (`pan_x`, `pan_y`), and edge crop percentages, translating them identically into FFmpeg `-vf` expressions (`build_crop_vf_filter`), normalized preview boxes (`compute_crop_box_normalized`), and deterministic filename tags (`file_tag`).
- Examples: `video_trimmer.py:50-191`.
- Pattern: `@dataclass` configuration object + pure projection functions used by both UI preview (`app.py:243`) and FFmpeg/MoviePy execution (`video_trimmer.py:1870`, `1923`).

**4-Tier Stream Acquisition & Localhost HTTP Relay:**
- Purpose: Decouples external YouTube networking (IPv4 enforcement, cookies, client rotation, retries) from FFmpeg execution so cloud datacenter IPs do not trigger HTTP 403 / FFmpeg exit code 8.
- Examples: `slice_hls_playlist_to_file()` (`video_trimmer.py:680`), `run_http_range_relay_trim()` (`video_trimmer.py:1115`), `download_single_clip_section()` (`video_trimmer.py:1616`).
- Pattern: Strategy chain (HLS Python slicer → Localhost Range proxy → `yt-dlp` sections → `pytubefix` fallback).

**`YdlCaptureLogger`:**
- Purpose: Silences `yt-dlp` stdout/stderr noise while capturing warnings and errors in memory.
- Examples: `utils.py:22-41`, instantiated in `yt_processor.py:97` and `video_trimmer.py:581`, `1480`.
- Pattern: Adapter implementing the `yt-dlp` logger interface (`debug`, `info`, `warning`, `error`).

## Entry Points

**Primary Streamlit App (`app.py`):**
- Location: `app.py` (invoked via `streamlit run app.py` in `Dockerfile:28` and `README.md:114`).
- Triggers: HTTP requests to Streamlit server (port `7860` in Docker, `8501` default locally).
- Responsibilities: Initializes `sys.path`/`cwd`, sanitizes environment, initializes `st.session_state`, renders UI, and orchestrates extraction, trimming, cropping, and downloads.

**Streamlit Community Cloud Wrapper (`streamlit_app.py`):**
- Location: `streamlit_app.py`
- Triggers: Default entrypoint searched by Streamlit Community Cloud.
- Responsibilities: Inserts repo root into `sys.path`, sets `cwd`, runs `sanitize_environment()`, and executes `app.py` via `runpy.run_path(..., run_name="__main__")`.

**Test Suite (`tests/test_processor.py`):**
- Location: `tests/test_processor.py`
- Triggers: `pytest -v` or `python -m unittest`.
- Responsibilities: Validates timestamp/byte/cookie/env utilities, heatmap peak and fallback extraction, FFmpeg crop filter math, local HLS slicing, and localhost HTTP Range relay behavior.

## Architectural Constraints

- **Threading:** Streamlit executes user sessions across worker threads; `run_http_range_relay_trim()` (`video_trimmer.py:1206`) spawns a `ThreadingMixIn` HTTP server on `127.0.0.1:0` with daemon threads and serializes `ydl.urlopen` calls through a per-call `threading.Lock()` (`video_trimmer.py:1347`) to avoid concurrent `cookiejar` mutation races.
- **Global state:** `utils.py:133` mutates `os.environ` at import time (`sanitize_environment()`). `video_trimmer.py:42-44, 392` mutates `os.environ["YTDLP_JS_RUNTIMES"]`, `os.environ["PATH"]`, and writes a binary shim into `<tempdir>/ffmpeg_bin/ffmpeg` at import time. `_IPV4_OPENER` (`video_trimmer.py:542`) is a module-level singleton opener.
- **Circular imports:** No circular imports exist; the dependency DAG flows strictly downward: `app.py` → `{yt_processor.py, video_trimmer.py, utils.py}`, `video_trimmer.py` → `{yt_processor.py, utils.py}`, `yt_processor.py` → `utils.py`.
- **Linux `MAX_ARG_STRLEN` (`E2BIG`):** All `subprocess.run` / `subprocess.Popen` calls to `ffmpeg` pass `env=get_clean_env()` (`utils.py:116`) to prevent `Errno 7` when large cookie or secret variables exist in the container environment.
- **IPv4 pinning:** YouTube signed `googlevideo.com` URLs are IP-bound to the extraction IP; both `yt-dlp` (`"source_address": "0.0.0.0"`) and `_create_ipv4_connection` (`socket.AF_INET`, `video_trimmer.py:491`) enforce IPv4 so IPv6/IPv4 dual-stack mismatches do not cause HTTP 403.

## Anti-Patterns

### Import-Time Side Effects & Process Mutation

**What happens:** Importing `utils.py` immediately calls `sanitize_environment()` (`utils.py:133`), and importing `video_trimmer.py` immediately sets `os.environ["YTDLP_JS_RUNTIMES"]` (`video_trimmer.py:44`) and runs `ensure_ffmpeg()` (`video_trimmer.py:392`), which probes binaries, spawns subprocesses, copies binaries to `<tempdir>/ffmpeg_bin`, and mutates `os.environ["PATH"]`.
**Why it's wrong:** Slows down imports, performs filesystem/subprocess I/O during module loading or test collection, and mutates process-wide `os.environ` as a hidden side effect.
**Do this instead:** Move `sanitize_environment()` and `ensure_ffmpeg()` initialization strictly into explicit startup hooks (`app.py`, `streamlit_app.py`) or lazy cached getters (`@functools.lru_cache`).

### Monolithic UI Script Mixing Orchestration and Subprocess Logic

**What happens:** `app.py` (`app.py:852-987`) defines nested orchestration functions (`ensure_source_video()`, `trim_single_segment_smart()`) inside the main script body and directly invokes `subprocess.run` for FFmpeg demo video synthesis (`app.py:863-875`) alongside Streamlit widget calls.
**Why it's wrong:** Couples business workflows (caching raw clips, generating synthetic demo MP4s, fallback switching) to Streamlit execution order, preventing unit testing of the smart caching/trimming logic outside a Streamlit runtime.
**Do this instead:** Move `generate_demo_video()` and clip-orchestration helpers into `video_trimmer.py` (or a dedicated `workflow.py` module) so `app.py` only binds UI state to service calls.

### Silent Exception Swallowing Across Fallback Loops

**What happens:** `yt_processor.py:127-129, 161-164` and `video_trimmer.py:1024-1025, 1362-1363, 1463-1464` catch `except Exception:` and `continue` without recording per-client failure reasons, and `app.py:71-90` duplicates the entire `video_trimmer` import list inside an `except ImportError` + `importlib.reload` block.
**Why it's wrong:** Masks whether a failure came from bot detection, missing JS runtime, expired cookies, or a transient network error, making cloud debugging opaque; duplicated import blocks risk drifting out of sync.
**Do this instead:** Capture `(profile_name, str(exc))` diagnostic entries during fallback loops and attach them to raised errors or UI diagnostics; keep clean single-block imports at module top.

## Error Handling

**Strategy:** Multi-tier silent fallback within acquisition loops, surfacing the last captured exception or a generic `RuntimeError`/`FileNotFoundError` to `app.py`, which strips ANSI escape codes (`strip_ansi`) and displays contextual cookie/bot-bypass guidance (`show_trim_error`, `app.py:890`).

**Patterns:**
- **Graceful tier fallback:** `app.py:970-981` and `app.py:1011-1023` catch failures in Direct Stream-Trim and automatically fall back to `ensure_source_video()` + `trim_selected_segments()`.
- **External library warning suppression:** `pytubefix` imports are wrapped in `warnings.catch_warnings()` ignoring `SyntaxWarning` on Python 3.12 (`yt_processor.py:133-135`, `video_trimmer.py:1378-1379`).
- **Temp artifact cleanup:** Temporary `.ts` and `.part` files are cleaned up in `finally:` blocks (`video_trimmer.py:950-955`, `1017-1023`) or pre-attempt glob loops (`video_trimmer.py:1572-1576`, `1698-1702`).

## Cross-Cutting Concerns

**Logging:** `YdlCaptureLogger` (`utils.py:22`) suppresses `yt-dlp` console output while buffering warnings/errors; FFmpeg runs with `stdout=subprocess.DEVNULL`/`PIPE` and `stderr` captured for error messages; runtime diagnostics are exposed in the UI via `get_ffmpeg_diagnostics()` (`video_trimmer.py:361`) and `get_cookie_stats()` (`utils.py:362`).
**Validation:** YouTube URL regex check (`is_valid_youtube_url`, `utils.py:436`), timestamp parsing bounds (`app.py:833`), cookie line format validation (`sanitize_cookie_content`, `utils.py:144`), and binary execution checks (`is_ffmpeg_executable`, `video_trimmer.py:221`).
**Authentication:** Optional YouTube session cookie injection supporting Netscape, JSON, Chrome/Edge DevTools, `Cookie:` header, and 2-column/alternating text formats (`utils.py:144-257`), resolved in priority order from disk (`user_cookies.txt`, `cookies.txt`), `/tmp/env_cookies.txt`, `st.secrets`, and `st.session_state`, then copied to an isolated per-PID working file (`utils.py:260-268`).

---

*Architecture analysis: 2026-11-12*
