# 🎬 YouTube Most-Viewed Timestamp Extractor & Video Trimmer

A Python + Streamlit web application that loads a YouTube video from its link, extracts the **Most Replayed (Most Viewed)** timestamp peaks from YouTube's engagement telemetry (`heatmap`), trims the video into top highlight segments, and lets you preview and download **either a particular clip (~10–15 MB) or all clips as a `.zip` bundle**.

---

## ⚡ Direct Stream-Trim (Cloud Optimized)
- **Zero Full-Video Download Required**: Directly streams and slices *only* the requested 30–60s highlight clip from YouTube's CDN using byte-range requests.
- **Ultra-lightweight**: Downloads only **10–15 MB** per clip instead of 1–2 GB source videos.
- **Fast**: Cuts and delivers clips in just 2–4 seconds.

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

## ☁️ Deploying to Streamlit Community Cloud (100% Free Forever)

This repository is pre-configured with `packages.txt` (installs FFmpeg) and both `app.py` / `streamlit_app.py` entrypoints.

1. **Fork or Push** this repository to your GitHub account (`https://github.com/Naveen-Boddepalli/yt-most-viewed-trimmer`).
   - *Ensure the repository is set to **Public** so Streamlit Cloud can access it.*
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub.
3. Click **Deploy an app** (or **New app**).
4. Fill in:
   - **Repository**: `naveen-boddepalli/yt-most-viewed-trimmer`
   - **Branch**: `main`
   - **Main file path**: `app.py` *(or `streamlit_app.py`)*
5. Click **Deploy!**

---

## 📁 Project Structure

```text
yt_most_viewed_trimmer/
├── .streamlit/
│   └── config.toml          # Streamlit dark theme & upload configuration
├── tests/
│   └── test_processor.py    # Unit & integration tests (heatmap peaks + FFmpeg trimming)
├── app.py                   # Streamlit frontend UI
├── streamlit_app.py         # Entrypoint for Streamlit Community Cloud
├── yt_processor.py          # YouTube metadata & Most-Replayed heatmap peak extractor
├── video_trimmer.py         # Direct stream trimming (yt-dlp) & FFmpeg segment cutter
├── utils.py                 # Timestamp parsing, formatting & URL validation helpers
├── packages.txt             # System FFmpeg for Streamlit Cloud & Linux
├── requirements.txt         # Python package dependencies
├── .gitignore               # Ignored virtualenvs and temporary media files
└── README.md                # Documentation
```

---

## 🚀 Local Installation & Run

### 1. Prerequisites
- **Python 3.9+**
- **FFmpeg** installed on your system `PATH`:
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
