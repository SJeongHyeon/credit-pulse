"""키 소유자가 로컬에서만 실행하는 OpenDART 수집 클라이언트."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.rag.models import DocumentMetadata, parse_bool


LIST_URL = "https://opendart.fss.or.kr/api/list.json"
COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
_RCEPT_NO_RE = re.compile(r"^\d{14}$")
_CORP_CODE_RE = re.compile(r"^\d{8}$")
_ATTACHMENT_ONLY_PREFIXES = ("[첨부정정]", "[첨부추가]")


class DartClientError(RuntimeError):
    """OpenDART 호출 실패. 메시지에는 키와 전체 URL을 넣지 않는다."""


@dataclass(frozen=True)
class CollectedDocument:
    metadata: DocumentMetadata
    directory: Path


class DartClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.getenv("DART_API_KEY", "")
        if not self.api_key:
            raise DartClientError("DART_API_KEY가 필요합니다. 키는 .env에만 저장하세요")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts는 1~3이어야 합니다")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._opener = opener or urllib.request.urlopen
        self._sleeper = sleeper

    def _request(self, url: str, params: dict[str, Any]) -> bytes:
        encoded = urllib.parse.urlencode({"crtfc_key": self.api_key, **params})
        request = urllib.request.Request(
            f"{url}?{encoded}",
            headers={"User-Agent": "proj3-a-dart-ingest/1.0"},
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    self._sleeper((0.5, 2.0)[min(attempt - 1, 1)])
        error_name = type(last_error).__name__ if last_error else "UnknownError"
        raise DartClientError(
            f"OpenDART 요청 실패({error_name}, {self.max_attempts}회 시도)"
        ) from last_error

    @staticmethod
    def _load_json(raw: bytes, operation: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DartClientError(f"OpenDART {operation} 응답 형식이 올바르지 않습니다") from exc
        status = str(payload.get("status", ""))
        if status == "013":
            return {"status": status, "list": [], "total_page": 0}
        if status != "000":
            message = str(payload.get("message", "알 수 없는 오류"))[:200]
            raise DartClientError(f"OpenDART {operation} 오류 status={status}: {message}")
        return payload

    def list_annual_reports(
        self,
        begin_date: str,
        end_date: str,
        *,
        corp_cls: str = "Y",
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        if not re.fullmatch(r"\d{8}", begin_date) or not re.fullmatch(r"\d{8}", end_date):
            raise ValueError("날짜는 YYYYMMDD 형식이어야 합니다")
        reports: list[dict[str, Any]] = []
        page = 1
        while True:
            raw = self._request(
                LIST_URL,
                {
                    "bgn_de": begin_date,
                    "end_de": end_date,
                    "corp_cls": corp_cls,
                    "pblntf_ty": "A",
                    "page_no": page,
                    "page_count": 100,
                },
            )
            payload = self._load_json(raw, "목록 조회")
            for report in payload.get("list", []):
                name = str(report.get("report_nm", ""))
                if "사업보고서" not in name or "반기보고서" in name or "분기보고서" in name:
                    continue
                if name.startswith(_ATTACHMENT_ONLY_PREFIXES):
                    continue
                reports.append(dict(report))
                if max_records is not None and len(reports) >= max_records:
                    return reports
            total_page = int(payload.get("total_page", 0) or 0)
            if page >= total_page:
                return reports
            page += 1

    def download_document(self, rcept_no: str) -> bytes:
        if not _RCEPT_NO_RE.fullmatch(rcept_no):
            raise ValueError("rcept_no는 14자리 숫자여야 합니다")
        raw = self._request(DOCUMENT_URL, {"rcept_no": rcept_no})
        if raw.startswith(b"PK"):
            return raw
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = raw[:200].decode("utf-8", errors="replace")
            message = re.sub(r"crtfc_key=[^&\s<]+", "crtfc_key=***", message)
            raise DartClientError(f"원문 응답이 ZIP이 아닙니다: {message}")
        status = str(payload.get("status", ""))
        message = str(payload.get("message", "알 수 없는 오류"))[:200]
        raise DartClientError(f"OpenDART 원문 조회 오류 status={status}: {message}")

    def company_profile(self, corp_code: str) -> dict[str, str]:
        """층화 표본에 필요한 기업개황 필드만 반환한다."""

        if not _CORP_CODE_RE.fullmatch(corp_code):
            raise ValueError("corp_code는 8자리 숫자여야 합니다")
        payload = self._load_json(
            self._request(COMPANY_URL, {"corp_code": corp_code}),
            "기업개황 조회",
        )
        induty_code = str(payload.get("induty_code", "")).strip()
        if not induty_code.isdigit():
            raise DartClientError(f"기업개황 업종코드 없음: corp_code={corp_code}")
        return {
            "corp_code": corp_code,
            "corp_name": str(payload.get("corp_name", "")).strip(),
            "corp_cls": str(payload.get("corp_cls", "")).strip(),
            "stock_code": str(payload.get("stock_code", "")).strip(),
            "induty_code": induty_code,
        }

    def collect(self, target: dict[str, Any], corpus_dir: str | Path) -> CollectedDocument:
        rcept_no = str(target["rcept_no"])
        raw = self.download_document(rcept_no)
        output_dir = Path(corpus_dir) / rcept_no
        output_dir.mkdir(parents=True, exist_ok=True)

        raw_hash = hashlib.sha256(raw).hexdigest()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        metadata = DocumentMetadata(
            rcept_no=rcept_no,
            corp_code=str(target["corp_code"]),
            corp_name=str(target["corp_name"]),
            fiscal_year=int(target["fiscal_year"]),
            report_type=str(target.get("report_type", "annual")),
            filed_at=str(target.get("filed_at") or target.get("rcept_dt", "")),
            collected_at=now,
            source_url=f"{DOCUMENT_URL}?rcept_no={rcept_no}",
            raw_sha256=raw_hash,
            superseded=parse_bool(target.get("superseded", False)),
        )
        _write_bytes_atomic(output_dir / "document.zip", raw)
        _write_text_atomic(
            output_dir / "metadata.json",
            json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
        return CollectedDocument(metadata=metadata, directory=output_dir)


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
