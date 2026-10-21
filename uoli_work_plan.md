# UOLI Chatbot — Work Plan

Updated: 19 October 2026
Every spec starts from this file. One spec = one session = one branch.
The list of weak points (W1–W36) is in `uoli_pre_evaluation_guide.md`.

---

## 1. The big picture

The work has 5 parts, in this order:

| Part | Name | Simple question |
|---|---|---|
| A | Get ready | Is the chatbot and its data clean and new? |
| B | Test data | Do we have good questions to test with? |
| C | Component tests | Does each small part work alone? |
| D | Workflow tests | Do the parts work together for one job? |
| E | System tests | Is the whole chatbot good, safe and fast? |

Why this order: if we test before Part A, we test old garbage. If we test the whole chatbot before its parts, a low score does not tell us what to fix.

From your pictures, we can test at 3 levels:
- Component: one small part (prompt, retriever...).
- Workflow: a few parts doing one job (one question to one answer).
- Entire application: everything together, as the student sees it.

And at the top level we check 3 risks:
- Quality: is the answer right?
- Safety: can the answer cause harm?
- Operational: is it fast, cheap and does it stay up?

---

## 2. Three ways to test

- **Program (P):** normal code checks the answer against a fixed right answer. Free. Best when there is one clear right answer.
- **AI judge (L):** an AI reads the answer and gives a score. It costs money, and you run it. Best for meaning.
- **Human (H):** you read the answers yourself. Best for the final truth, and to check that P and L are not wrong.

Rule: use P first, L for meaning, and H on a small sample.

---

## 3. Part A — Get ready

| Spec | Task |
|---|---|
| 01 | Check which old links work, redirect or are dead (you run the tool) |
| 02 | Find all new pages and PDFs on the new website (you run the tool) |
| 03 | Update the link lists in code. All links in `config.py` only |
| 04 | Study the new website: header, footer, menus, cards, tables (you run the tool) |
| 05 | Update the scraper for React |
| 06 | Cleaning rules: no header, footer or repeated blocks. Fix keyword search words |
| 07 | Small test scrape of about 15 pages into a test folder (you run it) |
| 08 | Full scrape and rebuild, then a data quality report (you run it) |
| 09 | Make the test path the same as the student path, and record time and cost |
| 10 | Fix wrong-answer bugs: chitchat, keywords, routing, cache, line breaks |
| 11 | Fix speed and cost |
| 12 | Fix many-users problems and clean the code |
| 12b | Daily refresh: find new pages, re-check empty pages, remove deleted pages |

Part A is done when: all links are working, the data report shows no header, footer or repeated garbage, and tests use the same path as students.

The document extraction workflow (scrape, clean, cut, store) is tested inside specs 07 and 08, because it must pass before we rebuild.

---

## 4. Part B — Test data

**Spec 13 — Question sets for fixing.** We use these while we fix the chatbot.
- About 100 routing questions, each with the right agent.
- About 60 search questions, each with the right page and a fact it must contain.
- 10 short chats (3–4 turns each).
- About 20 tricky messages for safety.

**Spec 14 — The 100-question final exam.** Kept hidden. We never fix the chatbot using these.
- Written from the NEW website, with the right answer and source page for each.
- Mix: admissions, fees, faculty, policy, campus, general, out-of-scope, Roman Urdu, two-part questions.
- Claude drafts, you review. Then CLAUDE.md is updated (70 → 100).

Why two sets: if we fix the bot using the same questions we score it with, the score goes up but real students do not get better answers.

---

## 5. Part C — Component tests

Targets below are starting numbers. Each spec will confirm them.

### Spec 15 — Prompt
- P: fill each of the 6 prompts with sample text. No crash, no `{` left over, the rules are inside.
- L: give fixed good documents. Does the answer use them? Does it say "not available" when the fact is missing? Does it avoid inventing?
- H: read 10 answers per agent.
- Target: no crash; answers stay true to the documents 90%+.

### Spec 16 — Query rewriter
Parts: question expansion, follow-up fixer, two-part splitter, campus rewrite.
- P: a list of questions with the expected result. A follow-up gets its topic added. "BS and MS programs" is not split. "When was UOLI started and who is the VC" is split into two.
- L: does the campus rewrite keep the same meaning?
- H: read 20 rewrites.
- Target: 100% on the known list.

### Spec 17 — Router (decides which agent answers)
- P: the 100 routing questions. A real question is never treated as chitchat. "syllabus" must not match "bus".
- L: check the AI router on the same questions.
- H: read every wrong route.
- Target: when keywords decide, 100% right. AI router 95%+.

### Spec 18 — Embedding model and vector database
- P: vectors have 3072 numbers. The same text gives the same vector. No empty or repeated chunks. A saved sentence finds itself.
- L: not needed.
- H: read 20 random chunks. They must be clean, with no header or footer text.
- Target: 0 empty chunks, 0 repeated chunks.

### Spec 19 — Retriever (finds the documents)
- P: on the 60 search questions, is the right page in the top 5? How high is it? How many questions found nothing?
- L: AI marks if each found chunk is useful.
- H: read the misses.
- Target: right page in top 5 for 90%+; found-nothing = 0.

### Spec 20 — Reranker, context gate, parent swap
- P: with fixed documents, the right one is first, footer text is pushed down, 3 to 8 are kept, repeats are removed, an email is not lost in the swap.
- L: not needed.
- H: check 10 swaps.
- Target: right document in top 3 for 90%+.

### Spec 21 — Output parser (turns AI text into the final answer)
- P: line breaks, bullets and tables survive the stream. No `ANSWER:` word in the answer. `[DONE]` is always sent.
- L: not needed.
- H: look at the chat page.
- Target: the streamed text equals the original 100%.

### Spec 22 — Memory (sessions, summary, cache)
- P: save and load work. If Valkey is off, it still works. Same question gives a cache hit. A follow-up does not. Failure answers are not saved. Cache speed with 10, 100 and 1,000 entries.
- L: is the summary true to the chat?
- H: read 5 summaries.
- Target: 0 wrong cache hits.

### Spec 23 — Guardrails (refusals and safety checks)
- P: on the safety set, off-topic questions are refused. Real UOLI questions are never refused.
- L: does the document checker drop a useful document? Does the self-check catch an invented fact?
- H: read every refusal.
- Target: real questions refused = 0.

---

## 6. Part D — Workflow tests

We use the real student path (stream).

### Spec 24 — Question to answer (RAG workflow)
Runs: cache, router, search, rerank, answer. Done for each of the 6 agents.
- P: right agent, right documents, the answer has the fact it must contain, no empty answer.
- L: faithfulness and "did we find enough" on the search set.
- H: read the 10 worst.
- Target: fact present 90%+.

### Spec 25 — Supervisor and agents (two-part questions)
- P: on two-part questions, both parts get documents. Count wrong-agent cases.
- L: does the answer cover every part?
- H: read the two-part answers.
- Target: both parts covered 85%+.

### Spec 26 — Multi-turn chat
- P: on the 10 chats, follow-ups go to the right agent, nothing is wrongly refused, no wrong cache hit, history is saved.
- L: is the follow-up answer about the right person or topic?
- H: read all 10 chats.
- Target: 0 lost follow-ups.

### Spec 27 — Answer format and sources
- P: sources belong to this question. Lists and tables stay intact. A clear message shows when the AI fails.
- L: not needed.
- H: look at 10 answers in the browser.
- Target: wrong sources = 0.

---

## 7. Part E — System tests

Whole chatbot, real student path, hidden 100 questions.

### Spec 28 — Quality (is the answer right?)
- P: exact facts (numbers, emails, names) match the right answer.
- L: RAGAS (faithfulness, answer relevancy, context precision, context recall) and DeepEval (correctness, invented facts).
- H: read the 10 worst and 10 random answers.
- Target: faithfulness 0.85+, relevancy 0.80+, precision 0.75+, recall 0.80+.

### Spec 29 — Safety (can it cause harm?)
- P: off-topic is refused, no invented contacts, no leaking of the system prompt.
- L: AI checks for toxic, biased or tricked answers.
- H: read every safety answer.
- Target: 0 invented contacts, 0 tricks that work.

### Spec 30 — Operational (fast, cheap, stays up)
- P: time to first word and time to full answer (average, and the slow end: 95 in 100 are faster than this). AI calls and money per question. Load test with 1, 5, 10 and 20 students at once. Failure tests: Valkey off, OpenAI slow, empty database.
- L: not needed.
- H: feel the speed in the browser.
- Target: first word under 3 seconds, full answer under 8 seconds (at the slow end). No mixed answers between students.

### Spec 31 — Final review
- P: not needed.
- L: a second judge (DeepEval) agrees with the first (RAGAS).
- H: you mark 30 answers as right, partly right or wrong, and we compare with the scores.
- Target: you and the AI judges agree on most answers.

Notes:
- Run the speed test with the cache off, then on, to see the gain.
- Run speed tests 2–3 times. Internet speed changes during the day.
- Change one thing at a time, then measure.

---

## 8. Rules for every spec

1. One spec, one session, one branch (`feature/NN-name`).
2. Specs are short, in easy words, with a "What you do" list.
3. Claude gives every command you must run, right in the chat.
4. Claude never runs scraping, rebuild, the server, or any eval or AI judge. You run them.
5. Each spec says if it needs git pull, push and a PR.
6. Never fix the chatbot using the hidden 100 questions.
7. After each spec: check against its acceptance list, merge, then the next spec.
8. Keep a simple log: date, what changed, scores, times, cost.

---

## 9. Where we are (21 October 2026)

- 00 Backup of `data/`: done.
- 01 URL health check: done, merged.
- 02 Find new pages and PDFs: done, merged.
- 02b Find hidden pages (extra spec, click crawl): done, merged.
- 03 Update link lists in code: done, merged.
- 04 Study the new website structure: done, merged.
- 05 Update the scraper for React: next.
- 06 onward: waiting.

Full status, rules and findings: `.claude/PROJECT_STATUS.md`.
