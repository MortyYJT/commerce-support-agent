# Contributing

These rules apply to everyone who changes this repository, humans and AI agents alike. They are written once, here.

## Language

Write source comments, docstrings, the README, contribution guides, and engineering documentation in English. Chinese planning and learning records belong in note/.

Keep Chinese text when it is runtime or test data: chat interface copy, brand names, customer-service prompts, public error messages, and test inputs or outputs. Do not translate those strings as part of documentation cleanup.

## Branches

- Never commit directly to `main`.
- Branch from an up-to-date `main` as `<owner>/<topic>`, where `<owner>` is the agent that did the work (`claude/`, `codex/`, `dsh/`, …) or `me/` for your own. New agents take their own prefix; do not reuse another agent's. Use short kebab-case topics, e.g. `claude/add-login-form`. Existing `codex/` branches are kept as history.
- Never force-push. Never rewrite history that has been pushed.

## Commits

Follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/). The standard permits an omitted scope and an omitted body. This repository adds two mandatory requirements: every commit must include a scope and a non-empty English body.

Use this subject format: `type(scope): imperative summary`.

~~~text
type(scope): imperative summary

- English bullet describing what changed, why it changed, and how it was verified
- Another bullet if needed
~~~

Example:

~~~text
feat(ui): add the streaming customer chat page

- Add a responsive chat interface that renders server events progressively.
- Preserve completed turns so follow-up questions include prior context.
- Verify the stream and interaction flow in the browser.
~~~

- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`, `perf`. Use `feat(ui)` or `fix(ui)` for UI behavior or visual changes. Reserve `style` for formatting changes that do not change behavior or product appearance.
- One logical change per commit. Every body item starts with `- `.
- Inspect the staged changes and run `git diff --cached --check` before committing.
- Keep ignored `.env` files and `.superpowers/` scratch files out of commits.

## Pull requests

- Open one PR per branch into `main`. Fill in the PR template.
- Keep PRs reviewable: one topic, with verification evidence in the description.
- CI must pass. Merge with squash and rewrite the squash message to follow the commit format above (scope, English body with `- ` items). Claude merges once CI is green; auto-merge stays off.

## Checks

Run `bash .claude/check.sh` before opening a PR. It matches the CI `offline` job.

## Never commit

Secrets, `.env` files, credentials, private contact details, personal real names, or paths that identify a local machine.
