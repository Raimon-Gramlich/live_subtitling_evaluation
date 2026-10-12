# Subtitle Evaluation

## Overview

A simple python web app that can show either one video with multiple sets of subtitles or multiple videos simultaneously side-by-side with their respective subtitles. The subtitles can be rated by the user using a feedback form. Multiple sets of one short video for each set and their respective subtitles will be displayed.

## Features

* Importing the dataset through drag-and-drop or upload of a zip file
* Option to either show one large video with multiple subtitles sharing the screen space or
* Supports multiple sets of video + subtitles
* At the end the feedback data can be exported

## Getting Started

### Prerequisites

If you don't have `uv` installed, run the official installer command:

```Shell
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Installation

1. Clone the repository:

   ```Shell
   git clone https://github.com/Raimon-Gramlich/live_subtitling_evaluation.git
   ```
2. Install the necessary dependencies:

   ```Shell
   cd live_subtitling_evaluation
   uv sync

   # or pip install -r requirements.txt
   ```

### Usage

```Shell
uv run streamlit run app.py
```

### Input

Zip file containing sets of .mp4 video files and 3 to 4 corresponding .vtt subtitle files as well as one manifest.json file.

### Output

Feedback data as an excel sheet.
