"""M4 Renderer — FFmpeg-based deterministic video renderer.
Produces 9:16 vertical MP4 from ContentPackage.
"""
from __future__ import annotations
import math
import re
import subprocess
import hashlib
import json
from pathlib import Path
from typing import Optional
from autopilot.core.contracts import RenderPlan, RenderOutput, RenderQualityResult, RenderScene
from autopilot.core.config import CONFIG
from autopilot.core.ffmpeg_runner import FFmpegRunner


def _escape_filter_path(p: Path) -> str:
    """Escape path for use in FFmpeg filter parameters without quotes."""
    s = str(p.resolve()).replace("\\", "/")
    return s.replace(":", "\\\\:")


def get_system_font() -> Optional[str]:
    """Locate an available system TTF font for drawtext filter."""
    if Path("C:/Windows/Fonts/arial.ttf").exists():
        return "C\\\\:/Windows/Fonts/arial.ttf"
    elif Path("C:/Windows/Fonts/segoeui.ttf").exists():
        return "C\\\\:/Windows/Fonts/segoeui.ttf"
    elif Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists():
        return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return None


def format_caption(text: str, max_chars: int = 24) -> str:
    """Format narration text into max 2 readable caption lines for short-form 9:16 portrait video."""
    words = text.split()
    lines = []
    curr: list[str] = []
    curr_len = 0
    for w in words:
        clean_w = re.sub(r"[^\w\s\-_.,!?'\"]", "", w)
        if not clean_w:
            continue
        add_len = len(clean_w) + (1 if curr else 0)
        if curr_len + add_len <= max_chars:
            curr.append(clean_w)
            curr_len += add_len
        else:
            if curr:
                lines.append(" ".join(curr))
            curr = [clean_w]
            curr_len = len(clean_w)
    if curr:
        lines.append(" ".join(curr))
    return "\n".join(lines[:2])


class FFmpegRenderer:
    def __init__(self, profile: str = "vertical_short"):
        self.profile = profile
        self.runner = FFmpegRunner()
        self.render_dir = CONFIG.get_artifacts_dir() / "jobs"

    def render(self, plan: RenderPlan, out_path: str) -> RenderOutput:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        scenes = plan.scenes or []
        if not scenes:
            raise RuntimeError(f"Render plan '{plan.plan_id}' has no scenes.")

        for idx, s in enumerate(scenes):
            ap = s.get("asset_path") or s.get("normalized_path")
            if not ap or not Path(ap).exists():
                raise RuntimeError(
                    f"Missing required visual asset for scene '{s.get('scene_id', idx)}' in job '{plan.job_id}': {ap}"
                )

        duration = sum(s.get("duration_sec", 5) for s in scenes)
        font = get_system_font()

        # Multi-scene render: render segments with motion, badges, and kinetic captions, then concatenate
        import tempfile
        segment_dir = Path(tempfile.mkdtemp(prefix="render_seg_"))
        segments = []
        for idx, s in enumerate(scenes):
            seg_path = segment_dir / f"seg_{idx}.mp4"
            ap = s.get("asset_path") or s.get("normalized_path")
            scene_image = str(ap)
            scene_voice = None
            for s2 in scenes:
                ap2 = s2.get("audio_path")
                if ap2 and Path(ap2).exists() and s2.get("scene_id") == s.get("scene_id"):
                    scene_voice = str(ap2)
                    break
            dur = s.get("duration_sec", 5)
            frames = max(25, int(math.ceil(dur * 25)))
            is_scene_video = str(scene_image).lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
            seg_cmd = ["ffmpeg", "-y"]
            if is_scene_video:
                seg_cmd.extend(["-stream_loop", "-1", "-i", scene_image])
            else:
                seg_cmd.extend(["-loop", "1", "-i", scene_image])
            if scene_voice and Path(scene_voice).exists():
                seg_cmd.extend(["-i", scene_voice])
            else:
                seg_cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono"])

            audio_input_index = 1
            # Motion filter: subtle Ken Burns on still images
            if is_scene_video:
                v_base = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
            else:
                motion_mode = idx % 3
                if motion_mode == 0:
                    v_base = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0008,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=25,format=yuv420p"
                elif motion_mode == 1:
                    v_base = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='if(lte(zoom,1.0),1.08,max(1.0,zoom-0.0008))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=25,format=yuv420p"
                else:
                    v_base = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='1.05':x='if(lte(on,-1),(x+0.5),min(iw-iw/zoom,x+0.4))':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=25,format=yuv420p"

            extra_filters = []
            if font:
                # Title card / badge
                badge = s.get("on_screen_text")
                if not badge and len(scenes) > 2 and 0 < idx < len(scenes) - 1:
                    badge = f"FACT {idx:02d}"
                if badge:
                    clean_badge = re.sub(r"[:]+", " - ", str(badge))
                    clean_badge = re.sub(r"[^\w\s\-]", "", clean_badge).upper().strip()
                    if clean_badge:
                        badge_file = segment_dir / f"badge_{idx}.txt"
                        badge_file.write_text(clean_badge, encoding="utf-8")
                        esc_badge_path = _escape_filter_path(badge_file)
                        extra_filters.append(
                            f"drawtext=fontfile={font}:textfile={esc_badge_path}:fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=280"
                        )

                # Kinetic caption overlay
                narration = s.get("narration") or s.get("caption_text")
                if narration:
                    clean_narration = str(narration).replace(":", " - ").replace("'", "")
                    cap = format_caption(clean_narration).upper()
                    if cap.strip():
                        cap_file = segment_dir / f"caption_{idx}.txt"
                        cap_file.write_text(cap, encoding="utf-8")
                        esc_cap_path = _escape_filter_path(cap_file)
                        extra_filters.append(
                            f"drawtext=fontfile={font}:textfile={esc_cap_path}:fontsize=42:fontcolor=white:borderw=4:bordercolor=black:line_spacing=14:x=(w-text_w)/2:y=1380"
                        )

            v_filter_full = v_base
            if extra_filters:
                v_filter_full += "," + ",".join(extra_filters)
            v_filter_full += f"[v{idx}]"

            # Audio filter: apply DC-blocking highpass and loudnorm on voice, format to 44.1kHz stereo for clean AAC encoding
            if scene_voice and Path(scene_voice).exists():
                a_filter = f"[{audio_input_index}:a]highpass=f=60,loudnorm=I=-18:LRA=11:TP=-1.5,aformat=sample_rates=44100:channel_layouts=stereo[a{idx}]"
            else:
                a_filter = f"[{audio_input_index}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{idx}]"

            seg_cmd.extend([
                "-filter_complex",
                f"{v_filter_full};{a_filter}",
                "-map", f"[v{idx}]",
                "-map", f"[a{idx}]",
                "-t", str(dur),
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                "-r", "25",
                "-threads", "2",
                str(seg_path),
            ])
            seg_res = subprocess.run(seg_cmd, capture_output=True, text=True)
            if seg_res.returncode != 0:
                raise RuntimeError(f"Segment {idx} render failed (exit code {seg_res.returncode}): {seg_res.stderr}")
            segments.append(str(seg_path))

        if len(segments) > 1:
            # Concatenate segments
            concat_inputs = []
            for seg in segments:
                concat_inputs.extend(["-i", seg])
            concat_filter = ""
            for i in range(len(segments)):
                concat_filter += f"[{i}:v][{i}:a]"
            concat_filter += f"concat=n={len(segments)}:v=1:a=1[outv][outa]"
            concat_cmd = ["ffmpeg", "-y"] + concat_inputs + [
                "-filter_complex", concat_filter,
                "-map", "[outv]", "-map", "[outa]",
                "-t", str(duration),
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-r", "25", "-threads", "2",
                str(out),
            ]
            result = subprocess.run(concat_cmd, capture_output=True, text=True)
            if result.returncode != 0 or not out.exists():
                raise RuntimeError(
                    f"Multi-scene render concatenation failed (exit code {result.returncode}): {result.stderr or 'Output file not created.'}"
                )
        elif len(segments) == 1:
            import shutil
            shutil.copy2(segments[0], str(out))
        else:
            raise RuntimeError(f"No segments rendered for plan {plan.plan_id}")

        # Post-render audio stream integrity validation
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_name,sample_rate,channels",
            "-of", "json",
            str(out),
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        if probe_res.returncode == 0 and probe_res.stdout:
            try:
                streams = json.loads(probe_res.stdout).get("streams", [])
                a_stream = next((s for s in streams if s.get("codec_name") == "aac"), None)
                if not a_stream:
                    raise RuntimeError(f"Render output '{out}' missing AAC audio stream")
                sr = int(a_stream.get("sample_rate", 0))
                if sr < 16000 or sr > 48000:
                    raise RuntimeError(f"Render output '{out}' has invalid audio sample rate: {sr} Hz (expected 44100 Hz / 48000 Hz)")
            except Exception as e:
                if "invalid audio sample rate" in str(e) or "missing AAC audio stream" in str(e):
                    raise

        checksum = hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None
        output = RenderOutput(
            output_path=str(out),
            duration_sec=float(duration),
            width=1080,
            height=1920,
            codec_video="h264",
            codec_audio="aac",
            container="mp4",
            file_size_bytes=out.stat().st_size if out.exists() else 0,
            checksum_sha256=checksum,
            profile=self.profile,
        )
        return output
