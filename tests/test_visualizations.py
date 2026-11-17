import unittest

import pandas as pd

from src.visualizations import (
    aggregate_incidents_by_period,
    build_incident_trend,
    build_type_composition,
)


class TrendVisualizationTests(unittest.TestCase):
    def setUp(self):
        self.incidents = pd.DataFrame(
            {
                "id": [101, 102, 103, 104, 105, 106],
                "incident_date": pd.to_datetime(
                    [
                        "2025-01-01",
                        "2025-01-01",
                        "2025-01-02",
                        "2025-01-08",
                        "2025-02-01",
                        "2026-01-01",
                    ]
                ),
                "incident_type": ["Theft", "Assault", "Theft", "Theft", "Assault", "Theft"],
            }
        )

    def test_selected_granularity_changes_calendar_buckets(self):
        expected_period_counts = {
            "Daily": 5,
            "Weekly": 4,
            "Monthly": 3,
            "Yearly": 2,
        }

        for granularity, expected_count in expected_period_counts.items():
            with self.subTest(granularity=granularity):
                counts = aggregate_incidents_by_period(self.incidents, granularity)
                self.assertEqual(len(counts), expected_count)
                self.assertEqual(counts["reported_incidents"].sum(), 6)

    def test_week_starts_on_monday_and_groups_incidents(self):
        weekly = aggregate_incidents_by_period(self.incidents, "Weekly")
        self.assertEqual(weekly.iloc[0]["period_start"], pd.Timestamp("2024-12-30"))
        self.assertEqual(weekly.iloc[0]["reported_incidents"], 3)

    def test_trend_chart_contains_aggregated_values(self):
        figure = build_incident_trend(self.incidents, "Yearly")
        self.assertEqual(list(figure.data[0].y), [5, 1])

    def test_type_composition_groups_non_top_types_as_other(self):
        incidents = self.incidents.copy()
        incidents.loc[incidents.index[:4], "incident_type"] = [
            "Theft",
            "Assault",
            "Robbery",
            "Fraud",
        ]
        incidents.loc[incidents.index[4:], "incident_type"] = ["Other A", "Other B"]

        figure = build_type_composition(incidents, "Yearly")
        self.assertIn("Other", {trace.name for trace in figure.data})

    def test_rejects_unknown_granularity(self):
        with self.assertRaises(ValueError):
            aggregate_incidents_by_period(self.incidents, "Quarterly")


if __name__ == "__main__":
    unittest.main()
