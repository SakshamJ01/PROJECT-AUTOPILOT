"""Production Engine Adapters — MoneyPrinterTurbo and native FFmpeg implementations."""
from autopilot.core.contracts import (
    ProductionEngineProtocol,
    ProductionRequest,
    ProductionResult,
    ProductionEngineType,
)
from autopilot.providers.production.moneyprinter_adapter import MoneyPrinterTurboAdapter
from autopilot.providers.production.ffmpeg_adapter import FFmpegProductionAdapter
from autopilot.providers.production.factory import get_production_engine, register_production_engine

__all__ = [
    "ProductionEngineProtocol",
    "ProductionRequest",
    "ProductionResult",
    "ProductionEngineType",
    "MoneyPrinterTurboAdapter",
    "FFmpegProductionAdapter",
    "get_production_engine",
    "register_production_engine",
]
