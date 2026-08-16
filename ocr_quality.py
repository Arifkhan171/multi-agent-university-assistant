"""Decides whether a block of OCR'd text is worth storing.

WHY THIS MODULE EXISTS
──────────────────────
web_scraper decided what to READ from an image by looking at its FILENAME. An
allowlist of words — "screenshot", "notice", "prospectus", "fee" — had to appear
in the URL or the alt text or the image was never downloaded.

That filter was written for a real, measured failure: switching image reading on
pulled in 50 Facebook event posters and 12 ceremony photographs, the corpus grew
by roughly 600 chunks, and answers got worse. The filename was the only thing
separating the good images from the bad ones, so the filename became the gate.

But a filename gate fails in the direction that costs content. The university
names things however it likes, and anything named in a way nobody predicted is
never even looked at. Measured on the live site: 34 real image URLs on
/faculty/education, of which the allowlist admits 22 — and it admits them only
because someone happened to think of the word "screenshot".

WHAT THIS MODULE DOES INSTEAD
─────────────────────────────
Move the decision from BEFORE the read to AFTER it. Download and OCR the image,
then judge the TEXT. Reading is free — EasyOCR runs locally on the CPU, no API
call, no token cost — so there is no reason to guess from a filename what a
picture contains when the picture can simply be read.

Four measurements, and the reason each one is here:

  length       A document has something to say. Below ~150 characters an image is
               a logo crop or a caption. Measured: the three header-logo crops
               previously admitted read 57, 64 and 79 characters; every genuine
               document read 281 or more. 150 sits inside that gap.

  confidence   EasyOCR's own estimate of how legible each box was, now available
               because the caller switched to detail=1 for table reconstruction.
               This is the single best signal and it was thrown away before.
               Measured on this site's course tables: 0.88 to 0.93.

  letterish    Fraction of non-space characters that are letters, digits, or
               punctuation a document actually uses. Kills the case the old
               comments recorded verbatim:
                   "(t {;? Vt. ToV Patzv Vch1 61+"
               which is what OCR returns when pointed at a photograph.

  wordish      Fraction of tokens that are shaped like words or numbers rather
               than character soup. A second opinion on the same failure, because
               a photograph can produce high-letterish nonsense.

WHAT IT DELIBERATELY DOES NOT DO
────────────────────────────────
It does not judge whether the CONTENT is useful — only whether it is text. An
event poster that reads cleanly passes here. Deciding that event ephemera should
not crowd out academic policy is a retrieval and categorisation problem, and it
is solved in those layers, not by refusing to read a picture.

USED BY
───────
web_scraper._extract_images_text   — page images
pdf_ingest                         — scanned PDF pages

Both must agree on what counts as readable text, or the same notice ingested as
a PDF and as a screenshot would be treated differently.

No API calls. No network. Pure string measurement.
"""

from __future__ import annotations

import re

# Below this, an image is a logo, a caption, or a button — not a document.
# See the measured gap in the module docstring.
MIN_CHARS = 150

# Mean EasyOCR confidence over the kept boxes.
#
# Deliberately well below the 0.88-0.93 that this site's clean course tables
# measure. The threshold's job is to reject a photograph, not to demand a perfect
# scan: a genuine scanned policy page photographed slightly out of focus is still
# the only copy of that policy, and losing it costs more than a few wrong
# characters do.
MIN_MEAN_CONFIDENCE = 0.45

# Fraction of non-space characters that belong to a document's alphabet.
CHARS_OK = re.compile(r"[A-Za-z0-9.,:;()/%&'\"\-+#*@\[\]|]")
MIN_LETTERISH = 0.80

# Fraction of tokens shaped like a word or a number.
MIN_WORDISH = 0.55

# A token that is a word: two or more letters. Case is not tested — OCR routinely
# returns "EnglishL" for "English L" and rejecting mixed case would throw away
# correct readings of real headings.
_WORD = re.compile(r"^[A-Za-z]{2,}$")

# A token that is a number as documents write them: "3(3+0)", "2(2+0)", "17",
# "3-0", "410051", "1.2.3". Table cells are mostly these, which is exactly why
# wordish alone must never be the only test applied to a table.
_NUMBER = re.compile(r"^[0-9][0-9.,:/()+\-]*$")

# A token that is a short label documents use: "S#", "No", "1_", "2na", "Rs.".
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.#_/\-]{0,4}$")


def measure(text: str, confidence: float = 1.0) -> dict:
    """Compute the four signals for one block of OCR'd text.

    Returns a dict with `length`, `confidence`, `letterish`, `wordish`. Separated
    from the decision below so a dry-run can print the numbers for known-good and
    known-bad images and the thresholds can be set from data rather than taste.
    """
    stripped = (text or "").strip()
    if not stripped:
        return {"length": 0, "confidence": float(confidence),
                "letterish": 0.0, "wordish": 0.0}

    dense = re.sub(r"\s+", "", stripped)
    letterish = (len(CHARS_OK.findall(dense)) / len(dense)) if dense else 0.0

    # Pipes are the table delimiter this pipeline emits, not content, so they must
    # not count as tokens when judging whether the content is word-shaped.
    tokens = [t for t in re.split(r"[\s|]+", stripped) if t]
    if tokens:
        good = sum(1 for t in tokens
                   if _WORD.match(t) or _NUMBER.match(t) or _LABEL.match(t))
        wordish = good / len(tokens)
    else:
        wordish = 0.0

    return {"length": len(stripped), "confidence": float(confidence),
            "letterish": letterish, "wordish": wordish}


def is_usable(text: str, confidence: float = 1.0,
              is_table: bool = False) -> tuple[bool, str]:
    """Should this OCR block be stored?

    Args:
        text: the reconstructed text, as table_reconstruct.reconstruct produced it.
        confidence: mean OCR confidence over the boxes that survived.
        is_table: True when the block is a reconstructed grid.

    Returns:
        (keep, reason) — `reason` names the failing signal, or "ok". It is
        returned rather than logged here so the caller can log it against the
        image URL, which is the only form in which the message is useful.

    WHY is_table CHANGES THE RULES
    ──────────────────────────────
    A fee schedule or a semester course list is mostly digits and codes. Its
    wordish score is legitimately low — "311", "3(3+0)", "2(2+0)" are the content,
    not noise. Applying the prose test to a table would reject exactly the
    material this pipeline was rebuilt to preserve. Confidence and letterish still
    apply, and between them they are what actually catch an illegible scan.
    """
    m = measure(text, confidence)

    if m["length"] < MIN_CHARS:
        return False, f"too short ({m['length']} chars)"
    if m["confidence"] < MIN_MEAN_CONFIDENCE:
        return False, f"low OCR confidence ({m['confidence']:.2f})"
    if m["letterish"] < MIN_LETTERISH:
        return False, f"not document characters ({m['letterish']:.2f})"
    if not is_table and m["wordish"] < MIN_WORDISH:
        return False, f"not word-shaped ({m['wordish']:.2f})"
    return True, "ok"
