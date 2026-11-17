import unittest
from datetime import datetime

import pandas as pd

from etl import transform_records


def record(
    incident_id="25001925",
    date_time="2025-03-06T01:07:00.000",
    incident_type=" INCIDENT ",
    location=" newport rd ",
    **overrides,
):
    row = {
        "date_time": date_time,
        "id": incident_id,
        "type": incident_type,
        "subtype": "LARCENY FROM BUILDING",
        "location": location,
        "last_updated": "2025-03-11T09:35:52.000",
        "description": " A report was taken. ",
    }
    row.update(overrides)
    return row


class TransformRecordsTests(unittest.TestCase):
    def test_normalizes_and_derives_calendar_fields(self):
        result = transform_records([record()])

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "id"], 25001925)
        self.assertEqual(result.loc[0, "type"], "INCIDENT")
        self.assertEqual(result.loc[0, "location"], "NEWPORT RD")
        self.assertEqual(result.loc[0, "description"], "A report was taken.")
        self.assertEqual(result.loc[0, "date_time"], datetime(2025, 3, 6, 1, 7))
        self.assertEqual(result.loc[0, "year"], 2025)
        self.assertEqual(result.loc[0, "day_of_week"], 4)

    def test_discards_bad_ids_and_keeps_latest_duplicate(self):
        older = record(description="Earlier version")
        newer = record(
            date_time="2025-03-06T02:07:00.000",
            description="Updated version",
        )
        result = transform_records([older, record(incident_id="not-an-id"), newer])

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "description"], "Updated version")
        self.assertEqual(result.loc[0, "hour"], 2)

    def test_rejects_missing_required_fields(self):
        with self.assertRaisesRegex(ValueError, "Required field 'location'"):
            transform_records([record(location=" ")])

    def test_rejects_invalid_incident_datetime(self):
        with self.assertRaisesRegex(ValueError, "Invalid date_time"):
            transform_records([record(date_time="not-a-date")])

    def test_converts_offset_datetime_to_cambridge_local_time(self):
        result = transform_records(
            [record(date_time="2025-03-06T06:07:00+00:00")]
        )
        self.assertEqual(
            result.loc[0, "date_time"],
            pd.Timestamp("2025-03-06 01:07:00"),
        )

    def test_requires_core_source_columns(self):
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            transform_records([{"id": "1"}])


if __name__ == "__main__":
    unittest.main()
