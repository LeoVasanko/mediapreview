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


def quiet_vips_logging() -> None:
    """Silence libvips per-operation chatter without hiding deprecations.

    pyvips redirects every GLib message ("VIPS: threadpool completed ...")
    onto the ``pyvips`` logger at INFO; cap that logger at WARNING. pyvips's
    own diagnostics (e.g. deprecated-argument notices) are logged on the
    ``pyvips.voperation`` child logger and stay at the default INFO.

    Opt-in: applications that want quiet vips output call this once during
    their own logging setup. mediapreview never calls it on import.
    """
    logging.getLogger("pyvips").setLevel(logging.WARNING)
    logging.getLogger("pyvips.voperation").setLevel(logging.INFO)
