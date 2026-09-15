"""Production Engine Factory — selects and instantiates production engine adapters."""
from __future__ import annotations

from typing import Dict, Type, Optional
from autopilot.core.contracts import ProductionEngineProtocol
from autopilot.providers.production.moneyprinter_adapter import MoneyPrinterTurboAdapter
from autopilot.providers.production.ffmpeg_adapter import FFmpegProductionAdapter

_ENGINE_REGISTRY: Dict[str, Type[ProductionEngineProtocol]] = {
    "moneyprinterturbo": MoneyPrinterTurboAdapter,
    "moneyprinter": MoneyPrinterTurboAdapter,
    "ffmpeg": FFmpegProductionAdapter,
    "native_ffmpeg": FFmpegProductionAdapter,
}


def register_production_engine(name: str, engine_cls: Type[ProductionEngineProtocol]):
    """Register a custom production engine adapter."""
    _ENGINE_REGISTRY[name.lower().strip()] = engine_cls


def get_production_engine(
    engine_name: Optional[str] = "moneyprinterturbo",
    profile: str = "vertical_short",
    **kwargs,
) -> ProductionEngineProtocol:
    """Instantiate the requested production engine adapter.
    
    Default is MoneyPrinterTurbo. If unconfigured or unavailable, MoneyPrinterTurboAdapter
    will fail-closed upon generation without silent fallback to FFmpeg.
    FFmpeg is only instantiated when explicitly requested.
    """
    key = (engine_name or "moneyprinterturbo").lower().strip()
    if key not in _ENGINE_REGISTRY:
        valid_keys = ", ".join(sorted(_ENGINE_REGISTRY.keys()))
        raise ValueError(f"Unknown production engine '{engine_name}'. Valid choices: {valid_keys}")

    engine_cls = _ENGINE_REGISTRY[key]
    if engine_cls is FFmpegProductionAdapter:
        return FFmpegProductionAdapter(profile=profile)
    return engine_cls(**kwargs)
