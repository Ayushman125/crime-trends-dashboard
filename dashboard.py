"""Local interactive dashboard for Cambridge Police Daily Log reports."""

from __future__ import annotations

from datetime import date
from datetime import datetime, timezone
import os
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from src.aws_lambda import extract_records, transform_records
from src.visualizations import build_incident_trend, build_type_composition

NAVY = "#12233F"
TEAL = "#167D8D"
BLUE = "#4D7EA8"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CAMBRIDGE_TZ = ZoneInfo("America/New_York")


@st.cache_data(ttl=6 * 60 * 60, show_spinner="Loading Cambridge police log data...")
def load_incidents() -> pd.DataFrame:
    """Load a privacy-safe snapshot from S3, Postgres, or the public API."""
    bucket = os.getenv("DATA_BUCKET")
    if bucket:
        import boto3
        from botocore.exceptions import BotoCoreError

        key = os.getenv("DATA_KEY", "curated/cpd_incidents.jsonl")
        try:
            response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
        except BotoCoreError as error:
            raise RuntimeError(
                f"Could not read s3://{bucket}/{key}; check the instance role and object."
            ) from error
        frame = pd.read_json(response["Body"], lines=True)
        metadata = response.get("Metadata", {})
        fetched_at = metadata.get("fetched-at-utc") or response.get("LastModified")
        source_last_updated = metadata.get("source-last-updated")
    elif os.getenv("DB_HOST"):
        import psycopg2

        status = None
        try:
            connection = psycopg2.connect(
                host=os.environ["DB_HOST"],
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                connect_timeout=10,
            )
            try:
                frame = pd.read_sql_query(
                    """
                    SELECT id, date_time, date_time::date AS incident_date,
                           type AS incident_type, subtype, location, year, month,
                           day, hour, day_of_week, last_updated
                    FROM cpd_incidents
                    """,
                    connection,
                )
                try:
                    status = pd.read_sql_query(
                        """
                        SELECT refreshed_at, source_last_updated
                        FROM cpd_etl_status
                        WHERE pipeline_name = 'cambridge_daily_log'
                        """,
                        connection,
                    )
                except psycopg2.errors.UndefinedTable:
                    status = None
            finally:
                connection.close()
        except psycopg2.Error as error:
            raise RuntimeError(
                "Could not read PostgreSQL; check that the database and ETL are running."
            ) from error
        fetched_at = status.iloc[0]["refreshed_at"] if status is not None and not status.empty else None
        source_last_updated = (
            status.iloc[0]["source_last_updated"]
            if status is not None and not status.empty
            else None
        )
    else:
        records = transform_records(extract_records())
        fetched_at = datetime.now(timezone.utc)
        frame = pd.DataFrame.from_records(records)
        source_last_updated = frame["last_updated"].max() if not frame.empty else None
    if frame.empty:
        raise ValueError(
            "No incident rows are available. If using Docker, wait for the first ETL run and refresh."
        )
    frame["date_time"] = pd.to_datetime(frame["date_time"], errors="raise")
    frame["incident_date"] = pd.to_datetime(frame["incident_date"], errors="raise")
    frame["last_updated"] = pd.to_datetime(frame["last_updated"], errors="coerce")
    frame.attrs["fetched_at_utc"] = _metadata_string(fetched_at)
    frame.attrs["source_last_updated"] = _metadata_string(source_last_updated)
    return frame


def _as_utc_timestamp(value: object) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _metadata_string(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return str(value)


def _format_date(value: pd.Timestamp | date) -> str:
    return pd.Timestamp(value).strftime("%b %d, %Y")


def _metric_delta(current: int, previous: int) -> str | None:
    if previous == 0:
        return "No prior-period records" if current else None
    change = (current - previous) / previous
    return f"{change:+.1%} vs prior period"


def _build_heatmap(frame: pd.DataFrame) -> go.Figure:
    matrix = frame.copy()
    matrix["weekday_name"] = matrix["day_of_week"].map(
        {number: name for number, name in enumerate(WEEKDAYS, start=1)}
    )
    counts = (
        matrix.pivot_table(
            index="weekday_name",
            columns="hour",
            values="id",
            aggfunc="nunique",
            fill_value=0,
        )
        .reindex(index=WEEKDAYS, columns=range(24), fill_value=0)
    )
    figure = px.imshow(
        counts,
        aspect="auto",
        color_continuous_scale=[[0, "#F3F6F8"], [0.45, "#84C2C5"], [1, NAVY]],
        labels={"x": "Hour of day", "y": "Day of week", "color": "Incidents"},
    )
    figure.update_layout(
        margin={"l": 8, "r": 8, "t": 12, "b": 8},
        coloraxis_colorbar={"title": "Incidents", "thickness": 12},
        xaxis={"dtick": 2, "side": "bottom"},
    )
    return figure


def _build_bar_chart(
    frame: pd.DataFrame, field: str, label: str, limit: int
) -> go.Figure:
    top = (
        frame.groupby(field, as_index=False)
        .agg(reported_incidents=("id", "nunique"))
        .nlargest(limit, "reported_incidents")
        .sort_values("reported_incidents", ascending=True)
    )
    figure = px.bar(
        top,
        x="reported_incidents",
        y=field,
        orientation="h",
        text="reported_incidents",
        labels={field: label, "reported_incidents": "Reported incidents"},
        color_discrete_sequence=[TEAL if field == "incident_type" else BLUE],
    )
    figure.update_traces(textposition="outside", cliponaxis=False)
    figure.update_layout(
        margin={"l": 8, "r": 30, "t": 12, "b": 8},
        xaxis={"title": None, "rangemode": "tozero", "gridcolor": "#E8EDF2"},
        yaxis={"title": None, "automargin": True},
        showlegend=False,
    )
    return figure


st.set_page_config(
    page_title="Cambridge Police Log | Incident Trends",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    .stApp { background: #F5F7FA; color: #17283F; }
    [data-testid="stHeader"] { background: rgba(245,247,250,.92); }
    [data-testid="stMetric"] {
      background: #FFFFFF; border: 1px solid #E5EAF0; padding: 18px 20px;
      border-radius: 12px; box-shadow: 0 2px 8px rgba(18,35,63,.04);
    }
    [data-testid="stMetricLabel"] p { color: #5D6B7A; font-size: .88rem; }
    .section-card {
      background: #FFFFFF; border: 1px solid #E5EAF0; border-radius: 12px;
      padding: 14px 18px 6px; margin-bottom: 14px;
    }
    .dashboard-note { color: #5D6B7A; font-size: .88rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

@st.fragment(run_every="1h")
def render_dashboard() -> None:
    with st.sidebar:
        st.markdown("## Report controls")
        st.caption("Filter the Cambridge Police Daily Log")
        if st.button("Refresh data now", width="stretch"):
            load_incidents.clear()
            st.rerun()

    try:
        incidents = load_incidents()
    except (
        OSError,
        URLError,
        ValueError,
        KeyError,
        RuntimeError,
        requests.RequestException,
    ) as error:
        st.error("Could not load the Cambridge Police Daily Log.")
        st.exception(error)
        st.stop()

    now_utc = pd.Timestamp.now(tz="UTC")
    now_local = now_utc.tz_convert(CAMBRIDGE_TZ)
    now_local_naive = now_local.tz_localize(None)
    latest_incident = incidents["date_time"].max()
    incident_age = now_local_naive - latest_incident
    fetched_at = _as_utc_timestamp(incidents.attrs.get("fetched_at_utc"))
    source_last_updated = incidents.attrs.get("source_last_updated")

    with st.sidebar:
        today = now_local.date()
        default_start = max(
            incidents["incident_date"].min().date(),
            today - pd.Timedelta(days=29),
        )
        selected_dates = st.date_input(
            "Incident date range",
            value=(default_start, today),
            min_value=incidents["incident_date"].min().date(),
            max_value=today,
            key=f"incident_date_range_{today:%Y%m%d}",
        )
        available_types = sorted(incidents["incident_type"].dropna().unique().tolist())
        selected_types = st.multiselect(
            "Incident type",
            options=available_types,
            default=available_types,
        )
        top_n = st.slider("Show top locations", min_value=5, max_value=20, value=10)
        st.divider()
        source_label = (
            "AWS S3 snapshot"
            if os.getenv("DATA_BUCKET")
            else "PostgreSQL"
            if os.getenv("DB_HOST")
            else "Cambridge Open Data · Dataset 3gki-wyrb"
        )
        st.caption(f"Source: {source_label}")
        st.caption("The dashboard checks for updates hourly.")
        if fetched_at is not None:
            local_fetch_time = fetched_at.tz_convert(CAMBRIDGE_TZ)
            st.caption(f"Snapshot checked: {local_fetch_time:%b %d, %Y %I:%M %p} local time")

    if fetched_at is None:
        st.warning(
            "The records loaded, but their fetch time is unavailable. "
            "Run the ETL once to initialize source freshness tracking."
        )
    elif now_utc - fetched_at > pd.Timedelta(hours=24):
        st.error(
            "The dashboard snapshot is over 24 hours old. "
            "Check the scheduled AWS Lambda/ETL run and its logs."
        )

    if incident_age > pd.Timedelta(hours=24):
        age_days = incident_age.days
        age_hours = incident_age.seconds // 3600
        age_label = (
            f"{age_days} days"
            if age_days
            else f"{age_hours} hours"
        )
        source_update_label = ""
        if (
            source_last_updated is not None
            and source_last_updated != "unknown"
            and not pd.isna(source_last_updated)
        ):
            source_updated = pd.Timestamp(source_last_updated)
            if source_updated.tzinfo is None:
                source_updated = source_updated.tz_localize(CAMBRIDGE_TZ)
            else:
                source_updated = source_updated.tz_convert(CAMBRIDGE_TZ)
            source_update_label = f" The source says its records were last updated {source_updated:%b %d, %Y %I:%M %p} local time."
        st.warning(
            f"The newest incident currently available from the city is "
            f"{_format_date(latest_incident)} ({age_label} ago). "
            f"There are no source records from the past 24 hours; refreshing "
            f"the dashboard cannot create records the city has not published."
            f"{source_update_label}"
        )

    if not isinstance(selected_dates, (tuple, list)) or len(selected_dates) != 2:
        st.warning("Select both a start date and an end date.")
        st.stop()

    start_date, end_date = selected_dates
    filtered = incidents.loc[
        incidents["incident_date"].dt.date.between(start_date, end_date)
        & incidents["incident_type"].isin(selected_types)
    ].copy()

    st.title("Cambridge Police Log")
    st.markdown(
        f"### Reported incident trends"
        f"<span style='color:#637386;font-size:1rem;font-weight:400'>"
        f" &nbsp;|&nbsp; {_format_date(start_date)} – {_format_date(end_date)}</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Counts describe entries in the Cambridge Police Daily Log. "
        "They are not a complete measure of crime or individual risk."
    )

    period_days = (end_date - start_date).days + 1
    previous_end = pd.Timestamp(start_date) - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(days=period_days - 1)
    previous_period = incidents.loc[
        incidents["incident_date"].dt.date.between(previous_start.date(), previous_end.date())
        & incidents["incident_type"].isin(selected_types)
    ]
    recent_24h = incidents.loc[
        incidents["date_time"].between(
            now_local_naive - pd.Timedelta(hours=24), now_local_naive
        )
        & incidents["incident_type"].isin(selected_types)
    ]
    current_total = int(filtered["id"].nunique())
    previous_total = int(previous_period["id"].nunique())
    incident_types_count = int(filtered["incident_type"].nunique())
    distinct_locations = int(filtered["location"].nunique())

    metric_columns = st.columns(5)
    metric_columns[0].metric(
        "Reported incidents",
        f"{current_total:,}",
        delta=_metric_delta(current_total, previous_total),
    )
    metric_columns[1].metric(
        "Incidents in past 24 hours",
        f"{recent_24h['id'].nunique():,}",
        help="Last 24 hours, for the selected incident types; independent of the date-range filter.",
    )
    metric_columns[2].metric("Incident types", f"{incident_types_count:,}")
    metric_columns[3].metric("Reported locations", f"{distinct_locations:,}")
    latest_filtered = filtered["incident_date"].max() if not filtered.empty else None
    metric_columns[4].metric(
        "Latest incident",
        pd.Timestamp(latest_filtered).strftime("%b %d") if latest_filtered is not None else "—",
        help=_format_date(latest_filtered) if latest_filtered is not None else None,
    )

    if filtered.empty:
        st.info("No incidents match the selected date and type filters.")
        st.stop()

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("Reported incidents over time")
    granularity = st.segmented_control(
        "Group the trend by",
        options=["Daily", "Weekly", "Monthly", "Yearly"],
        default="Daily",
        key="trend_granularity",
    )
    if granularity is None:
        granularity = "Daily"
    st.plotly_chart(
        build_incident_trend(filtered, granularity),
        width="stretch",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.subheader("Incident types over time")
        st.caption(
            f"Top five types by selected-period volume; remaining types are grouped as Other. "
            f"Currently grouped by {granularity.lower()}."
        )
        st.plotly_chart(
            build_type_composition(filtered, granularity),
            width="stretch",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.subheader("Reported incident types")
        st.plotly_chart(
            _build_bar_chart(filtered, "incident_type", "Incident type", 7),
            width="stretch",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.subheader("When incidents are logged")
        st.plotly_chart(_build_heatmap(filtered), width="stretch")
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.subheader(f"Top {top_n} reported locations")
        st.plotly_chart(
            _build_bar_chart(filtered, "location", "Street / location", top_n),
            width="stretch",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("View recent entries (descriptions excluded)"):
        recent = filtered.sort_values("date_time", ascending=False).head(100)
        st.dataframe(
            recent[
                [
                    "date_time",
                    "incident_type",
                    "subtype",
                    "location",
                    "id",
                ]
            ].rename(
                columns={
                    "date_time": "Incident date/time",
                    "incident_type": "Type",
                    "subtype": "Subtype",
                    "location": "Reported location",
                    "id": "Incident ID",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    st.markdown(
        "<p class='dashboard-note'>Source: Cambridge Police Department Daily Police Log. "
        "Street counts are reported-location counts, not geocoded hotspot estimates. "
        "Descriptions are intentionally excluded.</p>",
        unsafe_allow_html=True,
    )


render_dashboard()
