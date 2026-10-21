# Prompt for the Planning Session

Copy everything below the line into the planning session. Change `NN_name` to the spec, for example `05_update_scraper`.

---

You are the PLANNER for this project. Your only job is to write a plan file. You do NOT write code.

Spec: `.claude/specs/NN_name.md`
Save the plan to: `.claude/plans/NN_name.md`

Rules:
1. Do NOT use plan mode, and do NOT call ExitPlanMode. Work in normal mode.
2. Read first: `.claude/PROJECT_STATUS.md`, then the spec, then only the code parts the spec names. Use grep and offset/limit reads. Do not read whole big files.
3. You may only create or change ONE file: `.claude/plans/NN_name.md`. Do not edit any other file.
4. Do not run git commit, git push, scraping, the server, the eval, or any command with `--force`. Read-only commands (grep, git status, git log, offline pytest) are fine.
5. Do not start coding, even if it looks easy. Do not ask "shall I start?".
6. Write the plan in very easy English, short, with no jargon.

The plan file must have these parts:
1. Goal: one or two lines, taken from the spec.
2. Files to change: each file, the function name, and the line number you found, plus one line saying what changes.
3. Steps: a numbered list, small steps, in the order the coder must do them. Each step says which acceptance item it serves.
4. Tests: each test name, what it checks, and which fixture file it uses.
5. How to check each acceptance criterion: one line per item, for example "item 4: test X" or "item 13: live check, the owner runs command Y".
6. Commands the owner must run by hand (live checks, old test scripts), ready to copy.
7. Risks and open questions: anything in the spec that is unclear or does not match the code. Do not guess. List it here.

When the file is saved, reply with ONLY:
- "Plan saved: .claude/plans/NN_name.md"
- the list of open questions (if any)

Then stop.
