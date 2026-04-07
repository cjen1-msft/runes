#!/usr/bin/env python3
"""Script to process multiple log files."""

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Optional
from queue import PriorityQueue


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes (colors, formatting, etc.) from text."""
    # Matches ANSI escape sequences: ESC[ ... m (colors/formatting)
    # and other common escape sequences
    ansi_pattern = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][AB012]")
    return ansi_pattern.sub("", text)


def extract_timestamp(line: str) -> Optional[datetime]:
    """
    Attempt to extract a timestamp from a log line.
    
    Tries several common timestamp formats and returns the first match.
    Returns None if no timestamp is found.
    """
    # Strip ANSI codes before processing
    line = strip_ansi_codes(line)
    
    patterns = [
        # ISO 8601: 2026-02-04T12:34:56.789Z or 2026-02-04T12:34:56
        (r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z)?)", "%Y-%m-%dT%H:%M:%S"),
        # Common log format: 04/Feb/2026:12:34:56
        (r"(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})", "%d/%b/%Y:%H:%M:%S"),
        # Syslog style: Feb  4 12:34:56
        (r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})", "%b %d %H:%M:%S"),
        # Date and time: 2026-02-04 12:34:56
        (r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", "%Y-%m-%d %H:%M:%S"),
        # US format: 02/04/2026 12:34:56
        (r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", "%m/%d/%Y %H:%M:%S"),
        # Raw time: 12:34:56
        (r"(\d{2}:\d{2}:\d{2})", "%H:%M:%S"),
    ]
    
    for pattern, fmt in patterns:
        match = re.search(pattern, line)
        if match:
            timestamp_str = match.group(1)
            # Handle ISO format with Z suffix and optional microseconds
            if "T" in timestamp_str:
                timestamp_str = timestamp_str.rstrip("Z")
                if "." in timestamp_str:
                    timestamp_str = timestamp_str.split(".")[0]
            # Handle variable whitespace in syslog format
            if fmt == "%b %d %H:%M:%S":
                timestamp_str = " ".join(timestamp_str.split())
            try:
                return datetime.strptime(timestamp_str, fmt)
            except ValueError:
                continue
    
    return None

class FileStream:
    def __init__(self, filename: str, id: str):
        self.filename = filename
        self.id = id

        self.file = open(filename, "r")
        self.current_line = ""
        self.last_timestamp = None
        self.eof = False

        self._advance()

    def _advance(self):
        if self.eof:
            return

        line = self.file.readline()
        if line == "":
            self.eof = True
            self.current_line = None
        else:
            self.current_line = line
            next_timestamp = extract_timestamp(line)
            # If no timestamp is found use the timestamp of the last line to maintain order
            if next_timestamp:
                self.last_timestamp = next_timestamp

def stream_aggregator(streams):
    pqueue = PriorityQueue()

    for stream in streams:
        if not stream.eof:
            pqueue.put((stream.last_timestamp or datetime.min, stream.id, stream))

    while not pqueue.empty():
        _, _, stream = pqueue.get()
        line = stream.current_line
        stream._advance()
        yield (stream.id, line)

        if not stream.eof:
            pqueue.put((stream.last_timestamp or datetime.min, stream.id, stream))


# ANSI background colors for distinguishing log sources
BG_COLORS = [
    "\x1b[41m",  # Red
    "\x1b[42m",  # Green
    "\x1b[43m",  # Yellow
    "\x1b[44m",  # Blue
    "\x1b[45m",  # Magenta
    "\x1b[46m",  # Cyan
    "\x1b[100m", # Bright Black
    "\x1b[101m", # Bright Red
    "\x1b[102m", # Bright Green
    "\x1b[103m", # Bright Yellow
    "\x1b[104m", # Bright Blue
    "\x1b[105m", # Bright Magenta
]
RESET = "\x1b[0m"


def print_aggregated_logs(filenames):
    streams = [FileStream(filename, i) for i, filename in enumerate(filenames)]
    for id, line in stream_aggregator(streams):
        bg_color = BG_COLORS[id % len(BG_COLORS)]
        print(f"{bg_color}{line.rstrip()}{RESET}")

def main():
    parser = argparse.ArgumentParser(description="Process multiple log files")
    parser.add_argument(
        "logs",
        nargs="+",
        help="Log files to process",
    )
    args = parser.parse_args()

    print_aggregated_logs(args.logs)

if __name__ == "__main__":
    main()

