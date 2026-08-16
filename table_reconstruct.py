"""Rebuilds a table's row/column grid from positioned text boxes.

WHY THIS MODULE EXISTS
──────────────────────
A table read by OCR arrives as a bag of text boxes with pixel coordinates. The
grid is entirely implicit in those coordinates. Throw them away and the table is
gone — not degraded, gone — because "331" and "3(3+0)" are only related by the
fact that they sit on the same horizontal line.

web_scraper._extract_images_text used to call

    readtext(image_np, detail=0, paragraph=True)

and both arguments destroy the grid:

  detail=0        returns strings only. The coordinates are discarded inside
                  EasyOCR, so no later stage can recover the layout.
  paragraph=True  merges neighbouring boxes into prose blocks. On a table it
                  merges DOWN a column, because the boxes in a column are closer
                  to each other than to the next column across.

The measured result, still present in the live database for
uoli.edu.pk/faculty/education:

    S: No 1 2 3 5 6 | Course code 311 321 331 341 351 361 |
    Credit Hours 3(3+0) 2(2+0) 3(3+0) 3(3+0) 303+0) 3(3+0) 17

Every number is there and every relationship between them is lost. Asked "how
many credit hours is course 331?", a model reading that has to guess by counting
positions in two unaligned lists. It will sometimes be right, which is worse than
always being wrong, because the error is invisible.

WHAT THIS MODULE DOES
─────────────────────
Takes EasyOCR's detail=1 output — (box, text, confidence) — and rebuilds the
grid in two clustering passes:

  1. ROWS    — group boxes whose vertical centres coincide, tolerance scaled to
               the median text height so it adapts to any image resolution.
  2. COLUMNS — cluster the horizontal centres of every cell across ALL rows at
               once, then assign each cell to its nearest column.

Pass 2 is what makes ragged rows line up. Clustering each row on its own would
put a 3-cell row's cells in columns 0,1,2 and a 5-cell row's in 0,1,2,3,4, and
the two rows would disagree about what column 1 means. Clustering globally means
a column is a property of the table, not of the row — which is what a column is.

It also decides, region by region, which parts of the image are a table and
which are prose, rather than forcing one verdict on a whole page. A scanned
policy page is routinely both: a tabular letterhead above, paragraphs below, and
sometimes a fee schedule in the middle.

USED BY
───────
web_scraper._extract_images_text   — screenshots of course tables, notices
pdf_ingest                         — scanned policy PDFs, where every page is
                                     an image and some pages are fee schedules

Both call reconstruct(). Neither should reimplement any of this: one definition
of "what is a row" is the only way the two paths can stay consistent.

No API calls. No network. Pure geometry.
"""

from __future__ import annotations

import statistics


# A cell must clear this OCR confidence to be trusted in a grid.
#
# Deliberately low. A wrongly-read digit is bad, but a cell silently dropped from
# a table is worse: it shifts every following cell one column left and turns a
# correct row into a plausible wrong one. Better to keep a doubtful cell and let
# the confidence summary on the block warn the reader.
MIN_CELL_CONFIDENCE = 0.20

# Vertical tolerance for "these boxes are on the same line", as a multiple of the
# median box height. 0.6 was chosen because table rows on this university's
# screenshots are separated by roughly one text height, so half a text height
# cleanly splits rows without merging a tall header into the row below it.
ROW_TOLERANCE = 0.6

# Horizontal tolerance for "these cells start in the same column", as a multiple
# of the median box height (height, not width — width varies with how many
# characters a cell holds, height does not, so height is the stable unit).
# Applied to LEFT EDGES, not centres; see _geometry.
COL_TOLERANCE = 1.5

# Below this many rows, a grid is not a table. Two rows is a header plus one
# record, which is a table; one row is a caption.
MIN_TABLE_ROWS = 2

# A table needs at least this many columns in at least MIN_TABLE_ROWS rows.
MIN_TABLE_COLS = 2

# How many column bands two consecutive rows must BOTH fill before they count as
# belonging to the same table. Two is the fewest that can express alignment at
# all: one shared column is satisfied by every left-aligned prose line on the
# page. See _tabular_rows for the measurements behind this.
MIN_SHARED_COLS = 2

# A run of aligned rows that is only PART of a larger page needs at least this
# many rows before it is believed. MIN_TABLE_ROWS still governs a grid that is a
# table end to end, e.g. a screenshot cropped to one course table.
#
# Measured on the seven-page scanned notification: the numbered clause list on
# page 6 produced eighteen alternating regions of two and three rows, so the
# rendered page flipped between pipes and prose every few lines. Every real table
# measured on this site is six rows or more. Two aligned lines inside a page of
# prose are a coincidence.
MIN_ISLAND_ROWS = 4

# If any column of a candidate table has cells this long on average, the region is
# a numbered list or a definitions list with a hanging indent, not a table.
#
# Measured, per column mean cell length:
#     real course tables          1..28 chars   (max over every column of six)
#     prose misread as tables    56..167 chars
# "| (c) | \"Chancellor\" means Chancellor of the University of Loralai; |" is not
# a table row; the pipes add no relation that the text did not already state, and
# they break the paragraph flow the answering model reads.
MAX_TABLE_CELL_CHARS = 55


def _geometry(box) -> tuple[float, float, float, float]:
    """Return (left_x, right_x, centre_y, height) for one EasyOCR polygon.

    EasyOCR gives four corner points, not a rectangle, because detected text can
    be rotated. min/max on the x values give the horizontal span, averaging the y
    values gives the vertical centre, and max-minus-min on y gives an upright
    height that is correct enough for a scanned page and degrades gracefully on a
    skewed one.

    WHY THE SPAN AND NOT THE CENTRE
    ───────────────────────────────
    The first version of this module clustered columns on each cell's centre_x.
    Measured on this university's four course-table screenshots, that produced 6
    to 8 columns for tables that have 5, because a centre is a function of how
    WIDE the text is, and cells in one column are not the same width:

        "Functional EnglishL"          centre ≈ 604
        "General Methods of Teaching"  centre ≈ 641

    Those two are the same column of the same table — they share a left edge, and
    their centres differ by more than a column gap. Clustering centres therefore
    splits one column in two and every row disagrees about what column 3 means.

    A cell's SPAN is the honest feature: a column is a horizontal band, a cell
    occupies part of it, and the cell belongs to whichever band it overlaps most.
    That works for left-aligned data and for centred headers with one rule, which
    is why the span is returned instead of a single scalar.
    """
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return (min(xs), max(xs), sum(ys) / len(ys), max(ys) - min(ys))


def _cluster_1d(values: list[float], tolerance: float) -> list[float]:
    """Cluster sorted scalars into centres, splitting wherever a gap exceeds
    `tolerance`.

    Single-linkage on one axis. Chosen over k-means because the number of columns
    is exactly what we do not know — k-means would need it as input, and guessing
    k is the whole problem. A gap threshold reads the count off the data instead.
    """
    if not values:
        return []

    ordered = sorted(values)
    groups: list[list[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])

    # The mean, not the first member: a column centre should sit in the middle of
    # its cells so that "nearest column" is a fair test for a cell on either edge.
    return [sum(g) / len(g) for g in groups]


def _nearest(value: float, centres: list[float]) -> int:
    """Index of the closest centre. Ties go left, which is arbitrary but stable."""
    best_i, best_d = 0, abs(value - centres[0])
    for i, c in enumerate(centres[1:], 1):
        d = abs(value - c)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _column_bands(cells: list[dict], tolerance: float) -> list[tuple[float, float]]:
    """Derive column bands (left, right) from the left edges of every cell.

    Left edges, because that is the feature cells in one column share regardless
    of how much text each holds — see _geometry for the measurement that forced
    this. Each band runs from its own left edge to the next column's, so the bands
    tile the page with no gaps: every cell lands somewhere, and a cell that
    straddles a boundary is resolved by overlap rather than dropped.
    """
    lefts = _cluster_1d([c["x0"] for c in cells], tolerance)
    if not lefts:
        return []
    far_right = max(c["x1"] for c in cells) + 1.0
    bands = []
    for i, left in enumerate(lefts):
        right = lefts[i + 1] if i + 1 < len(lefts) else far_right
        bands.append((left, max(right, left + 1.0)))
    return bands


def _assign_by_overlap(cell: dict, bands: list[tuple[float, float]]) -> int:
    """Index of the band this cell overlaps most.

    Overlap, not nearest-edge, so one rule covers both alignments a table uses:
    a left-aligned data cell overlaps its own band from the left, and a centred
    header overlaps its own band across the middle. Nearest-left-edge would put
    every centred header one column too far left.
    """
    best_i, best_overlap = 0, -1.0
    for i, (left, right) in enumerate(bands):
        overlap = min(cell["x1"], right) - max(cell["x0"], left)
        if overlap > best_overlap:
            best_i, best_overlap = i, overlap
    return best_i


def _drop_empty_columns(grid: list[list[str]]) -> list[list[str]]:
    """Remove any column that is empty in every row.

    A column nothing occupies is not a column, it is a clustering artefact — one
    stray wide box can open a band that no other cell falls into. Dropping it
    shortens every row and, more importantly, makes the column indices mean
    something when _merge_wrapped_rows compares which columns two rows occupy.
    """
    if not grid:
        return grid
    width = max(len(r) for r in grid)
    keep = [i for i in range(width)
            if any(i < len(r) and r[i].strip() for r in grid)]
    return [[(r[i] if i < len(r) else "") for i in keep] for r in grid]


# Words that begin a summary row rather than a wrapped continuation. A table's
# total line often carries one cell in a text column, which is shaped exactly like
# a wrap; only the word distinguishes them. Kept short and generic on purpose —
# this is a fact about tables, not about this university.
_SUMMARY_STARTS = ("total", "grand total", "sub total", "subtotal", "sum")


def _merge_wrapped_rows(grid: list[list[str]]) -> list[list[str]]:
    """Fold a row that is the continuation of the row above it into that row.

    A cell whose text is wider than its column wraps onto a second line, and OCR
    reports the second line as its own row. Measured on this site's screenshots,
    that is why a course name lost its tail:

        | 312 | Compulsory | English-II | 3(3+0) |
        |     |            | Skills)    |        |

    Only a row with EXACTLY ONE filled cell is treated as a wrap, and only when
    that cell sits in a column the row above also fills.

    WHY THE RULE IS THAT NARROW
    ───────────────────────────
    The first version accepted any row whose filled columns were a strict subset
    of the row above's. That is the textbook description of a wrap, and it was
    measured to be wrong: it merged genuine records. On this site's tables OCR
    sometimes misses a faint serial number, so a real course row arrives as

        |  | 3_  | Pakistan History of Education | 3-0 |
        |  |     | Research Methods in Education  | 3-0 |

    and the second line is a subset of the first with fewer filled cells — the
    wrap signature exactly. Merging produced "Pakistan History of Education
    Research Methods in Education | 3-0 3-0", two courses fused into one with two
    credit values. A split header is untidy; a fused record is false, and this
    module exists to keep table facts true.

    A genuine record almost always carries a value as well as a label, so
    requiring a single filled cell separates the two cases without needing to know
    which column holds values.

    Two further guards keep real rows out:

      * a lone cell in column 0 is a section label ("2nd Semester"), never a wrap,
        because the narrow serial-number column has nothing to wrap;
      * a cell opening with a summary word is a total line, not a continuation.
    """
    if not grid:
        return grid

    out: list[list[str]] = []
    for row in grid:
        filled = {i for i, c in enumerate(row) if c.strip()}
        if out and len(filled) == 1:
            prev_filled = {i for i, c in enumerate(out[-1]) if c.strip()}
            (only,) = filled
            summary = row[only].strip().lower().startswith(_SUMMARY_STARTS)
            if only != 0 and only in prev_filled and not summary:
                out[-1][only] = f"{out[-1][only]} {row[only].strip()}".strip()
                continue
        out.append(list(row))
    return out


def build_grid(ocr_results) -> tuple[list[list[str]], float]:
    """Turn EasyOCR detail=1 output into a rectangular grid of cell strings.

    Args:
        ocr_results: iterable of (box, text, confidence) as returned by
            ``reader.readtext(img, detail=1, paragraph=False)``. Anything whose
            shape does not match is skipped rather than raising, because OCR
            output is third-party data and one malformed entry must not lose the
            other forty.

    Returns:
        (grid, mean_confidence) — grid is a list of rows, every row padded to the
        same width so column i means the same thing in every row. Empty cells are
        "" and are meaningful: they say the table genuinely has a hole there.
    """
    cells = []
    for entry in ocr_results or []:
        try:
            box, text, conf = entry[0], entry[1], float(entry[2])
        except (TypeError, IndexError, ValueError):
            continue
        text = (text or "").strip()
        if not text or conf < MIN_CELL_CONFIDENCE:
            continue
        x0, x1, cy, h = _geometry(box)
        cells.append({"x0": x0, "x1": x1, "y": cy, "h": h,
                      "text": text, "conf": conf})

    if not cells:
        return [], 0.0

    # Median height is the unit for both tolerances. Using an absolute pixel
    # value instead would work on one screenshot and fail on the next: the same
    # table exported at 2× resolution has twice the gaps.
    unit = statistics.median([c["h"] for c in cells]) or 1.0

    # ── pass 1: rows ────────────────────────────────────────────
    row_centres = _cluster_1d([c["y"] for c in cells], unit * ROW_TOLERANCE)
    rows: dict[int, list[dict]] = {}
    for c in cells:
        rows.setdefault(_nearest(c["y"], row_centres), []).append(c)

    # ── pass 2: columns, clustered across ALL rows together ─────
    # This global pass is the point of the module. See the header note on why
    # per-row clustering cannot align ragged rows.
    bands = _column_bands(cells, unit * COL_TOLERANCE)
    width = len(bands)

    grid: list[list[str]] = []
    for row_i in sorted(rows):
        line = [""] * width
        for c in sorted(rows[row_i], key=lambda d: d["x0"]):
            col = _assign_by_overlap(c, bands)
            # Two boxes landing in one slot means the column tolerance merged
            # what OCR split — usually one wrapped label. Join with a space
            # rather than overwrite, so no text is silently lost.
            line[col] = f"{line[col]} {c['text']}".strip() if line[col] else c["text"]
        if any(line):
            grid.append(line)

    # ── pass 3: tidy up ─────────────────────────────────────────
    # Order matters. Empty columns go first so that the column indices
    # _merge_wrapped_rows compares are real columns, not clustering artefacts;
    # merging first and compacting second would let one artefact column block a
    # merge that should have happened.
    grid = _drop_empty_columns(grid)
    grid = _merge_wrapped_rows(grid)
    grid = _drop_empty_columns(grid)

    mean_conf = sum(c["conf"] for c in cells) / len(cells)
    return grid, mean_conf


def looks_like_table(grid: list[list[str]]) -> bool:
    """Is this grid a table, or prose that happens to have been laid out?

    Kept as a whole-grid convenience for callers that want one verdict, and used
    by segment() to judge an individual run of rows. Prefer segment(): a real page
    is usually part table and part prose, and one verdict for the whole page is
    wrong for both halves.

    A run is a table when at least MIN_TABLE_ROWS of its rows each carry
    MIN_TABLE_COLS or more filled cells AND agree with a neighbour about which
    columns those are. Agreement is the operative half — see _tabular_rows.
    """
    if len(grid) < MIN_TABLE_ROWS:
        return False
    return sum(_tabular_rows(grid)) >= MIN_TABLE_ROWS


def _tabular_rows(grid: list[list[str]]) -> list[bool]:
    """Per row: does it participate in a column structure a neighbour shares?

    WHY NEIGHBOUR AGREEMENT AND NOT A CELL COUNT
    ────────────────────────────────────────────
    The first version of looks_like_table asked only "do two rows carry two or
    more filled cells?". On the small course-table screenshots that separated
    table from prose perfectly. On a full A4 page rendered at 200 DPI it does not,
    and the failure was measured, not imagined: EasyOCR splits one prose LINE into
    several word-group boxes, so every prose line also carries two or more filled
    cells. A seven-page prose notification came back as seven tables and rendered

        |  |  |  |  | UNIVERSITY OF LORALAI OFFICE OF THE REGISTRAR |

    Fill ratio was tried next and rejected, also on measurement: real tables on
    this site fill 0.55-0.80 of their grid and prose pages fill 0.27-0.79, so the
    populations overlap and no threshold exists. One prose page was DENSER than a
    genuine course table.

    What actually separates them is agreement. A table's rows keep filling the
    same columns, because a column is a property of the table. Prose word-groups
    start wherever the previous word happened to end, so two consecutive prose
    lines rarely fill the same two column bands. That is the test.
    """
    sigs = [{i for i, c in enumerate(row) if c.strip()} for row in grid]
    wide = [len(s) >= MIN_TABLE_COLS for s in sigs]
    out = []
    for i, sig in enumerate(sigs):
        agrees = False
        if wide[i]:
            for j in (i - 1, i + 1):
                if 0 <= j < len(sigs) and wide[j] \
                        and len(sig & sigs[j]) >= MIN_SHARED_COLS:
                    agrees = True
                    break
        out.append(agrees)
    return out


def segment(grid: list[list[str]]) -> list[tuple[str, list[list[str]]]]:
    """Split a grid into consecutive ("table"|"prose", rows) regions.

    Runs of rows that share a column structure become candidate table regions;
    everything else is prose. A candidate is then demoted to prose unless it is
    large enough to be believed (MIN_ISLAND_ROWS, or MIN_TABLE_ROWS when the run
    is the entire grid) and none of its columns holds paragraph-length text
    (MAX_TABLE_CELL_CHARS).

    Demotion costs formatting, never content: a demoted region is still emitted,
    as prose lines with its cells joined by spaces. That asymmetry is deliberate.
    A table wrongly rendered as prose loses alignment the reader can often still
    infer; prose wrongly rendered as a table states relationships between cells
    that do not exist, and this module exists to keep table facts true.

    Surviving table regions are compacted to the columns they actually use, so a
    small table inside a wide page grid does not carry that page's empty gutters.
    """
    if not grid:
        return []
    flags = _tabular_rows(grid)

    runs: list[tuple[bool, list[list[str]]]] = []
    start = 0
    for i in range(1, len(grid) + 1):
        if i < len(grid) and flags[i] == flags[start]:
            continue
        runs.append((flags[start], grid[start:i]))
        start = i

    blocks: list[tuple[str, list[list[str]]]] = []
    for tabular, rows in runs:
        kind = "prose"
        if tabular:
            compact = _compact(rows)
            whole = len(rows) == len(grid)
            floor = MIN_TABLE_ROWS if whole else MIN_ISLAND_ROWS
            if len(compact) >= floor and not _has_prose_column(compact):
                kind = "table"
                rows = compact
        if blocks and kind == "prose" and blocks[-1][0] == "prose":
            blocks[-1][1].extend(rows)
        else:
            blocks.append((kind, list(rows)))
    return blocks


def _has_prose_column(rows: list[list[str]]) -> bool:
    """Does any column average paragraph-length text? See MAX_TABLE_CELL_CHARS."""
    if not rows:
        return False
    width = max(len(r) for r in rows)
    for i in range(width):
        lengths = [len(r[i].strip()) for r in rows
                   if i < len(r) and r[i].strip()]
        if lengths and sum(lengths) / len(lengths) > MAX_TABLE_CELL_CHARS:
            return True
    return False



def _compact(rows: list[list[str]]) -> list[list[str]]:
    """Drop columns that no row in THIS region fills."""
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    keep = [i for i in range(width)
            if any(i < len(r) and r[i].strip() for r in rows)]
    return [[(r[i] if i < len(r) else "") for i in keep] for r in rows]



def render_markdown(grid: list[list[str]]) -> str:
    """Render a grid as pipe-delimited rows, one line per row.

    Pipes, because that is what _extract_text already emits for HTML <table> (see
    web_scraper's table branch) and what the DOCX table loader emits. An answering
    model that has learned to read a fee table from one source then reads all
    three the same way, and a table sourced from a scan is indistinguishable in
    format from one sourced from HTML.

    No separator row is written. It carries no information for a reader that is
    not rendering the markdown, and it costs a line of embedding budget in every
    single table.
    """
    if not grid:
        return ""
    trimmed = [_strip_trailing_empties(r) for r in grid]
    return "\n".join("| " + " | ".join(r) + " |" for r in trimmed if any(r))


def _strip_trailing_empties(row: list[str]) -> list[str]:
    """Drop trailing empty cells from one row.

    Padding to full width matters for column alignment during construction; once
    rendered, trailing pipes are noise. Interior blanks are preserved — those are
    real holes in the table and shifting them would corrupt the alignment this
    module exists to protect.
    """
    end = len(row)
    while end > 0 and not row[end - 1].strip():
        end -= 1
    return row[:end]


def reconstruct(ocr_results) -> dict:
    """Single entry point. Rebuild whatever the OCR boxes describe.

    Returns a dict:
        text        str   — the page, region by region: table regions rendered
                            pipe-delimited, prose regions as plain lines
        is_table    bool  — True if any region is a table
        rows        int   — rows in the largest table region, else grid rows
        cols        int   — columns in the largest table region, else grid cols
        confidence  float — mean OCR confidence over kept cells, 0.0 if none

    Callers use `is_table` to decide whether to mark the block low-confidence,
    and `confidence` to decide whether to keep it at all. Both are returned
    rather than acted on here: this module knows geometry, not ingestion policy.

    A page is segmented rather than judged as a whole, because a scanned policy
    page is routinely a tabular letterhead above prose paragraphs, and one verdict
    for both halves formats one of them wrongly.
    """
    grid, conf = build_grid(ocr_results)
    if not grid:
        return {"text": "", "is_table": False, "rows": 0, "cols": 0,
                "confidence": 0.0}

    parts: list[str] = []
    best_rows, best_cols, any_table = 0, 0, False
    for kind, rows in segment(grid):
        if kind == "table":
            rendered = render_markdown(rows)
            if not rendered:
                continue
            any_table = True
            cols = max(len(r) for r in rows)
            if len(rows) * cols > best_rows * best_cols:
                best_rows, best_cols = len(rows), cols
            parts.append(rendered)
        else:
            # Prose: join each row's cells with spaces and each row with a
            # newline. The row grouping is still worth keeping — it preserves
            # line breaks that paragraph=True would have flattened, and headings
            # stay on their own line.
            lines = [" ".join(c for c in row if c.strip()).strip()
                     for row in rows]
            text = "\n".join(l for l in lines if l)
            if text:
                parts.append(text)

    if not any_table:
        best_rows, best_cols = len(grid), max(len(r) for r in grid)

    return {"text": "\n".join(parts), "is_table": any_table,
            "rows": best_rows, "cols": best_cols, "confidence": conf}
