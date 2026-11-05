from dotenv import load_dotenv
load_dotenv()
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import os
from collections import defaultdict, Counter
from datetime import datetime, timedelta
import statistics

token = os.environ.get("GITHUB_TOKEN")
if not token:
    raise ValueError("GITHUB_TOKEN environment variable is not set.")
headers = {"Authorization": f"Bearer {token}"}

MIN_RUNS = 5  # skip medians based on fewer runs than this

cutoff_date = datetime.now() - timedelta(days=90)  # 90 days ago
cutoff_date = cutoff_date.strftime("%Y-%m-%d")  # format as ISO 8601 string
print(f"Cutoff date for runs: {cutoff_date}")

def create_retry_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=5,  # Total number of retries to allow
        backoff_factor=1,  # Wait times: 1s, 2s, 4s, 8s, 16s... (1 * 2^attempt)
        status_forcelist=[429, 500, 502, 503, 504],  # HTTP status codes to retry
        allowed_methods=["GET"],  # HTTP methods to retry
        raise_on_status=False,  # Return the final response instead of raising MaxRetryError
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

session = create_retry_session()

def get_all_runs():
    url = "https://api.github.com/repos/actions/runner/actions/runs?branch=main&per_page=100&created=>=" + cutoff_date
    all_runs = []
    while url:
        response = session.get(url, headers=headers, timeout=(3.05, 10))
        response.raise_for_status()
        data = response.json()
        all_runs.extend(data["workflow_runs"])
        url = response.links.get("next", {}).get("url")
        print(f"Total so far: {len(all_runs)}")
    return all_runs

summary = defaultdict(Counter)  # path -> conclusion counts
duration_times = defaultdict(list)  # path -> durations in seconds
times = defaultdict(list)  # (path, conclusion) -> durations in minutes
long_failures = []  # messages for failures that ran longer than a day
oldest_date = None

def summarize_conclusions_by_path(run):
    duration = get_duration(run)
    if duration is not None:
        duration_times[run["path"]].append(duration)
    if run["conclusion"] is not None:
        summary[run["path"]][run["conclusion"]] += 1

def print_conclusions_by_path():
    for path, conclusions in sorted(summary.items(), key=lambda x: duration_times[x[0]]):
        done = conclusions["success"] + conclusions["failure"]
        fail_rate = (conclusions["failure"] / done) * 100 if done > 0 else 0
        median_duration = statistics.median(duration_times[path]) if duration_times[path] else 0
        if median_duration > 60:
            median_duration = f"{median_duration / 60:.2f} minutes"
        else:
            median_duration = f"{median_duration} seconds"
        print(f"Path: {path}, Success: {conclusions['success']}, Failure: {conclusions['failure']}, Action Required: {conclusions['action_required']}, Fail Rate: {fail_rate:.2f}%, Median Duration: {median_duration}, Total Runs: {len(duration_times[path])}")

def compare_success_vs_failure(run):
    if run["conclusion"] not in ("success", "failure"):
        return
    seconds = get_duration(run)
    if seconds is None:
        return
    times[(run["path"], run["conclusion"])].append(seconds / 60)

    if run["conclusion"] == "failure" and seconds is not None and seconds > 86400:
        long_failures.append(f"Failure: {run['path']} with duration {seconds / 60:.2f} minutes, run ID: {run['id']}, URL: {run['html_url']}, created at: {run['created_at']}, updated at: {run['updated_at']}, head_sha: {run['head_sha']}, run_started_at: {run['run_started_at']}")

def print_success_vs_failure():
    for message in long_failures:
        print(message)

    rows = []
    paths = sorted({path for path, _ in times})
    print(f"\n{'Path':55} {'OK runs':>8} {'Fail runs':>10} {'Med OK':>8} {'Med Fail':>9} {'Fail/OK':>8}")
    for path in paths:
        ok = times.get((path, "success"), [])
        bad = times.get((path, "failure"), [])
        med_ok = statistics.median(ok) if len(ok) >= MIN_RUNS else None
        med_bad = statistics.median(bad) if len(bad) >= MIN_RUNS else None
        ratio = med_bad / med_ok if med_ok and med_bad is not None else None
        fmt = lambda v, f: f"{v:{f}}" if v is not None else "-"
        print(f"{path:55} {len(ok):>8} {len(bad):>10} {fmt(med_ok, '8.2f'):>8} "
              f"{fmt(med_bad, '9.2f'):>9} {fmt(ratio, '7.2f') + ('x' if ratio is not None else ''):>8}")
        row = [path, len(ok), len(bad), round(med_ok, 2) if med_ok is not None else ' ', round(med_bad, 2) if med_bad is not None else ' ', round(ratio, 2) if ratio is not None else ' ']
        rows.append(row)

    with open("summary.csv", "w", encoding="utf-8") as f:
        f.write("Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\n")
        for row in rows:
            f.write(",".join(str(x) for x in row) + "\n")

def get_duration(run):
    if run["conclusion"] is not None:
        start_time = run.get("run_started_at")
        end_time = run.get("updated_at")
        if start_time and end_time:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            duration = (end_dt - start_dt).total_seconds()
            return duration

def find_oldest_date(run):
    global oldest_date
    start_time = run.get("created_at")
    if start_time:
        start_dt = datetime.fromisoformat(start_time)
        if oldest_date is None or start_dt < oldest_date:
            oldest_date = start_dt

try:
    all_runs = get_all_runs()
    for run in all_runs:
        find_oldest_date(run)
        summarize_conclusions_by_path(run)
        compare_success_vs_failure(run)
    print(f"Oldest run date: {oldest_date} Total runs retrieved: {len(all_runs)}")
    print_conclusions_by_path()
    print_success_vs_failure()
except requests.exceptions.HTTPError as e:
    r = e.response
    if r is not None and r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
        reset = int(r.headers.get("X-RateLimit-Reset", 0))
        print(f"Rate limit hit. Resets at epoch {reset}.")
    else:
        print(f"Request failed: {e}")
except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")