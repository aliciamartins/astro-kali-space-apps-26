"""Zero-dependency Python client and CLI for NASA's Physical Sciences Informatics (PSI) API."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
import html
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

BASE_URL = "https://psi.nasa.gov"
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "nasa-psi-python-client/1.0"


def _normalize_accession_number(investigation_id: int | str) -> str:
    """Return the numeric/decimal accession identifier without a 'PSI-' prefix."""
    value = str(investigation_id).strip()
    if value.upper().startswith("PSI-"):
        return value[4:]
    return value


def _normalize_investigation_ids(
    investigation_ids: str | int | Sequence[int | str],
) -> str:
    """Format single IDs, ranges, or sequences into a comma-separated API path segment."""
    if isinstance(investigation_ids, (str, int)):
        parts = [part.strip() for part in str(investigation_ids).split(",") if part.strip()]
    else:
        parts = [str(item).strip() for item in investigation_ids if str(item).strip()]

    if not parts:
        raise ValueError("investigation_ids must not be empty")

    normalized = [_normalize_accession_number(part) for part in parts]
    return ",".join(normalized)


def build_repo_folder_path(file_item: dict[str, Any]) -> Path:
    """Build the relative repository folder path: category / subcategory / subdirectory."""
    segments: list[str] = []
    for field in ("category", "subcategory", "subdirectory"):
        raw = str(file_item.get(field) or "").strip()
        if not raw:
            continue
        for part in raw.replace("\\", "/").split("/"):
            cleaned = part.strip()
            if cleaned and cleaned not in {".", ".."}:
                segments.append(cleaned)
    return Path(*segments) if segments else Path()


def iter_files(search_response: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (accession, file_item) pairs from a file search API response."""
    studies = search_response.get("studies") or {}
    for accession, study_data in studies.items():
        if not isinstance(study_data, dict):
            continue
        for file_item in study_data.get("study_files") or ():
            if isinstance(file_item, dict):
                yield accession, file_item


class PSIAPIClient:
    """Client for NASA's Physical Sciences Informatics (PSI) REST endpoints."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent

    def _open(self, url_or_path: str, accept: str = "*/*"):
        url = url_or_path if url_or_path.startswith(("http://", "https://")) else urljoin(f"{self.base_url}/", url_or_path.lstrip("/"))
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": accept})
        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404 and "<Code>NoSuchKey</Code>" in body:
                key_match = re.search(r"<Key>(.*?)</Key>", body)
                s3_key = key_match.group(1) if key_match else "unknown"
                raise RuntimeError(
                    f"File not available on NASA PSI storage (HTTP 404 NoSuchKey): "
                    f"API URL {url} redirected to missing S3 key {s3_key!r}."
                ) from exc
            raise RuntimeError(f"PSI API HTTP {exc.code} for {url}: {body[:300]}") from exc
        except URLError as exc:
            raise RuntimeError(f"PSI API request failed for {url}: {exc.reason}") from exc

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        with self._open(f"{path}{query}", accept="application/json") as response:
            return json.loads(response.read().decode("utf-8"))

    def get_investigations_metadata(
        self,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all public investigations, optionally filtering by title/objective keyword."""
        investigations: list[dict[str, Any]] = self._get_json("/geode-py/ws/api/investigations/metadata")
        if not keyword:
            return investigations

        needle = keyword.strip().lower()
        filtered: list[dict[str, Any]] = []
        for item in investigations:
            meta = item.get("metadata") or {}
            haystack = " ".join(
                str(meta.get(key) or "")
                for key in ("investigation_title", "investigation_objective")
            ).lower()
            if needle in haystack or needle in str(item.get("accession") or "").lower():
                filtered.append(item)
        return filtered

    def search_investigation_files(
        self,
        investigation_ids: str | int | Sequence[int | str],
        page: int | None = None,
        size: int = 25,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        """Search files in one or more investigations (e.g. 86, '4,10', or [4, 10]).

        Note: Although the PSI guide describes `page` as 0-indexed, the live API
        uses 1-based `page_number` and returns empty `studies` for `page=0`.
        Passing `page=None` omits the parameter (defaulting to page 1 on the server),
        and `page=0` is normalized to `1`.
        """
        if size < 1 or size > 25:
            raise ValueError("size must be between 1 and 25")

        ids_segment = _normalize_investigation_ids(investigation_ids)
        params: dict[str, Any] = {"size": size}
        if page is not None:
            if page < 0:
                raise ValueError("page must be >= 0")
            params["page"] = 1 if page == 0 else page
        if media_type:
            normalized_type = media_type.strip().lower()
            if normalized_type not in {"img", "video"}:
                raise ValueError("media_type must be 'img', 'video', or None")
            params["type"] = normalized_type

        return self._get_json(f"/geode-py/ws/api/investigations/files/search/{ids_segment}", params=params)

    def get_investigation_details(
        self,
        investigation_id: int | str,
        fields: str | Sequence[str] | None = "accession,doi,researchArea,subResearchArea",
    ) -> dict[str, Any]:
        """Fetch selected repository fields (e.g. accession, doi, researchArea) for one investigation."""
        accession_num = _normalize_accession_number(investigation_id)
        params: dict[str, Any] = {}
        if fields:
            if isinstance(fields, str):
                field_list = fields.strip()
            else:
                field_list = ",".join(str(f).strip() for f in fields if str(f).strip())
            if field_list:
                params["fields"] = field_list

        return self._get_json(f"/geode-py/ws/repo/investigations/PSI-{accession_num}", params=params)

    def download_file(
        self,
        investigation_id: int | str,
        file_name_or_item: str | dict[str, Any],
        dest_dir: str | Path = ".",
        preserve_folders: bool = False,
    ) -> Path:
        """Stream-download a PSI file from a file search item dict or raw file name."""
        accession_num = _normalize_accession_number(investigation_id)
        base_dest = Path(dest_dir)

        if isinstance(file_name_or_item, dict):
            file_name = str(file_name_or_item.get("file_name") or "").strip()
            if not file_name:
                raise ValueError("file_name_or_item dict must contain a non-empty 'file_name'")
            remote_url = file_name_or_item.get("remote_url")
            download_target = (
                html.unescape(str(remote_url))
                if remote_url
                else f"/geode-py/ws/api/investigations/PSI-{accession_num}/download?{urlencode({'source': 'datamanager', 'file': file_name})}"
            )
            if preserve_folders:
                base_dest = base_dest / build_repo_folder_path(file_name_or_item)
        else:
            file_name = str(file_name_or_item).strip()
            if not file_name:
                raise ValueError("file_name_or_item must not be empty")
            download_target = (
                f"/geode-py/ws/api/investigations/PSI-{accession_num}/download?"
                f"{urlencode({'source': 'datamanager', 'file': file_name})}"
            )

        safe_filename = Path(file_name).name
        if not safe_filename:
            raise ValueError(f"Could not determine a valid filename from {file_name!r}")

        base_dest.mkdir(parents=True, exist_ok=True)
        output_path = base_dest / safe_filename

        with self._open(download_target) as response, output_path.open("wb") as out_file:
            shutil.copyfileobj(response, out_file)

        return output_path


def _run_demo(client: PSIAPIClient, keyword: str = "combustion") -> None:
    """Demonstrate all four PSI API endpoints using live public data."""
    print(f"1) Fetching investigations matching {keyword!r}...")
    matches = client.get_investigations_metadata(keyword=keyword)
    print(f"   Found {len(matches)} matching investigations.")
    for item in matches[:5]:
        title = (item.get("metadata") or {}).get("investigation_title", "N/A")
        print(f"   - {item.get('accession')}: {title}")

    if not matches:
        return

    sample_id = matches[0]["accession_id"]
    print(f"\n2) Fetching repository details for PSI-{sample_id}...")
    details = client.get_investigation_details(sample_id, fields="accession,doi")
    print(f"   {json.dumps(details, indent=2)}")

    print(f"\n3) Searching files for PSI-{sample_id} (images sample)...")
    img_search = client.search_investigation_files(sample_id, media_type="img")
    img_files = list(iter_files(img_search))
    print(f"   Matched {len(img_files)} image files (hits={img_search.get('hits')}).")

    print(f"\n4) Searching all files for PSI-{sample_id}...")
    all_search = client.search_investigation_files(sample_id)
    all_files = list(iter_files(all_search))
    print(f"   Matched {len(all_files)} total files.")
    for accession, file_item in all_files[:5]:
        folder = build_repo_folder_path(file_item)
        print(
            f"   - [{accession}] {file_item.get('file_name')} "
            f"({file_item.get('file_size')} bytes) -> {folder}"
        )

    if all_files:
        smallest_accession, smallest_item = min(
            all_files,
            key=lambda pair: float(pair[1].get("file_size") or float("inf")),
        )
        saved_to = client.download_file(
            smallest_accession,
            smallest_item,
            dest_dir=Path("psi_downloads") / smallest_accession,
            preserve_folders=True,
        )
        print(f"\n5) Downloaded smallest file to: {saved_to}")


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(description="Access NASA's Physical Sciences Informatics (PSI) API.")
    subparsers = parser.add_subparsers(dest="command")

    demo_cmd = subparsers.add_parser("demo", help="Run an end-to-end demo across all 4 endpoints")
    demo_cmd.add_argument("--keyword", default="combustion", help="Keyword filter for metadata demo")

    meta_cmd = subparsers.add_parser("metadata", help="List investigations metadata")
    meta_cmd.add_argument("--keyword", help="Filter by title, objective, or accession")
    meta_cmd.add_argument("--limit", type=int, default=20, help="Max investigations to print")

    search_cmd = subparsers.add_parser("search", help="Search files in one or more investigations")
    search_cmd.add_argument("ids", help="Comma-separated IDs or ranges (e.g. 86 or 4,10)")
    search_cmd.add_argument("--page", type=int, default=None, help="Page number (1-indexed on live API)")
    search_cmd.add_argument("--size", type=int, default=25, help="Results per page (max 25)")
    search_cmd.add_argument("--type", dest="media_type", choices=["img", "video"], help="Media filter")
    search_cmd.add_argument("--limit", type=int, default=20, help="Max file entries to print")

    details_cmd = subparsers.add_parser("details", help="Get investigation details by accession")
    details_cmd.add_argument("id", help="Investigation accession (e.g. 117 or PSI-117)")
    details_cmd.add_argument(
        "--fields",
        default="accession,doi,researchArea,subResearchArea",
        help="Comma-separated fields",
    )

    dl_cmd = subparsers.add_parser("download", help="Download a file from an investigation")
    dl_cmd.add_argument("id", help="Investigation accession (e.g. 174 or PSI-174)")
    dl_cmd.add_argument("file_name", help="Exact file_name from file search")
    dl_cmd.add_argument("--out", default="psi_downloads", help="Destination directory")

    command_parsers: dict[str, argparse.ArgumentParser] = {
        "demo": demo_cmd,
        "metadata": meta_cmd,
        "search": search_cmd,
        "details": details_cmd,
        "download": dl_cmd,
    }

    return parser, command_parsers


def main(argv: Sequence[str] | None = None) -> int:
    parser, command_parsers = _build_parser()
    args = parser.parse_args(argv)

    client = PSIAPIClient()

    try:
        if args.command in (None, "demo"):
            _run_demo(client, keyword=getattr(args, "keyword", "combustion"))
            return 0

        if args.command == "metadata":
            items = client.get_investigations_metadata(keyword=args.keyword)
            print(json.dumps(items[: args.limit], indent=2))
            return 0

        if args.command == "search":
            result = client.search_investigation_files(
                args.ids,
                page=args.page,
                size=args.size,
                media_type=args.media_type,
            )
            files = [{"accession": acc, **item} for acc, item in iter_files(result)]
            print(json.dumps({"hits": result.get("hits"), "total_hits": result.get("total_hits"), "files": files[: args.limit]}, indent=2))
            return 0

        if args.command == "details":
            details = client.get_investigation_details(args.id, fields=args.fields)
            print(json.dumps(details, indent=2))
            return 0

        if args.command == "download":
            saved = client.download_file(args.id, args.file_name, dest_dir=args.out)
            print(f"Downloaded to {saved}")
            return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
