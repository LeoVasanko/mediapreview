"""Media preview CLI.

Usage:
  mediapreview <path> [-o OUTPUT] [-q QUALITY] [--maxsize N] [--maxzoom Z]
  mediapreview oosetup [<name>] [<port>]
  mediapreview (-h | --help)

Generate an AVIF preview for a media file (one-shot, in-process), or set up
the bundled OnlyOffice container.

Arguments:
  <path>  media file to preview
  <name>  container name [default: onlyoffice-mediapreview]
  <port>  container host port [default: 8988]

Options:
  -o OUTPUT    output .avif file (default: write AVIF bytes to stdout)
  -q QUALITY   preview quality [default: 60]
  --maxsize N  max preview dimension [default: 512]
  --maxzoom Z  max zoom factor [default: 2.0]
  -h --help    show this help

oosetup prints ONLYOFFICE_JWT_SECRET=<token> on stdout (logs go to stderr);
persist the token wherever your deployment keeps its configuration.
"""

import logging
import sys
from pathlib import Path

from docopt import docopt

from mediapreview.backends import dispatch
from mediapreview.util.logformat import EmojiFormatter


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(EmojiFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # pyvips is chatty at INFO ("threadpool completed ..." per operation).
    logging.getLogger("pyvips").setLevel(logging.WARNING)


def _oosetup(name: str, port: int) -> None:
    try:
        # Lazy import: keeps the base CLI free of office-extra concerns.
        from mediapreview.office import setup_docker  # noqa: PLC0415
    except ImportError as e:
        sys.stderr.write(f"error: {e}\n(hint: pip install mediapreview[office])\n")
        sys.exit(1)
    try:
        # Logs go to stderr; stdout carries only the secret line below.
        secret = setup_docker(name=name, port=port)
    except Exception as e:
        sys.stderr.write(f"error: OnlyOffice setup failed: {e}\n")
        sys.exit(1)
    # The caller owns the secret from here on — persist it wherever the
    # deployment keeps its configuration.
    sys.stdout.write(f"ONLYOFFICE_JWT_SECRET={secret}\n")


def _preview(args: dict) -> None:
    path = Path(args["<path>"])
    if not path.is_file():
        sys.stderr.write(f"error: no such file: {path}\n")
        sys.exit(2)

    result, resp = dispatch(
        path,
        quality=int(args["-q"]),
        maxsize=int(args["--maxsize"]),
        maxzoom=float(args["--maxzoom"]),
    )
    if not resp.ok or result is None:
        sys.stderr.write(f"error: {resp.error or 'preview failed'}\n")
        if resp.stderr:
            sys.stderr.write(f"{resp.stderr}\n")
        sys.exit(1)

    if args["-o"] is not None:
        Path(args["-o"]).write_bytes(result)
        where = args["-o"]
    else:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
        where = "stdout"
    sys.stderr.write(
        f"{path.name} -> {where} ({len(result)} bytes,"
        f" backend={resp.backend or 'unknown'})\n"
    )


def main() -> None:
    _configure_logging()
    # docopt matches usage patterns in order, so `oosetup` would be swallowed
    # by the <path> pattern if it came second. Dispatch it before parsing;
    # the main help above still documents both modes.
    if sys.argv[1:2] == ["oosetup"]:
        args = docopt(
            "Usage:\n  mediapreview oosetup [<name>] [<port>]", argv=sys.argv[1:]
        )
        _oosetup(
            args["<name>"] or "onlyoffice-mediapreview",
            int(args["<port>"] or 8988),
        )
        return
    _preview(docopt(__doc__))


if __name__ == "__main__":
    main()
