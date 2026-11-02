## Summary

<!-- What does this change and why? -->

## Checklist

- [ ] Commits are focused and follow the Angular convention (`type(scope): subject`)
- [ ] `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .` pass
- [ ] `uv run --frozen mypy` passes
- [ ] `uv run --frozen lint-imports` passes (core stays decoupled from interfaces)
- [ ] Relevant tests pass; behavioral changes have meaningful coverage
- [ ] Current guides and capability status reflect the implemented behavior
- [ ] UI layout changes were inspected against references at appropriate terminal sizes
