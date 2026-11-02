"""Semantic progress through the four launch stages."""

from rich.text import Text

from comparo.tui.theme import style


def launch_steps(active: int) -> Text:
    """Render completed, active, and pending stages with established status colors."""
    steps = Text()
    for index, label in enumerate(("Target", "Plan", "Run", "Analyze")):
        if index:
            steps.append("   →   ", style("dim"))
        steps.append(
            ("✓ " if index < active else "● " if index == active else "· ") + label,
            style(
                "status.passed"
                if index < active
                else "status.running"
                if index == active
                else "dim"
            ),
        )
    return steps
