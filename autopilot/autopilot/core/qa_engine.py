"""PRODUCTION QA ENGINE & QUALITY GATES — Milestone 5.
Deterministic, provider-neutral media quality evaluation and publication gating.
"""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    ContentPackage, RenderPlan, AssetArtifact,
    QAReport, QACheck, QAFinding, QAMetric, QAThreshold,
    QAArtifactReference, PublishReceipt, QAStatus, QASeverity
)
from autopilot.core.media_inspection import compute_checksum


class QAEngine:
    """Core QA Engine orchestrating all deterministic quality checks."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or CONFIG
        self.qa_version = "v1.0.0"

    def get_ffmpeg_version(self) -> str:
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout:
                return r.stdout.splitlines()[0]
        except Exception:
            pass
        return "unknown"

    def probe_media(self, media_path: Path) -> dict:
        """Run ffprobe to inspect streams and format metadata."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(media_path)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {r.stderr.strip() or 'Unknown error'}")
        return json.loads(r.stdout)

    # ------------------------------------------------------------------
    # Category A: Container / File Integrity
    # ------------------------------------------------------------------
    def check_container_integrity(self, media_path: Path, probe_data: Optional[dict] = None) -> QACheck:
        check_id = "check-container-integrity"
        category = "container_integrity"
        findings: List[QAFinding] = []

        if not media_path.exists():
            findings.append(QAFinding(
                finding_id=f"{check_id}-missing",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message=f"Rendered media file does not exist: {media_path}",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.BLOCK,
                severity=QASeverity.CRITICAL, message="Rendered media missing",
                findings=findings
            )

        file_size = media_path.stat().st_size
        if file_size == 0:
            findings.append(QAFinding(
                finding_id=f"{check_id}-zero-byte",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message="Rendered media file is zero bytes (empty)",
                measured_value=0,
                expected_value="> 0 bytes",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.BLOCK,
                severity=QASeverity.CRITICAL, message="Rendered media is empty",
                findings=findings
            )

        if probe_data is None:
            findings.append(QAFinding(
                finding_id=f"{check_id}-unreadable",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message="Container unreadable or corrupted (FFprobe failed to inspect)",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.BLOCK,
                severity=QASeverity.CRITICAL, message="Container unreadable",
                findings=findings
            )

        streams = probe_data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not v_stream:
            findings.append(QAFinding(
                finding_id=f"{check_id}-missing-video-stream",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message="Container missing required video stream",
            ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else QAStatus.PASS
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.CRITICAL if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(streams)} streams, {file_size} bytes",
            expected_value="Readable container with video stream",
            message="Container integrity validated" if status == QAStatus.PASS else "Container integrity issues detected",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category B: Video Properties
    # ------------------------------------------------------------------
    def check_video_properties(self, v_stream: Optional[dict], profile: str = "vertical_short") -> Tuple[QACheck, List[QAMetric]]:
        check_id = "check-video-properties"
        category = "video_properties"
        findings: List[QAFinding] = []
        metrics: List[QAMetric] = []

        expected_w = self.config.asset_target_width  # 1080
        expected_h = self.config.asset_target_height  # 1920

        if not v_stream:
            findings.append(QAFinding(
                finding_id=f"{check_id}-no-stream",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message="No video stream available to inspect properties",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.BLOCK,
                severity=QASeverity.CRITICAL, message="Video stream missing",
                findings=findings
            ), metrics

        w = int(v_stream.get("width") or 0)
        h = int(v_stream.get("height") or 0)
        codec = v_stream.get("codec_name", "")
        pix_fmt = v_stream.get("pix_fmt", "")

        # FPS calculation
        r_fps_str = v_stream.get("r_frame_rate", "25/1")
        fps = 0.0
        try:
            num, den = r_fps_str.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0.0
        except Exception:
            fps = float(v_stream.get("avg_frame_rate", "25").split("/")[0])

        metrics.append(QAMetric(
            metric_id="metric-video-width", name="video_width", category=category,
            value_numeric=float(w), unit="px", status=QAStatus.PASS if w == expected_w else QAStatus.BLOCK
        ))
        metrics.append(QAMetric(
            metric_id="metric-video-height", name="video_height", category=category,
            value_numeric=float(h), unit="px", status=QAStatus.PASS if h == expected_h else QAStatus.BLOCK
        ))
        metrics.append(QAMetric(
            metric_id="metric-video-fps", name="video_fps", category=category,
            value_numeric=round(fps, 2), unit="fps", status=QAStatus.PASS
        ))
        metrics.append(QAMetric(
            metric_id="metric-video-codec", name="video_codec", category=category,
            value_text=codec, status=QAStatus.PASS if codec in ("h264", "hevc") else QAStatus.WARN
        ))

        # Check resolution & aspect ratio
        if w != expected_w or h != expected_h:
            findings.append(QAFinding(
                finding_id=f"{check_id}-resolution-mismatch",
                check_id=check_id,
                category=category,
                severity=QASeverity.HIGH,
                status=QAStatus.BLOCK,
                message=f"Resolution mismatch: measured {w}x{h}, expected {expected_w}x{expected_h}",
                measured_value=f"{w}x{h}",
                expected_value=f"{expected_w}x{expected_h}",
            ))

        if codec not in ("h264", "hevc", "av1"):
            findings.append(QAFinding(
                finding_id=f"{check_id}-unsupported-codec",
                check_id=check_id,
                category=category,
                severity=QASeverity.MEDIUM,
                status=QAStatus.WARN,
                message=f"Video codec '{codec}' is outside preferred H.264/HEVC standards",
                measured_value=codec,
                expected_value="h264",
            ))

        if pix_fmt and "420" not in pix_fmt:
            findings.append(QAFinding(
                finding_id=f"{check_id}-pixel-format",
                check_id=check_id,
                category=category,
                severity=QASeverity.MEDIUM,
                status=QAStatus.WARN,
                message=f"Pixel format '{pix_fmt}' may cause playback issues; expected yuv420p",
                measured_value=pix_fmt,
                expected_value="yuv420p",
            ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.HIGH if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{w}x{h}, {codec}, {fps:.1f}fps",
            expected_value=f"{expected_w}x{expected_h}, h264, 25fps",
            message="Video properties compliant with profile" if status == QAStatus.PASS else "Video property non-compliances detected",
            findings=findings,
        ), metrics

    # ------------------------------------------------------------------
    # Category C: Audio Properties
    # ------------------------------------------------------------------
    def check_audio_properties(self, a_stream: Optional[dict], narration_expected: bool = True) -> Tuple[QACheck, List[QAMetric]]:
        check_id = "check-audio-properties"
        category = "audio_properties"
        findings: List[QAFinding] = []
        metrics: List[QAMetric] = []

        if not a_stream:
            if narration_expected:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-missing-audio",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message="Narration was expected but rendered output contains no audio stream",
                ))
                return QACheck(
                    check_id=check_id, category=category, status=QAStatus.BLOCK,
                    severity=QASeverity.CRITICAL, message="Missing audio stream",
                    findings=findings
                ), metrics
            else:
                return QACheck(
                    check_id=check_id, category=category, status=QAStatus.PASS,
                    severity=QASeverity.INFO, message="No audio expected and none present",
                    findings=[]
                ), metrics

        codec = a_stream.get("codec_name", "")
        sample_rate = int(a_stream.get("sample_rate") or 0)
        channels = int(a_stream.get("channels") or 0)

        metrics.append(QAMetric(
            metric_id="metric-audio-codec", name="audio_codec", category=category,
            value_text=codec, status=QAStatus.PASS if codec in ("aac", "mp3") else QAStatus.WARN
        ))
        metrics.append(QAMetric(
            metric_id="metric-audio-sample-rate", name="audio_sample_rate", category=category,
            value_numeric=float(sample_rate), unit="Hz", status=QAStatus.PASS if sample_rate >= 22050 else QAStatus.WARN
        ))
        metrics.append(QAMetric(
            metric_id="metric-audio-channels", name="audio_channels", category=category,
            value_numeric=float(channels), unit="channels", status=QAStatus.PASS if channels >= 1 else QAStatus.BLOCK
        ))

        if sample_rate < 16000:
            findings.append(QAFinding(
                finding_id=f"{check_id}-low-sample-rate",
                check_id=check_id,
                category=category,
                severity=QASeverity.LOW,
                status=QAStatus.WARN,
                message=f"Audio sample rate {sample_rate}Hz is low for production video",
                measured_value=sample_rate,
                expected_value=">= 22050Hz",
            ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.CRITICAL if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{codec}, {sample_rate}Hz, {channels}ch",
            expected_value="aac, >=22050Hz, >=1ch",
            message="Audio properties verified" if status == QAStatus.PASS else "Audio warnings detected",
            findings=findings,
        ), metrics

    # ------------------------------------------------------------------
    # Category D & M: Loudness Analysis
    # ------------------------------------------------------------------
    def check_loudness(self, media_path: Path) -> Tuple[QACheck, List[QAMetric]]:
        check_id = "check-loudness"
        category = "audio_loudness"
        findings: List[QAFinding] = []
        metrics: List[QAMetric] = []

        cmd = [
            "ffmpeg", "-i", str(media_path),
            "-af", "ebur128=peak=true",
            "-f", "null", "-"
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            stderr = r.stderr
        except Exception as exc:
            findings.append(QAFinding(
                finding_id=f"{check_id}-analysis-failed",
                check_id=check_id,
                category=category,
                severity=QASeverity.LOW,
                status=QAStatus.WARN,
                message=f"Loudness analysis command failed: {exc}",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.WARN,
                severity=QASeverity.LOW, message="Loudness analysis failed",
                findings=findings
            ), metrics

        # Parse EBU R128 output
        m_i = re.search(r"Integrated loudness:\s+I:\s+([-\d\.]+)\s+LUFS", stderr)
        m_lra = re.search(r"Loudness range:\s+LRA:\s+([-\d\.]+)\s+LU", stderr)
        m_tp = re.search(r"True peak:\s+Peak:\s+([-\d\.]+)\s+dBFS", stderr)

        i_val = float(m_i.group(1)) if m_i else None
        lra_val = float(m_lra.group(1)) if m_lra else None
        tp_val = float(m_tp.group(1)) if m_tp else None

        target_lufs = self.config.qa_loudness_target_lufs  # -14.0
        min_lufs = self.config.qa_loudness_min_lufs        # -24.0
        max_lufs = self.config.qa_loudness_max_lufs        # -10.0

        if i_val is not None:
            lufs_status = QAStatus.PASS
            if i_val < min_lufs:
                lufs_status = QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-too-quiet",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=f"Integrated loudness {i_val:.1f} LUFS is quieter than preferred {min_lufs} LUFS",
                    measured_value=i_val,
                    expected_value=f"[{min_lufs}, {max_lufs}] LUFS",
                ))
            elif i_val > max_lufs:
                lufs_status = QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-too-loud",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=f"Integrated loudness {i_val:.1f} LUFS is louder than preferred {max_lufs} LUFS",
                    measured_value=i_val,
                    expected_value=f"[{min_lufs}, {max_lufs}] LUFS",
                ))

            metrics.append(QAMetric(
                metric_id="metric-loudness-integrated", name="loudness_integrated_lufs", category=category,
                value_numeric=i_val, unit="LUFS", status=lufs_status,
                threshold=QAThreshold(name="integrated_loudness", target_value=target_lufs, min_value=min_lufs, max_value=max_lufs)
            ))

        if lra_val is not None:
            metrics.append(QAMetric(
                metric_id="metric-loudness-range", name="loudness_range_lra", category=category,
                value_numeric=lra_val, unit="LU", status=QAStatus.PASS
            ))

        if tp_val is not None:
            tp_status = QAStatus.PASS
            if tp_val > -0.5:
                tp_status = QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-clipping",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=f"Audio true peak {tp_val:.1f} dBFS indicates potential clipping",
                    measured_value=tp_val,
                    expected_value="<= -0.5 dBFS",
                ))
            metrics.append(QAMetric(
                metric_id="metric-loudness-true-peak", name="loudness_true_peak_dbfs", category=category,
                value_numeric=tp_val, unit="dBFS", status=tp_status
            ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.MEDIUM if status == QAStatus.WARN else QASeverity.INFO,
            measured_value=f"I: {i_val} LUFS, Peak: {tp_val} dBFS" if i_val is not None else "N/A",
            expected_value=f"{target_lufs} LUFS",
            message="Loudness within acceptable profile range" if status == QAStatus.PASS else "Loudness warnings detected",
            findings=findings,
        ), metrics

    # ------------------------------------------------------------------
    # Category N: Dead-Air / Silence Detection
    # ------------------------------------------------------------------
    def check_dead_air(self, media_path: Path) -> QACheck:
        check_id = "check-dead-air"
        category = "audio_silence"
        findings: List[QAFinding] = []

        max_silence = self.config.qa_max_silence_duration_sec  # 2.0s
        threshold_db = self.config.qa_silence_threshold_db    # -50.0dB

        cmd = [
            "ffmpeg", "-i", str(media_path),
            "-af", f"silencedetect=noise={threshold_db}dB:d={max_silence}",
            "-f", "null", "-"
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            stderr = r.stderr
        except Exception as exc:
            findings.append(QAFinding(
                finding_id=f"{check_id}-cmd-error", check_id=check_id, category=category,
                severity=QASeverity.LOW, status=QAStatus.WARN, message=f"Silence detect failed: {exc}"
            ))
            return QACheck(check_id=check_id, category=category, status=QAStatus.WARN, severity=QASeverity.LOW, message="Silence check error", findings=findings)

        # Parse silence events
        starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s+([-\d\.]+)", stderr)]
        ends_and_durs = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(r"silence_end:\s+([-\d\.]+)\s+\|\s+silence_duration:\s+([-\d\.]+)", stderr)]

        for idx, (end, dur) in enumerate(ends_and_durs):
            start = starts[idx] if idx < len(starts) else (end - dur)
            if dur >= max_silence:
                sev = QASeverity.HIGH if dur >= 5.0 else QASeverity.MEDIUM
                st = QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-segment-{idx}",
                    check_id=check_id,
                    category=category,
                    severity=sev,
                    status=st,
                    message=f"Extended audio silence detected: {dur:.2f}s ({start:.2f}s to {end:.2f}s)",
                    timestamp_start_sec=start,
                    timestamp_end_sec=end,
                    duration_sec=dur,
                    measured_value=f"{dur:.2f}s",
                    expected_value=f"< {max_silence}s",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.MEDIUM if status == QAStatus.WARN else QASeverity.INFO,
            measured_value=f"{len(ends_and_durs)} silence segment(s)",
            expected_value=f"No dead-air > {max_silence}s",
            message="Audio continuity clean (no dead-air)" if status == QAStatus.PASS else f"Detected {len(findings)} extended silence interval(s)",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category E: Black-Frame Detection
    # ------------------------------------------------------------------
    def check_black_frames(self, media_path: Path) -> QACheck:
        check_id = "check-black-frames"
        category = "black_frame_detection"
        findings: List[QAFinding] = []

        max_black = self.config.qa_max_black_duration_sec  # 1.0s

        cmd = [
            "ffmpeg", "-i", str(media_path),
            "-vf", "blackdetect=d=0.5:pic_th=0.98:pix_th=0.10",
            "-f", "null", "-"
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            stderr = r.stderr
        except Exception as exc:
            findings.append(QAFinding(
                finding_id=f"{check_id}-cmd-error", check_id=check_id, category=category,
                severity=QASeverity.LOW, status=QAStatus.WARN, message=f"Black frame detect failed: {exc}"
            ))
            return QACheck(check_id=check_id, category=category, status=QAStatus.WARN, severity=QASeverity.LOW, message="Black frame check error", findings=findings)

        matches = list(re.finditer(r"black_start:\s*([-\d\.]+)\s+black_end:\s*([-\d\.]+)\s+black_duration:\s*([-\d\.]+)", stderr))

        for idx, m in enumerate(matches):
            start = float(m.group(1))
            end = float(m.group(2))
            dur = float(m.group(3))
            if dur >= max_black:
                sev = QASeverity.HIGH if dur >= 2.0 else QASeverity.MEDIUM
                st = QAStatus.BLOCK if dur >= 3.0 else QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-segment-{idx}",
                    check_id=check_id,
                    category=category,
                    severity=sev,
                    status=st,
                    message=f"Full-frame black section detected: {dur:.2f}s ({start:.2f}s to {end:.2f}s)",
                    timestamp_start_sec=start,
                    timestamp_end_sec=end,
                    duration_sec=dur,
                    measured_value=f"{dur:.2f}s",
                    expected_value=f"< {max_black}s",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.HIGH if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(matches)} black segment(s)",
            expected_value=f"No solid black sections > {max_black}s",
            message="Visual frame continuity clean (no blackouts)" if status == QAStatus.PASS else f"Detected {len(findings)} blackout segment(s)",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category F: Freeze / Static-Frame Detection
    # ------------------------------------------------------------------
    def check_freeze_frames(self, media_path: Path, media_duration: Optional[float] = None) -> QACheck:
        check_id = "check-freeze-frames"
        category = "freeze_frame_detection"
        findings: List[QAFinding] = []

        max_freeze = self.config.qa_max_freeze_duration_sec  # 3.0s

        cmd = [
            "ffmpeg", "-i", str(media_path),
            "-vf", f"freezedetect=n=-50dB:d={max_freeze}",
            "-f", "null", "-"
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            stderr = r.stderr
        except Exception as exc:
            findings.append(QAFinding(
                finding_id=f"{check_id}-cmd-error", check_id=check_id, category=category,
                severity=QASeverity.LOW, status=QAStatus.WARN, message=f"Freeze detect failed: {exc}"
            ))
            return QACheck(check_id=check_id, category=category, status=QAStatus.WARN, severity=QASeverity.LOW, message="Freeze check error", findings=findings)

        starts = [float(m.group(1)) for m in re.finditer(r"lavfi\.freezedetect\.freeze_start:\s+([-\d\.]+)", stderr)]
        durs = [float(m.group(1)) for m in re.finditer(r"lavfi\.freezedetect\.freeze_duration:\s+([-\d\.]+)", stderr)]

        for idx, start in enumerate(starts):
            if idx < len(durs):
                dur = durs[idx]
            else:
                # If freeze started and reached EOF without resuming motion,
                # FFmpeg only prints freeze_start. Since freezedetect only emits freeze_start
                # once the freeze has lasted at least d seconds, dur is at least max_freeze.
                dur = (media_duration - start) if (media_duration and media_duration > start) else max_freeze

            if dur >= max_freeze:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-segment-{idx}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=f"Static/frozen frame run detected: {dur:.2f}s starting at {start:.2f}s",
                    timestamp_start_sec=start,
                    timestamp_end_sec=start + dur,
                    duration_sec=dur,
                    measured_value=f"{dur:.2f}s",
                    expected_value=f"< {max_freeze}s",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.MEDIUM if status == QAStatus.WARN else QASeverity.INFO,
            measured_value=f"{len(starts)} frozen segment(s)",
            expected_value=f"No frozen frame runs > {max_freeze}s",
            message="No abnormal static freezes detected" if status == QAStatus.PASS else f"Detected {len(findings)} static frame interval(s)",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category D: Duration / Timeline QA
    # ------------------------------------------------------------------
    def check_duration_timeline(
        self,
        actual_dur: float,
        plan: Optional[RenderPlan] = None,
        package: Optional[ContentPackage] = None,
    ) -> Tuple[QACheck, List[QAMetric]]:
        check_id = "check-duration-timeline"
        category = "duration_timeline"
        findings: List[QAFinding] = []
        metrics: List[QAMetric] = []

        metrics.append(QAMetric(
            metric_id="metric-actual-duration", name="actual_duration_seconds", category=category,
            value_numeric=round(actual_dur, 2), unit="seconds", status=QAStatus.PASS if actual_dur > 0 else QAStatus.BLOCK
        ))

        if actual_dur <= 0:
            findings.append(QAFinding(
                finding_id=f"{check_id}-zero-duration",
                check_id=check_id,
                category=category,
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message=f"Rendered media duration is invalid: {actual_dur}s",
                measured_value=actual_dur,
                expected_value="> 0.0s",
            ))
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.BLOCK,
                severity=QASeverity.CRITICAL, message="Invalid zero duration", findings=findings
            ), metrics

        # Retain raw speech duration for diagnostics
        raw_speech_dur = None
        if plan and getattr(plan, "raw_speech_duration_sec", None) is not None and float(plan.raw_speech_duration_sec) > 0:
            raw_speech_dur = float(plan.raw_speech_duration_sec)
        elif plan and plan.scenes:
            raw_speech_dur = sum(float(s.get("duration_sec", 0.0)) for s in plan.scenes)
        elif package and package.measured_duration_sec:
            raw_speech_dur = float(package.measured_duration_sec)

        if raw_speech_dur is not None and raw_speech_dur > 0:
            metrics.append(QAMetric(
                metric_id="metric-raw-speech-duration", name="raw_speech_duration_seconds", category=category,
                value_numeric=round(raw_speech_dur, 2), unit="seconds", status=QAStatus.PASS
            ))

        # Determine authoritative expected duration vs fallback
        expected_dur = 0.0
        if plan and getattr(plan, "rendered_duration_sec", None) is not None and float(plan.rendered_duration_sec) > 0:
            expected_dur = float(plan.rendered_duration_sec)
        elif plan and plan.scenes:
            expected_dur = sum(float(s.get("duration_sec", 0.0)) for s in plan.scenes)
        elif package and package.measured_duration_sec:
            expected_dur = float(package.measured_duration_sec)
        elif package and package.script and package.script.total_estimated_duration:
            expected_dur = float(package.script.total_estimated_duration)

        if expected_dur > 0:
            drift = abs(actual_dur - expected_dur)
            drift_pct = drift / expected_dur

            metrics.append(QAMetric(
                metric_id="metric-duration-drift", name="duration_drift_seconds", category=category,
                value_numeric=round(drift, 2), unit="seconds", status=QAStatus.PASS if drift <= 1.5 else QAStatus.WARN
            ))

            if drift > 3.0 and drift_pct > 0.25:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-major-drift",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.HIGH,
                    status=QAStatus.BLOCK,
                    message=f"Major duration drift: measured {actual_dur:.2f}s vs expected {expected_dur:.2f}s (drift {drift:.2f}s, {drift_pct:.1%})",
                    measured_value=actual_dur,
                    expected_value=expected_dur,
                ))
            elif drift > self.config.qa_duration_tolerance_sec:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-minor-drift",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.LOW,
                    status=QAStatus.WARN,
                    message=f"Duration variance within loose tolerance: measured {actual_dur:.2f}s vs expected {expected_dur:.2f}s",
                    measured_value=actual_dur,
                    expected_value=expected_dur,
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.HIGH if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{actual_dur:.2f}s",
            expected_value=f"{expected_dur:.2f}s" if expected_dur > 0 else "N/A",
            message="Duration verified" if status == QAStatus.PASS else "Duration variance detected",
            findings=findings,
        ), metrics

    # ------------------------------------------------------------------
    # Category H: Scene Coverage
    # ------------------------------------------------------------------
    def check_scene_coverage(self, plan: Optional[RenderPlan], package: Optional[ContentPackage]) -> QACheck:
        check_id = "check-scene-coverage"
        category = "scene_coverage"
        findings: List[QAFinding] = []

        if not package or not package.script:
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.PASS,
                severity=QASeverity.INFO, message="No script package provided for cross-coverage",
                findings=[]
            )

        script_scene_ids = [s.scene_id for s in package.script.scenes]
        plan_scene_ids = [s.get("scene_id") for s in plan.scenes] if plan else []

        missing_scenes = set(script_scene_ids) - set(plan_scene_ids)
        if missing_scenes:
            findings.append(QAFinding(
                finding_id=f"{check_id}-missing-scenes",
                check_id=check_id,
                category=category,
                severity=QASeverity.HIGH,
                status=QAStatus.BLOCK,
                message=f"Script scenes missing from RenderPlan: {sorted(list(missing_scenes))}",
                measured_value=plan_scene_ids,
                expected_value=script_scene_ids,
            ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else QAStatus.PASS
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.HIGH if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(plan_scene_ids)} of {len(script_scene_ids)} scenes present",
            expected_value=f"{len(script_scene_ids)} scenes",
            message="All intended scenes covered in render plan" if status == QAStatus.PASS else "Scene coverage gaps detected",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category G & Visual: Caption QA
    # ------------------------------------------------------------------
    def check_captions(self, package: Optional[ContentPackage], plan: Optional[RenderPlan]) -> QACheck:
        check_id = "check-captions"
        category = "caption_qa"
        findings: List[QAFinding] = []

        max_line_len = self.config.qa_caption_max_line_length  # 40
        max_lines = self.config.qa_caption_max_lines            # 2

        scenes = plan.scenes if plan and plan.scenes else (package.script.scenes if package and package.script else [])

        for idx, s in enumerate(scenes):
            scene_id = s.get("scene_id") if isinstance(s, dict) else s.scene_id
            text = (s.get("caption_text") if isinstance(s, dict) else getattr(s, "on_screen_text", None)) or ""
            dur = float(s.get("duration_sec") if isinstance(s, dict) else s.estimated_duration_seconds)

            if dur <= 0:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-non-positive-duration-{scene_id}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.HIGH,
                    status=QAStatus.BLOCK,
                    message=f"Scene {scene_id} has invalid non-positive duration: {dur}s",
                ))

            if text.strip():
                lines = text.strip().splitlines()
                if len(lines) > max_lines:
                    findings.append(QAFinding(
                        finding_id=f"{check_id}-line-count-{scene_id}",
                        check_id=check_id,
                        category=category,
                        severity=QASeverity.MEDIUM,
                        status=QAStatus.WARN,
                        message=f"Caption in scene {scene_id} exceeds maximum lines ({len(lines)} > {max_lines})",
                        measured_value=len(lines),
                        expected_value=f"<= {max_lines}",
                    ))
                for l_idx, line in enumerate(lines):
                    if len(line) > max_line_len:
                        findings.append(QAFinding(
                            finding_id=f"{check_id}-line-len-{scene_id}-{l_idx}",
                            check_id=check_id,
                            category=category,
                            severity=QASeverity.MEDIUM,
                            status=QAStatus.WARN,
                            message=f"Caption line in scene {scene_id} exceeds max characters ({len(line)} > {max_line_len})",
                            measured_value=len(line),
                            expected_value=f"<= {max_line_len}",
                        ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.HIGH if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(scenes)} scenes inspected",
            expected_value=f"<= {max_lines} lines, <= {max_line_len} chars/line",
            message="Captions comply with formatting and timing bounds" if status == QAStatus.PASS else "Caption formatting findings detected",
            findings=findings,
            evidence={"visual_qa_note": "Caption layout bounded against safe margins (80px horizontal, 160px vertical)"},
        )

    # ------------------------------------------------------------------
    # Category I: Asset / Provenance Integrity
    # ------------------------------------------------------------------
    def check_provenance(self, asset_artifacts: List[AssetArtifact]) -> QACheck:
        check_id = "check-provenance-integrity"
        category = "asset_provenance"
        findings: List[QAFinding] = []

        for art in asset_artifacts:
            target_path = art.normalized_path or art.source_path
            if not target_path or not Path(target_path).exists():
                findings.append(QAFinding(
                    finding_id=f"{check_id}-missing-file-{art.artifact_id}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message=f"Production asset file missing on disk: {target_path} (scene={art.scene_id})",
                ))
                continue

            if art.checksum_sha256:
                actual_hash = compute_checksum(target_path)
                exp_hash = art.provenance.normalized_hash_sha256 or art.checksum_sha256
                if actual_hash != exp_hash:
                    findings.append(QAFinding(
                        finding_id=f"{check_id}-checksum-mismatch-{art.artifact_id}",
                        check_id=check_id,
                        category=category,
                        severity=QASeverity.CRITICAL,
                        status=QAStatus.BLOCK,
                        message=f"Asset checksum mismatch for {target_path}",
                        measured_value=actual_hash,
                        expected_value=exp_hash,
                    ))

            if not art.provenance.provider:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-missing-provider-{art.artifact_id}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.HIGH,
                    status=QAStatus.BLOCK,
                    message=f"Asset {art.artifact_id} missing provider provenance",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else QAStatus.PASS
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.CRITICAL if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(asset_artifacts)} assets validated",
            expected_value="Complete provenance chain and verified hashes",
            message="All asset provenance and checksums verified" if status == QAStatus.PASS else "Asset provenance integrity blocked",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category J: Rights / License Gate
    # ------------------------------------------------------------------
    def check_rights_gate(self, asset_artifacts: List[AssetArtifact]) -> QACheck:
        check_id = "check-rights-gate"
        category = "rights_gate"
        findings: List[QAFinding] = []

        allow_partial = self.config.rights_policy_allow_partial

        for art in asset_artifacts:
            rights = (art.license.rights_status or "UNKNOWN").upper()
            lic_name = art.license.license_name

            if rights == "VERIFIED":
                continue
            elif rights == "PARTIALLY_VERIFIED":
                sev = QASeverity.HIGH if not allow_partial else QASeverity.LOW
                st = QAStatus.BLOCK if not allow_partial else QAStatus.WARN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-partial-{art.artifact_id}",
                    check_id=check_id,
                    category=category,
                    severity=sev,
                    status=st,
                    message=f"Asset {art.artifact_id} has PARTIALLY_VERIFIED rights ({lic_name})",
                    measured_value=rights,
                    expected_value="VERIFIED",
                ))
            elif rights == "REJECTED":
                findings.append(QAFinding(
                    finding_id=f"{check_id}-rejected-{art.artifact_id}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message=f"Asset {art.artifact_id} has REJECTED license: {lic_name}",
                    measured_value=rights,
                    expected_value="VERIFIED",
                ))
            else:  # UNKNOWN
                findings.append(QAFinding(
                    finding_id=f"{check_id}-unknown-{art.artifact_id}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message=f"Asset {art.artifact_id} has UNKNOWN rights ({lic_name}); production blocked",
                    measured_value=rights,
                    expected_value="VERIFIED",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else (
            QAStatus.WARN if any(f.status == QAStatus.WARN for f in findings) else QAStatus.PASS
        )
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.CRITICAL if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(asset_artifacts)} assets checked",
            expected_value="All assets VERIFIED",
            message="All production assets cleared by rights gate" if status == QAStatus.PASS else "Rights gate blocked unverified/rejected assets",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category K: Render-Plan Consistency
    # ------------------------------------------------------------------
    def check_render_plan_consistency(self, plan: Optional[RenderPlan]) -> QACheck:
        check_id = "check-render-plan-consistency"
        category = "render_plan_consistency"
        findings: List[QAFinding] = []

        if not plan:
            return QACheck(
                check_id=check_id, category=category, status=QAStatus.PASS,
                severity=QASeverity.INFO, message="No RenderPlan to evaluate", findings=[]
            )

        for s in plan.scenes:
            sid = s.get("scene_id", "unknown")
            ap = s.get("asset_path")
            if ap and not Path(ap).exists():
                findings.append(QAFinding(
                    finding_id=f"{check_id}-stale-asset-{sid}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message=f"RenderPlan references non-existent or stale asset artifact: {ap}",
                ))

            audio_p = s.get("audio_path")
            if audio_p and not Path(audio_p).exists():
                findings.append(QAFinding(
                    finding_id=f"{check_id}-stale-audio-{sid}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.CRITICAL,
                    status=QAStatus.BLOCK,
                    message=f"RenderPlan references non-existent audio artifact: {audio_p}",
                ))

        status = QAStatus.BLOCK if any(f.status == QAStatus.BLOCK for f in findings) else QAStatus.PASS
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.CRITICAL if status == QAStatus.BLOCK else QASeverity.INFO,
            measured_value=f"{len(plan.scenes)} scenes in plan",
            expected_value="All referenced artifacts exist on disk",
            message="RenderPlan artifacts consistent and valid" if status == QAStatus.PASS else "RenderPlan contains stale/missing artifact references",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Category L: Duplicate-Output Detection
    # ------------------------------------------------------------------
    def check_duplicate_output(self, media_path: Path, current_job_id: str, db_manager: Optional[Any] = None) -> QACheck:
        check_id = "check-duplicate-output"
        category = "duplicate_output"
        findings: List[QAFinding] = []

        file_hash = compute_checksum(media_path) if media_path.exists() else ""

        if db_manager and file_hash:
            existing = None
            if hasattr(db_manager, "get_job_by_output_checksum"):
                existing = db_manager.get_job_by_output_checksum(file_hash)

            if existing and existing.get("job_id") != current_job_id:
                findings.append(QAFinding(
                    finding_id=f"{check_id}-duplicate-{file_hash[:8]}",
                    check_id=check_id,
                    category=category,
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=f"Identical rendered output SHA-256 matches prior job '{existing.get('job_id')}'",
                    measured_value=file_hash,
                    expected_value="Unique content hash across different jobs",
                ))

        status = QAStatus.WARN if findings else QAStatus.PASS
        return QACheck(
            check_id=check_id,
            category=category,
            status=status,
            severity=QASeverity.MEDIUM if status == QAStatus.WARN else QASeverity.INFO,
            measured_value=file_hash[:16] if file_hash else "none",
            expected_value="Unique output hash",
            message="Output identity unique" if status == QAStatus.PASS else "Duplicate output detected across jobs",
            findings=findings,
        )

    # ------------------------------------------------------------------
    # Master Orchestration
    # ------------------------------------------------------------------
    def evaluate(
        self,
        media_path: str | Path,
        plan: Optional[RenderPlan] = None,
        package: Optional[ContentPackage] = None,
        asset_artifacts: Optional[List[AssetArtifact]] = None,
        profile: str = "vertical_short",
        strict: bool = False,
        job_id: Optional[str] = None,
        db_manager: Optional[Any] = None,
    ) -> QAReport:
        """Run all QA checks across the 13 categories and aggregate into a QAReport."""
        m_path = Path(media_path)
        job_id = job_id or (plan.job_id if plan else (package.content_item.content_id if package else "job-unknown"))
        content_id = package.content_item.content_id if package else (plan.content_id if plan else job_id)

        checks: List[QACheck] = []
        all_metrics: List[QAMetric] = []

        # 1. Probe media
        probe_data = None
        try:
            if m_path.exists() and m_path.stat().st_size > 0:
                probe_data = self.probe_media(m_path)
        except Exception:
            probe_data = None

        # A. Container / Integrity
        c_container = self.check_container_integrity(m_path, probe_data)
        checks.append(c_container)

        streams = probe_data.get("streams", []) if probe_data else []
        fmt = probe_data.get("format", {}) if probe_data else {}
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        actual_dur = float(fmt.get("duration", 0.0) or 0.0)

        # B. Video Properties
        c_video, m_video = self.check_video_properties(v_stream, profile=profile)
        checks.append(c_video)
        all_metrics.extend(m_video)

        # C. Audio Properties
        narration_needed = bool(package.voice_segments or package.voice_artifacts) if package else True
        c_audio, m_audio = self.check_audio_properties(a_stream, narration_expected=narration_needed)
        checks.append(c_audio)
        all_metrics.extend(m_audio)

        # D & M. Loudness & Dead-Air (only if media readable and has audio)
        if a_stream and m_path.exists():
            c_loudness, m_loudness = self.check_loudness(m_path)
            checks.append(c_loudness)
            all_metrics.extend(m_loudness)

            c_dead_air = self.check_dead_air(m_path)
            checks.append(c_dead_air)

        # E & F. Black Frames & Freeze Frames (only if media readable and has video)
        if v_stream and m_path.exists():
            c_black = self.check_black_frames(m_path)
            checks.append(c_black)

            c_freeze = self.check_freeze_frames(m_path, media_duration=actual_dur)
            checks.append(c_freeze)

        # G. Duration & Timeline
        c_dur, m_dur = self.check_duration_timeline(actual_dur, plan=plan, package=package)
        checks.append(c_dur)
        all_metrics.extend(m_dur)

        # H. Scene Coverage
        c_coverage = self.check_scene_coverage(plan=plan, package=package)
        checks.append(c_coverage)

        # I & Visual. Captions
        c_caption = self.check_captions(package=package, plan=plan)
        checks.append(c_caption)

        # J. Asset Provenance & Rights Gate
        if asset_artifacts:
            c_prov = self.check_provenance(asset_artifacts)
            checks.append(c_prov)

            c_rights = self.check_rights_gate(asset_artifacts)
            checks.append(c_rights)

        # K. Render-Plan Consistency
        c_plan = self.check_render_plan_consistency(plan)
        checks.append(c_plan)

        # L. Duplicate Output Detection
        c_dup = self.check_duplicate_output(m_path, current_job_id=job_id, db_manager=db_manager)
        checks.append(c_dup)

        # Aggregate Findings & Final Decision
        all_findings: List[QAFinding] = []
        for c in checks:
            all_findings.extend(c.findings)

        blocking_findings = [f for f in all_findings if f.status == QAStatus.BLOCK]
        warnings = [f for f in all_findings if f.status == QAStatus.WARN]

        # Determine overall status and publishability invariant
        if blocking_findings:
            overall_status = QAStatus.BLOCK
            publish_allowed = False
        elif warnings:
            if strict or self.config.qa_strict_mode:
                overall_status = QAStatus.BLOCK
                publish_allowed = False
            else:
                overall_status = QAStatus.WARN
                publish_allowed = True
        else:
            overall_status = QAStatus.PASS
            publish_allowed = True

        report_id = f"qa-{job_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        media_hash = compute_checksum(m_path) if m_path.exists() else None

        receipt = PublishReceipt(
            receipt_id=f"rcpt-{job_id}",
            job_id=job_id,
            content_id=content_id,
            status=overall_status,
            publish_allowed=publish_allowed,
            blocking_findings=blocking_findings,
            warnings=warnings,
            metrics={m.name: m.value_numeric if m.value_numeric is not None else m.value_text for m in all_metrics},
            qa_version=self.qa_version,
            media_path=str(m_path),
            media_checksum_sha256=media_hash,
        )

        input_hashes = {}
        if m_path.exists():
            input_hashes["rendered_media"] = media_hash or ""
        if plan and plan.scenes:
            for s in plan.scenes:
                ap = s.get("asset_path")
                if ap and Path(ap).exists():
                    input_hashes[f"asset_{s.get('scene_id')}"] = compute_checksum(ap)

        return QAReport(
            report_id=report_id,
            job_id=job_id,
            content_id=content_id,
            qa_version=self.qa_version,
            renderer_version="v1.0.0",
            ffmpeg_version=self.get_ffmpeg_version(),
            profile=profile,
            status=overall_status,
            publish_allowed=publish_allowed,
            checks=checks,
            findings=all_findings,
            metrics=all_metrics,
            receipt=receipt,
            input_artifact_hashes=input_hashes,
        )


def export_qa_artifacts(report: QAReport, out_dir: Path) -> dict:
    """Store standard QA receipt and diagnostic files."""
    quality_dir = out_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    report_path = quality_dir / "quality_report.json"
    findings_path = quality_dir / "findings.json"
    metrics_path = quality_dir / "metrics.json"
    receipt_path = quality_dir / "receipt.json"

    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    findings_path.write_text(
        json.dumps([f.model_dump() for f in report.findings], indent=2, default=str), encoding="utf-8"
    )
    metrics_path.write_text(
        json.dumps([m.model_dump() for m in report.metrics], indent=2, default=str), encoding="utf-8"
    )
    if report.receipt:
        receipt_path.write_text(report.receipt.model_dump_json(indent=2), encoding="utf-8")

    return {
        "quality_report": str(report_path),
        "findings": str(findings_path),
        "metrics": str(metrics_path),
        "receipt": str(receipt_path),
    }
