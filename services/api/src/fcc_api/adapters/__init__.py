"""Adapter factories. Scanning and extraction default to noop."""

from fcc_api.adapters.cdr import NoopCdr, get_cdr
from fcc_api.adapters.extraction import ExtractionResult, ExtractedField, NoopExtractor, PageText, get_extractor
from fcc_api.adapters.forms import MissingTemplateFiller, get_form_filler
from fcc_api.adapters.llm import NoopLanguageModel, get_language_model
from fcc_api.adapters.scanning import NoopScanner, get_scanner
from fcc_api.adapters.screening import NoopScreener, ScreeningHit, get_screener
from fcc_api.adapters.submission import UnconfiguredSubmitter, get_submitter

__all__ = [
    "ExtractionResult",
    "ExtractedField",
    "MissingTemplateFiller",
    "NoopCdr",
    "NoopExtractor",
    "NoopLanguageModel",
    "NoopScanner",
    "NoopScreener",
    "PageText",
    "ScreeningHit",
    "UnconfiguredSubmitter",
    "get_cdr",
    "get_extractor",
    "get_form_filler",
    "get_language_model",
    "get_scanner",
    "get_screener",
    "get_submitter",
]
