"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .usb_probe import (
    QUERY_SPECS,
    packet_preview,
    read_dpapi_backup,
    run_probe,
    run_read_only_query,
    write_private_blob,
    write_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goodix550c", description="Goodix 27c6:550c reverse-engineering lab"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("packets", help="show allow-listed requests without opening USB")

    probe = subparsers.add_parser("probe", help="read the sensor firmware identity")
    probe.add_argument(
        "--i-understand-this-sends-two-read-only-requests",
        action="store_true",
        help="required safety gate for live USB access",
    )
    probe.add_argument("--output", type=Path, help="optional JSON transcript path")

    identify = subparsers.add_parser("identify", help="run one isolated read-only query")
    identify.add_argument("field", choices=sorted(QUERY_SPECS))
    identify.add_argument(
        "--i-understand-this-sends-two-read-only-requests",
        action="store_true",
        help="required safety gate for live USB access",
    )
    identify.add_argument("--output", type=Path, help="optional JSON transcript path")

    backup = subparsers.add_parser(
        "read-dpapi-backup",
        help="read the inert Windows-sealed PSK backup into ignored secret storage",
    )
    backup.add_argument(
        "--i-understand-this-sends-two-read-only-requests",
        action="store_true",
        help="required safety gate for live USB access",
    )
    backup.add_argument(
        "--i-understand-this-reads-a-machine-sealed-secret",
        action="store_true",
        help="required acknowledgement for the encrypted DPAPI blob",
    )
    backup.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new file below research/secrets/ (must not already exist)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "packets":
        print(json.dumps(packet_preview(), indent=2, sort_keys=True))
        return 0

    if not args.i_understand_this_sends_two_read_only_requests:
        print(
            "refusing live access: the explicit read-only probe flag is required",
            file=sys.stderr,
        )
        return 2
    if (
        args.command == "read-dpapi-backup"
        and not args.i_understand_this_reads_a_machine_sealed_secret
    ):
        print("refusing secret read: the machine-sealed-secret flag is required", file=sys.stderr)
        return 2

    try:
        if args.command == "read-dpapi-backup":
            blob, metadata = read_dpapi_backup()
            written = write_private_blob(blob, args.output)
            metadata["output"] = str(written.relative_to(Path.cwd().resolve()))
            print(json.dumps(metadata, indent=2, sort_keys=True))
            return 0
        if args.command == "identify":
            query_result = run_read_only_query(args.field)
            rendered = json.dumps(query_result, indent=2, sort_keys=True) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            print(rendered, end="")
            return 0
        result = run_probe()
    except Exception as exc:  # CLI boundary: preserve a concise hardware error
        print(f"probe failed: {exc}", file=sys.stderr)
        return 1

    if args.output:
        write_result(result, args.output)
    print(result.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
