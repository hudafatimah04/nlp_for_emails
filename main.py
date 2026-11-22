"""Command-line entry point for the opportunity inbox reader."""

import argparse
import json
import sys
from collections.abc import Sequence

from config import settings
from gmail_integration.authentication import get_gmail_service
from gmail_integration.email_parser import parse_message
from gmail_integration.email_reader import get_message, list_message_ids


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch inbox messages and print one JSON summary per message."""
    parser = argparse.ArgumentParser(description="Read the configured Gmail inbox.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch recent inbox messages.")
    fetch.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    fetch.add_argument("--show-body", action="store_true", help="Print up to 500 body characters.")
    args = parser.parse_args(argv)
    if args.max < 1:
        parser.error("--max must be a positive integer")

    try:
        service = get_gmail_service()
        for message_id in list_message_ids(service, settings.ALLOWED_SENDERS, args.max):
            summary = parse_message(get_message(service, message_id))
            body = summary.pop("body_text") or ""
            summary["body_character_count"] = len(body)
            if args.show_body:
                summary["body_preview"] = body[:500]
            print(json.dumps(summary))
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
