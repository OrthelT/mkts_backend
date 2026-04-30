import logging
import logging.handlers
from logging import StreamHandler
import sys
import os
from typing import Optional, Dict

try:
    import colorlog
    COLOR_AVAILABLE = True
except ImportError:
    COLOR_AVAILABLE = False

def _find_project_root(start_dir: str) -> str:
    cur = os.path.abspath(start_dir)
    for _ in range(6):
        if os.path.exists(os.path.join(cur, "pyproject.toml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(os.path.join(start_dir, "..", "..", "..", ".."))

def _resolve_log_level() -> int:
    try:
        from mkts_backend.config.settings_service import SettingsService
        level_name = SettingsService().log_level
    except (FileNotFoundError, KeyError):
        return logging.INFO
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        raise ValueError(f"Invalid log_level in settings: {level_name!r}")
    return level

log_level = _resolve_log_level()

def configure_logging(
    name: str,
    use_colors: bool = True,
    console_level: int = log_level,
    file_level: int = log_level,
    custom_colors: Optional[Dict[str, str]] = None,
):
    logger = logging.getLogger(name)
    logger.setLevel(min(console_level, file_level))

    logger.handlers.clear()

    default_colors = {
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }

    log_colors = custom_colors if custom_colors else default_colors

    file_formatter = logging.Formatter(
        "%(asctime)s|%(name)s|%(levelname)s|%(funcName)s:%(lineno)d > %(message)s"
    )

    if COLOR_AVAILABLE and use_colors and sys.stdout.isatty():
        console_formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s|%(name)s|%(levelname)s|%(funcName)s:%(lineno)d > %(message)s",
            datefmt=None,
            reset=True,
            log_colors=log_colors,
            secondary_log_colors={},
            style='%'
        )
    else:
        console_formatter = file_formatter

    # Ensure logs directory exists at project root (pyproject.toml locator)
    project_root = _find_project_root(os.path.dirname(__file__))
    log_dir = os.path.join(project_root, "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file_path = os.path.join(log_dir, "mkts-backend.log")
    rotating_handler = logging.handlers.RotatingFileHandler(
        log_file_path, maxBytes=1048576, backupCount=5
    )
    rotating_handler.setFormatter(file_formatter)
    rotating_handler.setLevel(file_level)
    logger.addHandler(rotating_handler)

    stream_handler = StreamHandler()
    stream_handler.setFormatter(console_formatter)
    stream_handler.setLevel(console_level)
    logger.addHandler(stream_handler)

    return logger

