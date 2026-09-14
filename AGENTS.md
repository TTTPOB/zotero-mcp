# Project Instructions

## Version Bumps

- The single hand-maintained project version is `src/zotero_mcp/__init__.py::__version__`.
- For a release, update only that value, run `uv lock`, and commit the resulting `uv.lock` update.
- Use the matching Git tag format `v<version>`; do not hand-edit the generated version in `uv.lock` or `pyproject.toml`.
- Verify with `uv lock --check`, tests, and the tag version workflow before publishing.
