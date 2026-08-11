"""CLI entry point: delegates to the preview worker subprocess.

A richer CLI for one-shot preview generation and pool management can be
added later.
"""

from mediapreview.worker import main

if __name__ == "__main__":
    main()
