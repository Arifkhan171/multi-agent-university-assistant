# eval_ragas.py
# ═══════════════════════════════════════════════════════════════
# WHAT: Evaluates UOLI chatbot using RAGAS (4 metrics)
# WHY:  Gives real numbers for competition presentation
#
# HOW TO RUN:
#   python eval_ragas.py            → full run (45 questions)
#   python eval_ragas.py --test     → test run (5 questions only)
#   python eval_ragas.py --ragas    → skip chatbot, load checkpoint
#                                     and run RAGAS only
#
# COST ESTIMATE:
#   Chatbot run → uses your existing OpenAI budget (chatbot calls)
#   RAGAS run   → ~$0.30–$0.60 extra (GPT-4o-mini scoring calls)
#
# INSTALL BEFORE RUNNING:
#   pip install ragas==0.1.21 datasets
#
# FILES CREATED:
#   eval_raw_results.json    → checkpoint (chatbot answers)
#   UOLI_Eval_Results.xlsx   → final scores
# ═══════════════════════════════════════════════════════════════

import os
import sys
import json
import time
import uuid
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Config ────────────────────────────────────────────────────
DATASET_PATH     = "UOLI_Eval_Dataset.xlsx"
CHECKPOINT_PATH  = "eval_raw_results.json"
RESULTS_PATH     = "UOLI_Eval_Results.xlsx"

# These need multi-turn conversation — skip for now


SKIP_IDS = {"Q36", "Q37", "Q38", "Q39", "Q40",
            "Q41", "Q42", "Q43", "Q44", "Q45",
            "Q14", "Q15", "Q16","Q11","Q21","Q22"}

# ─────────────────────────────────────────────────────────────
# STEP 1 — Load questions from Excel
# ─────────────────────────────────────────────────────────────
def load_questions(test_mode: bool = False) -> list:
    """
    WHAT: Reads questions + ground truths from UOLI_Eval_Dataset.xlsx
    WHY:  Single source of truth — questions live in Excel, not here
    """
    import openpyxl

    if not os.path.exists(DATASET_PATH):
        print(f"ERROR: {DATASET_PATH} not found.")
        print("Make sure UOLI_Eval_Dataset.xlsx is in this folder.")
        sys.exit(1)

    wb = openpyxl.load_workbook(DATASET_PATH)
    ws = wb["Sheet1"]

    questions = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        qid          = row[0]
        category     = row[1]
        question     = row[2]
        ground_truth = row[3]

        # Skip category header rows (CAT 1, CAT 2 …)
        if not qid or not str(qid).startswith("Q"):
            continue

        # Skip conversational questions (need multi-turn handling)
        if qid in SKIP_IDS:
            continue

        # Skip empty rows
        if not question or not ground_truth:
            continue

        questions.append({
            "id":           qid,
            "category":     category or "Unknown",
            "question":     str(question).strip(),
            "ground_truth": str(ground_truth).strip(),
        })

    if test_mode:
        questions = questions[:5]
        print(f"TEST MODE: Running only first 5 questions.")

    print(f"Loaded {len(questions)} questions from {DATASET_PATH}")
    return questions


# ─────────────────────────────────────────────────────────────
# STEP 2 — Run chatbot on every question
# ─────────────────────────────────────────────────────────────
def collect_answers(questions: list) -> list:
    """
    WHAT: Sends each question to your chatbot.
          Captures the answer and retrieved chunks.

    WHY:  RAGAS needs 4 things per question:
            question + answer + contexts + ground_truth
          We collect answer and contexts here.

    NOTE: Fresh session per question — memory does not
          bleed between evaluation questions.
    """
    from knowledge_base import KnowledgeBase
    from graph.workflow import UniChatbot

    print("\nLoading knowledge base...")
    kb = KnowledgeBase()

    print("Starting chatbot...")
    bot = UniChatbot(kb)

    results = []
    total   = len(questions)
    retrieval_failures = []   # questions that retrieved nothing — see below

    print(f"\n{'─'*60}")
    print(f"Running {total} questions through chatbot")
    print(f"{'─'*60}\n")

    for i, q in enumerate(questions):
        qid      = q["id"]
        question = q["question"]

        print(f"[{i+1}/{total}] {qid}: {question[:55]}...")

        # Fresh session per question — no memory bleed
        session_id = f"eval_{qid}_{uuid.uuid4().hex[:6]}"

        try:
            result   = bot.chat(question, session_id=session_id)
            answer   = result.get("answer", "")
            docs     = result.get("docs", [])
            agent    = result.get("agent_used", "unknown")

            # RAGAS needs contexts as a list of strings, one per retrieved chunk.
            #
            # DO NOT SUBSTITUTE [""] FOR AN EMPTY RETRIEVAL. That is what stood
            # here, and it is why this regression went undiagnosed. A question
            # that retrieved nothing at all printed "Contexts : 1 chunks",
            # identical to a question that retrieved one good chunk, and was
            # handed to RAGAS as a corpus containing one empty document. Nine
            # questions were failing for a reason the harness was actively
            # hiding: not "the retrieval is weak" but "the retrieval returned
            # zero rows". Those are different bugs with different fixes, and the
            # instrument that is supposed to tell them apart must not blur them.
            #
            # RAGAS accepts an empty list. Zero is reported as zero.
            contexts = [doc.page_content for doc in docs]

            print(f"  Agent    : {agent}")
            print(f"  Contexts : {len(contexts)} chunks"
                  f"{'' if contexts else '   <-- RETRIEVAL FAILURE: 0 documents'}")
            if not contexts:
                retrieval_failures.append(f"{qid} [{agent}] {question[:60]}")
            print(f"  Answer   : {answer[:70]}...")

        except Exception as e:
            print(f"  ERROR    : {e}")
            answer   = ""
            contexts = []
            agent    = "error"
            retrieval_failures.append(f"{qid} [error] {e}")

        results.append({
            "id":           qid,
            "category":     q["category"],
            "question":     question,
            "ground_truth": q["ground_truth"],
            "answer":       answer,
            "contexts":     contexts,
            "agent_used":   agent,
        })

        print()

        # Small pause — avoid OpenAI rate limits
        if i < total - 1:
            time.sleep(0.5)

    # Zero retrieval is a plumbing fault, not a low score, and it must be
    # visible without scrolling back through a hundred question blocks. A run
    # with entries here has a bug to fix before any metric is worth reading.
    if retrieval_failures:
        print(f"{'═'*60}")
        print(f"RETRIEVAL FAILURES — {len(retrieval_failures)}/{total} "
              f"questions retrieved ZERO documents")
        print(f"{'═'*60}")
        for line in retrieval_failures:
            print(f"  {line}")
        print(f"{'═'*60}\n")
    else:
        print(f"Retrieval: all {total} questions returned at least one document.\n")

    return results


# ─────────────────────────────────────────────────────────────
# STEP 3 — Save checkpoint (JSON)
# ─────────────────────────────────────────────────────────────
def save_checkpoint(results: list) -> None:
    """
    WHAT: Saves chatbot answers to JSON before running RAGAS.
    WHY:  If RAGAS crashes, you do NOT re-run the chatbot.
          Just fix RAGAS and re-run with --ragas flag.
          Chatbot calls cost time + tokens. JSON is free.
    """
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Checkpoint saved → {CHECKPOINT_PATH}")
    print("If RAGAS fails, re-run with: python eval_ragas.py --ragas")


def load_checkpoint() -> list:
    """Load saved chatbot answers from checkpoint."""
    with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
        results = json.load(f)
    print(f"Loaded {len(results)} results from {CHECKPOINT_PATH}")
    return results


# ─────────────────────────────────────────────────────────────
# STEP 4 — Run RAGAS evaluation
# ─────────────────────────────────────────────────────────────
def run_ragas(results: list) -> dict:
    """
    WHAT: Runs 4 RAGAS metrics on all collected results.

    WHY:  These 4 numbers tell you exactly where your
          chatbot is strong and where it fails:
            faithfulness      → hallucination check
            answer_relevancy  → did it answer the question?
            context_precision → were retrieved chunks useful?
            context_recall    → did it retrieve enough?

    COST: ~$0.30–$0.60 (GPT-4o-mini scoring calls by RAGAS)
    """
    try:
        import sys
        from unittest.mock import MagicMock
        sys.modules['langchain_community.chat_models.vertexai'] = MagicMock()
        
        from ragas import evaluate
        from ragas.metrics import (
            Faithfulness,
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
        )
        from datasets import Dataset
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
    except ImportError:
        print("\nERROR: RAGAS not installed.")
        print("Run this first: pip install ragas==0.1.21 datasets")
        sys.exit(1)

    print(f"\nBuilding RAGAS dataset from {len(results)} results...")

    # A zero-retrieval question must still be scored, and scored as a failure —
    # dropping it would quietly raise the averages by removing the worst cases.
    # RAGAS metrics divide by the number of retrieved contexts, so an empty list
    # can come back NaN and poison the aggregate mean; a single empty string
    # scores a deterministic 0 on precision, recall and faithfulness, which is
    # the honest result.
    #
    # NOTE WHERE THIS SUBSTITUTION HAPPENS. It is here, at scoring time, and not
    # in run_chatbot() where it used to be. There it ran BEFORE the retrieval
    # count was printed, so a question that retrieved nothing logged
    # "Contexts : 1 chunks" and looked identical to one that worked. The count
    # in the log and the checkpoint JSON is now the true count; only the scorer
    # sees the placeholder.
    empty = sum(1 for r in results if not r["contexts"])
    if empty:
        print(f"  {empty} question(s) retrieved nothing — scoring them as 0, "
              f"not dropping them.")
    contexts = [r["contexts"] or [""] for r in results]

    dataset = Dataset.from_dict({
        "question":     [r["question"]     for r in results],
        "answer":       [r["answer"]       for r in results],
        "contexts":     contexts,
        "ground_truth": [r["ground_truth"] for r in results],
    })

    print("Running RAGAS evaluation (this takes a few minutes)...")
    print("RAGAS is making LLM calls to score each question.\n")

    eval_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4o-mini"))
    eval_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings())

    scores = evaluate(
        dataset=dataset,
        metrics=[
            Faithfulness(),
            AnswerRelevancy(),
            ContextPrecision(),
            ContextRecall(),
        ],
        llm=eval_llm,
        embeddings=eval_embeddings,
    )

    return scores


# ─────────────────────────────────────────────────────────────
# STEP 5 — Save final results to Excel
# ─────────────────────────────────────────────────────────────
def save_results(results: list, scores) -> None:
    """
    WHAT: Saves per-question answers + scores to Excel.
    WHY:  Professional output for competition presentation.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
    GREEN_FILL  = PatternFill("solid", fgColor="C6EFCE")
    RED_FILL    = PatternFill("solid", fgColor="FFCCCC")
    AMBER_FILL  = PatternFill("solid", fgColor="FFEB9C")
    WHITE_FONT  = Font(color="FFFFFF", bold=True, name="Arial", size=10)
    BOLD        = Font(bold=True, name="Arial", size=10)
    NORM        = Font(name="Arial", size=10)
    WRAP        = Alignment(wrap_text=True, vertical="top")
    thin        = Side(style="thin", color="CCCCCC")
    BDR         = Border(left=thin, right=thin, top=thin, bottom=thin)

    wb = openpyxl.Workbook()

    # ── Sheet 1: Per-question results ─────────────────────────
    ws1 = wb.active
    ws1.title = "Per-Question Results"

    headers = [
        "#", "Category", "Question", "Ground Truth",
        "Chatbot Answer", "Chunks", "Agent",
        "Faithfulness", "Ans Relevancy",
        "Ctx Precision", "Ctx Recall"
    ]
    widths = [5, 18, 42, 42, 42, 8, 12, 14, 14, 14, 12]

    for col, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws1.cell(row=1, column=col, value=h)
        cell.font      = WHITE_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = WRAP
        cell.border    = BDR
        ws1.column_dimensions[cell.column_letter].width = w

    ws1.row_dimensions[1].height = 22

    # Get per-question scores from RAGAS result DataFrame
    try:
        scores_df = scores.to_pandas()
        has_per_q  = True
    except Exception:
        scores_df  = None
        has_per_q  = False

    def score_fill(val):
        if not isinstance(val, float):
            return PatternFill()
        if val >= 0.75:
            return GREEN_FILL
        if val >= 0.50:
            return AMBER_FILL
        return RED_FILL

    for i, r in enumerate(results):
        row_num = i + 2

        # Per-question scores (if available from RAGAS)
        if has_per_q and i < len(scores_df):
            row_s  = scores_df.iloc[i]
            faith  = row_s.get("faithfulness",      None)
            relev  = row_s.get("answer_relevancy",  None)
            prec   = row_s.get("context_precision", None)
            recall = row_s.get("context_recall",    None)
        else:
            faith = relev = prec = recall = None

        def fmt(v):
            return round(v, 3) if isinstance(v, float) else "—"

        values = [
            r["id"],
            r["category"],
            r["question"],
            r["ground_truth"],
            r["answer"],
            len(r["contexts"]),
            r.get("agent_used", ""),
            fmt(faith), fmt(relev), fmt(prec), fmt(recall),
        ]

        for col, val in enumerate(values, 1):
            cell           = ws1.cell(row=row_num, column=col, value=val)
            cell.font      = BOLD if col == 1 else NORM
            cell.alignment = WRAP
            cell.border    = BDR
            if col >= 8:
                original = [faith, relev, prec, recall][col - 8]
                cell.fill = score_fill(original)

        ws1.row_dimensions[row_num].height = 60

    ws1.freeze_panes = "A2"

    # ── Sheet 2: Summary ──────────────────────────────────────
    ws2 = wb.create_sheet("Summary")
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 18

    # Pull overall averages
    try:
        overall = dict(scores)
        faith_avg  = overall.get("faithfulness",      "N/A")
        relev_avg  = overall.get("answer_relevancy",  "N/A")
        prec_avg   = overall.get("context_precision", "N/A")
        recall_avg = overall.get("context_recall",    "N/A")
    except Exception:
        faith_avg = relev_avg = prec_avg = recall_avg = "N/A"

    def fmt_avg(v):
        return round(v, 3) if isinstance(v, float) else v

    summary = [
        ("UOLI CHATBOT — RAGAS EVALUATION",     "",                   "title"),
        ("",                                     "",                   "blank"),
        ("Questions evaluated",                  len(results),         "info"),
        ("Skipped (conversational)",             len(SKIP_IDS),        "info"),
        ("",                                     "",                   "blank"),
        ("METRIC",                               "SCORE (0 – 1)",      "header"),
        ("Faithfulness",                         fmt_avg(faith_avg),   "score"),
        ("Answer Relevancy",                     fmt_avg(relev_avg),   "score"),
        ("Context Precision",                    fmt_avg(prec_avg),    "score"),
        ("Context Recall",                       fmt_avg(recall_avg),  "score"),
        ("",                                     "",                   "blank"),
        ("SCORE GUIDE",                          "",                   "header"),
        ("0.75 – 1.00  Excellent",               "",                   "info"),
        ("0.50 – 0.74  Good",                    "",                   "info"),
        ("0.25 – 0.49  Needs improvement",       "",                   "info"),
        ("0.00 – 0.24  Problem — fix urgently",  "",                   "info"),
    ]

    for i, (label, val, kind) in enumerate(summary, 1):
        c1 = ws2.cell(row=i, column=1, value=label)
        c2 = ws2.cell(row=i, column=2, value=val)

        if kind == "title":
            c1.font = Font(bold=True, name="Arial", size=13, color="1F4E79")
        elif kind == "header":
            for c in (c1, c2):
                c.fill = HEADER_FILL
                c.font = WHITE_FONT
        elif kind == "score":
            c1.font = Font(bold=True, name="Arial", size=11)
            c2.font = Font(bold=True, name="Arial", size=11)
            c2.fill = score_fill(val)
        else:
            c1.font = NORM
            c2.font = NORM

        ws2.row_dimensions[i].height = 22

    wb.save(RESULTS_PATH)
    print(f"\nResults saved → {RESULTS_PATH}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    test_mode   = "--test"  in sys.argv
    ragas_only  = "--ragas" in sys.argv

    print("=" * 60)
    print("  UOLI CHATBOT — RAGAS EVALUATION")
    if test_mode:
        print("  MODE: TEST (5 questions only)")
    elif ragas_only:
        print("  MODE: RAGAS only (loading checkpoint)")
    else:
        print("  MODE: FULL (45 questions)")
    print("=" * 60)

    # ── Collect answers or load checkpoint ────────────────────
    if ragas_only:
        if not os.path.exists(CHECKPOINT_PATH):
            print(f"\nERROR: No checkpoint found at {CHECKPOINT_PATH}")
            print("Run without --ragas first to collect chatbot answers.")
            sys.exit(1)
        results = load_checkpoint()

    else:
        questions = load_questions(test_mode=test_mode)
        results   = collect_answers(questions)
        save_checkpoint(results)

    # ── Run RAGAS ─────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("Starting RAGAS evaluation...")
    print(f"{'─'*60}")

    scores = run_ragas(results)

    # ── Print to terminal ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RAGAS SCORES — UOLI CHATBOT")
    print("=" * 60)
    print(scores)

    # ── Save to Excel ─────────────────────────────────────────
    save_results(results, scores)

    print("\nDone. Open UOLI_Eval_Results.xlsx for full breakdown.")
    print("=" * 60)


if __name__ == "__main__":
    main()