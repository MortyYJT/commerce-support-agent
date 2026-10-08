# Repository instructions

Applies to the whole repository. Higher-priority instructions and the user's explicit scope win over this file.
Keep this file short. Details live in [docs/engineering/agent-guidelines.md](docs/engineering/agent-guidelines.md) and are read on demand.

## Always apply

- Check premises, logical gaps, and missing information before acting. Judge independently; separate facts, inferences, predictions, and preferences. Verify numbers, people, and outcomes. Never invent results.
- Never commit secrets, `.env` files, private contact details, the owner's real name, or identifying local paths. Identify the owner only by their public handle (see the user-level rules).
- Talk to the user in Chinese. Write source comments, docstrings, and engineering docs in English. Chinese planning and learning records go in `note/`. Preserve Chinese runtime and test strings (UI copy, prompts, public errors, test examples).

## Workflow

Pick one track before writing anything, and say which one.

**Product / logic work** (data models, rules, backend, agents, tools, anything with a business rule):

```
OpenSpec change (what & why) -> plan -> TDD (red, then green) -> review -> verification -> archive the change
```

- Review: run it in a fresh-context subagent (e.g. `/code-review`). Report only gaps that affect correctness or the stated requirements, not style.
- Verification: run the project check command and show its output as evidence. Do not claim "done" without it.
- Prompt-only or data-only changes use a labeled evaluation instead of TDD.

**Pure interface work** (pages, styling; logic is NOT included):

```
design in chat -> build -> verify in a real browser -> screenshots
```

An explicit user exception (for example "Vibe coding" for the chat page) is followed as stated, without adding the brainstorm, TDD, or review gates the user waived.

Roles: OpenSpec owns *what* we build and the current-truth specs. Superpowers owns *how* we build it correctly (brainstorm, plan, TDD, review, verification). Do not write a second spec; the OpenSpec change is the spec.

## Git

- Follow [CONTRIBUTING.md](CONTRIBUTING.md) for branches, commits, and pull requests.
- Work on `claude/<topic>` branches and open a pull request into `main`. The user has authorized Claude to merge a pull request once CI is green, using squash. Never push to `main`, force-push, or rewrite pushed history. Never enable auto-merge.

## Session continuity

- At the start of a session, read `note/handoff.md` if it exists, and check it against `git status` and `git log` before trusting it.
- Before ending a session, or when the user asks for a handoff, overwrite `note/handoff.md` (format in the guidelines, "Communication and documentation").

## Read before the relevant task

Do not load the whole guidelines file for small edits. Read only the listed sections.

| Task | Sections in [agent guidelines](docs/engineering/agent-guidelines.md) |
| --- | --- |
| Resolving uncertainty, preparing factual content | Judgment and evidence |
| Importing content, assets, or profile data | Identity and privacy |
| Planning, implementing, or verifying a change | Scope and implementation; Verification and deployment |
| Git state, commits, pushes, pull requests | Git and changes; Identity and privacy |
| Deploying or releasing | Verification and deployment; Git and changes |
| Writing docs or reporting milestones | Communication and documentation |

## Project-specific

- **What this is:** A Python 3.11 FastAPI e-commerce customer-support demo that streams Chinese replies over SSE, stores chats in MySQL, and runs one of five bounded tools per user message. Stack is fixed (FastAPI, LangChain, SQLAlchemy, MySQL 8.4.11 in Docker); do not swap it.
- **Stack and commands:** install `python -m pip install -r requirements-dev.txt && python -m pip install --no-deps --no-build-isolation --editable .`, run `docker compose up -d --build`, check `bash .claude/check.sh` (same commands as the CI `offline` job).
- **Domain red lines:**
  - Never invent data; mark unverified items as 待核验.
  - Never print API keys or `.env` values.
  - Never truncate demo tables or delete the `mysql_data` volume; MySQL tests use only guarded `commerce_support_test_*` databases.
  - Current policy comes only from `src/commerce_support/resources/customer_support/v1/`; `legacy/original/` is audit-only.
  - Report separately: configured, check passed, verified on this machine, not verified remotely. Report automated checks and manual answer quality separately.
- **Ask first before:** adding a dependency, changing the database schema, touching deployment or CI, deleting files, running real-model calls (they cost money and add demo records). Look up library APIs in current official documentation (Context7 if configured; otherwise say which source was used).
