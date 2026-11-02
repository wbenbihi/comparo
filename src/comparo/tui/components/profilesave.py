"""Review and save an authored execution profile through the application boundary."""

import asyncio
from typing import ClassVar
from typing import Protocol
from typing import cast
from typing import override

from textual import on
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.screen import ModalScreen

from comparo.application.profile_authoring import ProfileAuthoringError
from comparo.application.profile_authoring import ProfileDocument
from comparo.tui.backend import TuiBackend
from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.diffview import DiffView
from comparo.tui.components.panel import Panel
from comparo.tui.components.typography import Caption
from comparo.tui.components.valueinput import ValueInput
from comparo.tui.models.diff import build_source_document
from comparo.tui.models.launch import LaunchDraft


class _BackendOwner(Protocol):
    backend: TuiBackend


class ProfileSave(ModalScreen[bool]):
    """Require a concrete YAML preview before enabling the exclusive file write."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ProfileSave { align: center middle; background: $background 80%; }
    ProfileSave > Panel { width: 100; max-width: 95%; height: 90%; }
    ProfileSave DiffView { height: 1fr; }
    ProfileSave Caption { height: auto; }
    """

    def __init__(self, draft: LaunchDraft) -> None:
        """Freeze the reviewed selection while authoring its identity."""
        super().__init__()
        self.draft = draft
        self.document: ProfileDocument | None = None
        self.busy = False
        self.generation = 0

    @override
    def compose(self) -> ComposeResult:
        yield Panel(
            Caption("Save a new ExecutionProfile · existing sources are never overwritten"),
            ValueInput("Profile ID", maximum_length=63, id="profile-id"),
            ValueInput("Name", maximum_length=120, id="profile-name"),
            Caption("Mutation approval stays session-only. Inherited defaults remain inherited."),
            DiffView(id="profile-yaml"),
            Caption(
                "Enter an ID and name, then preview the exact file.",
                id="profile-status",
                markup=False,
            ),
            ActionBar(
                (
                    Action("preview", "Preview YAML", primary=True),
                    Action("save", "Save profile", enabled=False),
                    Action("cancel", "Cancel"),
                ),
                id="profile-actions",
            ),
            title="SAVE EXECUTION PROFILE",
        )

    @on(ValueInput.Changed)
    def _edit(self, event: ValueInput.Changed) -> None:
        event.stop()
        self.generation += 1
        self.document = None
        self.query_one(DiffView).document = None
        self._actions()

    def _actions(self) -> None:
        self.query_one(ActionBar).actions = (
            Action("preview", "Preview YAML", enabled=not self.busy),
            Action(
                "save",
                "Save profile",
                primary=True,
                enabled=self.document is not None and not self.busy,
            ),
            Action("cancel", "Cancel", enabled=not self.busy),
        )

    @on(ActionBar.Requested)
    def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if self.busy:
            return
        if event.action == "cancel":
            self.action_cancel()
        else:
            self._perform(event.action)

    @work(exclusive=True, group="profile-save")
    async def _perform(self, action: str) -> None:
        backend = cast(_BackendOwner, self.app).backend
        self.busy = True
        self._actions()
        generation = self.generation
        try:
            if action == "preview":
                document = await asyncio.to_thread(
                    backend.prepare_profile,
                    self.draft.options(),
                    self.query_one("#profile-id", ValueInput).value,
                    self.query_one("#profile-name", ValueInput).value,
                )
                if generation != self.generation or not self.app.is_running:
                    return
                self.document = document
                self.query_one(DiffView).document = build_source_document(document.content)
                self.query_one("#profile-status", Caption).update(
                    f"New file: {document.destination}"
                )
            elif self.document is not None:
                await asyncio.to_thread(backend.save_profile, self.document)
                if self.app.is_running:
                    self.dismiss(True)
        except ProfileAuthoringError as error:
            self.query_one("#profile-status", Caption).update(str(error))
        except FileExistsError:
            self.query_one("#profile-status", Caption).update(
                "That file already exists. Choose another ID."
            )
        except Exception:  # noqa: BLE001 - filesystem and source exceptions stay private
            self.query_one("#profile-status", Caption).update(
                "Could not save the profile. Check the project and destination."
            )
        finally:
            self.busy = False
            if self.is_mounted and self.app.is_running:
                self._actions()

    def action_cancel(self) -> None:
        """Close without writing; an in-flight save must finish before dismissing."""
        if not self.busy:
            self.dismiss(False)
