---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# Codebase Structure

**Analysis Date:** 2026-11-12

## Directory Layout

```text
yt_most_viewed_trimmer/
├── .streamlit/
│   └── config.toml             # Streamlit dark theme & 500 MB upload limit
├── tests/
│   └── test_processor.py       # Unit & integration tests (unittest suite)
├── .planning/                  # GSD planning & codebase analysis artifacts
├── app.py                      # Main Streamlit UI, charts, crop preview & orchestration
├── streamlit_app.py            # Streamlit Community Cloud wrapper calling app.py
├── yt_processor.py             # YouTube metadata, heatmap peaks, fallbacks & size estimation
├── video_trimmer.py            # 4-tier direct stream-trim, FFmpeg/MoviePy cropper & ZIP bundler
├── utils.py                    # Env sanitization, cookie parsing, JS runtime & formatting helpers
├── Dockerfile                  # Python 3.11-slim + FFmpeg/curl container for port 7860
├── .dockerignore               # Excludes git, caches, and local media outputs from Docker build
├── .gitignore                  # Excludes caches, virtualenvs, secrets, cookies, and media outputs
├── packages.txt                # Apt packages (ffmpeg, nodejs) for Streamlit Community Cloud
├── requirements.txt            # Python dependencies (streamlit, yt-dlp, deno, pytubefix, etc.)
├── runtime.txt                 # Streamlit Cloud Python version pin (python-3.12)
├── .python-version             # Local Python version marker (3.12)
├── user_cookies.txt            # Optional gitignored local cookie file (if present; do not commit)
└── README.md                   # Project documentation & deployment guide
```

## Directory Purposes

**Root Directory (`/`):**
- Purpose: Flat Python application layout containing all runtime modules, deployment manifests, and configuration files.
- Contains: Streamlit entrypoints (`app.py`, `streamlit_app.py`), domain/processing modules (`yt_processor.py`, `video_trimmer.py`, `utils.py`), container/cloud manifests (`Dockerfile`, `packages.txt`, `requirements.txt`, `runtime.txt`, `.python-version`), and documentation (`README.md`).
- Key files: `app.py`, `yt_processor.py`, `video_trimmer.py`, `utils.py`, `streamlit_app.py`.

**`.streamlit/`:**
- Purpose: Holds Streamlit framework configuration.
- Contains: Theme palette (`primaryColor`, dark backgrounds) and server upload limits (`maxUploadSize = 500`).
- Key files: `.streamlit/config.toml`.

**`tests/`:**
- Purpose: Houses automated unit and integration tests runnable via `pytest` or `unittest`.
- Contains: Test suites exercising extraction algorithms, FFmpeg crop filters, cookie normalization, HLS slicing, and the localhost HTTP Range relay.
- Key files: `tests/test_processor.py`.

## Key File Locations

**Entry Points:**
- `app.py`: Primary Streamlit frontend and session orchestrator (`streamlit run app.py`, used by `Dockerfile` and local runs).
- `streamlit_app.py`: Community Cloud entrypoint wrapper (`runpy.run_path` into `app.py`).

**Configuration:**
- `.streamlit/config.toml`: Dark UI theme and server upload size configuration.
- `requirements.txt`: Python package dependencies (`streamlit`, `yt-dlp[default]`, `deno`, `pytubefix`, `curl_cffi`, `static-ffmpeg`, `imageio-ffmpeg`, `moviepy`, `ffmpeg-python`, `pandas`, `numpy`, `plotly`).
- `packages.txt`: OS-level packages (`ffmpeg`, `nodejs`) for Streamlit Community Cloud / Debian environments.
- `Dockerfile`: Container definition (`python:3.11-slim`, non-root `user` UID 1000, exposes port `7860` for Hugging Face Spaces).
- `runtime.txt` & `.python-version`: Pin Python `3.12` for cloud/local tooling.
- `.gitignore` & `.dockerignore`: Exclude `.env*`, `*cookies*.txt`, `*cookies*.json`, `user_cookies.txt`, media outputs (`*.mp4`, `*.zip`, `downloads/`, `trimmed_clips/`), and caches.

**Core Logic:**
- `yt_processor.py`: `HighlightSegment`, `fetch_video_metadata()`, `extract_most_viewed_segments()`, `extract_fallback_segments()`, `estimate_video_size()`, `estimate_clip_size()`.
- `video_trimmer.py`: `CropConfig`, `CROP_PRESETS`, `build_crop_vf_filter()`, `compute_crop_box_normalized()`, `ensure_ffmpeg()`, `slice_hls_playlist_to_file()`, `run_http_range_relay_trim()`, `download_single_clip_section()`, `download_and_trim_segments_direct()`, `download_full_video()`, `trim_clip_ffmpeg()`, `trim_selected_segments()`, `create_clips_zip()`.
- `utils.py`: `sanitize_environment()`, `get_clean_env()`, `get_js_runtimes()`, `get_ytdlp_cache_dir()`, `YdlCaptureLogger`, `sanitize_cookie_content()`, `get_yt_cookies_file()`, `save_yt_cookies()`, `clear_yt_cookies()`, `get_cookie_stats()`, timestamp/size/URL/filename helpers.

**Testing:**
- `tests/test_processor.py`: Contains `TestYouTubeMostViewedTrimmer` (`unittest.TestCase`) covering all core modules.

## Naming Conventions

**Files:**
- Lowercase `snake_case` Python modules at repo root (`app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`, `utils.py`).
- Test files prefixed with `test_` inside `tests/` (`tests/test_processor.py`).
- Generated output clips follow `{sanitized_title}_rank{rank}_{start}_to_{end}{crop_tag}.mp4` (e.g., `Video_rank1_00m42s_to_01m05s_crop_9x16.mp4` via `CropConfig.file_tag` in `video_trimmer.py:81` and `app.py:916`).
- Generated ZIP bundles follow `{sanitized_title}_most_viewed_clips.zip` (`app.py:984`, `1041`).

**Directories:**
- Lowercase `snake_case` for code and runtime workspace subdirectories (`tests/`, `downloads/`, `trimmed_clips/`, `raw_clips_cache/`).
- Dot-prefixed directories for tooling/framework metadata (`.streamlit/`, `.planning/`).

## Where to Add New Code

**New Feature (UI + Processing):**
- Backend extraction/analysis logic: `yt_processor.py` (or a new root module if introducing a distinct domain such as audio/subtitle analysis).
- Video download, FFmpeg filter, or crop preset logic: `video_trimmer.py` (register new presets in `CROP_PRESETS` and handle them in `CropConfig`, `build_crop_vf_filter()`, and `compute_crop_box_normalized()`).
- UI controls and rendering: `app.py`.
- Tests: Add test methods to `tests/test_processor.py` or create `tests/test_<feature>.py`.

**New Component/Module:**
- Implementation: Place new Python modules at the repository root alongside `yt_processor.py` and `video_trimmer.py` (since the project uses a flat root package layout rather than `src/`).

**Utilities:**
- Shared helpers (environment, cookies, formatting, validation, runtime discovery): `utils.py`.

## Special Directories

**`.streamlit/`:**
- Purpose: Streamlit theme and server runtime settings.
- Generated: No.
- Committed: Yes (`.streamlit/config.toml` is tracked; `.streamlit/secrets.toml` is gitignored).

**`.planning/`:**
- Purpose: GSD planning and codebase mapping documentation (`.planning/codebase/`).
- Generated: Yes (by GSD tooling).
- Committed: Optional / workflow-managed.

**Runtime Temp Directories (System `tempfile.gettempdir()`):**
- Purpose: Per-session workspace (`yt_trimmer_<random>/` holding `downloads/`, `trimmed_clips/`, `raw_clips_cache/`), shared `yt-dlp` solver cache (`yt_dlp_cache/`), FFmpeg binary shim (`ffmpeg_bin/`), and isolated per-PID cookie jars (`yt_working_cookies_<pid>.txt`, `env_cookies.txt`).
- Generated: Yes, at runtime.
- Committed: No (created outside repo in OS temp directory).

---

*Structure analysis: 2026-11-12*
