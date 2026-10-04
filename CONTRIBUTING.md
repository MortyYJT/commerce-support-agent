# Contributing

## Language

Write source comments, docstrings, the README, contribution guides, and engineering documentation in English. Chinese planning and learning records belong in note/.

Keep Chinese text when it is runtime or test data: chat interface copy, brand names, customer-service prompts, public error messages, and test inputs or outputs. Do not translate those strings as part of documentation cleanup.

## Commit messages

Follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/). The standard permits an omitted scope and an omitted body. This repository adds two mandatory requirements: every commit must include a scope and a non-empty English body.

Use this subject format: `type(scope): imperative summary`.

~~~text
type(scope): imperative summary
~~~

The summary must be an English imperative phrase. Put a blank line after the subject, then write a non-empty English body. Every body item must start with `- `. Explain what changed, why it changed, and how it was verified. Use one or more bullets.

~~~text
feat(ui): add the streaming customer chat page

- Add a responsive chat interface that renders server events progressively.
- Preserve completed turns so follow-up questions include prior context.
- Verify the stream and interaction flow in the browser.
~~~

Use `feat(ui)` or `fix(ui)` for UI behavior or visual changes. Reserve `style` for formatting changes that do not change behavior or product appearance.

## Git and pull requests

Work on a codex/ branch and open a pull request into main. The user merges pull requests manually; do not enable automatic merging or push directly to main.

Do not amend, rebase, force-push, or otherwise rewrite already-pushed history. Keep ignored `.env` files and `.superpowers/` scratch files out of commits. Before committing, inspect the staged changes and run `git diff --cached --check`.
