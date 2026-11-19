"""DART 수집·파싱·청킹에서 공유하는 데이터 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentMetadata:
    rcept_no: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    report_type: str = "annual"
    filed_at: str = ""
    collected_at: str = ""
    source_url: str = ""
    raw_sha256: str = ""
    superseded: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DocumentMetadata":
        return cls(
            rcept_no=str(value["rcept_no"]),
            corp_code=str(value["corp_code"]),
            corp_name=str(value["corp_name"]),
            fiscal_year=int(value["fiscal_year"]),
            report_type=str(value.get("report_type", "annual")),
            filed_at=str(value.get("filed_at", "")),
            collected_at=str(value.get("collected_at", "")),
            source_url=str(value.get("source_url", "")),
            raw_sha256=str(value.get("raw_sha256", "")),
            superseded=parse_bool(value.get("superseded", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedBlock:
    chapter: str
    section_path: tuple[str, ...]
    note_no: str | None
    table_title: str | None
    ordinal: int
    text: str
    is_table: bool = False
    # 파서 내부에서 같은 TABLE/TBL 요소의 행을 식별한다. 청크 JSON 스키마에는
    # 노출하지 않고 A/B/C 표 청킹 실험에서만 사용한다.
    table_group: str | None = None


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    corpus_id: str
    rcept_no: str
    corp_code: str
    corp_name: str
    fiscal_year: int
    chapter: str
    section_path: tuple[str, ...]
    note_no: str | None
    table_title: str | None
    ordinal: int
    content: str
    content_sha256: str
    is_table: bool
    near_dup: bool = False
    superseded: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["section_path"] = list(self.section_path)
        return value


@dataclass
class DocumentBuildResult:
    metadata: DocumentMetadata
    status: str
    corpus_id: str = ""
    chunks: list[ChunkRecord] = field(default_factory=list)
    chars_original: int = 0
    chars_kept: int = 0
    separator_counts: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    parser: str = "dart_xml_v9"
    error: str | None = None

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.metadata.rcept_no,
            "corpus_id": self.corpus_id,
            "rcept_no": self.metadata.rcept_no,
            "corp_code": self.metadata.corp_code,
            "corp_name": self.metadata.corp_name,
            "fiscal_year": self.metadata.fiscal_year,
            "report_type": self.metadata.report_type,
            "filed_at": self.metadata.filed_at,
            "source_url": self.metadata.source_url,
            "collected_at": self.metadata.collected_at,
            "raw_sha256": self.metadata.raw_sha256,
            "superseded": self.metadata.superseded,
            "parser": self.parser,
            "status": self.status,
            "chunks": len(self.chunks),
            "unique_chunks": len(
                {
                    chunk.content_sha256
                    for chunk in self.chunks
                    if not chunk.near_dup and not chunk.superseded
                }
            ),
            "chars_original": self.chars_original,
            "chars_kept": self.chars_kept,
            "separator_counts": self.separator_counts,
            "truncated": self.truncated,
            "error": self.error,
        }


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "n"}:
            return False
        if normalized in {"1", "true", "yes", "y"}:
            return True
    raise ValueError(f"superseded는 boolean이어야 합니다: {value!r}")
