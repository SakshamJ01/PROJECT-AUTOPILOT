"""M4 Renderer — FFmpeg-based deterministic video renderer.
Produces 9:16 vertical MP4 from ContentPackage.

P0 rewrite:
  - TRUE kinetic captions: builds 2-4 word phrases (max 5) from the
    authoritative Faster-Whisper word timestamps via KineticTypographyEngine
    and burns an animated .ass subtitle track (entrance timing, emphasis,
    safe zones). No more "one static text block per scene".
  - AudioSceneGraph is wired into production: the final mix contains
    VOICE + BGM + SFX with real sidechain ducking and fades.
"""
from __future__ import annotations
import math
import re
import subprocess
import hashlib
import json
from pathlib import Path
from typing import Optional, List, Any, Dict
from autopilot.core.contracts import RenderPlan, RenderOutput, RenderQualityResult, RenderScene
from autopilot.core.config import CONFIG
from autopilot.core.ffmpeg_runner import FFmpegRunner


def _escape_filter_path(p: Path) -> str:
    """Escape path for use in FFmpeg filter parameters without quotes."""
    s = str(p.resolve()).replace("\\", "/")
    return s.replace(":", "\\\\:")


def _escape_ass_path(p: Path) -> str:
    """Escape a .ass subtitle path for the libass `ass` filter.

    libass requires exactly ONE backslash before the drive colon when the
    path is single-quoted; the double-backslash form used by drawtext is
    rejected by the ass filter.
    """
    s = str(p.resolve()).replace("\\", "/")
    return s.replace(":", "\\:")


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
    """Legacy helper retained for compatibility; kinetic captions supersede it."""
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


def _build_kinetic_ass_for_scene(
    scene: Dict[str, Any],
    out_ass: Path,
    engine: "KineticTypographyEngine",
) -> bool:
    """Generate an animated .ass subtitle for ONE scene from word timestamps.

    Returns True on success. Phrases are 2-4 words (max 5) and inherit the
    authoritative Whisper timing so captions sync to speech.
    """
    words_raw = scene.get("word_timestamps") or []
    narration = scene.get("narration") or ""
    if not words_raw:
        return False
    # Adapt both WordTimestamp shapes (start_sec/end_sec and start/end).
    words: List[Any] = []
    for w in words_raw:
        try:
            words.append(
                type(
                    "W",
                    (),
                    {
                        "word": str(w.get("word", "")),
                        "start": float(w.get("start", w.get("start_sec", 0.0))),
                        "end": float(w.get("end", w.get("end_sec", 0.0))),
                        "confidence": w.get("probability", w.get("confidence", None)),
                    },
                )()
            )
        except Exception:
            continue
    if not words:
        return False

    from autopilot.core.timeline import CaptionPosition, PlatformSafeZone

    emphasis = scene.get("emphasis_words") or []
    phrases = engine.segment_words_into_phrases(
        words, min_words=2, max_words=4, emphasis_keywords=emphasis
    )
    if not phrases:
        return False

    cap_pos_raw = (scene.get("caption_plan") or {}).get("position") or "LOWER"
    try:
        cap_pos = CaptionPosition(str(cap_pos_raw).upper())
    except Exception:
        cap_pos = CaptionPosition.LOWER
    platform_raw = (scene.get("caption_plan") or {}).get("platform_safe_zone") or "YOUTUBE_SHORTS"
    try:
        platform = PlatformSafeZone(str(platform_raw).upper())
    except Exception:
        platform = PlatformSafeZone.YOUTUBE_SHORTS

    ass = engine.generate_ass_script(
        phrases=phrases,
        style_preset=(scene.get("caption_plan") or {}).get("style_preset", "hormozi_yellow_pop"),
        position=cap_pos,
        platform=platform,
        saliency_y=(scene.get("crop_framing") or {}).get("saliency_y", 0.5),
        animated=True,
    )
    out_ass.parent.mkdir(parents=True, exist_ok=True)
    out_ass.write_text(ass, encoding="utf-8")
    return True


def _apply_audio_scene_graph_mix(
    scenes: List[Dict[str, Any]],
    segments: List[str],
    out_video: Path,
    work_dir: Path,
) -> Dict[str, Any]:
    """Build the VOICE + BGM + SFX master via AudioSceneGraph and mux it in.

    Deterministic SFX placement is derived from narrative word timestamps
    (scene boundaries), never fired randomly because a scene exists.
    Returns the persisted mix manifest on success, or ``{}`` when no mastered
    mix replaced the segment audio. The manifest is written to
    ``<job>/audio/audio_mix_manifest.json`` so QA can verify real ducking.
    """
    from autopilot.core.audio_scene_graph import AudioSceneGraphEngine

    if not segments:
        return {}

    # Determine per-scene timing and voice paths from the scene dicts.
    rows: List[Dict[str, Any]] = []
    cursor = 0.0
    for idx, s in enumerate(scenes):
        dur = float(s.get("duration_sec", 5.0) or 5.0)
        voice = s.get("audio_path")
        sfx_events = []
        # Deterministic SFX: hook on the FIRST scene, transition on scene
        # boundaries, payoff on the LAST scene. Placement uses the narrative
        # word timestamps (start of the first phrase) when available.
        is_first = idx == 0
        is_last = idx == len(scenes) - 1
        words = s.get("word_timestamps") or []
        first_word_t = 0.0
        if words:
            try:
                first_word_t = float(words[0].get("start", words[0].get("start_sec", 0.0)))
            except Exception:
                first_word_t = 0.0
        if is_first:
            sfx_events.append({"cue": "whoosh_fast", "time_sec": max(0.0, first_word_t), "volume_db": -6.0})
        elif is_last:
            sfx_events.append({"cue": "bass_drop", "time_sec": max(0.0, first_word_t), "volume_db": -6.0})
        else:
            sfx_events.append({"cue": "digital_pop", "time_sec": max(0.0, first_word_t), "volume_db": -9.0})

        rows.append({
            "scene_id": str(s.get("scene_id", f"scene_{idx}")),
            "start_sec": round(cursor, 3),
            "end_sec": round(cursor + dur, 3),
            "duration_sec": round(dur, 3),
            "voice_path": voice,
            "voice_duration_sec": dur,
            "sfx_events": sfx_events,
        })
        cursor += dur

    total_dur = cursor
    engine = AudioSceneGraphEngine()
    graph = engine.build_scene_graph_from_rows(rows, total_duration_sec=total_dur)

    master_path = work_dir / "master_mix.wav"
    mix = engine.mix_and_master(graph, master_path)
    if not mix.success or not Path(mix.master_audio_path).exists():
        return {}

    manifest: Dict[str, Any] = {
        "applied": True,
        "master_duration_sec": round(total_dur, 3),
        "mix_manifest": mix.mix_manifest,
        "stitch_report": getattr(engine, "last_stitch_report", {}),
        "sfx_manifest": [
            {
                "cue": item.get("cue"),
                "time_sec": round(float(item.get("time_sec", 0.0)), 3),
                "volume_db": item.get("volume_db"),
            }
            for item in (mix.sfx_manifest or [])
        ],
    }
    # A mix that could not be measured must not claim verified ducking.
    manifest["ducking_verified"] = bool(mix.mix_manifest.get("ducking_verified"))
    manifest["ducking_db"] = mix.mix_manifest.get("ducking_db")

    # Persist evidence next to the render so QA reads real measured data.
    job_dir = out_video.parent.parent
    audio_dir = job_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "audio_mix_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # Mux the mastered audio over the concatenated video.
    mux_path = work_dir / "muxed.mp4"
    mux_cmd = [
        "ffmpeg", "-y",
        "-i", str(out_video),
        "-i", str(mix.master_audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(mux_path),
    ]
    r = subprocess.run(mux_cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not mux_path.exists():
        manifest["applied"] = False
        manifest["mux_error"] = r.stderr[-500:]
        (audio_dir / "audio_mix_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return {}
    # Promote the muxed file to the final output.
    import shutil

    shutil.move(str(mux_path), str(out_video))
    return manifest


class FFmpegRenderer:
    def __init__(self, profile: str = "vertical_short"):
        self.profile = profile
        self.runner = FFmpegRunner()
        self.render_dir = CONFIG.get_artifacts_dir() / "jobs"

    def render(self, plan: RenderPlan, out_path: str) -> RenderOutput:
        out = Path(out_path)
        # Only create the parent when it is an actual subdirectory; a bare
        # filename ("out.mp4") resolves to the current directory, which needs
        # no creation.
        parent = out.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
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
        audio_mix_errors: List[str] = []

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
            kinetic_ass: Optional[Path] = None
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

            # P0 TRUE KINETIC CAPTIONS: segment authoritative Whisper word
            # timestamps into 2-4 word phrases (max 5) and burn an animated
            # .ass track. Falls back to the legacy static block ONLY when no
            # word timestamps are available for this scene.
            from autopilot.core.kinetic_typography import KineticTypographyEngine

            kinetic_engine = KineticTypographyEngine()
            kinetic_ass = segment_dir / f"captions_{idx}.ass"
            has_kinetic = _build_kinetic_ass_for_scene(s, kinetic_ass, kinetic_engine)
            if has_kinetic:
                # The libass `ass` filter (not `subtitles`) with a quoted,
                # single-backslash-escaped path is the reliable form on
                # Windows / ffmpeg 9.
                esc_ass = _escape_ass_path(kinetic_ass)
                extra_filters.append(f"ass='{esc_ass}'")
            elif font:
                narration = s.get("narration") or s.get("caption_text")
                if narration:
                    clean_narration = str(narration).replace(":", " - ").replace("'", "")
                    cap = format_caption(clean_narration).upper()
                    if cap.strip():
                        cap_file = segment_dir / f"caption_{idx}.txt"
                        cap_file.write_text(cap, encoding="utf-8")
                        esc_cap_path = _escape_filter_path(cap_file)

                        from autopilot.core.timeline import CaptionPosition, PlatformSafeZone

                        cap_pos_raw = s.get("caption_plan", {}).get("position") or s.get("crop_framing", {}).get("caption_safe_zone") or "LOWER"
                        try:
                            cap_pos_enum = CaptionPosition(str(cap_pos_raw).upper())
                        except Exception:
                            cap_pos_enum = CaptionPosition.LOWER

                        platform_raw = s.get("caption_plan", {}).get("platform_safe_zone") or "YOUTUBE_SHORTS"
                        try:
                            platform_enum = PlatformSafeZone(str(platform_raw).upper())
                        except Exception:
                            platform_enum = PlatformSafeZone.YOUTUBE_SHORTS

                        saliency_y = s.get("crop_framing", {}).get("saliency_y", 0.5)
                        _, cap_y = kinetic_engine.compute_caption_coordinates(
                            position=cap_pos_enum,
                            platform=platform_enum,
                            saliency_y=saliency_y,
                        )

                        extra_filters.append(
                            f"drawtext=fontfile={font}:textfile={esc_cap_path}:fontsize=44:fontcolor=white:borderw=5:bordercolor=black:line_spacing=14:x=(w-text_w)/2:y={cap_y}"
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
            # NOTE: No -t flag here — each segment is already individually
            # clipped to its scene duration.  A redundant -t on the concat
            # would truncate the output because H.264 GOP alignment causes
            # each encoded segment to be slightly shorter than requested,
            # and the accumulated shortfall compounds across many scenes.
            concat_cmd = ["ffmpeg", "-y"] + concat_inputs + [
                "-filter_complex", concat_filter,
                "-map", "[outv]", "-map", "[outa]",
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

        # ------------------------------------------------------------------
        # P0 AUDIO: wire AudioSceneGraph into production.
        # Build a 3-track mix (VOICE + BGM + SFX) with real sidechain ducking
        # from the scene voice segments, then mux over the concatenated video.
        # ------------------------------------------------------------------
        audio_mix_manifest: Dict[str, Any] = {}
        try:
            audio_mix_manifest = _apply_audio_scene_graph_mix(scenes, segments, out, segment_dir)
            if not audio_mix_manifest.get("applied"):
                audio_mix_errors.append("audio scene graph mix did not apply")
        except Exception as exc:
            # Audio mix enhancement must never destroy the render; the voice
            # track from the segments is still present. Record and continue.
            audio_mix_errors.append(str(exc))
        audio_mix_applied = bool(audio_mix_manifest.get("applied"))

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

        # Probe the actual rendered duration from the file — never trust
        # the pre-computed plan sum which can diverge from reality.
        actual_duration = float(duration)  # fallback
        dur_probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(out),
        ]
        dur_probe_res = subprocess.run(dur_probe_cmd, capture_output=True, text=True)
        if dur_probe_res.returncode == 0 and dur_probe_res.stdout.strip():
            try:
                actual_duration = float(dur_probe_res.stdout.strip())
            except ValueError:
                pass

        # Post-render narration truncation guard: the rendered video must
        # never be shorter than the narration audio it contains.
        if actual_duration < duration - 1.0:
            raise RuntimeError(
                f"Rendered video ({actual_duration:.2f}s) is shorter than the "
                f"narration timeline ({duration:.2f}s) by "
                f"{duration - actual_duration:.2f}s — audio would be truncated. "
                f"This indicates an encoding issue."
            )

        checksum = hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None
        # Record the mix outcome next to the media so downstream QA never has
        # to guess whether ducking was measured or silently skipped.
        if audio_mix_manifest or audio_mix_errors:
            try:
                prov_path = out.parent / "render_audio_mix.json"
                prov_path.write_text(
                    json.dumps(
                        {
                            "audio_mix_applied": audio_mix_applied,
                            "audio_mix_errors": audio_mix_errors,
                            "audio_mix_manifest": audio_mix_manifest,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
        output = RenderOutput(
            output_path=str(out),
            duration_sec=actual_duration,
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
