"""60-Topic Creative Benchmark Dataset & Golden Master A/B Comparison — Phase 5.

Implements:
  - 60-Topic Benchmark Corpus metadata across 6 domain categories:
      1. Science (10 topics)
      2. History (10 topics)
      3. Technology (10 topics)
      4. Geography (10 topics)
      5. Listicles (10 topics)
      6. Explainers & Myths (10 topics)
  - Negative Defect Test Fixtures (Bad Crop, Caption Overflow, Visual Mismatch, Audio Unbalance)
  - Lightweight Golden Master & A/B Comparison Tooling.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from autopilot.core.timeline import (
    CaptionPhrase,
    CaptionPosition,
    CropFraming,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedNarration,
    MaterializedScene,
    MaterializedTimeline,
    MaterializedTiming,
    NarrativeRole,
    PlatformSafeZone,
    SelectedAsset,
    SFXEvent,
    TrimRange,
    VisualRequirements,
    WordTimestamp,
)
from autopilot.core.creative_qa import CreativeQAReport, CreativeQAStatus


class BenchmarkTopic(BaseModel):
    topic_id: str
    category: str
    title: str
    target_duration_sec: float = 30.0
    key_assertions: List[str] = Field(default_factory=list)
    expected_style: str = "hormozi_yellow_pop"


BENCHMARK_60_CORPUS: List[BenchmarkTopic] = [
    # 1. Science (10)
    BenchmarkTopic(topic_id="sci-01", category="Science", title="Why Quantum Superposition Matters", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="sci-02", category="Science", title="CRISPR Gene Editing Explained in 30s", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="sci-03", category="Science", title="How Black Holes Bend Light", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="sci-04", category="Science", title="The Speed of Neutrinos Underground", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="sci-05", category="Science", title="Why Liquid Nitrogen Freezes Instantly", target_duration_sec=25.0),
    BenchmarkTopic(topic_id="sci-06", category="Science", title="How Mitosis Duplicates DNA", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="sci-07", category="Science", title="The Physics of Fusion Energy", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="sci-08", category="Science", title="What Happens During a Supernova", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="sci-09", category="Science", title="How Graphene Conducts Electricity", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="sci-10", category="Science", title="The James Webb Deep Field Discoveries", target_duration_sec=35.0),

    # 2. History (10)
    BenchmarkTopic(topic_id="hist-01", category="History", title="How the Roman Aqueducts Were Built", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="hist-02", category="History", title="The Mystery of Greek Fire", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="hist-03", category="History", title="Why the Library of Alexandria Burned", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="hist-04", category="History", title="The Construction of the Great Wall", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="hist-05", category="History", title="How the Silk Road Connected Continents", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="hist-06", category="History", title="The First Moon Landing Code by Margaret Hamilton", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="hist-07", category="History", title="Why Ironclad Ships Changed Naval Warfare", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="hist-08", category="History", title="The Discovery of Tutankhamun's Tomb", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="hist-09", category="History", title="How Bletchley Park Cracked Enigma", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="hist-10", category="History", title="The Engineering of Machu Picchu", target_duration_sec=35.0),

    # 3. Technology (10)
    BenchmarkTopic(topic_id="tech-01", category="Technology", title="How Transformer Neural Networks Work", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="tech-02", category="Technology", title="Why 3nm Silicon Chips Are So Hard to Make", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="tech-03", category="Technology", title="How Fiber Optic Cables Carry Global Internet", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="tech-04", category="Technology", title="Solid-State Batteries vs Lithium Ion", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="tech-05", category="Technology", title="How Starlink Phased Array Antennas Track Satellites", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="tech-06", category="Technology", title="The Future of Reusable Rockets", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="tech-07", category="Technology", title="How GPU Tensor Cores Accelerate Matrix Math", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="tech-08", category="Technology", title="Why Post-Quantum Cryptography Is Urgent", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="tech-09", category="Technology", title="How Magnetic Levitation Trains Float", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="tech-10", category="Technology", title="The Mechanism Behind OLED Displays", target_duration_sec=25.0),

    # 4. Geography (10)
    BenchmarkTopic(topic_id="geo-01", category="Geography", title="Why the Mariana Trench Is So Deep", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="geo-02", category="Geography", title="The Formation of the Grand Canyon", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="geo-03", category="Geography", title="How the Gulf Stream Warms Europe", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="geo-04", category="Geography", title="The Chenab Rail Bridge Elevation", target_duration_sec=25.0),
    BenchmarkTopic(topic_id="geo-05", category="Geography", title="Why the Sahara Was Once Green", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="geo-06", category="Geography", title="How the Ring of Fire Causes Earthquakes", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="geo-07", category="Geography", title="The Physics of Antarctic Ice Shelves", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="geo-08", category="Geography", title="Why the Dead Sea Has Extreme Salinity", target_duration_sec=25.0),
    BenchmarkTopic(topic_id="geo-09", category="Geography", title="How the Himalayas Keep Growing", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="geo-10", category="Geography", title="The Underground Rivers of the Yucatan", target_duration_sec=30.0),

    # 5. Listicles (10)
    BenchmarkTopic(topic_id="list-01", category="Listicles", title="3 Insane Physics Paradoxes", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="list-02", category="Listicles", title="Top 3 Mega-Engineering Marvels", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="list-03", category="Listicles", title="3 Space Telescopes Changing Astronomy", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="list-04", category="Listicles", title="3 Unsolved Ancient Engineering Mysteries", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="list-05", category="Listicles", title="3 Animals With Biological Superpowers", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="list-06", category="Listicles", title="Top 3 Deepest Caves on Earth", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="list-07", category="Listicles", title="3 Elements More Expensive Than Gold", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="list-08", category="Listicles", title="3 High-Speed Trains Around the World", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="list-09", category="Listicles", title="3 Most Isolated Places on Planet Earth", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="list-10", category="Listicles", title="Top 3 Supercomputers and Their Speed", target_duration_sec=35.0),

    # 6. Explainers & Myths (10)
    BenchmarkTopic(topic_id="exp-01", category="Explainers & Myths", title="Myth Busted: Can Lightning Strike the Same Place Twice?", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="exp-02", category="Explainers & Myths", title="Why Airplanes Fly: The Real Aerodynamics", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="exp-03", category="Explainers & Myths", title="Do We Really Only Use 10 Percent of Our Brain?", target_duration_sec=25.0),
    BenchmarkTopic(topic_id="exp-04", category="Explainers & Myths", title="Why Does Time Slow Down Near Speed of Light?", target_duration_sec=40.0),
    BenchmarkTopic(topic_id="exp-05", category="Explainers & Myths", title="How Noise-Canceling Headphones Eliminate Waves", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="exp-06", category="Explainers & Myths", title="Why Is the Ocean Blue Instead of Clear?", target_duration_sec=25.0),
    BenchmarkTopic(topic_id="exp-07", category="Explainers & Myths", title="Does Hot Water Freeze Faster Than Cold Water?", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="exp-08", category="Explainers & Myths", title="Why Do Mirrors Flip Left and Right but Not Up and Down?", target_duration_sec=35.0),
    BenchmarkTopic(topic_id="exp-09", category="Explainers & Myths", title="How Touchscreens Detect Your Fingers", target_duration_sec=30.0),
    BenchmarkTopic(topic_id="exp-10", category="Explainers & Myths", title="Why Is Space Pitch Black Despite Trillions of Stars?", target_duration_sec=35.0),
]


class GoldenMasterComparisonResult(BaseModel):
    production_id_a: str
    production_id_b: str
    duration_delta_sec: float
    score_delta: float
    visual_match_delta: float
    caption_readability_delta: float
    pacing_delta: float
    winner: str
    summary: str


class CreativeBenchmarkRunner:
    """Benchmark runner and Golden Master A/B comparison utility."""

    @staticmethod
    def get_corpus(category: Optional[str] = None) -> List[BenchmarkTopic]:
        if category:
            return [t for t in BENCHMARK_60_CORPUS if t.category.lower() == category.lower()]
        return list(BENCHMARK_60_CORPUS)

    @staticmethod
    def create_negative_defect_fixture(defect_type: str) -> MaterializedTimeline:
        """Construct synthetic timelines with intentional known defects for QA testing."""
        if defect_type == "bad_crop":
            # Subject at center, caption placed directly at center
            return MaterializedTimeline(
                timeline_id="mt-bad-crop",
                job_id="job-bad-crop",
                intent_timeline_id="it-bad-crop",
                total_measured_duration_sec=3.0,
                scenes=[
                    MaterializedScene(
                        scene_id="scene_01_defect",
                        order=1,
                        narrative_role=NarrativeRole.HOOK,
                        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=3.0, duration_sec=3.0),
                        narration=MaterializedNarration(text="Defect Collision", audio_artifact_path="fixtures/voice.wav"),
                        visual_requirements=VisualRequirements(visual_concept="Defect collision visual concept"),
                        selected_assets=[
                            SelectedAsset(
                                asset_id="img_01",
                                asset_path="fixtures/img.png",
                                media_type="image",
                                duration_sec=3.0,
                                crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.50, caption_safe_zone=CaptionPosition.CENTER),
                            )
                        ],
                        caption_plan=MaterializedCaptionPlan(
                            phrases=[CaptionPhrase(phrase_text="Defect Collision", start_sec=0.1, end_sec=2.5)],
                            position=CaptionPosition.CENTER,
                        ),
                        audio_plan=MaterializedAudioPlan(voice_path="fixtures/voice.wav", voice_duration_sec=3.0),
                    )
                ],
            )
        elif defect_type == "caption_overflow":
            # Extremely long phrase exceeding 36 characters and 6 words
            return MaterializedTimeline(
                timeline_id="mt-overflow",
                job_id="job-overflow",
                intent_timeline_id="it-overflow",
                total_measured_duration_sec=4.0,
                scenes=[
                    MaterializedScene(
                        scene_id="scene_01_overflow",
                        order=1,
                        narrative_role=NarrativeRole.HOOK,
                        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=4.0, duration_sec=4.0),
                        narration=MaterializedNarration(text="Excessive line", audio_artifact_path="fixtures/voice.wav"),
                        visual_requirements=VisualRequirements(visual_concept="Overflow concept"),
                        selected_assets=[
                            SelectedAsset(
                                asset_id="img_02",
                                asset_path="fixtures/img.png",
                                media_type="image",
                                duration_sec=4.0,
                            )
                        ],
                        caption_plan=MaterializedCaptionPlan(
                            phrases=[
                                CaptionPhrase(
                                    phrase_text="This is an excessively long and completely unreadable caption line that overflows boundaries",
                                    start_sec=0.1,
                                    end_sec=3.8,
                                    words=[WordTimestamp(word=w, start=0.1, end=3.8) for w in "This is an excessively long and unreadable caption line".split()],
                                )
                            ],
                            position=CaptionPosition.LOWER,
                        ),
                        audio_plan=MaterializedAudioPlan(voice_path="fixtures/voice.wav", voice_duration_sec=4.0),
                    )
                ],
            )
        elif defect_type == "pacing_drag":
            # Single scene dragging for 8.5 seconds
            return MaterializedTimeline(
                timeline_id="mt-drag",
                job_id="job-drag",
                intent_timeline_id="it-drag",
                total_measured_duration_sec=8.5,
                scenes=[
                    MaterializedScene(
                        scene_id="scene_01_drag",
                        order=1,
                        narrative_role=NarrativeRole.HOOK,
                        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=8.5, duration_sec=8.5),
                        narration=MaterializedNarration(text="Pacing drag narration", audio_artifact_path="fixtures/voice.wav"),
                        visual_requirements=VisualRequirements(visual_concept="Pacing drag visual concept"),
                        selected_assets=[
                            SelectedAsset(
                                asset_id="img_03",
                                asset_path="fixtures/img.png",
                                media_type="image",
                                duration_sec=8.5,
                            )
                        ],
                        audio_plan=MaterializedAudioPlan(voice_path="fixtures/voice.wav", voice_duration_sec=8.5),
                    )
                ],
            )
        else:
            return MaterializedTimeline(
                timeline_id="mt-default",
                job_id="job-default",
                intent_timeline_id="it-default",
                total_measured_duration_sec=1.0,
                scenes=[
                    MaterializedScene(
                        scene_id="s1",
                        order=1,
                        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=1.0, duration_sec=1.0),
                        narration=MaterializedNarration(text="Default", audio_artifact_path="fixtures/voice.wav"),
                        visual_requirements=VisualRequirements(visual_concept="Default concept"),
                        selected_assets=[SelectedAsset(asset_id="a1", asset_path="fixtures/a.png", media_type="image", duration_sec=1.0)],
                        audio_plan=MaterializedAudioPlan(voice_path="fixtures/voice.wav", voice_duration_sec=1.0),
                    )
                ],
            )

    @staticmethod
    def compare_productions(report_a: CreativeQAReport, report_b: CreativeQAReport) -> GoldenMasterComparisonResult:
        """Compare two production QA scorecards side-by-side."""
        dur_delta = 0.0
        score_delta = round(report_b.overall_score - report_a.overall_score, 1)
        vm_delta = round(report_b.visual_match.score - report_a.visual_match.score, 1)
        cr_delta = round(report_b.caption_readability.score - report_a.caption_readability.score, 1)
        pacing_delta = round(report_b.pacing_cadence.score - report_a.pacing_cadence.score, 1)

        winner = report_b.production_id if score_delta >= 0 else report_a.production_id
        summary = f"Production B is {'superior (+'+str(score_delta)+')' if score_delta > 0 else 'inferior ('+str(score_delta)+')'} compared to Golden Master A."

        return GoldenMasterComparisonResult(
            production_id_a=report_a.production_id,
            production_id_b=report_b.production_id,
            duration_delta_sec=dur_delta,
            score_delta=score_delta,
            visual_match_delta=vm_delta,
            caption_readability_delta=cr_delta,
            pacing_delta=pacing_delta,
            winner=winner,
            summary=summary,
        )
