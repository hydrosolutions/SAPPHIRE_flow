from __future__ import annotations

import logging
import os

import structlog


def _shared_processors() -> list[structlog.types.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def _apply_structlog_config(
    processors: list[structlog.types.Processor], config_level: str
) -> None:
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if os.environ.get("SAPPHIRE_ENV") == "dev"
        else structlog.processors.JSONRenderer()
    )

    wrap = structlog.stdlib.ProcessorFormatter.wrap_for_formatter
    structlog.configure(
        processors=[*processors, wrap],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(getattr(logging, config_level.upper(), logging.INFO))

    for key, val in os.environ.items():
        if key.startswith("SAPPHIRE_LOG_"):
            module = key[len("SAPPHIRE_LOG_") :].lower().replace("_", ".")
            module_logger = logging.getLogger(f"sapphire_flow.{module}")
            module_logger.setLevel(getattr(logging, val.upper(), logging.INFO))


def _add_prefect_context(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    from prefect.runtime import flow_run, task_run

    if (frid := flow_run.id) is not None:
        event_dict.setdefault("flow_run_id", str(frid))
    if (fname := flow_run.flow_name) is not None:
        event_dict.setdefault("flow_name", fname)
    if (tname := task_run.task_name) is not None:
        event_dict.setdefault("task_name", tname)
    return event_dict


def configure_prefect_logging(config_level: str = "INFO") -> None:
    shared = _shared_processors()
    processors = [shared[0], _add_prefect_context, *shared[1:]]
    _apply_structlog_config(processors, config_level)


def configure_api_logging(config_level: str = "INFO") -> None:
    _apply_structlog_config(_shared_processors(), config_level)


def configure_test_logging() -> None:
    processors = _shared_processors()
    wrap = structlog.stdlib.ProcessorFormatter.wrap_for_formatter

    structlog.configure(
        processors=[*processors, wrap],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
