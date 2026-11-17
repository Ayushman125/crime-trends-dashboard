# Cambridge Police Log: ETL and Local Crime Trends Dashboard

## What this project does

The assignment uses the Cambridge Police Department's [Daily Police Log](https://data.cambridgema.gov/Public-Safety/Daily-Police-Log/3gki-wyrb)
(Socrata dataset `3gki-wyrb`). It is Cambridge, Massachusetts data—not Chicago
data. The project extracts the public API, validates and transforms incidents,
loads them into PostgreSQL, and visualizes reported-entry trends in a local
interactive Streamlit dashboard.

The dashboard uses interactive Plotly charts in a Streamlit web app. It can run
locally before any AWS account or deployment is configured.

## Quick start on Windows (no Docker or AWS)

Install **Git** and **Python 3.11 or 3.12** first. Then run these commands in
PowerShell to clone the project and launch the dashboard:

```powershell
git clone https://github.com/Ayushman125/crime-trends-dashboard.git
cd crime-trends-dashboard
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-local.ps1
```

The script creates a project-local virtual environment, installs the
requirements, and starts Streamlit. Open **http://localhost:8501**. Stop the
dashboard with **Ctrl+C** in that PowerShell window. No `.env` or database
setup is needed for this local preview; it fetches data from the public
Cambridge API. An internet connection is required.

On macOS/Linux, install Git and Python 3.11 or 3.12, then run:

```bash
git clone https://github.com/Ayushman125/crime-trends-dashboard.git
cd crime-trends-dashboard
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

Open **http://localhost:8501** and stop with **Ctrl+C**.

The dashboard fetches the Cambridge API on first load, rechecks hourly while
the page is open, and refreshes its cache at least every six hours. It also has
a **Refresh data now** button.

### What's on the dashboard

- KPI cards for reported incidents, selected-period change vs. the previous
  period, distinct incident types, reported locations, and latest incident.
- Interactive daily, weekly, monthly, and yearly incident trend. The selector
  also changes the time scale of the incident-type composition chart.
- Stacked area chart for the five most frequent selected-period incident types;
  less frequent types are grouped as “Other”.
- Ranked incident-type and street/location charts.
- Day-of-week by hour heatmap.
- Date and incident-type filters, top-location control, and recent-record
  table (free-text descriptions are deliberately excluded).
- A rolling 30-day default date range, a past-24-hours incident KPI, source
  snapshot retrieval time, and warnings when either the pipeline snapshot or
  city feed is stale.
- Responsive layout, a restrained navy/teal palette, and clear caveats.

The source does not contain verified latitude/longitude or neighborhood
boundaries, so the dashboard does not invent a map or claim geospatial hotspots.
These are police-log entries, not a complete measure of crime rates or risk.
Use the date filter to choose the historical window, then select Daily, Weekly,
Monthly, or Yearly above the trend chart. A weekday/weekend comparison or a
day-of-week chart can be added, but the current weekday-by-hour heatmap already
covers those dimensions. A map should only be added after obtaining and
validating coordinates or an authoritative boundary join; the log's street
labels alone are not sufficient.
When checked on November 16, 2026, the live city API's newest incident was
November 2 (last source update November 5). The dashboard correctly shows zero
incidents in the past 24 hours and a source-delay warning for that snapshot.
Refreshing or scheduling an ETL cannot create newer incidents until the city
publishes them.

## Project architecture

```text
Cambridge Socrata API
     ├── Local dashboard mode: Streamlit -> Plotly charts in your browser
     ├── Docker mode: Python ETL -> PostgreSQL -> Streamlit dashboard
     └── AWS ETL mode: Lambda + six-hour schedule -> private S3 snapshot
```

The dashboard chooses its data source using environment variables:

1. `DATA_BUCKET` set: read the safe, JSON Lines snapshot from private S3 (AWS
   instance role/credentials are required).
2. Else `DB_HOST` set: query selected non-description columns from PostgreSQL.
3. Else: fetch and transform the public Socrata API directly. This local mode
   checks the API at least every six hours while the dashboard remains open.

The default local command uses option 3, making it the fastest way to preview
the visuals. Docker Compose includes PostgreSQL/ETL plus a dashboard service
that reads the database. The AWS Lambda job stores a sanitized snapshot in S3.

## Run the assignment's Docker ETL and dashboard

Docker is optional for the local preview above, but is used by the assignment's
containerized PostgreSQL pipeline.

1. Install and start Docker Desktop.
2. In PowerShell, copy the example environment file and edit the password:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

3. Build and start PostgreSQL, the scheduled ETL worker, and the dashboard:

   ```powershell
   docker compose up --build -d
   docker compose ps
   docker compose logs -f etl
   ```

4. Open `http://localhost:8501`. The dashboard queries the safe columns in
   PostgreSQL. The ETL runs once at start, then repeats daily.
5. To stop the services and preserve database contents:

   ```powershell
   docker compose down
   ```

   Do not use `docker compose down -v` unless you intend to delete the database
   volume and its data.

## Test the code locally

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover API record normalization, required-field validation,
deduplication, timestamp conversion, description exclusion, and the AWS S3
payload. The AWS transform was also tested against the live public API.

## AWS: host the Docker Compose stack on EC2

The full Compose stack (PostgreSQL, ETL, and dashboard) can run on one EC2
instance. For this small project, start with Ubuntu 24.04 on a `t3.small` (2 GiB
RAM) with an encrypted 16 GiB EBS volume. Check the current `us-east-1` price
before launch; an EC2 instance, storage, and public IPv4 address can incur
charges. A `$30/month` AWS Budget alert is a warning, not a spending cap, and
credits may not cover every charge.

1. In AWS Billing, create a monthly budget alert at `$24` and confirm your
   remaining credit and eligible services. Stop or terminate resources manually
   before charges exceed your limit.
2. Launch Ubuntu 24.04 on EC2. Use a key pair you control, encrypted storage,
   and a security group that allows **SSH (port 22) only from your current
   public IP**. Do not open dashboard port `8501` or PostgreSQL port `5432` to
   the internet. Use `t3.small` or larger; smaller instances may run out of
   memory with all three containers.
3. Install Docker Compose on the instance and upload the application files.
   From PowerShell in the project folder, package only the required files (this
   deliberately excludes `.env`, the notebook, and `.venv`):

   ```powershell
   Compress-Archive -Path dashboard.py,Dockerfile,docker-compose.yml,.dockerignore,requirements.txt,src,.streamlit,.env.example -DestinationPath deploy.zip -Force
   scp -i "$HOME\.ssh\crime-project.pem" .\deploy.zip ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
   ```

   On EC2, install and start Docker, then unpack the upload:

   ```bash
   sudo apt-get update
   sudo apt-get install -y docker.io docker-compose-v2 unzip
   sudo systemctl enable --now docker
   unzip -o deploy.zip -d crime-trends
   cd crime-trends
   cp .env.example .env
   nano .env
   ```

   Set a unique, strong `POSTGRES_PASSWORD` in `.env`, and keep
   `DASHBOARD_BIND_ADDRESS=127.0.0.1`. Never upload `.env` or put cloud
   credentials in it.
4. Build and start the services:

   ```bash
   sudo docker compose up --build -d
   sudo docker compose ps
   sudo docker compose logs -f etl
   ```

5. From your Windows machine, create a private SSH tunnel:

   ```powershell
   ssh -i "$HOME\.ssh\crime-project.pem" -L 8501:127.0.0.1:8501 ubuntu@<EC2_PUBLIC_IP>
   ```

   Keep that SSH window open and visit `http://localhost:8501`. The dashboard
   remains private; only SSH is reachable from your IP.
6. To stop the containers but keep their database volume, run
   `sudo docker compose down`. The EC2 instance still incurs charges until it
   is stopped or terminated. Terminating it deletes its root disk and database
   unless you have made a snapshot or backup.

The Compose dashboard reads from PostgreSQL; the ETL starts at container
startup and then runs daily. The local Docker image runs as a non-root user and
the image build excludes local environments, notebooks, and secrets.

### Optional scheduled AWS S3 ETL

The supplied [SAM template](./template.yaml) independently creates a scheduled
Lambda that writes a private, description-free snapshot to S3. This is optional
when the EC2 Compose ETL is running. To deploy it, install AWS SAM CLI and use
an authorized AWS profile:

```powershell
$env:AWS_PROFILE = "crime-project"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sts get-caller-identity
sam validate --lint
sam build
sam deploy --guided --capabilities CAPABILITY_IAM
```

EventBridge runs that Lambda every six hours, with retries. Run it manually
once after deployment to create the first snapshot. Existing stacks need
`sam build` and `sam deploy` to receive schedule and metadata changes. Check
the dashboard's snapshot time and warning banner. Remove the SAM stack and
confirm its S3 contents before deleting the data.

## Assignment design decisions

- **Extract:** fetch every page from the public Socrata API; retry transient
  failures.
- **Validate:** require incident ID, type, location, and date/time; surface
  malformed required data instead of silently reporting false counts.
- **Transform:** normalize text, convert zoned timestamps to Cambridge local
  time, derive year/month/day/hour/weekday, and deduplicate by ID.
- **Load:** use PostgreSQL upserts for the containerized local pipeline. The
  AWS version writes a single sanitized JSONL snapshot to private S3.
- **Schedule:** Streamlit checks hourly, cached data expires after six hours,
  and AWS ETL runs every six hours. These schedules refresh the pipeline, but
  they cannot produce incidents missing from the city's source feed.
- **Privacy:** free-text descriptions are excluded from dashboard queries and
  the AWS snapshot. Raw PostgreSQL retains the source description for ETL
  completeness; restrict database access.
- **Geography:** street labels are categories, not coordinates. Add a map only
  after joining a documented, authoritative geography source and validating
  match coverage.
- **Interpretation:** counts represent reports in the source log. They do not
  establish causality, crime rates, or individual risk.
