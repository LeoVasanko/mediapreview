"""Shared log formatting helpers."""

import logging
import unicodedata

LEVEL_EMOJI = {
    logging.DEBUG: "🔍",
    logging.INFO: "ℹ️",  # noqa: RUF001
    logging.WARNING: "⚠️",
    logging.ERROR: "🛑",
    logging.CRITICAL: "🛑",
}


def display_width(text: str) -> int:
    return sum(
        1 + (unicodedata.east_asian_width(c) in "FW")
        for c in text
        if unicodedata.category(c) != "Mn"
    )


def format_level_prefix(levelno: int) -> str:
    emoji = LEVEL_EMOJI.get(levelno, "▪️")
    prefix = f"{emoji} "
    return prefix + (" " * max(0, 3 - display_width(prefix)))


class EmojiFormatter(logging.Formatter):
    """Compact formatter: emoji + message, no timestamp/level text/logger name."""

    def format(self, record: logging.LogRecord) -> str:
        return format_level_prefix(record.levelno) + record.getMessage()
