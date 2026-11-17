import json
import unittest
from datetime import datetime
from unittest.mock import patch

from aws_lambda import lambda_handler, transform_records


def record(**overrides):
    source = {
        "date_time": "2025-03-06T01:07:00.000",
        "id": "25001925",
        "type": " INCIDENT ",
        "subtype": " larceny from building ",
        "location": " newport rd ",
        "last_updated": "2025-03-11T09:35:52.000",
        "description": "Sensitive plaintext must not be published in dashboard data.",
    }
    source.update(overrides)
    return source


class AwsTransformTests(unittest.TestCase):
    def test_builds_typed_public_rows_without_description(self):
        rows = transform_records([record()])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 25001925)
        self.assertEqual(rows[0]["date_time"], "2025-03-06 01:07:00")
        self.assertEqual(rows[0]["incident_type"], "INCIDENT")
        self.assertEqual(rows[0]["location"], "NEWPORT RD")
        self.assertEqual(rows[0]["day_of_week"], 4)
        self.assertNotIn("description", rows[0])

    def test_discards_bad_ids_and_uses_latest_update(self):
        rows = transform_records(
            [
                record(description="old", last_updated="2025-03-10T09:00:00"),
                record(description="new", last_updated="2025-03-11T09:00:00"),
                record(id="invalid"),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_updated"], "2025-03-11 09:00:00")

    def test_does_not_replace_known_update_with_missing_update(self):
        rows = transform_records(
            [
                record(description="known", last_updated="2025-03-11T09:00:00"),
                record(description="unknown", last_updated=None),
            ]
        )
        self.assertEqual(rows[0]["last_updated"], "2025-03-11 09:00:00")

    def test_converts_zoned_times_to_cambridge_local_time(self):
        rows = transform_records(
            [record(date_time="2025-03-06T06:07:00+00:00")]
        )
        self.assertEqual(rows[0]["date_time"], "2025-03-06 01:07:00")
        self.assertEqual(
            datetime.fromisoformat(rows[0]["date_time"]),
            datetime(2025, 3, 6, 1, 7),
        )

    def test_fails_on_missing_required_fields(self):
        with self.assertRaisesRegex(ValueError, "missing a required"):
            transform_records([record(location=" ")])

    def test_lambda_writes_only_safe_snapshot_to_s3(self):
        rows = transform_records([record()])
        s3 = unittest.mock.Mock()
        with (
            patch("aws_lambda.extract_records", return_value=[record()]),
            patch.dict("os.environ", {"DATA_BUCKET": "example-bucket"}),
            patch.dict("sys.modules", {"boto3": unittest.mock.Mock(client=lambda _: s3)}),
        ):
            result = lambda_handler({}, None)

        self.assertEqual(result["records"], 1)
        args = s3.put_object.call_args.kwargs
        saved = json.loads(args["Body"].decode("utf-8"))
        self.assertEqual(args["Key"], "curated/cpd_incidents.jsonl")
        self.assertEqual(saved, rows[0])
        self.assertNotIn("description", saved)
        self.assertIn("fetched-at-utc", args["Metadata"])
        self.assertEqual(
            args["Metadata"]["source-latest-incident"],
            "2025-03-06 01:07:00",
        )


if __name__ == "__main__":
    unittest.main()
