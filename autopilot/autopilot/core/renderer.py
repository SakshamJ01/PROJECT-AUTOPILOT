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


def get_system_font() -> Optional[str]:
    """Locate an available system TTF font for drawtext filter."""
    if Path("C:/Windows/Fonts/arial.ttf").exists():
        return "C\\\\:/Windows/Fonts/arial.ttf"
    elif Path("C:/Windows/Fonts/segoeui.ttf").exists():
        return "C\\\\:/Windows/Fonts/segoeui.ttf"
    elif Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists():
        return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return None


def format_caption(text: str, max_chars: int = 28) -> str:
    """Format narration text into max 2 readable caption lines for short-form video."""
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
        image_path = None
        for s in scenes:
            ap = s.get("asset_path") or s.get("normalized_path")
            if ap and Path(ap).exists():
                image_path = str(ap)
                break

        if not image_path:
            image_path = str(CONFIG.get_artifacts_dir() / "providers" / "fixtures" / "fixture_image.png")
            if not Path(image_path).exists():
                image_path = str(Path(__file__).resolve().parent.parent / "providers" / "fixture_image.png")

        voice_path = None
        for s in scenes:
            ap = s.get("audio_path")
            if ap and Path(ap).exists():
                voice_path = str(ap)
                break
        if not voice_path:
            import glob
            voice_files = glob.glob(str(CONFIG.get_artifacts_dir() / "jobs" / plan.job_id / "voice" / "*.wav"))
            if voice_files:
                voice_path = voice_files[0]

        duration = sum(s.get("duration_sec", 5) for s in scenes) if scenes else 5.0
        font = get_system_font()

        # Multi-scene render: render segments with motion, badges, and kinetic captions, then concatenate
        import tempfile
        segment_dir = Path(tempfile.mkdtemp(prefix="render_seg_"))
        segments = []
        for idx, s in enumerate(scenes):
            seg_path = segment_dir / f"seg_{idx}.mp4"
            ap = s.get("asset_path") or s.get("normalized_path")
            scene_image = str(ap) if ap and Path(ap).exists() else image_path
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
                        extra_filters.append(
                            f"drawtext=fontfile={font}:text='{clean_badge}':fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=280"
                        )

                # Kinetic caption overlay
                narration = s.get("narration") or s.get("caption_text")
                if narration:
                    clean_narration = str(narration).replace(":", " - ").replace("'", "")
                    cap = format_caption(clean_narration).upper()
                    # In FFmpeg drawtext inline string, newline must be escaped as literal \n
                    cap_escaped = cap.replace("\n", "\\n").replace("%", "")
                    if cap_escaped:
                        extra_filters.append(
                            f"drawtext=fontfile={font}:text='{cap_escaped}':fontsize=44:fontcolor=white:borderw=4:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y=1380"
                        )

            v_filter_full = v_base
            if extra_filters:
                v_filter_full += "," + ",".join(extra_filters)
            v_filter_full += f"[v{idx}]"

            # Audio filter: apply loudnorm on voice for professional social loudness
            if scene_voice and Path(scene_voice).exists():
                a_filter = f"[{audio_input_index}:a]loudnorm=I=-18:LRA=11:TP=-1.5,adelay=0|0[a{idx}]"
            else:
                a_filter = f"[{audio_input_index}:a]adelay=0|0[a{idx}]"

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
                "-b:a", "128k",
                "-r", "25",
                "-threads", "2",
                str(seg_path),
            ])
            seg_res = subprocess.run(seg_cmd, capture_output=True, text=True)
            if seg_res.returncode != 0:
                if len(scenes) > 1:
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
                "-c:a", "aac", "-b:a", "128k", "-r", "25", "-threads", "2",
                str(out),
            ]
            result = subprocess.run(concat_cmd, capture_output=True, text=True)
        else:
            # Single scene render
            cmd = ["ffmpeg", "-y"]
            is_video_asset = str(image_path).lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
            if is_video_asset:
                cmd.extend(["-stream_loop", "-1", "-i", image_path])
            else:
                cmd.extend(["-loop", "1", "-i", image_path])
            if voice_path and Path(voice_path).exists():
                cmd.extend(["-i", voice_path])
            else:
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono"])

            frames = max(25, int(math.ceil(duration * 25)))
            if is_video_asset:
                v_base = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
            else:
                v_base = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0008,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=25,format=yuv420p"

            extra_filters = []
            if font and scenes:
                badge = scenes[0].get("on_screen_text")
                if badge:
                    clean_badge = re.sub(r"[:]+", " - ", str(badge))
                    clean_badge = re.sub(r"[^\w\s\-]", "", clean_badge).upper().strip()
                    if clean_badge:
                        extra_filters.append(
                            f"drawtext=fontfile={font}:text='{clean_badge}':fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-text_w)/2:y=280"
                        )
                narration = scenes[0].get("narration") or scenes[0].get("caption_text")
                if narration:
                    clean_narration = str(narration).replace(":", " - ").replace("'", "")
                    cap = format_caption(clean_narration).upper()
                    cap_escaped = cap.replace("\n", "\\n").replace("%", "")
                    if cap_escaped:
                        extra_filters.append(
                            f"drawtext=fontfile={font}:text='{cap_escaped}':fontsize=44:fontcolor=white:borderw=4:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y=1380"
                        )

            v_filter_full = v_base
            if extra_filters:
                v_filter_full += "," + ",".join(extra_filters)
            v_filter_full += "[v]"

            if voice_path and Path(voice_path).exists():
                a_filter = "[1:a]loudnorm=I=-18:LRA=11:TP=-1.5,adelay=0|0[aout]"
            else:
                a_filter = "[1:a]adelay=0|0[aout]"

            cmd.extend([
                "-filter_complex",
                f"{v_filter_full};{a_filter}",
                "-map", "[v]",
                "-map", "[aout]",
                "-t", str(duration),
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-r", "25",
                "-threads", "2",
                str(out),
            ])
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                cmd_fallback = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", image_path,
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", str(duration),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-r", "25", "-threads", "2",
                    str(out),
                ]
                result = subprocess.run(cmd_fallback, capture_output=True, text=True)
        # Verify render actually produced the output file
        if result.returncode != 0 or not out.exists():
            if len(scenes) > 1:
                raise RuntimeError(
                    f"Multi-scene render failed (exit code {result.returncode}): {result.stderr or 'Output file not created.'}"
                )
            # Last-resort single-scene fallback using first available scene
            fallback_image = image_path
            fallback_voice = voice_path
            # Use first scene duration as safe fallback
            fb_dur = scenes[0].get("duration_sec", 5) if scenes else 5.0
            cmd_fb = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", fallback_image,
                "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                "-t", str(fb_dur),
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "32k", "-r", "25", "-threads", "2",
                str(out),
            ]
            if fallback_voice and Path(fallback_voice).exists():
                cmd_fb = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", fallback_image,
                    "-i", fallback_voice,
                    "-filter_complex", "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p[v];[1:a]adelay=0|0[aout]",
                    "-map", "[v]", "-map", "[aout]",
                    "-t", str(fb_dur),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "32k", "-r", "25", "-threads", "2",
                    str(out),
                ]
            result = subprocess.run(cmd_fb, capture_output=True, text=True)
        # Note: caption/text overlay omitted in MVP for simplicity; can be added via drawtext filter
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
