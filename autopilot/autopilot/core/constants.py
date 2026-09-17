"""Cross-module constants for safety boundaries.

``LEARNING_FORBIDDEN_MODULES`` is the authoritative list of modules that the
analytics-learning engine must NEVER import or call.  Learning influences
*which ideas get preference*; it must never touch production, publishing,
provider authentication, or safety policy.  A dedicated test asserts that
``autopilot.core.learning`` (and its transitive imports) stays well clear of
this list.
"""

LEARNING_FORBIDDEN_MODULES = (
    # Production machinery
    "autopilot.core.worker",
    "autopilot.core.pipeline",
    "autopilot.core.renderer",
    "autopilot.core.ffmpeg_runner",
    "autopilot.core.asset_pipeline",
    "autopilot.core.asset_cache",
    "autopilot.core.qa_engine",
    "autopilot.core.batch",
    # Publishing machinery
    "autopilot.core.publisher",
    "autopilot.providers.youtube_publisher",
    "autopilot.providers.postiz_publisher",
    "autopilot.providers.mock_publisher",
    # Provider authentication / network providers (learning consumes stored facts only)
    "autopilot.providers.youtube_oauth",
    "autopilot.providers.youtube_analytics",
    "autopilot.providers.mock_analytics",
    "autopilot.providers.openai_llm_provider",
    "autopilot.providers.local_llm_adapter",
    "autopilot.providers.kokoro_tts_provider",
    "autopilot.providers.sapi_tts_provider",
    "autopilot.providers.crawl4ai_provider",
    "autopilot.providers.wikipedia_provider",
    "autopilot.providers.openverse_provider",
    "autopilot.providers.production",
)
