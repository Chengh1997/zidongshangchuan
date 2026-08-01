from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    display_name: str


@dataclass(frozen=True)
class Chapter:
    code: str
    title: str
    parent_code: str | None
    allow_upload: bool
    disabled_upload: bool
    existing_pdf_link: str = ""
    replace_status: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class UploadItem:
    chapter_code: str
    chapter_title: str
    source_path: str = ""
    prepared_pdf: str = ""
    confidence: str = "unmatched"
    status: str = "pending"
    reason: str = ""
    verified_link: str = ""
    verified_status: str = ""
    action: str = "upload"
    chapter_submitted: bool = False
    completion_evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    run_id: str
    mode: str
    project_dir: str
    bidder: str
    zjzbs_path: str
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    items: list[UploadItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "project_dir": self.project_dir,
            "bidder": self.bidder,
            "zjzbs_path": self.zjzbs_path,
            "generated_at": self.generated_at,
            "notes": self.notes,
            "summary": self.summary(),
            "items": [item.to_dict() for item in self.items],
        }

    def summary(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result
