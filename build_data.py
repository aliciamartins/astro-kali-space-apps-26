"""Build a static JSON snapshot from NASA's PSI API for the GitHub Pages dashboard."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys
from typing import Any

from psi_api_client import BASE_URL, PSIAPIClient, build_repo_folder_path

DEFAULT_RESEARCH_AREA = "Combustion Science"
DETAIL_FIELDS = "accession,doi,researchArea,subResearchArea"
DETAIL_WORKERS = 10
FILE_WORKERS = 6


def _enrich_investigation_details(
    client: PSIAPIClient,
    investigation: dict[str, Any],
) -> dict[str, Any]:
    """Fetch repository details and merge doi, researchArea, and subResearchArea into metadata."""
    enriched = dict(investigation)
    meta = dict(investigation.get("metadata") or {})
    inv_id = investigation.get("accession_id")
    accession = str(investigation.get("accession") or f"PSI-{inv_id}")

    if inv_id is not None:
        try:
            details = client.get_investigation_details(inv_id, fields=DETAIL_FIELDS)
            for field in ("doi", "researchArea", "subResearchArea"):
                if field in details and details[field] is not None:
                    meta[field] = details[field]
        except (RuntimeError, TimeoutError, OSError) as exc:
            print(f"Warning: {accession} details fetch failed ({exc})", file=sys.stderr)

    enriched["metadata"] = meta
    return enriched


def _summarize_study_files(
    study_data: dict[str, Any],
    max_files: int,
) -> dict[str, Any]:
    """Summarize file counts, size, categories, and up to `max_files` file entries."""
    study_files = [item for item in (study_data.get("study_files") or ()) if isinstance(item, dict)]
    categories: Counter[str] = Counter()
    total_bytes = 0
    sample_files: list[dict[str, Any]] = []

    for index, file_item in enumerate(study_files):
        category = str(file_item.get("category") or "Uncategorized").strip() or "Uncategorized"
        categories[category] += 1
        total_bytes += int(float(file_item.get("file_size") or 0))

        if index < max_files:
            remote_url = html.unescape(str(file_item.get("remote_url") or "")).strip()
            download_url = (
                remote_url
                if remote_url.startswith(("http://", "https://"))
                else f"{BASE_URL}{remote_url}"
            )
            folder_path = build_repo_folder_path(file_item)
            sample_files.append(
                {
                    "file_name": file_item.get("file_name") or "",
                    "file_size": int(float(file_item.get("file_size") or 0)),
                    "category": category,
                    "folder": folder_path.as_posix() if str(folder_path) != "." else "",
                    "download_url": download_url,
                    "restricted": bool(file_item.get("restricted", False)),
                }
            )

    reported_count = int(study_data.get("file_count") or 0)
    return {
        "file_count": max(reported_count, len(study_files)),
        "total_bytes": total_bytes,
        "categories": dict(sorted(categories.items())),
        "sample_files": sample_files,
    }


def _fetch_study_summary(
    client: PSIAPIClient,
    investigation: dict[str, Any],
    max_files: int,
) -> tuple[str, dict[str, Any]]:
    """Fetch and summarize files for one investigation, falling back to video-only on API 500/timeout."""
    inv_id = investigation.get("accession_id")
    accession = str(investigation.get("accession") or f"PSI-{inv_id}")
    empty_summary: dict[str, Any] = {
        "file_count": 0,
        "total_bytes": 0,
        "categories": {},
        "sample_files": [],
    }
    if inv_id is None:
        return accession, empty_summary

    try:
        search_response = client.search_investigation_files(inv_id)
    except (RuntimeError, TimeoutError, OSError) as exc:
        try:
            search_response = client.search_investigation_files(inv_id, media_type="video")
        except (RuntimeError, TimeoutError, OSError):
            print(f"Warning: {accession} file search failed ({exc})", file=sys.stderr)
            empty_summary["error"] = "File index exceeds NASA API response limit — browse directly on psi.nasa.gov"
            return accession, empty_summary

    studies = search_response.get("studies") or {}
    study_data = studies.get(accession)
    if not isinstance(study_data, dict) and studies:
        study_data = next((v for v in studies.values() if isinstance(v, dict)), None)

    if isinstance(study_data, dict):
        return accession, _summarize_study_files(study_data, max_files=max_files)
    return accession, empty_summary


def build_dashboard_data(
    research_area: str | None = DEFAULT_RESEARCH_AREA,
    include_all: bool = False,
    max_files: int = 50,
) -> dict[str, Any]:
    """Fetch metadata, research areas, and file summaries from NASA PSI."""
    client = PSIAPIClient(timeout=20.0)
    all_investigations = client.get_investigations_metadata()

    enriched_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        future_to_idx = {
            executor.submit(_enrich_investigation_details, client, item): idx
            for idx, item in enumerate(all_investigations)
        }
        for future in as_completed(future_to_idx):
            enriched_by_index[future_to_idx[future]] = future.result()

    enriched_all = [enriched_by_index[idx] for idx in range(len(all_investigations))]

    if include_all or not research_area:
        selected = enriched_all
        active_research_area: str | None = None
    else:
        target_area = research_area.strip().lower()
        selected = [
            item
            for item in enriched_all
            if str((item.get("metadata") or {}).get("researchArea") or "").strip().lower() == target_area
        ]
        active_research_area = research_area.strip()

    files_by_accession: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=FILE_WORKERS) as executor:
        futures = [
            executor.submit(_fetch_study_summary, client, item, max_files)
            for item in selected
        ]
        for future in as_completed(futures):
            accession, summary = future.result()
            files_by_accession[accession] = summary

    ordered_files = {
        str(item.get("accession") or f"PSI-{item.get('accession_id')}"): files_by_accession.get(
            str(item.get("accession") or f"PSI-{item.get('accession_id')}"),
            {"file_count": 0, "total_bytes": 0, "categories": {}, "sample_files": []},
        )
        for item in selected
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_area": active_research_area,
        "total_public_investigations": len(all_investigations),
        "investigations": selected,
        "files_by_accession": ordered_files,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build static JSON snapshot for the NASA PSI dashboard.")
    parser.add_argument(
        "--research-area",
        default=DEFAULT_RESEARCH_AREA,
        help=f"Filter investigations by exact researchArea (default: {DEFAULT_RESEARCH_AREA!r})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_all",
        help="Include all public investigations without researchArea filtering",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=50,
        help="Maximum representative file entries stored per investigation (default: 50)",
    )
    parser.add_argument(
        "--out",
        default="data/dashboard_data.json",
        help="Output JSON file path (default: data/dashboard_data.json)",
    )
    args = parser.parse_args(argv)

    payload = build_dashboard_data(
        research_area=args.research_area,
        include_all=args.include_all,
        max_files=args.max_files,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)

    inv_count = len(payload["investigations"])
    total_files = sum(entry["file_count"] for entry in payload["files_by_accession"].values())
    print(f"Wrote {inv_count} investigations and {total_files} indexed files to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
