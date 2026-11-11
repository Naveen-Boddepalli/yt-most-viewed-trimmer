---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# Testing Patterns

**Analysis Date:** 2026-11-11

## Test Framework

**Runner:**
- Python stdlib `unittest` (`unittest.TestCase` in `tests/test_processor.py`), typically invoked with `pytest` (`README.md`)
- Config: None (no `pytest.ini`, `pyproject.toml`, or `tox.ini` present)

**Assertion Library:**
- Standard `unittest.TestCase` assertions (`assertEqual`, `assertAlmostEqual`, `assertTrue`, `assertFalse`, `assertIn`, `assertNotIn`, `assertGreater`, `assertLess`, `assertGreaterEqual`, `assertIsNone`, `assertIsNotNone`, `skipTest`)

**Run Commands:**
```bash
pytest -v                              # Run all tests (23 tests in tests/test_processor.py)
python3 -m unittest discover -s tests  # Run via stdlib unittest directly
pytest --collect-only -q               # Verify test discovery
```

## Test File Organization

**Location:**
- Separate `tests/` directory at repository root (not co-located with source modules)

**Naming:**
- `tests/test_<module>.py` (`tests/test_processor.py`)

**Structure:**
```text
tests/
└── test_processor.py   # Single TestCase class (`TestYouTubeMostViewedTrimmer`) covering utils, yt_processor, and video_trimmer
```

## Test Structure

**Suite Organization:**
```python
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestYouTubeMostViewedTrimmer(unittest.TestCase):
    def test_timestamp_helpers(self):
        self.assertEqual(format_timestamp(75), "01:15")
        ...

if __name__ == "__main__":
    unittest.main()
```

**Patterns:**
- **Setup:** Inline inside each test method using `with tempfile.TemporaryDirectory() as tmp:` for filesystem isolation (`tests/test_processor.py:203,313,349,420,474`)
- **Teardown:** Context-managed `TemporaryDirectory()` cleanup; `try ... finally: srv.shutdown(); srv.server_close()` for the ephemeral HTTP server test (`tests/test_processor.py:553-555`)
- **Environment Guards:** Conditional `self.skipTest(...)` when FFmpeg is missing (`tests/test_processor.py:201-202,311-312,347-348,384-385,466-467`) or when sandbox blocks loopback TCP sockets (`tests/test_processor.py:529-530`)

## Mocking

**Framework:** `unittest.mock` (`patch`) from Python stdlib

**Patterns:**
```python
from unittest.mock import patch
import errno

with patch("subprocess.run", side_effect=OSError(errno.E2BIG, "Argument list too long")):
    self.assertTrue(is_ffmpeg_executable(ffmpeg))
```
(`tests/test_processor.py:378-390`)

**What to Mock:**
- OS error edge cases like `OSError(errno.E2BIG, ...)` on `subprocess.run` (`test_is_ffmpeg_executable_e2big_handling`)
- Networked stream URLs via local `http.server` fixtures (`test_http_range_relay_trim_end_to_end`) or `file://` URIs (`test_python_native_hls_playlist_slicer`)

**What NOT to Mock:**
- FFmpeg encoding, crop filter evaluation, and ZIP creation — integration tests invoke the real `ensure_ffmpeg()` binary against generated synthetic video streams (`tests/test_processor.py:200-255,310-377,465-555`)

## Fixtures and Factories

**Test Data:**
Synthetic MP4 and HLS test streams are generated on the fly with `ffmpeg` `lavfi` sources (`testsrc` + `sine`):
```python
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
(`tests/test_processor.py:206-218`, repeated in lines `316-328`, `354-366`, `477-489`)

**Location:**
- Inline within `tests/test_processor.py`; no shared `conftest.py` or static media fixtures committed to git

## Coverage

**Requirements:** None enforced (no coverage threshold or CI check configured)

**View Coverage:**
```bash
pytest --cov=. --cov-report=term-missing   # Requires optional pytest-cov package
```

## Test Types

**Unit Tests (`tests/test_processor.py`):**
- Formatting & parsing: `test_timestamp_helpers`, `test_format_bytes`, `test_strip_ansi`, `test_url_validation`
- Estimation & peak extraction: `test_estimate_video_size`, `test_estimate_clip_size`, `test_heatmap_peak_extraction`, `test_fallback_extraction`
- Crop filter & geometry: `test_crop_config_and_vf_builder`
- Cookie parsing & environment safety: `test_get_yt_cookies_file_and_json_conversion`, `test_devtools_table_cookies_loadable_by_mozilla_cookie_jar`, `test_sanitize_environment_and_clean_env`
- Discovery & selectors: `test_ffmpeg_detection_and_shim`, `test_client_profiles_and_cookie_isolation`, `test_get_js_runtimes_structure`, `test_cache_dir_and_ydl_capture_logger`, `test_select_hls_formats_separate_av_and_cookieless_guard`, `test_select_https_formats_and_available_at_wait`, `test_is_ffmpeg_executable_e2big_handling`

**Integration Tests (`tests/test_processor.py`):**
- `test_end_to_end_trim_crop_and_zip`: Generates MP4, runs `extract_most_viewed_segments()`, trims & crops across `none`, `9:16`, `1:1`, `9:16_blur`, and `custom` modes, and creates a `.zip` bundle
- `test_video_dimensions_and_aspect_crops`: Verifies exact output pixel dimensions (`202x360`, `360x360`, `288x360`) after FFmpeg cropping
- `test_python_native_hls_playlist_slicer`: Generates local `.m3u8` + `.ts` HLS segments via FFmpeg and verifies `slice_hls_playlist_to_file()` over `file://` URI
- `test_http_range_relay_trim_end_to_end`: Spins up a threaded localhost HTTP Range server (`206 Partial Content`) and verifies `run_http_range_relay_trim()` produces a `360x360` cropped MP4

**E2E Tests:**
- Not used (no Streamlit AppTest or browser UI tests for `app.py` / `streamlit_app.py`)

## Common Patterns

**Async / Threaded Testing:**
```python
srv = _Srv(("127.0.0.1", 0), _RangeServerHandler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    ...
finally:
    srv.shutdown()
    srv.server_close()
```
(`tests/test_processor.py:521-555`)

**Error Testing:**
```python
with patch("subprocess.run", side_effect=OSError(errno.E2BIG, "Argument list too long")):
    self.assertTrue(is_ffmpeg_executable(ffmpeg))
self.assertFalse(is_ffmpeg_executable("/nonexistent/binary/path"))
```
(`tests/test_processor.py:388-392`)

---

*Testing analysis: 2026-11-11*
