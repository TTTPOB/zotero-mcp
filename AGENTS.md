# Project Instructions

## Version Bumps

- The single hand-maintained project version is `src/zotero_mcp/__init__.py::__version__`.
- For a release, update only that value, run `uv lock`, and commit the resulting `uv.lock` update.
- Use the matching Git tag format `v<version>`; do not hand-edit the generated version in `uv.lock` or `pyproject.toml`.
- Verify with `uv lock --check`, tests, and the tag version workflow before publishing.

## Architecture Decision Records

- Record every architecture or interface decision as an ADR in `docs/adr/NNNN-short-title.md`, numbered sequentially. This includes decisions to defer or reject a change. Existing record: `docs/adr/0001-defer-dsh-compatible-output-schema.md`.
- Each ADR states its status, date, and decider: `人类` (the human maintainer) or `Agent`. Also record the context, the decision, the rationale, the options considered, and the conditions that would trigger re-evaluation.
- A human decision may be reversed or changed only with explicit human authorization. An agent must not reverse one on its own, even when circumstances change; it should report the change and ask.
- A human may reverse an agent decision at any time. An agent may also reverse an agent decision when circumstances have materially changed; it must write a new ADR that supersedes the old one, explain what changed, and mark the old ADR as superseded.
