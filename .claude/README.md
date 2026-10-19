# .claude/ — AI-Assisted Development Workspace

This folder contains all planning and specification documents
for the UOLI University AI Chatbot project.
It is version-controlled so the full thinking behind every
feature is preserved alongside the code.

---

## Folder Structure

.claude/
├── specs/ # One spec per feature — the WHAT and WHY
├── plans/ # One plan per spec — the HOW (steps for implementation)
└── README.md # This file


---

## Workflow

Each feature follows this exact session flow:

| Step | Session | Model | Output saved to |
|------|---------|-------|-----------------|
| 1. Write spec | Spec session | Opus | `.claude/specs/NN_feature_name.md` |
| 2. Write plan | Plan session | Opus | `.claude/plans/NN_feature_name.md` |
| 3. Implement | Code session | Sonnet | `tools/`, `tests/` |

---

## Git Workflow Per Feature

git pull origin main
git checkout -b feature/NN-feature-name

implement, test, get approval

git push origin feature/NN-feature-name

open Pull Request → review → merge → delete branch

---

## File Naming Convention

Spec and plan files are numbered to match:

.claude/specs/01_url_health_check.md ←→ .claude/plans/01_url_health_check.md
.claude/specs/02_link_discovery.md ←→ .claude/plans/02_link_discovery.md


---

## What is NOT in git

Generated output files (CSV reports, markdown summaries) are
gitignored. Only source code and planning documents are committed.
See `.gitignore` for full details.