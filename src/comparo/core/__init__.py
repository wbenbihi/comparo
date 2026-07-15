"""The comparo engine.

This package is the architectural core: loading, resolution, matrix expansion,
execution, diffing, and gating. It defines *ports* (abstract protocols) that
adapters implement, and it must not import from ``comparo.cli``,
``comparo.tui``, or ``comparo.adapters`` — the dependency arrow points inward
only. The rule is enforced in CI by import-linter.
"""
