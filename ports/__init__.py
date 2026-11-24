"""
Layer: Ports — Protocol interfaces only.
Adapters implement these; the application layer depends only on these contracts.
"""
from .interpreter import ErrorInterpreter
from .ocr import OcrEngine
from .reader import TableReader
from .renderer import PageRenderer

__all__ = ["ErrorInterpreter", "OcrEngine", "TableReader", "PageRenderer"]
