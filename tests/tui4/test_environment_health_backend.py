"""Real Environment health execution through the TUI4 backend boundary."""

import asyncio
import shutil
from pathlib import Path

import msgspec
import pytest

from comparo.application import CompareExecutionControls
from comparo.application import ComparePreparationResult
from comparo.tui4.backend import TuiBackend
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.resource_presenters import environment_health_check_targets

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


async def _respond(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    status: int,
) -> None:
    """Serve one bounded JSON response for a loopback health Request."""
    await reader.readuntil(b"\r\n\r\n")
    body = b'{"status":"ok"}'
    reason = b"OK" if status == 200 else b"Service Unavailable"
    writer.write(
        b"HTTP/1.1 "
        + str(status).encode("ascii")
        + b" "
        + reason
        + b"\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode("ascii")
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize(
    ("status", "expected_healthy", "minimum_adverse", "expected_gate"),
    [(200, True, 0, "passed"), (503, False, 1, "failed")],
)
def test_health_backend_executes_assertions_and_persists_reportable_run(
    tmp_path: Path,
    status: int,
    expected_healthy: bool,
    minimum_adverse: int,
    expected_gate: str,
) -> None:
    """Health uses normal HTTP, assertion, gate, artifact, and RunService paths."""

    async def scenario() -> None:
        server = await asyncio.start_server(
            lambda reader, writer: _respond(reader, writer, status),
            host="127.0.0.1",
            port=0,
        )
        sockets = server.sockets
        assert sockets
        port = int(sockets[0].getsockname()[1])
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        environments = project / ".comparo" / "environments.yaml"
        environments.write_text(
            environments.read_text(encoding="utf-8").replace("18080", str(port)),
            encoding="utf-8",
        )

        backend = TuiBackend(project)
        try:
            catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
            assert catalog is not None
            environment = next(
                entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
            )
            targets = environment_health_check_targets(environment)

            batch = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                targets,
            )

            assert batch.diagnostics == ()
            assert batch.value is not None
            assert batch.value.request_count == len(targets) == 1
            assert batch.value.completion == "complete"
            assert batch.value.gate == expected_gate
            assert batch.value.healthy is expected_healthy
            assert batch.value.adverse_facts >= minimum_adverse
            assert backend.runs.count == 1
            assert backend.artifact_root is not None
            assert any(backend.artifact_root.iterdir())
        finally:
            await backend.shutdown()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_concurrent_first_health_runs_share_backend_initialization_safely(
    tmp_path: Path,
) -> None:
    """Different Environments may start together without racing root or preparation state."""

    async def scenario() -> None:
        server = await asyncio.start_server(
            lambda reader, writer: _respond(reader, writer, 200),
            host="127.0.0.1",
            port=0,
        )
        sockets = server.sockets
        assert sockets
        port = int(sockets[0].getsockname()[1])
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        environments = project / ".comparo" / "environments.yaml"
        environments.write_text(
            environments.read_text(encoding="utf-8").replace("18080", str(port)),
            encoding="utf-8",
        )
        backend = TuiBackend(project)
        try:
            catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
            assert catalog is not None
            selected = tuple(
                entry
                for entry in catalog.entries.values()
                if entry.summary.coordinate.kind == "Environment"
                and entry.facts.role in {"baseline", "candidate"}
            )
            assert len(selected) == 2

            batches = await asyncio.gather(
                *(
                    backend.run_environment_health_checks(
                        entry.summary.coordinate,
                        environment_health_check_targets(entry),
                    )
                    for entry in selected
                )
            )

            assert all(batch.diagnostics == () for batch in batches)
            assert all(batch.value is not None and batch.value.healthy for batch in batches)
            assert backend.runs.count == 2
            assert backend.artifact_root is not None
        finally:
            await backend.shutdown()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_health_backend_rejects_stale_environment_revision_before_execution(
    tmp_path: Path,
) -> None:
    """A highlighted revision cannot authorize a newly changed Environment definition."""

    async def scenario() -> None:
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        backend = TuiBackend(project)
        try:
            catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
            assert catalog is not None
            environment = next(
                entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
            )
            targets = environment_health_check_targets(environment)
            source = project / ".comparo" / "environments.yaml"
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "environment: baseline",
                    "environment: changed",
                ),
                encoding="utf-8",
            )

            batch = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                targets,
            )

            assert batch.value is None
            assert batch.diagnostics == ("health-check.revision.changed",)
            assert backend.runs.count == 0
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_health_backend_rejects_stale_request_revision_before_execution(
    tmp_path: Path,
) -> None:
    """Every health subject is revision-pinned, independently of the Environment."""

    async def scenario() -> None:
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        backend = TuiBackend(project)
        try:
            catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
            assert catalog is not None
            environment = next(
                entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
            )
            targets = environment_health_check_targets(environment)
            assert targets
            forged = msgspec.structs.replace(
                targets[0],
                fingerprint=("0" * 64 if targets[0].fingerprint != "0" * 64 else "1" * 64),
            )

            batch = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                (forged, *targets[1:]),
            )

            assert batch.value is None
            assert batch.diagnostics == ("health-check.revision.changed",)
            assert backend.runs.count == 0
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_health_backend_rejects_duplicate_or_untyped_probe_selections(tmp_path: Path) -> None:
    """The backend boundary remains strict even when bypassing the configuration compiler."""

    async def scenario() -> None:
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        backend = TuiBackend(project)
        try:
            catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
            assert catalog is not None
            environment = next(
                entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
            )
            target = environment_health_check_targets(environment)[0]

            duplicate = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                (target, target),
            )
            wrong_kind = msgspec.structs.replace(target, kind="Scenario")
            invalid = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                (wrong_kind,),
            )
            mutable = await backend.run_environment_health_checks(
                environment.summary.coordinate,
                [target],  # type: ignore[arg-type]
            )
            wrong_environment = await backend.run_environment_health_checks(
                msgspec.structs.replace(environment.summary.coordinate, kind="Request"),
                (target,),
            )

            assert duplicate.diagnostics == ("health-check.selection.invalid",)
            assert invalid.diagnostics == ("health-check.selection.invalid",)
            assert mutable.diagnostics == ("health-check.selection.invalid",)
            assert wrong_environment.diagnostics == ("health-check.selection.invalid",)
            assert backend.runs.count == 0
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_cancelled_health_preparation_is_joined_and_discarded_before_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation cannot orphan a preparation thread past backend shutdown."""

    async def scenario() -> None:
        project = tmp_path / "project"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
        backend = TuiBackend(project)
        catalog = build_catalog(await asyncio.to_thread(backend.load_workspace))
        assert catalog is not None
        environment = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        targets = environment_health_check_targets(environment)
        started = asyncio.Event()
        release = asyncio.Event()
        original = backend._prepare_health_context

        def delayed_prepare(
            environment_id: str,
            request_ids: tuple[str, ...],
        ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
            backend_loop.call_soon_threadsafe(started.set)
            future = asyncio.run_coroutine_threadsafe(release.wait(), backend_loop)
            assert future.result(timeout=3)
            return original(environment_id, request_ids)

        backend_loop = asyncio.get_running_loop()
        monkeypatch.setattr(backend, "_prepare_health_context", delayed_prepare)
        health = asyncio.create_task(
            backend.run_environment_health_checks(environment.summary.coordinate, targets)
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        health.cancel()
        await asyncio.sleep(0)
        shutdown = asyncio.create_task(backend.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        health.cancel()
        await asyncio.sleep(0)
        assert not health.done()
        assert not shutdown.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await health
        await shutdown
        assert backend.compare.closed
        assert backend.runs.count == 0

    asyncio.run(scenario())
