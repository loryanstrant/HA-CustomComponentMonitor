# Agent instructions

<!-- navgen:start-here:begin -->
## Start here (generated)

1. Read [NAVIGATION.md](NAVIGATION.md) - the repository map: docs, headings,
   entry points, public symbols, operations.
2. Use [`.agent/navigation.json`](.agent/navigation.json) when structured paths
   are easier to consume than prose.
3. Use the Gitea MCP server for live state: issues, pull requests, CI results.

Do not hand-edit `NAVIGATION.md` or `.agent/navigation.json`; they are generated
by navgen (`tools/navgen/navgen repo` in dev-standards) and refreshed nightly.
<!-- navgen:start-here:end -->

## Validation

- Build: none. The integration is plain Python and the four bundled cards in
  `custom_components/custom_component_monitor/www/` are plain JS with no build
  step, no bundler and no `package.json`.
- Test: `pip install -r requirements_test.txt && pytest` for the integration,
  then **by hand** for the cards —
  `for f in custom_components/custom_component_monitor/www/*.js; do node --check "$f"; done`
  and `for t in tests/*.cjs; do node "$t"; done`.
- Lint: none configured.

### The card tests are not in CI, and cannot be put there from here

CI and releases for this repo run on the **GitHub** mirror; Gitea Actions is
switched off for it. The mirror is a one-way push authenticated by a token
without `workflow` scope, so GitHub refuses any push whose commits touch
`.github/workflows/*` — **do not edit `.github/workflows/validate.yaml` or
`release.yaml` from a session.** One such commit blocks the mirror for every
later push, including release tags, and reverting it does not clear the block
because the check is per commit. Run the card commands above before tagging a
release instead. See the 2026-11-12 entry in [DECISIONS.md](DECISIONS.md).
