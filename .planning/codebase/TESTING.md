---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# Testing Patterns

**Analysis Date:** 2026-11-12

## Test Framework

**Runner:**
- `unittest` (`unittest.TestCase`) executed with `pytest` (`pytest 9.0.3` on `Python 3.12.9`; `.pytest_cache/` present)
- Config: None (`pytest.ini`, `pyproject.toml`, `setup.cfg`, and `conftest.py` are not present)

**Assertion Library:**
- Standard library `unittest.TestCase` methods (`assertEqual`, `assertAlmostEqual`, `assertTrue`, `assertFalse`, `assertIn`, `assertNotIn`, `assertIsNone`, `assertIsNotNone`, `assertGreater`, `assertGreaterEqual`, `assertLess`)

**Run Commands:**
```bash
pytest -v                                  # Run all tests (documented in README.md)
python3 -m pytest -v                       # Run all tests with explicit interpreter
python3 -m unittest discover -s tests -v   # Run via stdlib unittest runner
pytest -v -k "crop"                        # Run filtered subset of tests
```
*(No watch-mode or coverage plugin is configured; `pytest-cov` is not listed in `requirements.txt`.)*

## Test File Organization

**Location:**
- Separate `tests/` directory at the repository root (`tests/test_processor.py`).

**Naming:**
- Test files: `tests/test_<area>.py` (`tests/test_processor.py`).
- Test class: `PascalCase` inheriting `unittest.TestCase` (`TestYouTubeMostViewedTrimmer` in `tests/test_processor.py:57`).
- Test methods: `test_<behavior_or_feature>` (`test_timestamp_helpers`, `test_heatmap_peak_extraction`, `test_is_ffmpeg_executable_e2big_handling`).

**Structure:**
```text
tests/
└── test_processor.py    # 23 unit and integration tests for utils, yt_processor, video_trimmer
```

## Test Structure

**Suite Organization:**
```python
"""Unit and integration tests for YouTube heatmap extraction, cloud 403/cookie fixes, and video cropping."""

import json
import tempfile
import subprocess
from pathlib import Path
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import (...)
from yt_processor import (...)
from video_trimmer import (...)


class TestYouTubeMostViewedTrimmer(unittest.TestCase):
    def test_timestamp_helpers(self):
        self.assertEqual(format_timestamp(75), "01:15")
        self.assertEqual(parse_timestamp("01:15"), 75.0)
```

**Patterns:**
- **Root path insertion:** Prepend the repository root to `sys.path` at module top (`tests/test_processor.py:9-10`) before importing `utils`, `yt_processor`, and `video_trimmer`.
- **Per-test isolation without `setUp`/`tearDown`:** Avoid shared mutable class fixtures; create temporary directories inline using `with tempfile.TemporaryDirectory() as tmp:` (`tests/test_processor.py:203, 313, 349, 420, 474`) and clean up local servers in `try ... finally` (`srv.shutdown(); srv.server_close()` at `tests/test_processor.py:553-555`).
- **Environment capability guards:** Skip tests cleanly with `self.skipTest(...)` when external runtime dependencies or sandbox permissions are unavailable:
  - Missing FFmpeg binary (`if not has_ffmpeg(): self.skipTest("FFmpeg not installed")` at `tests/test_processor.py:201-202, 311-312, 347-348, 466-467`)
  - Missing FFmpeg on `PATH` (`if not ffmpeg: self.skipTest("ffmpeg not on PATH")` at `tests/test_processor.py:384-385`)
  - Blocked loopback TCP sockets (`except PermissionError: self.skipTest("Loopback TCP sockets blocked by sandbox")` at `tests/test_processor.py:529-530`)
- **Executable entrypoint:** Include `if __name__ == "__main__": unittest.main()` at the bottom of `tests/test_processor.py:558-559`.

## Mocking

**Framework:** `unittest.mock` (standard library) plus callable dependency injection.

**Patterns:**
```python
# 1. Patching OS/subprocess failures (tests/test_processor.py:378-389)
from unittest.mock import patch
import errno

with patch("subprocess.run", side_effect=OSError(errno.E2BIG, "Argument list too long")):
    self.assertTrue(is_ffmpeg_executable(ffmpeg))
```
```python
# 2. Dependency injection with an ephemeral local HTTP Range server (tests/test_processor.py:535-548)
def _open_fn(t_url, hdrs):
    req = urllib.request.Request(t_url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=10)

run_http_range_relay_trim(
    ffmpeg_exe=ffmpeg_exe,
    output_path=out_clip,
    start_time=1.5,
    end_time=4.0,
    open_url_fn=_open_fn,
    stream_specs={"/m": (f"http://127.0.0.1:{port}/video.mp4", {})},
    crop_config=CropConfig(mode="1:1", pan_x=0.5),
    progress_callback=lambda p, m: prog_events.append((p, m)),
)
```

**What to Mock:**
- OS kernel limits and subprocess edge errors that cannot be deterministically triggered in unit tests (e.g., `OSError(errno.E2BIG, ...)` via `patch("subprocess.run", ...)` in `test_is_ffmpeg_executable_e2big_handling`).
- External YouTube/CDN HTTP transport via injected `open_url_fn` / `fetch_fn` callbacks backed by local `file://` URIs or `http.server.HTTPServer` on `127.0.0.1:0`.

**What NOT to Mock:**
- Real FFmpeg invocations (`trim_clip_ffmpeg`, `trim_selected_segments`, `get_video_dimensions`, `create_clips_zip`) — run against synthetic `lavfi` test media.
- Cookie parsing and `http.cookiejar.MozillaCookieJar` loading (`test_devtools_table_cookies_loadable_by_mozilla_cookie_jar` writes a real temporary file and loads it with `MozillaCookieJar`).
- Format selection and profile ordering (`_build_client_profiles`, `_select_hls_formats`, `_select_https_formats`) — pass representative format dicts directly.

## Fixtures and Factories

**Test Data:**
```python
# Synthetic heatmap fixture (tests/test_processor.py:123-130)
heatmap = []
for i in range(20):
    score = 0.15
    if i == 3:
        score = 0.95
    elif i == 14:
        score = 0.80
    heatmap.append({"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": score})
```
```python
# Synthetic MP4 media factory via FFmpeg lavfi (tests/test_processor.py:206-218)
subprocess.run(
    [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(src_mp4),
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=True,
)
```

**Location:**
- All test data dicts, cookie strings, format lists, and synthetic FFmpeg media generators are defined inline inside each test method in `tests/test_processor.py` (no separate `fixtures/` directory or `conftest.py`).

## Coverage

**Requirements:** None enforced.

**View Coverage:**
Not configured (`pytest-cov` / `coverage` is not in `requirements.txt`, though `.coverage` and `htmlcov/` are ignored in `.gitignore`).

**Observed Suite Status:**
- Running `python3 -m pytest -v` collects 23 items in `tests/test_processor.py`: 22 passed, 1 skipped (`test_http_range_relay_trim_end_to_end` skips when loopback TCP sockets raise `PermissionError` in sandboxed environments).
- **Untested modules/functions:** `app.py` (Streamlit UI & session state), `streamlit_app.py`, `fetch_video_metadata` (`yt_processor.py`), `download_full_video`, `_try_python_hls_direct_slice`, `_try_python_http_range_slice`, `_download_with_pytubefix`, `download_single_clip_section`, `download_and_trim_segments_direct`, `get_ffmpeg_diagnostics` (`video_trimmer.py`), `save_yt_cookies`, `clear_yt_cookies`, `get_cookie_stats`, `sanitize_filename` (`utils.py`).

## Test Types

**Unit Tests:**
- Pure-Python logic in `utils.py`, `yt_processor.py`, and `video_trimmer.py` (`test_timestamp_helpers`, `test_format_bytes`, `test_strip_ansi`, `test_estimate_video_size`, `test_estimate_clip_size`, `test_url_validation`, `test_heatmap_peak_extraction`, `test_fallback_extraction`, `test_crop_config_and_vf_builder`, `test_client_profiles_and_cookie_isolation`, `test_get_yt_cookies_file_and_json_conversion`, `test_devtools_table_cookies_loadable_by_mozilla_cookie_jar`, `test_sanitize_environment_and_clean_env`, `test_get_js_runtimes_structure`, `test_cache_dir_and_ydl_capture_logger`, `test_select_hls_formats_separate_av_and_cookieless_guard`, `test_select_https_formats_and_available_at_wait`, `test_is_ffmpeg_executable_e2big_handling`).

**Integration Tests:**
- Real FFmpeg binary execution, HLS `.m3u8`/`.ts` playlist slicing, crop dimension verification, ZIP archiving, and localhost HTTP Range streaming in `tests/test_processor.py` (`test_ffmpeg_detection_and_shim`, `test_end_to_end_trim_crop_and_zip`, `test_video_dimensions_and_aspect_crops`, `test_python_native_hls_playlist_slicer`, `test_http_range_relay_trim_end_to_end`).

**E2E Tests:**
- Not used (no Streamlit `AppTest`, browser automation, or live YouTube network tests).

## Common Patterns

**Progress Callback & Event Capture Testing:**
```python
# Capture callback invocations in a local list (tests/test_processor.py:533, 547, 552)
prog_events = []
run_http_range_relay_trim(
    ...,
    progress_callback=lambda p, m: prog_events.append((p, m)),
)
self.assertGreaterEqual(len(prog_events), 1)
```

**Cookie Parsing & `MozillaCookieJar` Verification:**
```python
# Validate sanitized Netscape output parses cleanly via stdlib MozillaCookieJar (tests/test_processor.py:415-425)
sanitized = sanitize_cookie_content(devtools_table)
with tempfile.TemporaryDirectory() as tmp:
    cf = Path(tmp) / "c.txt"
    cf.write_text(sanitized, encoding="utf-8")
    cj = http.cookiejar.MozillaCookieJar(str(cf))
    cj.load(ignore_discard=True, ignore_expires=True)
    self.assertEqual([c.name for c in cj], ["LOGIN_INFO"])
```

**Environment Cleanup Testing:**
```python
# Verify oversized and cookie env vars are stripped (tests/test_processor.py:299-307)
os.environ["TEST_HUGE_VAR"] = "X" * 10000
os.environ["TEST_YOUTUBE_COOKIES"] = "# Netscape HTTP Cookie File\nexample.com\tTRUE\t/\tTRUE\t0\tk\tv"
sanitize_environment()
self.assertNotIn("TEST_HUGE_VAR", os.environ)
self.assertNotIn("TEST_YOUTUBE_COOKIES", os.environ)
```

---

*Testing analysis: 2026-11-12*
