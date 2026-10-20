"""Copy the page and PDF lists from url_lists/*.csv into config.py (spec 03).

Offline tool: it never visits the website. `--check` only validates the CSV
files. `--write` validates, then rewrites the block between the two marker
lines in config.py and changes nothing outside the markers.
"""
import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

if __package__ in (None, ""):  # run as `python tools/build_url_lists.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LIST_DIR = ROOT / "url_lists"
CONFIG_PATH = ROOT / "config.py"

BEGIN_MARKER = "# BEGIN URL LISTS"
END_MARKER = "# END URL LISTS"
SITE = "https://uoli.edu.pk"

PAGE_COLUMNS = ("address", "category", "keep", "note")
PDF_COLUMNS = ("address", "category", "label", "keep", "note")

Row = Dict[str, str]


class UrlListError(Exception):
    """A CSV or config.py problem. The message names the file, row and value."""


def read_rows(path: Path, columns: Tuple[str, ...]) -> List[Row]:
    """Rows of one CSV; stops with the missing column named."""
    # utf-8-sig drops the BOM that Excel adds when it saves a CSV.
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise UrlListError(f"{path.name}: missing column(s): {', '.join(missing)}")
        return [
            {c: (row.get(c) or "").strip() for c in columns} for row in reader
        ]


def _address_problem(address: str) -> str:
    if not address.startswith(SITE):
        return f"address does not start with {SITE}"
    if "?" in address or "#" in address:
        return "address has ? or #"
    if address.endswith("/"):
        return "address ends with /"
    return ""


def _row_problems(row: Row, is_pdf: bool) -> List[str]:
    problems = []
    address_problem = _address_problem(row["address"])
    if address_problem:
        problems.append(address_problem)
    if row["category"] not in config.CATEGORIES:
        problems.append(f"category '{row['category']}' is not one of {config.CATEGORIES}")
    if row["keep"] not in ("yes", "no"):
        problems.append(f"keep '{row['keep']}' must be yes or no")
    if is_pdf and row["keep"] == "yes" and not row["label"]:
        problems.append("kept PDF has no label")
    return problems


def check_rows(rows: List[Row], name: str, is_pdf: bool) -> List[str]:
    """Every problem found in one file, as readable lines."""
    errors: List[str] = []
    first_seen: Dict[str, int] = {}
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        for problem in _row_problems(row, is_pdf):
            errors.append(f"{name} row {number}: {problem}: {row['address']}")
        # Compared by path so '/x' and '/x/' count as the same address.
        key = config._url_path(row["address"])
        if key in first_seen:
            errors.append(
                f"{name} row {number}: same address as row {first_seen[key]}: {row['address']}"
            )
        else:
            first_seen[key] = number
    return errors


def load_lists(list_dir: Path = LIST_DIR) -> Tuple[List[Row], List[Row]]:
    """Both files, validated. Raises UrlListError listing every problem."""
    pages = read_rows(list_dir / "pages.csv", PAGE_COLUMNS)
    pdfs = read_rows(list_dir / "pdfs.csv", PDF_COLUMNS)
    errors = check_rows(pages, "pages.csv", False) + check_rows(pdfs, "pdfs.csv", True)
    if errors:
        raise UrlListError("\n".join(errors))
    return pages, pdfs


def render_block(pages: List[Row], pdfs: List[Row]) -> str:
    """The text that goes between the markers (kept rows only)."""
    lines = [
        "# Made by tools/build_url_lists.py from url_lists/pages.csv and pdfs.csv.",
        "# Do not edit by hand: change the CSV and run the tool again.",
        "UNIVERSITY_PAGES = [",
    ]
    for row in pages:
        if row["keep"] == "yes":
            lines.append(f'    {{"url": {row["address"]!r}, "category": {row["category"]!r}}},')
    lines += ["]", "", "PDF_SOURCES = ["]
    for row in pdfs:
        if row["keep"] == "yes":
            lines.append(
                f'    {{"url": {row["address"]!r}, "category": {row["category"]!r}, '
                f'"label": {row["label"]!r}}},'
            )
    lines.append("]")
    return "\n".join(lines)


def replace_block(source: str, block: str) -> str:
    """`source` with the marker block replaced; stops if markers are not exactly one pair."""
    if source.count(BEGIN_MARKER) != 1 or source.count(END_MARKER) != 1:
        raise UrlListError(
            f"config.py must contain exactly one '{BEGIN_MARKER}' and one '{END_MARKER}' line"
        )
    start = source.index(BEGIN_MARKER)
    end = source.index(END_MARKER)
    if end < start:
        raise UrlListError(f"'{END_MARKER}' comes before '{BEGIN_MARKER}' in config.py")
    head = source[: start + len(BEGIN_MARKER)]
    newline = "\r\n" if "\r\n" in source else "\n"
    return head + newline + block.replace("\n", newline) + newline + source[end:]


def write_config(pages: List[Row], pdfs: List[Row], path: Path = CONFIG_PATH) -> bool:
    """Rewrite the block in config.py. True if the file changed."""
    # newline="" keeps the file's own line endings untouched.
    with open(path, newline="", encoding="utf-8") as handle:
        old = handle.read()
    new = replace_block(old, render_block(pages, pdfs))
    if new == old:
        return False
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(new)
    return True


def _print_counts(pages: List[Row], pdfs: List[Row]) -> None:
    kept_pages = [r for r in pages if r["keep"] == "yes"]
    kept_pdfs = [r for r in pdfs if r["keep"] == "yes"]
    print(f"pages.csv: {len(pages)} rows, {len(kept_pages)} kept")
    print(f"pdfs.csv : {len(pdfs)} rows, {len(kept_pdfs)} kept")
    for category in config.CATEGORIES:
        count = sum(1 for r in kept_pages if r["category"] == category)
        print(f"  pages in {category}: {count}")


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Copy url_lists/*.csv into config.py.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate the CSV files only")
    mode.add_argument("--write", action="store_true", help="validate, then rewrite config.py")
    args = parser.parse_args(argv)

    try:
        pages, pdfs = load_lists()
        _print_counts(pages, pdfs)
        print("0 errors")
        if args.write:
            changed = write_config(pages, pdfs)
            print("config.py updated" if changed else "config.py already up to date")
    except UrlListError as error:
        print(f"ERRORS:\n{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
