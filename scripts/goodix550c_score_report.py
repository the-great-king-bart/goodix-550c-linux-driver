#!/usr/bin/env python3
"""Summarise SIGFM match scores from a guarded fprintd session log.

fprintd reports only match or no-match, which is not enough to choose a
threshold: it hides how close a genuine attempt came to failing and how close an
impostor came to passing. The driver logs the score for every stored sample, so
this reads them back and reports the two distributions side by side.

Presentations are labelled from a plain-text file the operator writes as they
go, one line per attempt, so an attempt can be attributed without the log ever
containing anything about whose finger it was:

    genuine   right index, flat
    genuine   right index, rolled left
    impostor  left thumb

Attempts and labels are paired in order. Unlabelled attempts are reported
separately rather than guessed at.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

BEST_RE = re.compile(r"(?:Verify|Identify) best SIGFM score:\s*(\d+)\s*\(best_min:\s*(\d+)\)")
SAMPLE_RE = re.compile(r"verify: sample (\d+) sigfm_score (\d+)")


class Attempt:
    def __init__(self, best: int, threshold: int, samples: list[int]) -> None:
        self.best = best
        self.threshold = threshold
        self.samples = samples

    @property
    def accepted(self) -> bool:
        return self.best >= self.threshold


def parse_log(text: str) -> list[Attempt]:
    """Collect one Attempt per reported score, with the samples preceding it."""
    attempts: list[Attempt] = []
    pending: list[int] = []

    for line in text.splitlines():
        sample = SAMPLE_RE.search(line)
        if sample is not None:
            pending.append(int(sample.group(2)))
            continue

        best = BEST_RE.search(line)
        if best is not None:
            attempts.append(Attempt(int(best.group(1)), int(best.group(2)), pending))
            pending = []

    return attempts


def read_labels(path: Path | None) -> list[str]:
    if path is None:
        return []
    labels = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        labels.append(line.split(None, 1)[0].lower())
    return labels


def describe(name: str, scores: list[int], threshold: int) -> list[str]:
    if not scores:
        return [f"{name}: no attempts"]

    ordered = sorted(scores)
    lines = [
        f"{name}: {len(scores)} attempts",
        f"  min {ordered[0]}  median {int(statistics.median(ordered))}  max {ordered[-1]}",
    ]
    if name == "genuine":
        failed = [s for s in scores if s < threshold]
        lines.append(f"  below threshold ({threshold}): {len(failed)} -> false rejects")
        weakest = min((s for s in scores if s >= threshold), default=0)
        lines.append(f"  margin of the weakest accepted: {weakest}")
    else:
        passed = [s for s in scores if s >= threshold]
        lines.append(f"  at or above threshold ({threshold}): {len(passed)} -> FALSE ACCEPTS")
        lines.append(f"  closest impostor came to the threshold: {ordered[-1]}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument(
        "--labels",
        type=Path,
        help="one label per attempt in order: 'genuine' or 'impostor'",
    )
    args = parser.parse_args(argv)

    attempts = parse_log(args.log.read_text(encoding="utf-8", errors="replace"))
    if not attempts:
        print("No match scores in this log.", file=sys.stderr)
        return 1

    threshold = attempts[0].threshold
    labels = read_labels(args.labels)

    groups: dict[str, list[int]] = {"genuine": [], "impostor": [], "unlabelled": []}
    for index, attempt in enumerate(attempts):
        label = labels[index] if index < len(labels) else "unlabelled"
        if label not in groups:
            label = "unlabelled"
        groups[label].append(attempt.best)

    print(f"{len(attempts)} attempts, threshold {threshold}\n")
    for name in ("genuine", "impostor"):
        for line in describe(name, groups[name], threshold):
            print(line)
        print()

    if groups["unlabelled"]:
        print(f"unlabelled: {len(groups['unlabelled'])} attempts -> {groups['unlabelled']}")
        print("Label them before drawing any conclusion about the threshold.\n")

    genuine, impostor = groups["genuine"], groups["impostor"]
    if genuine and impostor:
        separation = min(genuine) - max(impostor)
        print(f"Separation between weakest genuine and strongest impostor: {separation}")
        if separation <= 0:
            print("The two distributions overlap. No threshold separates them cleanly.")
    elif not impostor:
        print("No impostor attempts recorded, so this says nothing about false accepts.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
