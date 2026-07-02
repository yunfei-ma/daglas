#!/usr/bin/env python3
"""Run the sitemap discovery tool against a benchmark set of Swedish news
sites and print a pass/fail table.

Usage:
    python tools/discover_sitemaps_verify.py
    python tools/discover_sitemaps_verify.py --verify
"""

from __future__ import annotations

import argparse
import sys

from tools.discover_sitemaps import (
    discover_sitemaps,
)

BENCHMARK_SITES = [
    "svt.se",
    "dn.se",
    "svd.se",
    "aftonbladet.se",
    "expressen.se",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify sitemap discovery against benchmark sites"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Also run LLM verification on each site",
    )
    parser.add_argument(
        "--llm-backend",
        default="",
        help="LLM backend: ollama, mlx, or llamacpp (default: ollama)",
    )
    args = parser.parse_args(argv)

    llm_provider = None
    if args.verify:
        try:
            import daglas.config as daglas_config

            if daglas_config.config is None:
                daglas_config.config = daglas_config.load_config()
            if args.llm_backend:
                daglas_config.config.llm_backend = args.llm_backend

            from daglas.lesson.llm import Llm

            llm_provider = Llm(data_dir=daglas_config.config.data_dir)
        except Exception as e:
            print(f"Warning: could not initialise LLM: {e}")

    print(f"{'Site':<20} {'Discovery':<12} {'Verification':<12} {'Articles Found'}")
    print("-" * 70)
    failures = 0

    for site in BENCHMARK_SITES:
        result = discover_sitemaps(
            site,
            verify=args.verify,
            llm_provider=llm_provider,
        )

        all_flat = result.found_via_robots + result.found_via_probing
        total_entries = sum(f.entry_count for f in all_flat)
        discovery_ok = len(all_flat) >= 1

        if args.verify and all_flat and llm_provider:
            best = max(all_flat, key=lambda f: f.entry_count)
            verification_ok = best.llm_verdict is not None and best.llm_verdict.suitable
        else:
            verification_ok = True

        discovery_str = "pass" if discovery_ok else "FAIL"
        verify_str = "pass" if verification_ok else "FAIL"
        if not args.verify:
            verify_str = "skipped"

        if not discovery_ok or not verification_ok:
            failures += 1

        print(f"{site:<20} {discovery_str:<12} {verify_str:<12} {total_entries}")
        for err in result.errors:
            print(f"  {'':<20} error: {err}")

    print("-" * 70)
    if failures:
        print(
            f"{len(BENCHMARK_SITES) - failures}/{len(BENCHMARK_SITES)} passed, {failures} failed"
        )
        return 1

    print(f"All {len(BENCHMARK_SITES)} sites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
