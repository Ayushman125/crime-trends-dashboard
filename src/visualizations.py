"""Reusable charts and time-bucket aggregation for the dashboard."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

TEAL = "#167D8D"
NAVY = "#12233F"
TYPE_COLORS = ["#167D8D", "#4D7EA8", "#E1A04A", "#7B6FA8", "#5A9B79", "#A9B5C2"]

GRANULARITY_LABELS = {
    "Daily": "Day",
    "Weekly": "Week",
    "Monthly": "Month",
    "Yearly": "Year",
}


def _period_starts(frame: pd.DataFrame, granularity: str) -> pd.Series:
    if granularity not in GRANULARITY_LABELS:
        raise ValueError(f"Unsupported trend granularity: {granularity}")

    dates = frame["incident_date"]
    if granularity == "Daily":
        return dates.dt.floor("D")
    if granularity == "Weekly":
        return dates.dt.to_period("W-SUN").dt.start_time
    if granularity == "Monthly":
        return dates.dt.to_period("M").dt.start_time
    return dates.dt.to_period("Y").dt.start_time


def aggregate_incidents_by_period(
    frame: pd.DataFrame, granularity: str
) -> pd.DataFrame:
    """Count distinct incidents in each selected calendar period."""
    data = frame.assign(period_start=_period_starts(frame, granularity))
    return (
        data.groupby("period_start", as_index=False)
        .agg(reported_incidents=("id", "nunique"))
        .sort_values("period_start")
    )


def build_incident_trend(frame: pd.DataFrame, granularity: str) -> go.Figure:
    counts = aggregate_incidents_by_period(frame, granularity)
    figure = px.line(
        counts,
        x="period_start",
        y="reported_incidents",
        markers=True,
        labels={
            "period_start": GRANULARITY_LABELS[granularity],
            "reported_incidents": "Reported incidents",
        },
    )
    figure.update_traces(line={"color": TEAL, "width": 3}, marker={"size": 7})
    figure.update_layout(
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        xaxis={"title": None, "showgrid": False},
        yaxis={"title": None, "rangemode": "tozero", "gridcolor": "#E8EDF2"},
        hovermode="x unified",
    )
    return figure


def build_type_composition(frame: pd.DataFrame, granularity: str) -> go.Figure:
    """Show the top five incident types over time, grouping the rest as Other."""
    top_types = (
        frame.groupby("incident_type")["id"]
        .nunique()
        .nlargest(5)
        .index
    )
    data = frame.assign(
        period_start=_period_starts(frame, granularity),
        type_group=frame["incident_type"].where(
            frame["incident_type"].isin(top_types), "Other"
        ),
    )
    grouped = (
        data.groupby(["period_start", "type_group"], as_index=False)
        .agg(reported_incidents=("id", "nunique"))
        .pivot(
            index="period_start",
            columns="type_group",
            values="reported_incidents",
        )
        .fillna(0)
        .astype(int)
    )
    type_order = [value for value in top_types if value in grouped.columns]
    if "Other" in grouped.columns:
        type_order.append("Other")
    chart_data = grouped.reindex(columns=type_order).reset_index()
    figure = px.area(
        chart_data,
        x="period_start",
        y=type_order,
        labels={
            "period_start": GRANULARITY_LABELS[granularity],
            "value": "Reported incidents",
            "variable": "Incident type",
        },
        color_discrete_sequence=TYPE_COLORS,
        category_orders={"variable": type_order},
    )
    figure.update_layout(
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        xaxis={"title": None, "showgrid": False},
        yaxis={"title": None, "rangemode": "tozero", "gridcolor": "#E8EDF2"},
        legend={"title": None, "orientation": "h", "y": 1.12},
        hovermode="x unified",
    )
    return figure
