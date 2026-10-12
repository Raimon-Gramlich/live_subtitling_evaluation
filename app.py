"""Module for evaluation of multiple subtitle sets."""
import base64
import html
import io
import json
import posixpath
import re
import zipfile

import pandas as pd
import streamlit as st


CRITERIA = [
    "Line Breaks",
    "Timing",
    "Readability",
    "Reading Time",
    "Comprehension",
]

TOP_SLOT_SKIP_SECONDS = 0.2

def load_dataset(uploaded_file):
    """Load dataset as zip file containing videos and subtitle files. Return manifest sets and file bytes."""
    with zipfile.ZipFile(uploaded_file) as archive:
        files = {}
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            normalized = posixpath.normpath(name)
            if normalized.startswith("../") or normalized.startswith("/"):
                raise ValueError(f"Unsafe path in ZIP: {name}")
            files[normalized] = archive.read(name)

    manifest_name = next(
        (name for name in ("manifest.json", "dataset.json") if name in files),
        None,
    )
    if not manifest_name:
        raise ValueError("ZIP must contain a root-level manifest.json or dataset.json")

    manifest = json.loads(files[manifest_name].decode("utf-8"))
    sets = manifest if isinstance(manifest, list) else manifest.get("sets", [manifest])
    if not isinstance(sets, list) or not sets:
        raise ValueError("Manifest must describe at least one set")

    base_dir = posixpath.dirname(manifest_name)

    def file_path(value):
        if not isinstance(value, str):
            raise ValueError("Manifest file paths must be strings")
        path = posixpath.normpath(posixpath.join(base_dir, value))
        if path.startswith("../") or path.startswith("/"):
            raise ValueError(f"Unsafe manifest path: {value}")
        if path not in files:
            raise ValueError(f"File referenced by manifest was not found: {value}")
        return path

    validated = []
    for index, item in enumerate(sets):
        if not isinstance(item, dict) or not item.get("video"):
            raise ValueError(f"Set {index + 1} is missing its video")
        subtitles = item.get("subtitles", [])
        if not subtitles:
            raise ValueError(f"Set {index + 1} has no subtitles")

        validated.append({
            "title": item.get("title", f"Set {index + 1}"),
            "video": file_path(item["video"]),
            "subtitles": [
                {
                    "name": subtitle.get("subtitle_name", f"Subtitle {i + 1}"),
                    "path": file_path(subtitle["subtitle_path"]),
                }
                for i, subtitle in enumerate(subtitles)
            ],
        })

    return validated, files


def parse_vtt(data):
    """Parse cue timing and text for the custom multi-track caption overlay."""
    text = data.decode("utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n[ \t]*\r?\n", text.strip())
    cues = []

    def seconds(timestamp):
        parts = timestamp.replace(",", ".").split(":")
        return sum(float(part) * (60 ** index) for index, part in enumerate(reversed(parts)))

    for block in blocks:
        lines = block.splitlines()
        timing_index = next(
            (index for index, line in enumerate(lines) if "-->" in line),
            None,
        )
        if timing_index is None:
            continue

        start, end = lines[timing_index].split("-->", 1)
        start_time = start.strip().split()[0]
        end_time = end.strip().split()[0]
        cue_text = "\n".join(lines[timing_index + 1:]).strip()
        cue_text = html.unescape(re.sub(r"<[^>]*>", "", cue_text))
        if cue_text:
            cues.append({
                "start": seconds(start_time),
                "end": seconds(end_time),
                "text": cue_text,
            })

    for cue in cues:
        short_top_slot_starts = [
            later_cue["start"]
            for later_cue in cues
            if cue["start"] < later_cue["start"] < cue["end"]
            and cue["end"] - later_cue["start"] < TOP_SLOT_SKIP_SECONDS
        ]
        cue["skip_at"] = min(short_top_slot_starts) if short_top_slot_starts else None

    return cues


def video_panel(video_bytes, subtitle_tracks, panel_id, muted=False, spread_tracks=False):
    video_uri = "data:video/mp4;base64," + base64.b64encode(video_bytes).decode("ascii")
    tracks = [
        {
            "name": track["name"],
            "cues": parse_vtt(track["bytes"]),
        }
        for track in subtitle_tracks
    ]
    tracks_json = json.dumps(tracks, ensure_ascii=True).replace("<", "\\u003c")

    return f"""
    <section class="panel">
      <div class="player">
        <video id="video-{panel_id}" controls playsinline
               {"muted" if muted else ""} preload="metadata">
          <source src="{video_uri}" type="video/mp4">
          Your browser cannot play this video.
        </video>
        <div id="captions-{panel_id}" class="captions"
             data-spread-tracks="{"true" if spread_tracks else "false"}"></div>
      </div>
      <script>
        window.subtitleTracks = window.subtitleTracks || {{}};
        window.subtitleTracks["{panel_id}"] = {tracks_json};
      </script>
    </section>
    """


def render_players(set_item, files, mode):
    if mode == "Single video":
        tracks = [
            {"name": subtitle["name"], "bytes": files[subtitle["path"]]}
            for subtitle in set_item["subtitles"]
        ]
        panels = [
            video_panel(
                files[set_item["video"]],
                tracks,
                "0",
                spread_tracks=True,
            )
        ]
    else:
        panels = [
            video_panel(
                files[set_item["video"]],
                [{"name": subtitle["name"], "bytes": files[subtitle["path"]]}],
                str(i),
                muted=(i > 0),
            )
            for i, subtitle in enumerate(set_item["subtitles"])
        ]

    sync_script = """
    const videos = [...document.querySelectorAll("video")];
    let syncing = false;

    function copyState(source, eventName) {
      if (syncing) return;
      syncing = true;
      videos.forEach(target => {
        if (target === source) return;
        try {
          if (eventName === "play") {
            target.currentTime = source.currentTime;
            target.play().catch(() => {});
          } else if (eventName === "pause") {
            target.pause();
          } else if (eventName === "seeking") {
            target.currentTime = source.currentTime;
          } else if (eventName === "ratechange") {
            target.playbackRate = source.playbackRate;
          }
        } catch (_) {}
      });
      setTimeout(() => syncing = false, 80);
    }

    videos.forEach(video => {
      const panelId = video.id.replace("video-", "");
      const tracks = (window.subtitleTracks || {})[panelId] || [];
      const container = document.getElementById("captions-" + panelId);
      if (!container) return;

      const spreadTracks = container.dataset.spreadTracks === "true";
      const positions = tracks.map((_, index) => {
        if (!spreadTracks || tracks.length === 1) return 88;
        return 12 + index * (76 / (tracks.length - 1));
      });

      tracks.forEach((spec, index) => {
        const lane = document.createElement("div");
        lane.className = "caption" + (spreadTracks ? "" : " fit-caption");
        lane.style.top = positions[index] + "%";
        lane.textContent = "";
        container.appendChild(lane);
        spec.lane = lane;
      });

      if (!spreadTracks) {
        const fitCaptions = () => tracks.forEach(track => fitCaption(track.lane));
        new ResizeObserver(fitCaptions).observe(container);
        fitCaptions();
      }
    });

    function fitCaption(lane) {
      lane.style.fontSize = "";
      lane.classList.remove("wrap-caption");
      if (lane.hidden || !lane.textContent) return;

      const baseSize = parseFloat(getComputedStyle(lane).fontSize);
      const availableWidth = lane.clientWidth;
      const contentWidth = lane.scrollWidth;
      if (contentWidth > availableWidth && availableWidth > 0) {
        const fittedSize = baseSize * availableWidth / contentWidth;
        if (fittedSize < 12) {
          lane.style.fontSize = "12px";
          lane.classList.add("wrap-caption");
        } else {
          lane.style.fontSize = fittedSize + "px";
        }
      }
    }

    videos.forEach(video => {
      ["play", "pause", "seeking", "ratechange"].forEach(name =>
        video.addEventListener(name, () => copyState(video, name))
      );
    });

    function showCaptions() {
      videos.forEach(video => {
        const panelId = video.id.replace("video-", "");
        const tracks = (window.subtitleTracks || {})[panelId] || [];
        const container = document.getElementById("captions-" + panelId);
        tracks.forEach(track => {
          const lines = track.cues
            .filter(cue =>
              video.currentTime >= cue.start &&
              video.currentTime < cue.end &&
              (cue.skip_at === null || video.currentTime < cue.skip_at)
            )
            .flatMap(cue => cue.text.split("\\n"));
          const text = lines.length
            ? track.name + ": " + lines.join("\\n")
            : "";
          track.lane.hidden = !text;
          if (track.lane.textContent !== text) {
            track.lane.textContent = text;
            if (container.dataset.spreadTracks === "false") {
              fitCaption(track.lane);
            }
          }
        });
      });
      requestAnimationFrame(showCaptions);
    }
    showCaptions();
    """

    columns = (
        "1fr" if mode == "Single video"
        else f"repeat({len(panels)}, minmax(0, 1fr))"
    )
    page = f"""
    <style>
      body {{ margin: 0; font-family: sans-serif; }}
      .grid {{ display: grid; grid-template-columns: {columns}; gap: 12px; }}
      .panel {{ min-width: 0; }}
      .player {{ position: relative; }}
      .captions {{ position: absolute; inset: 0; pointer-events: none; }}
      video {{
        width: 100%;
        display: block;
        object-fit: contain;
        background: #111;
      }}
      .caption {{
        position: absolute;
        left: 5%;
        width: 90%;
        transform: translateY(-50%);
        color: white;
        text-align: center;
        white-space: pre-line;
        font-size: clamp(14px, 2vw, 26px);
        font-weight: bold;
        text-shadow: 1px 1px 3px black, -1px -1px 3px black;
      }}
      .caption.fit-caption {{ white-space: pre; }}
      .caption.fit-caption.wrap-caption {{
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }}
      @media (max-width: 700px) {{
        .grid {{ grid-template-columns: 1fr; }}
      }}
    </style>
    <div class="grid">{''.join(panels)}</div>
    <script>{sync_script}</script>
    """
    st.iframe(page, height="content") #, scrolling=False)


def rating_key(set_index, subtitle_index, criterion):
    return f"rating_{set_index}_{subtitle_index}_{criterion}"


def save_feedback_value(key):
    st.session_state.setdefault("saved_feedback", {})[key] = st.session_state[key]


st.set_page_config(page_title="Subtitle Evaluation", layout="wide")
st.title("Subtitle Evaluation")

st.sidebar.header("📁 Data Source Setup")

upload = st.sidebar.file_uploader("Load a dataset ZIP", type=["zip"])
if not upload:
    st.info("Upload a ZIP containing manifest.json and the referenced media files.")
    st.stop()

try:
    dataset, files = load_dataset(upload)
except Exception as exc:
    st.error(f"Could not load dataset: {exc}")
    st.stop()

if "current_set" not in st.session_state:
    st.session_state.current_set = 0
current = min(st.session_state.current_set, len(dataset) - 1)
set_item = dataset[current]

mode = st.radio(
    "Display mode",
    ["Single video", "Multiple synced videos"],
    horizontal=True,
)

saved_feedback = st.session_state.setdefault("saved_feedback", {})

previous, spacer, next_button = st.columns([1, 5, 1])
with previous:
    previous_top = st.button(
        "← Previous",
        key=f"btn_top_prev_{current}",
        disabled=current == 0,
    )
with spacer:
    st.subheader(
        f"{set_item['title']} — Step {current + 1} of {len(dataset)}",
        text_alignment="center",
    )
with next_button:
    next_top = st.button(
        "Next →",
        key=f"btn_top_next_{current}",
        disabled=current >= len(dataset) - 1,
    )

if previous_top:
    st.session_state.current_set = current - 1
    st.rerun()
if next_top:
    st.session_state.current_set = current + 1
    st.rerun()

render_players(set_item, files, mode)


@st.fragment
def render_feedback(set_index, item, all_sets):
    for subtitle_index, subtitle in enumerate(item["subtitles"]):
        st.markdown(f"#### Ratings — {subtitle['name']}")
        cols = st.columns(len(CRITERIA))
        for col, criterion in zip(cols, CRITERIA):
            with col:
                st.write(criterion)
                key = rating_key(set_index, subtitle_index, criterion)
                if key in saved_feedback:
                    st.session_state[key] = saved_feedback[key]
                st.feedback(
                    "stars",
                    key=key,
                    on_change=save_feedback_value,
                    args=(key,),
                )
        comment_key = f"comment_{set_index}_{subtitle_index}"
        if comment_key in saved_feedback:
            st.session_state[comment_key] = saved_feedback[comment_key]
        st.text_area(
            f"Comments — {subtitle['name']}",
            key=comment_key,
            on_change=save_feedback_value,
            args=(comment_key,),
        )

    rows = []
    for feedback_set_index, feedback_item in enumerate(all_sets):
        for feedback_subtitle_index, feedback_subtitle in enumerate(
            feedback_item["subtitles"]
        ):
            row = {
                "Set": feedback_item["title"],
                "Subtitle": feedback_subtitle["name"],
            }
            for criterion in CRITERIA:
                value = saved_feedback.get(
                    rating_key(
                        feedback_set_index,
                        feedback_subtitle_index,
                        criterion,
                    )
                )
                row[criterion] = value + 1 if value is not None else None

            row["Comments"] = saved_feedback.get(
                f"comment_{feedback_set_index}_{feedback_subtitle_index}", ""
            )
            rows.append(row)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Feedback")

    st.download_button(
        "Export feedback (.xlsx)",
        data=output.getvalue(),
        file_name="subtitle_feedback.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

render_feedback(current, set_item, dataset)

previous, spacer, next_button = st.columns([1, 5, 1])
with previous:
    previous_bottom = st.button(
        "← Previous",
        key=f"btn_bottom_prev_{current}",
        disabled=current == 0,
    )
with next_button:
    next_bottom = st.button(
        "Next →",
        key=f"btn_bottom_next_{current}",
        disabled=current >= len(dataset) - 1,
    )
if previous_bottom:
    st.session_state.current_set = current - 1
    st.rerun()
if next_bottom:
    st.session_state.current_set = current + 1
    st.rerun()
