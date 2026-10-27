---
title: YouTube Most Viewed Trimmer
emoji: 🎬
colorFrom: red
colorTo: indigo
sdk: streamlit
sdk_version: 1.42.0
app_file: app.py
pinned: false
license: mit
---

# 🎬 YouTube Most-Viewed Timestamp Extractor & Video Trimmer (Streamlit App)

A Python + Streamlit web application that loads a YouTube video from its link, extracts the **Most Replayed (Most Viewed)** timestamp peaks from YouTube's engagement telemetry (`heatmap`), trims the video into those top highlight segments, and displays them inline for previewing and downloading **either a particular clip or all clips as a `.zip` bundle**.

---

## ✨ Key Features

1. **YouTube Heatmap ('Most Replayed') Peak Extraction (`yt_processor.py`)**
   - Uses `yt-dlp` to inspect the video's `heatmap` telemetry array (`start_time`, `end_time`, `value` in `[0.0, 1.0]`).
   - Detects local maxima, expands around contiguous high-intensity replay regions, applies configurable padding and min/max clip duration constraints, and merges overlapping windows.
   - **Automatic Multi-Tier Fallback**: If a video does not have YouTube's Most Replayed heatmap enabled, the extractor automatically falls back to **YouTube Chapters**, **Description Timestamps**, or **Uniform Highlight Sampling**.
2. **Interactive Engagement Curve (`app.py` + `plotly`)**
   - Plots the full 0–100% replay intensity timeline with shaded highlight windows marking each extracted segment (`#1`, `#2`, ...).
3. **Pre-Download File Size Visibility & Estimation (`yt_processor.py` + `utils.py`)**
   - **Full Video Download Size**: Estimates video download size for every resolution (360p, 480p, 720p, 1080p) before downloading using yt-dlp stream metadata, bitrates, and duration.
   - **Per-Segment Size Display**: Shows estimated clip size (e.g. `~3.5 MB`) in the segments table, trimming toolbar, and on clip cards before trimming, and exact trimmed file size before downloading.
   - **Download Buttons & ZIP Size**: Shows exact sizes on individual clip download buttons and the combined `.zip` bundle button.
4. **Flexible Video Trimming & Downloading (`video_trimmer.py`)**
   - **Trim a Particular Segment**: Pick any specific rank from the dropdown (or click the inline button on any segment card) to trim, preview (`st.video`), and download just that clip (`st.download_button`).
   - **Trim All Segments + `.zip` Bundle**: One-click batch trimming of all top $K$ segments plus a single button to download all trimmed `.mp4` files in a `.zip` archive.
   - **Custom Timestamp Range Trimmer**: Manually add and trim any `MM:SS - MM:SS` interval.
   - **Offline Sample Demo Mode**: Click **Load Sample Heatmap Demo** in the header to test the heatmap peak detector, interactive chart, and FFmpeg video trimming immediately without needing an external link.

---

## 📁 Project Structure

```text
yt_most_viewed_trimmer/
├── .streamlit/
│   └── config.toml          # Streamlit dark theme & upload configuration
├── tests/
│   └── test_processor.py    # Unit & integration tests (heatmap peaks + FFmpeg trimming)
├── app.py                   # Streamlit frontend UI
├── yt_processor.py          # YouTube metadata & Most-Replayed heatmap peak extractor
├── video_trimmer.py         # Video download (yt-dlp) & segment trimming (FFmpeg / MoviePy)
├── utils.py                 # Timestamp parsing, formatting & URL validation helpers
├── requirements.txt         # Python package dependencies
├── .gitignore               # Ignored virtualenvs and temporary media files
└── README.md                # Setup & usage documentation
```

---

## 🚀 Quickstart & Installation

### 1. Prerequisites
- **Python 3.9+**
- **FFmpeg** installed on your system `PATH` (recommended for fast, frame-accurate H.264 trimming and MP4 stream merging):
  - **macOS**: `brew install ffmpeg`
  - **Ubuntu / Debian**: `sudo apt update && sudo apt install -y ffmpeg`
  - **Windows**: `winget install Gyan.FFmpeg` or `choco install ffmpeg`

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Streamlit Application
```bash
streamlit run app.py
```

### 4. Run Unit Tests
```bash
python3 -m unittest discover -s tests
```

---

## 🤗 Deploying to Hugging Face Spaces (Free Cloud Hosting)

This repository is pre-configured for 1-click hosting on **[Hugging Face Spaces](https://huggingface.co/spaces)**:

1. **Create a new Space:**
   - Go to [Hugging Face Spaces](https://huggingface.co/new-space).
   - Give your space a name (e.g. `yt-most-viewed-trimmer`).
   - Select **Streamlit** as the Space SDK.
   - Choose **Public** or **Private**.
2. **Push your code to the Space:**
   - Clone your Hugging Face Space repository or push this repository directly:
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/<your-space-name>
   git push space main
   ```
3. **Automatic Setup:**
   - Hugging Face automatically detects `packages.txt` and installs system `ffmpeg`.
   - Hugging Face installs Python dependencies from `requirements.txt`.
   - It launches `app.py` directly with **Direct Stream-Trim** enabled.
4. **Enjoy:**
   - You can open your Space URL on any phone or browser.
   - Paste a YouTube URL, click Trim, and you only download the final lightweight 10–15 MB highlight clips!
