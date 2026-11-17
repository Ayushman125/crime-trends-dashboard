"""Docker-free AWS Lambda ETL that publishes a privacy-safe S3 snapshot."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DATASET_URL = "https://data.cambridgema.gov/resource/3gki-wyrb.json"
PAGE_SIZE = 50_000
CAMBRIDGE_TZ = ZoneInfo("America/New_York")
PUBLIC_FIELDS = (
    "id",
    "date_time",
    "incident_date",
    "incident_type",
    "subtype",
    "location",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "second",
    "day_of_week",
    "last_updated",
)

logger = logging.getLogger(__name__)


def _request_page(offset: int, app_token: str | None = None) -> list[dict[str, Any]]:
    params = urlencode(
        {
            "$limit": PAGE_SIZE,
            "$offset": offset,
            "$order": "date_time ASC, id ASC",
        }
    )
    headers = {"Accept": "application/json"}
    if app_token:
        headers["X-App-Token"] = app_token
    request = Request(f"{DATASET_URL}?{params}", headers=headers)

    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                page = json.loads(response.read())
            if not isinstance(page, list):
                raise ValueError("Socrata returned a non-array response.")
            return page
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                logger.exception("Socrata request failed at offset %s", offset)
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Socrata retry loop ended unexpectedly.")


def extract_records(app_token: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = _request_page(offset, app_token)
        records.extend(page)
        logger.info("Fetched %s source records", len(records))
        if len(page) < PAGE_SIZE:
            return records
        offset += PAGE_SIZE


def _parse_datetime(value: Any, field: str, record_id: Any) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field} for incident {record_id}: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(CAMBRIDGE_TZ).replace(tzinfo=None)
    return parsed


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def transform_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and shape records without descriptions or third-party packages."""
    by_id: dict[int, tuple[datetime | None, dict[str, Any]]] = {}
    invalid_ids = 0
    for source in records:
        raw_id = str(source.get("id", "")).strip()
        if not raw_id.isdecimal():
            invalid_ids += 1
            continue
        incident_id = int(raw_id)

        incident_datetime = _parse_datetime(
            source.get("date_time"), "date_time", incident_id
        )
        incident_type = str(source.get("type") or "").strip().upper()
        location = str(source.get("location") or "").strip().upper()
        if incident_datetime is None or not incident_type or not location:
            raise ValueError(
                f"Incident {incident_id} is missing a required date/time, type, or location."
            )
        last_updated = _parse_datetime(
            source.get("last_updated"), "last_updated", incident_id
        )
        existing = by_id.get(incident_id)
        if existing:
            previous_update = existing[0]
            if previous_update and (
                last_updated is None or last_updated < previous_update
            ):
                continue

        row = {
            "id": incident_id,
            "date_time": incident_datetime,
            "incident_date": incident_datetime.date().isoformat(),
            "incident_type": incident_type,
            "subtype": str(source.get("subtype") or "").strip().upper() or None,
            "location": location,
            "year": incident_datetime.year,
            "month": incident_datetime.month,
            "day": incident_datetime.day,
            "hour": incident_datetime.hour,
            "minute": incident_datetime.minute,
            "second": incident_datetime.second,
            "day_of_week": incident_datetime.isoweekday(),
            "last_updated": last_updated,
        }
        by_id[incident_id] = (last_updated, row)

    if invalid_ids:
        logger.warning("Discarded %s records with non-numeric IDs", invalid_ids)

    safe_rows: list[dict[str, Any]] = []
    for _, row in sorted(by_id.values(), key=lambda entry: entry[1]["id"]):
        row["date_time"] = _format_datetime(row["date_time"])
        row["last_updated"] = _format_datetime(row["last_updated"])
        safe_rows.append({field: row[field] for field in PUBLIC_FIELDS})
    return safe_rows


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Fetch and replace the current privacy-safe analytics snapshot in S3."""
    import boto3

    records = extract_records(os.getenv("SOCRATA_APP_TOKEN") or None)
    safe_rows = transform_records(records)
    if not safe_rows:
        raise ValueError("Socrata returned no valid incidents; refusing to overwrite S3.")

    fetched_at = datetime.now(timezone.utc)
    latest_incident = max(row["date_time"] for row in safe_rows)
    source_last_updated_values = [
        row["last_updated"] for row in safe_rows if row["last_updated"] is not None
    ]
    body = "".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in safe_rows
    ).encode("utf-8")
    bucket = os.environ["DATA_BUCKET"]
    key = os.getenv("DATA_KEY", "curated/cpd_incidents.jsonl")
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/x-ndjson",
        ServerSideEncryption="AES256",
        Metadata={
            "fetched-at-utc": fetched_at.isoformat(),
            "source-latest-incident": latest_incident,
            "source-last-updated": max(source_last_updated_values, default="unknown"),
        },
    )
    result = {
        "bucket": bucket,
        "key": key,
        "records": len(safe_rows),
        "bytes": len(body),
        "fetched_at_utc": fetched_at.isoformat(),
        "source_latest_incident": latest_incident,
    }
    logger.info("Published %s privacy-safe incidents to s3://%s/%s", len(safe_rows), bucket, key)
    return result
