"""Memory across live runs: which roles were already reviewed (and what was decided), and when each lane was last
searched. Stored as JSON on the candidate's machine (runs/history.json by default), not committed."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .schemas import Decision, JobPosting


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def employer_key(employer: str) -> str:
    """First word of the employer name, so 'BNP Paribas (CIB)' and 'BNP Paribas' match."""
    words = re.findall(r"[a-z0-9]+", employer.lower())
    return words[0] if words else ""


class History:
    def __init__(self, path: Path, data: dict | None = None) -> None:
        self.path = path
        self.data = data or {"reviewed": [], "lanes": {}}

    @classmethod
    def load(cls, path: Path) -> "History":
        if path.exists():
            return cls(path, json.loads(path.read_text(encoding="utf-8")))
        return cls(path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def lane_last_searched(self) -> dict[str, str]:
        return dict(self.data.get("lanes", {}))

    def find(self, posting: JobPosting) -> dict | None:
        """An earlier review of the same role: same URL, or same employer and title."""
        url, key, title = _norm(posting.url or ""), employer_key(posting.employer), _norm(posting.title)
        for rec in self.data.get("reviewed", []):
            if url and _norm(rec.get("url") or "") == url:
                return rec
            if key and key == employer_key(rec["employer"]) and title == _norm(rec["title"]):
                return rec
        return None

    def record_run(self, run_id: str, today: date, decisions: list[Decision], postings: dict[str, JobPosting],
                   lanes: list[str]) -> None:
        for d in decisions:
            p = postings.get(d.posting_id)
            self.data.setdefault("reviewed", []).append({
                "employer": d.employer, "title": d.title, "url": p.url if p else None, "lane": d.lane,
                "verdict": d.verdict, "tier": d.tier, "run_id": run_id, "date": today.isoformat(),
            })
        for lane in lanes:
            self.data.setdefault("lanes", {})[lane] = today.isoformat()
