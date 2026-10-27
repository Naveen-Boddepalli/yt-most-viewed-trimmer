"""Unit tests for YouTube heatmap peak extraction, fallback extraction, and video trimming."""

import tempfile
import subprocess
from pathlib import Path
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import (
    format_timestamp,
    parse_timestamp,
    is_valid_youtube_url,
    sanitize_filename,
    format_bytes,
    strip_ansi,
)
from yt_processor import (
    extract_most_viewed_segments,
    extract_fallback_segments,
    estimate_video_size,
    estimate_clip_size,
)
from video_trimmer import (
    has_ffmpeg,
    ensure_ffmpeg,
    get_ffmpeg_path,
    download_single_clip_section,
    download_and_trim_segments_direct,
    trim_clip_ffmpeg,
    trim_selected_segments,
    create_clips_zip,
)


class TestYouTubeMostViewedTrimmer(unittest.TestCase):
    def test_timestamp_helpers(self):
        self.assertEqual(format_timestamp(75), "01:15")
        self.assertEqual(format_timestamp(3661), "01:01:01")
        self.assertEqual(parse_timestamp("01:15"), 75.0)
        self.assertEqual(parse_timestamp("01:01:01"), 3661.0)

    def test_format_bytes(self):
        self.assertEqual(format_bytes(None), "Unknown")
        self.assertEqual(format_bytes(-10), "Unknown")
        self.assertEqual(format_bytes(500), "500 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(150 * 1024), "150.0 KB")
        self.assertEqual(format_bytes(25 * 1024 * 1024), "25.0 MB")
        self.assertEqual(format_bytes(1500 * 1024 * 1024), "1.46 GB")

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[0;31mERROR:\x1b[0m test"), "ERROR: test")
        self.assertEqual(strip_ansi("\x1b[0;32m 129.25KiB/s \x1b[0m"), " 129.25KiB/s ")
        self.assertEqual(strip_ansi("clean text"), "clean text")
        self.assertEqual(strip_ansi(""), "")

    def test_estimate_video_size(self):
        # 1. Video and audio stream with explicit filesize
        info_with_formats = {
            "duration": 60.0,
            "formats": [
                {"height": 720, "vcodec": "avc1.4d401f", "acodec": "none", "ext": "mp4", "filesize": 15_000_000},
                {"height": 1080, "vcodec": "avc1.640028", "acodec": "none", "ext": "mp4", "filesize": 35_000_000},
                {"height": None, "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "filesize": 2_000_000, "abr": 128},
            ],
        }
        # At 720p, should select the 720p video format (15MB) + audio format (2MB) = 17MB
        sz_720 = estimate_video_size(info_with_formats, max_height=720)
        self.assertEqual(sz_720, 17_000_000)

        # At 1080p, should select 1080p video format (35MB) + audio format (2MB) = 37MB
        sz_1080 = estimate_video_size(info_with_formats, max_height=1080)
        self.assertEqual(sz_1080, 37_000_000)

        # 2. Formats with bitrate (tbr in kbps)
        info_bitrate = {
            "duration": 10.0,
            "formats": [
                {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "tbr": 2000},
                {"height": None, "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "abr": 128},
            ],
        }
        sz_tbr = estimate_video_size(info_bitrate, max_height=720)
        # 2000 kbps = 2,000,000 bps -> / 8 * 10 = 2,500,000 bytes for video
        # 128 kbps = 128,000 bps -> / 8 * 10 = 160,000 bytes for audio
        self.assertEqual(sz_tbr, 2_660_000)

        # 3. Fallback without formats (using duration and standard bitrate)
        info_fallback = {"duration": 100.0}
        sz_fb_720 = estimate_video_size(info_fallback, max_height=720)
        self.assertGreater(sz_fb_720, 10_000_000)
        sz_fb_360 = estimate_video_size(info_fallback, max_height=360)
        self.assertLess(sz_fb_360, sz_fb_720)

        # 4. Empty info
        self.assertEqual(estimate_video_size(None), 0)
        self.assertEqual(estimate_video_size({}), 0)

    def test_estimate_clip_size(self):
        # Clip duration 15s out of 60s total, total size 20MB -> 5MB
        c_sz = estimate_clip_size(clip_duration=15.0, total_duration=60.0, total_size_bytes=20_000_000)
        self.assertEqual(c_sz, 5_000_000)

        # Proportional ratio checks
        c_half = estimate_clip_size(clip_duration=30.0, total_duration=60.0, total_size_bytes=20_000_000)
        self.assertEqual(c_half, 10_000_000)

    def test_url_validation(self):
        self.assertTrue(is_valid_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_valid_youtube_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertFalse(is_valid_youtube_url("https://example.com/video.mp4"))

    def test_heatmap_peak_extraction(self):
        # 20 bins covering 200 seconds (10s each) with two distinct peaks at bin 3 (30-40s) and bin 14 (140-150s)
        heatmap = []
        for i in range(20):
            score = 0.15
            if i == 3:
                score = 0.95
            elif i == 14:
                score = 0.80
            heatmap.append({"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": score})

        segs = extract_most_viewed_segments(
            heatmap=heatmap,
            video_duration=200.0,
            top_k=2,
            min_score_threshold=0.5,
            min_clip_duration=12.0,
            max_clip_duration=30.0,
            padding_seconds=2.0,
        )
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].rank, 1)
        self.assertAlmostEqual(segs[0].score, 0.95)
        self.assertTrue(segs[0].start_time <= 30.0 and segs[0].end_time >= 40.0)
        self.assertEqual(segs[1].rank, 2)
        self.assertAlmostEqual(segs[1].score, 0.80)

    def test_fallback_extraction(self):
        info = {
            "duration": 180.0,
            "chapters": [
                {"start_time": 0.0, "end_time": 25.0, "title": "Intro"},
                {"start_time": 25.0, "end_time": 60.0, "title": "Key Demo"},
            ],
        }
        segs = extract_fallback_segments(info, top_k=2, clip_duration=20.0)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].source, "chapters")

    def test_ffmpeg_detection_and_shim(self):
        self.assertTrue(has_ffmpeg())
        path = ensure_ffmpeg()
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_end_to_end_trim_and_zip(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_mp4 = tmp_path / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(src_mp4),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            heatmap = [
                {"start_time": 0.0, "end_time": 3.0, "value": 0.2},
                {"start_time": 3.0, "end_time": 6.0, "value": 0.92},
                {"start_time": 6.0, "end_time": 10.0, "value": 0.3},
            ]
            segs = extract_most_viewed_segments(
                heatmap=heatmap,
                video_duration=10.0,
                top_k=1,
                min_score_threshold=0.5,
                min_clip_duration=3.0,
                max_clip_duration=5.0,
                padding_seconds=0.5,
            )
            clips_map = trim_selected_segments(
                source_video=src_mp4,
                segments=segs,
                clips_dir=tmp_path / "clips",
                video_title="Unit Test Video",
            )
            self.assertIn(1, clips_map)
            self.assertTrue(clips_map[1].exists())
            self.assertGreater(clips_map[1].stat().st_size, 1000)

            zip_file = create_clips_zip(clips_map, tmp_path / "bundle.zip")
            self.assertTrue(zip_file.exists())

    def test_direct_stream_trimmer_functions_available(self):
        import inspect
        self.assertTrue(callable(download_single_clip_section))
        self.assertTrue(callable(download_and_trim_segments_direct))
        sig1 = inspect.signature(download_single_clip_section)
        self.assertIn("url", sig1.parameters)
        self.assertIn("start_time", sig1.parameters)
        self.assertIn("end_time", sig1.parameters)
        sig2 = inspect.signature(download_and_trim_segments_direct)
        self.assertIn("segments", sig2.parameters)


if __name__ == "__main__":
    unittest.main()
