"""Local LLM adapter — Phase 3.
Uses existing LLMProvider protocol; connects to local OpenAI-compatible endpoint if available.
Does not crash when unavailable; reports optional status.
No model download required.
"""
from __future__ import annotations
from autopilot.providers.contracts import LLMProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ScriptDocument


class LocalLLMProvider(LLMProvider):
    provider_name = "local_llm"
    capability = CapabilityMetadata(
        max_resolution="1080p", supports_9_16=True, local_only=True,
        license_note="Local inference adapter — requires separate model server (Ollama/llama.cpp) not bundled",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        # Check if local endpoint is reachable (optional)
        try:
            import urllib.request
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as resp:
                return ProviderHealth(healthy=True, provider_name=self.provider_name, details={"endpoint": "ollama_local"})
        except Exception:
            return ProviderHealth(healthy=False, provider_name=self.provider_name, error="Local LLM endpoint not available (optional)", details={"note": "Install Ollama/llama.cpp locally to enable"})

    def generate_script(self, topic: str, **kwargs) -> ScriptDocument:
        # If enabled and endpoint available, could call; for Phase 3 we return a clear failure with explanation
        # rather than inventing fake output. This ensures the pipeline is explicit.
        raise ValueError(f"Local LLM adapter requires a local model server (Ollama/llama.cpp). Topic: {topic}. Use MockScriptProvider for deterministic Phase 3 testing.")
