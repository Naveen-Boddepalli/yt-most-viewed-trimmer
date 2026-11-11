---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# Codebase Structure

**Analysis Date:** 2026-11-11

## Directory Layout

```text
yt_most_viewed_trimmer 2/
├── .streamlit/
│   └── config.toml            # Streamlit dark theme & 500MB maxUploadSize
├── tests/
│   └── test_processor.py      # Unit & integration tests (23 tests via unittest)
├── .planning/                 # GSD planning & codebase documentation
├── app.py                     # Primary Streamlit UI, Plotly chart, crop preview & trim orchestration
├── streamlit_app.py           # Streamlit Community Cloud entrypoint wrapper -> app.py
├── yt_processor.py            # YouTube metadata, Most-Replayed peak & fallback segment extractor
├── video_trimmer.py           # 4-tier stream trimmer, HLS/Range slicers, FFmpeg cropper & ZIP bundler
├── utils.py                   # Cookie parser/isolator, env sanitizer, JS runtime & formatting helpers
├── requirements.txt           # Python dependencies
├── packages.txt               # System apt dependencies (ffmpeg, nodejs) for Streamlit Cloud
├── runtime.txt                # Python runtime pin (python-3.12)
├── .python-version            # Python version marker (3.12)
├── Dockerfile                 # Containerized Hugging Face Spaces / Docker definition (port 7860)
├── .dockerignore              # Docker build context exclusions
├── .gitignore                 # Git exclusions (caches, media outputs, secrets, cookies)
├── README.md                  # Project documentation & deployment instructions
└── user_cookies.txt           # Local gitignored YouTube cookie jar file (untracked runtime artifact)
```

## Directory Purposes

**`.streamlit/`:**
- Purpose: Streamlit framework configuration
- Contains: TOML configuration only
- Key files: `.streamlit/config.toml`

**`tests/`:**
- Purpose: Automated unit and integration verification
- Contains: `unittest` test suite (`test_*.py`)
- Key files: `tests/test_processor.py`

**`.planning/`:**
- Purpose: GSD workflow artifacts and codebase analysis docs
- Contains: `.planning/codebase/*.md`
- Key files: `.planning/codebase/STACK.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/STRUCTURE.md`, `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`, `.planning/codebase/INTEGRATIONS.md`, `.planning/codebase/CONCERNS.md`

## Key File Locations

**Entry Points:**
- `app.py`: Main Streamlit web application (`streamlit run app.py` and `Dockerfile` `CMD`)
- `streamlit_app.py`: Streamlit Community Cloud wrapper delegating to `app.py` via `runpy.run_path`

**Configuration:**
- `.streamlit/config.toml`: Theme colors and upload size limit (`maxUploadSize = 500`)
- `requirements.txt`: Python package dependencies
- `packages.txt`: System packages (`ffmpeg`, `nodejs`)
- `runtime.txt` & `.python-version`: Python `3.12` target
- `Dockerfile` & `.dockerignore`: Container packaging

**Core Logic:**
- `yt_processor.py`: `HighlightSegment`, `fetch_video_metadata()`, `extract_most_viewed_segments()`, `extract_fallback_segments()`, `estimate_video_size()`, `estimate_clip_size()`
- `video_trimmer.py`: `CropConfig`, `CROP_PRESETS`, `build_crop_vf_filter()`, `compute_crop_box_normalized()`, `ensure_ffmpeg()`, `slice_hls_playlist_to_file()`, `run_http_range_relay_trim()`, `download_single_clip_section()`, `download_and_trim_segments_direct()`, `download_full_video()`, `trim_clip_ffmpeg()`, `trim_selected_segments()`, `create_clips_zip()`
- `utils.py`: `sanitize_environment()`, `get_clean_env()`, `get_js_runtimes()`, `sanitize_cookie_content()`, `get_yt_cookies_file()`, `get_cookie_stats()`, timestamp/byte/filename helpers

**Testing:**
- `tests/test_processor.py`: Single test module containing `TestYouTubeMostViewedTrimmer` (`23` test methods)

## Naming Conventions

**Files:**
- Root modules use flat `snake_case.py` (`yt_processor.py`, `video_trimmer.py`, `streamlit_app.py`, `utils.py`, `app.py`)
- Tests use `tests/test_<area>.py` (`tests/test_processor.py`)

**Directories:**
- Lowercase `snake_case` or dot-prefixed config/tool directories (`.streamlit/`, `tests/`, `.planning/`)
- Runtime temporary workspace subdirectories inside `tempfile.mkdtemp(prefix="yt_trimmer_")`: `downloads/`, `trimmed_clips/`, `raw_clips_cache/`

## Where to Add New Code

**New UI Feature / Control:**
- Primary code: `app.py` (sidebar settings in lines `300-526`, main actions/cards in lines `540-1155`)
- Tests: `tests/test_processor.py` (for pure logic extracted into helper modules)

**New Crop Preset or Video Filter:**
- Implementation: Add preset key/label to `CROP_PRESETS` (`video_trimmer.py:50-58`), filter expression to `build_crop_vf_filter()` (`video_trimmer.py:96-148`), preview box math to `compute_crop_box_normalized()` (`video_trimmer.py:151-191`), and UI controls in `app.py:350-408`
- Tests: Extend `test_crop_config_and_vf_builder()` and `test_video_dimensions_and_aspect_crops()` in `tests/test_processor.py`

**New Extraction / Scoring Heuristic:**
- Implementation: `yt_processor.py` (returning `List[HighlightSegment]`)
- Tests: Add test methods to `TestYouTubeMostViewedTrimmer` in `tests/test_processor.py`

**New Download Tier or Stream Protocol:**
- Implementation: Add tier helper and integrate into `download_single_clip_section()` (`video_trimmer.py:1616-1800`) and optionally `download_full_video()` (`video_trimmer.py:1536-1613`)
- Tests: Unit test format selectors and local mock/relay paths in `tests/test_processor.py`

**Utilities:**
- Shared helpers: `utils.py`

## Special Directories

**`.pytest_cache/`:**
- Purpose: Pytest test run cache
- Generated: Yes (by `pytest`)
- Committed: No (excluded in `.gitignore` and `.dockerignore`)

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes (by Python interpreter)
- Committed: No (excluded in `.gitignore` and `.dockerignore`)

---

*Structure analysis: 2026-11-11*
