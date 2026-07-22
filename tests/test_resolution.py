"""Tests for the unified resolution engine — the value directives everywhere."""

from pathlib import Path

import pytest

from comparo.core.provenance import Origin
from comparo.core.resolution import Context
from comparo.core.resolution import InterpolationError
from comparo.core.resolution import SecretError
from comparo.core.resolution import resolve_value


def _ctx(
    *,
    execute: bool = False,
    variables: dict[str, str] | None = None,
    secret_names: frozenset[str] = frozenset(),
    secret_values: dict[str, str] | None = None,
    instances: dict[str, object] | None = None,
    root: Path | None = None,
) -> Context:
    store = instances or {}
    return Context(
        variables=variables or {},
        secret_names=secret_names,
        mask_secrets=not execute,
        secret_values=secret_values or {},
        instances=store.get,
        root=root,
    )


# ── $var ────────────────────────────────────────────────────────────────────


def test_var_resolves_a_variable() -> None:
    val, trail = resolve_value({"$var": "HOST"}, _ctx(variables={"HOST": "api.example.com"}))
    assert val == "api.example.com"
    assert trail[0].origin is Origin.VARIABLE
    assert not trail[0].tainted


def test_var_is_secret_first_masked_in_display_real_in_execute() -> None:
    names = frozenset({"TOKEN"})
    masked, _ = resolve_value({"$var": "TOKEN"}, _ctx(secret_names=names))
    assert masked == "••••••"
    real, trail = resolve_value(
        {"$var": "TOKEN"},
        _ctx(execute=True, secret_names=names, secret_values={"TOKEN": "sk-real"}),
    )
    assert real == "sk-real"
    assert trail[0].origin is Origin.SECRET  # masking keyed off secrets:
    assert trail[0].tainted


def test_var_missing_name_raises() -> None:
    with pytest.raises(InterpolationError, match="required variable 'NOPE'"):
        resolve_value({"$var": "NOPE"}, _ctx())


# ── inline $env / $file resolve real values everywhere ──────────────────────


def test_inline_env_resolves_the_real_value_in_both_sinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPARO_INLINE_ENV", "port-9000")
    for execute in (False, True):
        val, trail = resolve_value({"$env": "COMPARO_INLINE_ENV"}, _ctx(execute=execute))
        assert val == "port-9000"  # real value, not the mask — masking is not the directive's job
        assert trail[0].origin is Origin.ENV
        assert not trail[0].tainted  # the redactor floor masks it iff it is a declared secret


def test_inline_env_unset_degrades_in_display_but_fails_in_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COMPARO_UNSET_INLINE", raising=False)
    shown, _ = resolve_value({"$env": "COMPARO_UNSET_INLINE"}, _ctx())
    assert shown == ""  # a preview must not crash on an unset var
    with pytest.raises(SecretError):
        resolve_value({"$env": "COMPARO_UNSET_INLINE"}, _ctx(execute=True))


def test_inline_file_resolves_within_root(tmp_path: Path) -> None:
    (tmp_path / "tok.txt").write_text("file-value\n", encoding="utf-8")
    val, trail = resolve_value({"$file": "tok.txt"}, _ctx(execute=True, root=tmp_path))
    assert val == "file-value"  # stripped
    assert trail[0].origin is Origin.FILE
    assert not trail[0].tainted


def test_inline_file_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("leak", encoding="utf-8")
    with pytest.raises(SecretError, match="escapes"):
        resolve_value({"$file": "../outside.txt"}, _ctx(execute=True, root=root))


# ── inline $from ────────────────────────────────────────────────────────────


def test_inline_from_falls_back_to_the_first_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPARO_MISS_1", raising=False)
    monkeypatch.setenv("COMPARO_HIT_2", "hit")
    val, _ = resolve_value(
        {"$from": [{"$env": "COMPARO_MISS_1"}, {"$env": "COMPARO_HIT_2"}]}, _ctx(execute=True)
    )
    assert val == "hit"


def test_from_trail_never_dumps_a_literal_fallback_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A $from list can carry a $literal secret fallback; the provenance detail must
    # never render the list, or the fallback secret would leak into the trail UI.
    monkeypatch.delenv("COMPARO_MISS_3", raising=False)
    secret = "s3cr3t-fallback"
    val, trail = resolve_value(
        {"$from": [{"$env": "COMPARO_MISS_3"}, {"$literal": secret}]}, _ctx(execute=True)
    )
    assert val == secret  # it resolved
    assert all(secret not in (t.detail or "") for t in trail)  # but never in the trail
    assert trail[0].detail == "$from"


# ── $literal / $val preserved ───────────────────────────────────────────────


def test_literal_passes_through_verbatim_without_resolving() -> None:
    # $literal is the interpolation escape hatch: a reference-shaped dict is data.
    body = {"$use": "diff.nope", "keep": "${NOT_INTERPOLATED}"}
    val, trail = resolve_value({"$literal": body}, _ctx())
    assert val == body  # untouched
    assert trail == []  # no trail for a literal


def test_val_cycle_is_detected() -> None:
    ctx = _ctx(instances={"a": {"$val": "b"}, "b": {"$val": "a"}})
    with pytest.raises(InterpolationError, match="cycle"):
        resolve_value({"$val": "a"}, ctx)


def test_val_shares_one_cycle_guard_across_a_nested_tree() -> None:
    # A single engine guards the whole walk, so a $val reached twice on separate
    # branches is fine, but a genuine loop is caught wherever it closes.
    ctx = _ctx(instances={"leaf": "L", "self": {"loop": {"$val": "self"}}})
    val, _ = resolve_value([{"$val": "leaf"}, {"$val": "leaf"}], ctx)
    assert val == ["L", "L"]  # same instance twice, no false cycle
    with pytest.raises(InterpolationError, match="cycle"):
        resolve_value({"$val": "self"}, ctx)


def test_unknown_sigil_passes_through_untouched() -> None:
    val, trail = resolve_value({"$mystery": "x"}, _ctx())
    assert val == {"$mystery": "x"}
    assert trail == []


# ── declaration-driven masking (the redactor floor, not the directive) ──────


def test_inline_env_value_that_is_a_declared_secret_is_masked_by_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An inline $env resolves its real value; it is masked ONLY because that value
    # is also a declared secret, caught by the redactor's value-keyed floor.
    from comparo.core.redaction import Redactor

    monkeypatch.setenv("COMPARO_SHARED", "the-real-secret")
    # the same value is declared as a project secret, so the floor collects it
    redact = Redactor(values=("the-real-secret",)).text
    val, _ = resolve_value({"$env": "COMPARO_SHARED"}, _ctx(execute=True))
    assert val == "the-real-secret"  # the engine resolves real
    assert "the-real-secret" not in redact(str(val))  # the floor masks it
