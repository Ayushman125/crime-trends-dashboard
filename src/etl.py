"""Daily ETL for the Cambridge Police Daily Log Socrata dataset."""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, time as datetime_time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

DATASET_URL = "https://data.cambridgema.gov/resource/3gki-wyrb.json"
PAGE_SIZE = 50_000
EXPECTED_COLUMNS = (
    "date_time",
    "id",
    "type",
    "subtype",
    "location",
    "last_updated",
    "description",
)
REQUIRED_COLUMNS = ("date_time", "id", "type", "location")
INSERT_COLUMNS = (
    "date_time",
    "id",
    "type",
    "subtype",
    "location",
    "description",
    "last_updated",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "second",
    "day_of_week",
)
CAMBRIDGE_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger("crime_etl")


def extract_records() -> list[dict[str, Any]]:
    """Fetch every page from the public Socrata API."""
    app_token = os.getenv("SOCRATA_APP_TOKEN")
    headers = {"X-App-Token": app_token} if app_token else {}
    records: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "$limit": PAGE_SIZE,
            "$offset": offset,
            "$order": "date_time ASC, id ASC",
        }
        response: requests.Response | None = None
        for attempt in range(3):
            try:
                response = requests.get(
                    DATASET_URL, params=params, headers=headers, timeout=60
                )
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    logger.exception("Socrata request failed at offset %s", offset)
                    raise
                time.sleep(2**attempt)

        assert response is not None
        page = response.json()
        if not isinstance(page, list):
            raise ValueError("Socrata returned an unexpected response; expected a JSON array.")
        records.extend(page)
        logger.info("Fetched %s records so far", len(records))

        if len(page) < PAGE_SIZE:
            return records
        offset += PAGE_SIZE


def _parse_local_datetime(value: Any, *, field: str, row_number: int) -> datetime | None:
    if value is None or pd.isna(value) or not str(value).strip():
        return None

    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field} at source row {row_number}: {value!r}") from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(CAMBRIDGE_TZ).replace(tzinfo=None)
    return parsed


def transform_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Validate, normalize, deduplicate, and enrich source records."""
    frame = pd.DataFrame.from_records(records)
    missing_columns = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(f"Socrata schema is missing required columns: {sorted(missing_columns)}")

    for column in EXPECTED_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame.loc[:, EXPECTED_COLUMNS].copy()

    numeric_ids = pd.to_numeric(frame["id"], errors="coerce")
    integral_ids = numeric_ids.notna() & numeric_ids.mod(1).eq(0)
    dropped_ids = int((~integral_ids).sum())
    frame = frame.loc[integral_ids].copy()
    frame["id"] = numeric_ids.loc[integral_ids].astype("int64")
    if dropped_ids:
        logger.warning("Discarded %s records with non-integral IDs", dropped_ids)

    for column in ("date_time", "type", "location"):
        missing = frame[column].isna() | frame[column].astype(str).str.strip().eq("")
        if missing.any():
            raise ValueError(
                f"Required field {column!r} is missing in {int(missing.sum())} source records."
            )

    parsed_dates = [
        _parse_local_datetime(value, field="date_time", row_number=index)
        for index, value in enumerate(frame["date_time"].tolist(), start=1)
    ]
    frame["date_time"] = pd.to_datetime(parsed_dates)
    if frame["date_time"].isna().any():
        raise ValueError("One or more records have no valid incident date/time.")

    parsed_updates = [
        _parse_local_datetime(value, field="last_updated", row_number=index)
        for index, value in enumerate(frame["last_updated"].tolist(), start=1)
    ]
    frame["last_updated"] = pd.to_datetime(parsed_updates)

    for column in ("type", "subtype", "location"):
        frame[column] = frame[column].map(
            lambda value: str(value).strip().upper() if pd.notna(value) else None
        )
    frame["description"] = frame["description"].map(
        lambda value: str(value).strip() if pd.notna(value) else None
    )

    original_count = len(frame)
    frame = frame.drop_duplicates(subset=["id"], keep="last").copy()
    logger.info(
        "Removed %s duplicate IDs; retaining %s records",
        original_count - len(frame),
        len(frame),
    )

    incident_dates = frame["date_time"]
    frame["year"] = incident_dates.dt.year.astype("int16")
    frame["month"] = incident_dates.dt.month.astype("int8")
    frame["day"] = incident_dates.dt.day.astype("int8")
    frame["hour"] = incident_dates.dt.hour.astype("int8")
    frame["minute"] = incident_dates.dt.minute.astype("int8")
    frame["second"] = incident_dates.dt.second.astype("int8")
    frame["day_of_week"] = incident_dates.dt.isocalendar().day.astype("int8")

    return frame.loc[:, INSERT_COLUMNS].reset_index(drop=True)


def load_records(frame: pd.DataFrame) -> int:
    """Create the database objects and idempotently upsert transformed records."""
    if frame.empty:
        logger.warning("No valid records to load.")
        return 0

    import psycopg2
    from psycopg2.extras import execute_values

    connection = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=15,
    )
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cpd_incidents (
                        date_time TIMESTAMP NOT NULL,
                        id BIGINT PRIMARY KEY,
                        type TEXT NOT NULL,
                        subtype TEXT,
                        location TEXT NOT NULL,
                        description TEXT,
                        last_updated TIMESTAMP,
                        year SMALLINT NOT NULL,
                        month SMALLINT NOT NULL,
                        day SMALLINT NOT NULL,
                        hour SMALLINT NOT NULL,
                        minute SMALLINT NOT NULL,
                        second SMALLINT NOT NULL,
                        day_of_week SMALLINT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cpd_etl_status (
                        pipeline_name TEXT PRIMARY KEY,
                        refreshed_at TIMESTAMPTZ NOT NULL,
                        source_latest_incident TIMESTAMP NOT NULL,
                        source_last_updated TIMESTAMP,
                        record_count BIGINT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE OR REPLACE VIEW cpd_incidents_dashboard AS
                    SELECT
                        id,
                        date_time,
                        date_time::date AS incident_date,
                        type AS incident_type,
                        subtype,
                        location,
                        year,
                        month,
                        day,
                        hour,
                        day_of_week,
                        last_updated
                    FROM cpd_incidents
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO cpd_etl_status (
                        pipeline_name, refreshed_at, source_latest_incident,
                        source_last_updated, record_count
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (pipeline_name) DO UPDATE SET
                        refreshed_at = EXCLUDED.refreshed_at,
                        source_latest_incident = EXCLUDED.source_latest_incident,
                        source_last_updated = EXCLUDED.source_last_updated,
                        record_count = EXCLUDED.record_count
                    """,
                    (
                        "cambridge_daily_log",
                        datetime.now(timezone.utc),
                        frame["date_time"].max().to_pydatetime(),
                        frame["last_updated"].max().to_pydatetime()
                        if frame["last_updated"].notna().any()
                        else None,
                        len(frame),
                    ),
                )

                columns = ", ".join(INSERT_COLUMNS)
                updates = ", ".join(
                    f"{column} = EXCLUDED.{column}"
                    for column in INSERT_COLUMNS
                    if column != "id"
                )
                statement = f"""
                    INSERT INTO cpd_incidents ({columns})
                    VALUES %s
                    ON CONFLICT (id) DO UPDATE SET {updates}
                    """
                rows = [
                    tuple(None if pd.isna(value) else value for value in row)
                    for row in frame.itertuples(index=False, name=None)
                ]
                execute_values(cursor, statement, rows, page_size=1_000)
        logger.info("Upserted %s records into cpd_incidents", len(frame))
        return len(frame)
    finally:
        connection.close()


def run_pipeline() -> int:
    records = extract_records()
    logger.info("Extracted %s source records", len(records))
    transformed = transform_records(records)
    return load_records(transformed)


def _seconds_until_next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    next_run = datetime.combine(now.date(), datetime_time.min, tzinfo=timezone.utc)
    from datetime import timedelta

    next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run once immediately, then repeat at 00:00 UTC each day.",
    )
    args = parser.parse_args()

    run_pipeline()
    while args.schedule:
        delay = _seconds_until_next_utc_midnight()
        logger.info("Next daily run in %.0f seconds (00:00 UTC)", delay)
        time.sleep(delay)
        run_pipeline()


if __name__ == "__main__":
    main()
