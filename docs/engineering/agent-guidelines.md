# Detailed agent guidelines

This document expands the root `AGENTS.md`. Read only the sections listed there for the task at hand; do not load unrelated sections for routine changes. If instructions conflict, follow higher-priority instructions and the user's explicit scope.

## Judgment and evidence

- Before acting, check for false premises, logical gaps, and missing information. Resolve what you can from reliable sources; ask only when the missing answer materially affects correctness, scope, or an irreversible choice. Continue independent work while waiting.
- Exercise independent judgment. Distinguish verified facts, inferences, predictions, and subjective preferences when the distinction matters. Do not agree merely to please the user.
- Verify material claims about numbers, people, and outcomes against primary sources where practical. Prefer current source code, tests, logs, and live pages over README claims, memory, or search snippets. State uncertainty and verification limits. Never invent metrics, credentials, experience, or results.
- If a user premise is wrong, say so directly and respectfully: give the evidence, the practical risk, and a better interpretation. Call out overlooked variables, costs, tradeoffs, and biases that could change the decision.

## Identity and privacy

- Identify the owner only by their public handle (`MortyYJT` / `谷鱼Y`). Do not put the owner's real name in repository files, UI, metadata, assets, filenames, sample data, docs, commit messages, or author/committer fields.
- Use a repository-local Git identity with the public handle and the verified GitHub noreply address. Check author/committer attribution before publishing. Do not change global Git settings, and do not rewrite pushed history to repair attribution.
- Review imported resumes, screenshots, documents, and generated artifacts for identity leaks before committing them.
- Never commit secrets, `.env` files, API keys, tokens, private contact details (phone, personal email, address), or local filesystem paths that reveal who or where the owner is. Never print a token in output.
- Use public profile details only within the approved scope.

## Communication and documentation

- Talk to the user in Chinese. Keep this file, the README, engineering docs, source comments, and docstrings in English. Put Chinese planning and learning records in `note/`. Preserve intended Chinese UI copy.
- Record meaningful decisions, milestone evidence, and unresolved issues in `note/` as work progresses. Keep notes concise and free of private identity details.
- Delivery report, in this order: (1) 你要做什么 - the single action required from the user, or "nothing"; (2) 我完成了什么 - one line per item with its evidence; (3) 我遇到什么问题 - what went wrong and how it was resolved; (4) 还没完成什么 - what is unfinished or unverified.
- Open every section with a one-line summary. Lead with the user's action. One idea per line. Report code, tests, CI, local run, and deployment separately. Write "not run" or "not verified" plainly.
- Put reasoning, tradeoffs, and history in `note/`, not in the report.
- **Handoff.** `note/handoff.md` is a single file holding the current state for the next session or the next agent (for example after `/clear`, or when work moves between Claude and Codex). Overwrite it; never append, because history is in git. It states: date, commit and branch; the goal; the OpenSpec change id; what is done, each with evidence; what is in progress, including uncommitted or unpushed state; ordered next steps; decisions and why; open questions for the user; how to run and verify; traps. Keep it under about 60 lines. It must not contain secrets, private contact details, or identifying local paths. Treat it as a claim to verify against git, not as truth.

## Scope and implementation

- **Product layer.** `note/product/vision.md` says what we build and what we do not. `roadmap.md` orders the work. `backlog.md` holds raw ideas. A new idea goes into the backlog first; it becomes work only when promoted.
- **OpenSpec.** Behavior changes go through OpenSpec: current-truth specs live in `openspec/specs/`, each proposed change in `openspec/changes/<id>/` (proposal, spec deltas, tasks), and finished changes are archived. Install and use the official CLI (`@fission-ai/openspec`); follow the commands it generates for this tool rather than older tutorials. Do not write a parallel spec elsewhere.
- **Superpowers.** Use it for execution quality: brainstorming when the idea is unclear, a plan only for large changes (reference the OpenSpec change id), TDD, code review, and verification-before-completion.
- Follow the agreed design, spec, plan, and review stages. Respect explicit exceptions the user grants; do not infer new ones, and do not add approval gates for routine, reversible, already-authorized work.
- Keep the first version focused. Prefer small, reusable pieces. Add APIs, persistence, authentication, services, or dependencies only for a concrete requirement, and ask first before adding a dependency. Confirm dependency versions and APIs against current documentation.
- Types do not replace runtime validation of untrusted input.
- Check licensing before importing third-party code or assets, and preserve required notices. Do not redistribute purchased or licensed course material in a public repository.

## Git and changes

- Inspect status, branch, HEAD, and remotes before changing repository state. Preserve unrelated user work.
- Use `<agent>/<topic>` branches (your agent's prefix, per CONTRIBUTING.md) and reviewable pull requests; the user merges into `main`. Bootstrapping a brand-new repository on `main` requires explicit authorization.
- Commit format, branch naming, and PR rules are in `CONTRIBUTING.md`. Run `git diff --cached --check` before committing.
- Treat committing, pushing, releasing, merging, and irreversible deletion as separate actions. Proceed when the session clearly authorizes the action; otherwise prepare a reviewable result and name the specific missing authorization. Do not ask for the same permission twice.

## Verification and deployment

- Verify behavior with checks appropriate to the change. Use meaningful tests for logic, data handling, and server behavior; avoid tests that merely restate the implementation. Validate visual changes in a real browser, including narrow and wide layouts, keyboard access, theme, and reduced motion where applicable.
- Run the project check command (lint, typecheck, relevant tests, production build) before declaring product code ready. Report skipped or unavailable checks and their practical limits. Document-only changes need content and diff checks, not application tests.
- Evidence over assertion: show the test output, the command run and what it returned, or a screenshot. "Looks done" is not a signal.
- Independent review for non-trivial changes: have a fresh-context subagent review the diff against the OpenSpec change or plan. Tell it to flag only gaps that affect correctness or the stated requirements; chasing every finding leads to over-engineering.
- Before changing any deployment state, confirm the account, project, repository, branch, and environment. A local build, a manual deploy, or a green CI run alone does not prove automatic deployment works; verify a real Git-triggered deployment and the resulting URL before claiming it is fixed.
- For destructive actions, confirm the exact target at the final step. Prefer the reversible action that satisfies the request.
