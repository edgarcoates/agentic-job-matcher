"""Run context shared by all agents: configuration, the model layer and the tracer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .history import History
from .llm import LLM
from .trace import Tracer


@dataclass
class Context:
    llm: LLM
    tracer: Tracer
    profile: dict
    thesis: dict
    settings: dict
    today: date
    postings_dir: Path
    history: History | None = None  # memory across live runs; None in practice/eval runs

    @property
    def is_practice(self) -> bool:
        return self.settings.get("source") == "files" and self.postings_dir.name == "practice"

    @property
    def practice_note(self) -> str:
        """Appended to judging prompts when running on the fictional sample postings."""
        if not self.is_practice:
            return ""
        return (
            "\n\nThis run uses practice postings: employers marked \"(fictional)\" and careers.example.com links "
            "are expected. Do not count them against credibility; judge each posting as if those details were real."
        )

    @property
    def clock(self) -> str:
        """One shared date for every agent. Without it, a model falls back on its own idea of today, and agents
        disagree about which postings are fresh or closed."""
        return f"Today is {self.today.isoformat()}: the run date. Every check in this run uses it; so must you.\n\n"

    def model(self, agent: str) -> str:
        return self.settings["models"][agent]

    @property
    def profile_yaml(self) -> str:
        return yaml.safe_dump(self.profile, sort_keys=False, allow_unicode=True)

    @property
    def thesis_yaml(self) -> str:
        return yaml.safe_dump(self.thesis, sort_keys=False, allow_unicode=True)
