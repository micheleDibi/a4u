from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import Settings

# WeasyPrint, generando i PDF, parsa gli SVG dei diagrammi Mermaid: Mermaid
# mette `fill`/`stroke`/`stroke-width`/`stroke-dasharray`/... negli attributi
# `style="..."` degli elementi SVG, e il validatore CSS di WeasyPrint li
# segnala come "unknown property" (sono proprieta' SVG, non CSS HTML). Sono
# centinaia di warning non azionabili per ogni PDF — il renderer SVG applica
# comunque quegli stili. Li filtriamo, MANTENENDO gli altri warning di
# WeasyPrint (font mancanti, immagini rotte, ecc.).
_WEASYPRINT_SVG_NOISE_RE = re.compile(
    r"Ignored `(?:fill|stroke|stop-color|stop-opacity|paint-order|"
    r"shape-rendering|text-anchor|dominant-baseline|marker-|color-interpolation)"
    r"[^`]*`.*unknown property",
    re.IGNORECASE,
)


class _WeasyPrintSvgNoiseFilter(logging.Filter):
    """Scarta i soli warning 'unknown property' SVG di WeasyPrint."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("weasyprint"):
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        return not _WEASYPRINT_SVG_NOISE_RE.search(message)

# Context-var iniettato dal middleware request_id e dalle dipendenze auth.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)


def _add_context_vars(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    rid = request_id_ctx.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    uid = user_id_ctx.get()
    if uid:
        event_dict.setdefault("user_id", uid)
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configura logging stdlib + structlog. Da chiamare all'avvio."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_context_vars,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    handler.addFilter(_WeasyPrintSvgNoiseFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(noisy).handlers = [handler]
        logging.getLogger(noisy).propagate = False
        logging.getLogger(noisy).setLevel("INFO" if noisy.startswith("uvicorn") else "WARNING")


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
