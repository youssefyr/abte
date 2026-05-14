from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Iterable, cast, Literal, Tuple
import json
import uuid

from .models import BenchmarkRecord, BenchmarkSummary, ComputeTarget


class BenchmarkStore:
    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._root_dir / "slm_benchmarks.json"

    @property
    def file_path(self) -> Path:
        return self._file_path

    def _read_payload(self) -> dict:
        if not self._file_path.exists():
            return {"records": []}
        try:
            return json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            return {"records": []}

    def _write_payload(self, payload: dict) -> None:
        temp_path = self._file_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._file_path)

    def all_records(self) -> list[BenchmarkRecord]:
        payload = self._read_payload()
        records: list[BenchmarkRecord] = []
        for item in payload.get("records", []):
            if not isinstance(item, dict):
                continue
            try:
                records.append(BenchmarkRecord(**item))
            except Exception:
                continue
        return records

    def append(self, record: BenchmarkRecord) -> None:
        payload = self._read_payload()
        records = payload.setdefault("records", [])
        records.append(asdict(record))
        payload["records"] = records[-200:]
        self._write_payload(payload)

    def create_record(self, **kwargs) -> BenchmarkRecord:
        return BenchmarkRecord(
            benchmark_id=str(uuid.uuid4()),
            **kwargs,
        )

    def find_matching(
        self,
        *,
        model_path: str,
        backend: str,
        prompt_bucket: str,
    ) -> list[BenchmarkRecord]:
        return [
            record
            for record in self.all_records()
            if record.model_path == model_path
            and record.backend == backend
            and record.prompt_bucket == prompt_bucket
        ]

    def summarize(
        self,
        *,
        model_path: str,
        backend: str,
        prompt_bucket: str,
    ) -> BenchmarkSummary | None:
        records = self.find_matching(
            model_path=model_path,
            backend=backend,
            prompt_bucket=prompt_bucket,
        )
        successful = [r for r in records if r.success]
        if not successful:
            return None

        def med(target: ComputeTarget) -> float | None:
            values = [r.duration_seconds for r in successful if r.target == target]
            return round(float(median(values)), 4) if values else None

        cpu_median = med("cpu")
        gpu_median = med("gpu")
        hybrid_median = med("hybrid")

        candidates: dict[ComputeTarget, float | None] = {
            "cpu": cpu_median,
            "gpu": gpu_median,
            "hybrid": hybrid_median,
        }
        valid_items: list[Tuple[ComputeTarget, float]] = [
            (k, v) for k, v in candidates.items() if v is not None
        ]
        best_target = min(valid_items, key=lambda item: item[1])[0] if valid_items else None

        return BenchmarkSummary(
            best_target=best_target,
            cpu_median_seconds=cpu_median,
            gpu_median_seconds=gpu_median,
            hybrid_median_seconds=hybrid_median,
            record_count=len(successful),
        )