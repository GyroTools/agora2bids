"""Command line interface: ``agora2bids <exam_id> --output <bids_root>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .pipeline import run
from .report import summary


def load_dotenv(path: Path) -> None:
    """Fill missing environment variables from a ``.env`` file (real environment variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agora2bids", description="Convert an Agora exam (study) into a BIDS dataset.")
    p.add_argument("exam_id", type=int, help="Agora exam (study) id")
    p.add_argument("-o", "--output", type=Path, required=True, help="BIDS dataset root (created or extended)")
    p.add_argument("--url", default=None, help="Agora URL (default: $AGORA_URL)")
    p.add_argument("--api-key", default=None, help="Agora API key (default: $AGORA_API_KEY)")
    p.add_argument("--dry-run", action="store_true", help="classify from the parameters only; download and write nothing")
    p.add_argument("--keep-temp", action="store_true", help="keep the temporary download/conversion directory")
    p.add_argument("--temp-dir", type=Path, default=None, help="parent directory for temporary files")
    p.add_argument("--task-label", default="unknown", help="BIDS task label for functional series (default: unknown)")
    p.add_argument("--dataset-name", default="Agora export", help="Name in dataset_description.json (new datasets only)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(Path(".env"))
    url = args.url or os.environ.get("AGORA_URL")
    api_key = args.api_key or os.environ.get("AGORA_API_KEY")
    if not url or not api_key:
        print("error: set AGORA_URL and AGORA_API_KEY (environment, .env, or --url/--api-key)", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    from .agora_source import AgoraSource

    try:
        source = AgoraSource(url, api_key)
        result = run(
            args.exam_id, args.output, source, dry_run=args.dry_run, keep_temp=args.keep_temp,
            task_label=args.task_label, dataset_name=args.dataset_name, temp_root=args.temp_dir,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(summary(result.entries))
    if result.session_dir:
        print(f"BIDS session written to {result.session_dir}")
    if result.report:
        print(f"report: {result.report}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
