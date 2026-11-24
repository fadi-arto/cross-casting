#!/usr/bin/env python3
"""CLI entry point for Cross-Casting audit."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"

_MIME_BY_EXT = {
    ".pdf": PDF, ".png": PNG, ".jpg": JPEG, ".jpeg": JPEG,
    ".xlsx": XLSX, ".xls": XLSX,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-Casting financial audit")
    parser.add_argument("file", help="PDF, image, or XLSX file to audit")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output JSON file (default: print to stdout)",
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.01,
        help="Absolute tolerance for casting checks (default: 0.01)",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_bytes()
    mime = _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")

    # Import here so settings are loaded after argparse (allows .env override)
    from decimal import Decimal
    from application.factory import build_pipeline
    from adapters.excel_reader import read_excel
    from config.settings import Settings, AppSettings, VlmSettings, AnthropicSettings, OcrSettings, LlmSettings
    from schemas.api import report_to_out

    settings = Settings(
        app=AppSettings(abs_tolerance=Decimal(str(args.tolerance))),
        vlm=VlmSettings(),
        anthropic=AnthropicSettings(),
        ocr=OcrSettings(),
        llm=LlmSettings(),
    )
    pipeline = build_pipeline(settings)

    if mime in (XLSX,):
        tables = read_excel(content, source_file=str(path))
        report = pipeline.process_tables(tables, source_file=str(path))
    elif mime == PDF:
        report = pipeline.process_pdf(content, source_file=str(path))
    elif mime in (PNG, JPEG):
        report = pipeline.process_image(content, mime, source_file=str(path))
    else:
        print(f"Error: unsupported file type {path.suffix!r}", file=sys.stderr)
        sys.exit(1)

    out = report_to_out(report)
    result_json = out.model_dump_json(indent=2)

    if args.output:
        Path(args.output).write_text(result_json, encoding="utf-8")
        print(f"Report saved to: {args.output}")
    else:
        print(result_json)

    # Exit 1 if errors found
    if not report.is_clean:
        print(
            f"\n⚠  {report.total_casting_errors} casting + "
            f"{report.total_cross_casting_errors} cross-casting errors found.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("\n✓ No arithmetic errors found.", file=sys.stderr)


if __name__ == "__main__":
    main()
