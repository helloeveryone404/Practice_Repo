"""Report GitHub Actions run statistics for a repository and write a CSV summary."""

import csv
import io
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

MIN_RUNS = 5  # skip medians based on fewer runs than this
LONG_FAILURE_SECONDS = 86400  # failures longer than a day are listed individually
DEFAULT_REPO = "actions/runner"
LOOKBACK_DAYS = 90
OUTPUT_PATH = "summary.csv"
CSV_HEADER = ["Path", "OK runs", "Fail runs", "Med OK", "Med Fail", "Fail/OK"]
API_RESULT_CAP = 1000  # GitHub returns at most this many results per filtered query


# --- Input/output: thin wrappers that tests can replace -----------------------

def load_environment():
    """Load variables from a .env file into the process environment."""
    load_dotenv()


def create_retry_session():
    """Return a requests.Session that retries GETs on rate limits and server errors."""
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


def fetch_all_runs(session, url, headers):
    """Fetch pages of workflow runs from url until no next page or API_RESULT_CAP runs; return (runs, total_count or None)."""
    all_runs = []
    total_count = None
    while url:
        response = session.get(url, headers=headers, timeout=(3.05, 10))
        response.raise_for_status()
        data = response.json()
        if total_count is None:
            total_count = data.get("total_count")
        all_runs.extend(data["workflow_runs"])
        # GitHub serves nothing past API_RESULT_CAP but can still send a "next" link to an empty page
        url = response.links.get("next", {}).get("url") if len(all_runs) < API_RESULT_CAP else None
        print(f"Total so far: {len(all_runs)}")
    return all_runs, total_count


def write_text(path, text):
    """Write text to path as UTF-8, replacing any existing file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --- Pure logic: data in, data out --------------------------------------------

def get_token(env):
    """Return GITHUB_TOKEN from the env mapping; raise ValueError if it is missing or empty."""
    token = env.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set.")
    return token


def build_headers(token):
    """Return the HTTP headers that authenticate GitHub API requests with token."""
    return {"Authorization": f"Bearer {token}"}


def compute_cutoff_date(now, days):
    """Return the date `days` before the datetime `now` as a YYYY-MM-DD string."""
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")


def build_runs_url(repo, cutoff_date):
    """Return the first-page URL for repo's main-branch runs created on or after cutoff_date."""
    return f"https://api.github.com/repos/{repo}/actions/runs?branch=main&per_page=100&created=>=" + cutoff_date


def get_duration(run):
    """Return a finished run's duration in seconds, or None if it has no conclusion or timestamps."""
    if run["conclusion"] is not None:
        start_time = run.get("run_started_at")
        end_time = run.get("updated_at")
        if start_time and end_time:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            duration = (end_dt - start_dt).total_seconds()
            return duration
    return None


@dataclass
class RunTotals:
    """Running totals that analyze_runs builds up one run at a time."""
    oldest_date: datetime | None = None
    summary: defaultdict = field(default_factory=lambda: defaultdict(Counter))  # path -> Counter of conclusions
    duration_times: defaultdict = field(default_factory=lambda: defaultdict(list))  # path -> durations in seconds
    times: defaultdict = field(default_factory=lambda: defaultdict(list))  # (path, conclusion) -> durations in minutes
    long_failures: list = field(default_factory=list)  # messages for failures longer than the threshold


def update_oldest_date(totals, run):
    """Set totals.oldest_date to run's created_at if that is earlier than the current value."""
    start_time = run.get("created_at")
    if not start_time:
        return
    start_dt = datetime.fromisoformat(start_time)
    if totals.oldest_date is None or start_dt < totals.oldest_date:
        totals.oldest_date = start_dt


def add_conclusion(totals, run, duration):
    """Record run's duration (if any) and its conclusion (if finished) under its path in totals."""
    if duration is not None:
        totals.duration_times[run["path"]].append(duration)
    if run["conclusion"] is not None:
        totals.summary[run["path"]][run["conclusion"]] += 1


def build_long_failure_message(run, duration):
    """Return the message printed for a failed run that took duration seconds, longer than the long-failure threshold."""
    return (f"Failure: {run['path']} with duration {duration / 60:.2f} minutes, "
            f"run ID: {run['id']}, URL: {run['html_url']}, "
            f"created at: {run['created_at']}, updated at: {run['updated_at']}, "
            f"head_sha: {run['head_sha']}, run_started_at: {run['run_started_at']}")


def add_success_failure_time(totals, run, duration, long_failure_seconds):
    """Record a success or failure run's duration in minutes in totals, and a message if it is a long failure."""
    if run["conclusion"] not in ("success", "failure"):
        return
    if duration is None:
        return
    totals.times[(run["path"], run["conclusion"])].append(duration / 60)
    if run["conclusion"] == "failure" and duration > long_failure_seconds:
        totals.long_failures.append(build_long_failure_message(run, duration))


def analyze_runs(all_runs, long_failure_seconds):
    """Loop over all_runs once and return the RunTotals built from them."""
    totals = RunTotals()
    for run in all_runs:
        duration = get_duration(run)
        update_oldest_date(totals, run)
        add_conclusion(totals, run, duration)
        add_success_failure_time(totals, run, duration, long_failure_seconds)
    return totals


def format_conclusion_lines(summary, duration_times):
    """Return one 'Path: ...' report line per path, sorted by path name; the median is 'n/a' when a path has no
    durations, and Total Runs counts every finished run (all conclusions) for the path."""
    lines = []
    for path, conclusions in sorted(summary.items(), key=lambda item: item[0]):
        durations = duration_times.get(path, [])
        done = conclusions["success"] + conclusions["failure"]
        fail_rate = (conclusions["failure"] / done) * 100 if done > 0 else 0
        if not durations:
            median_duration = "n/a"
        else:
            median = statistics.median(durations)
            median_duration = f"{median / 60:.2f} minutes" if median > 60 else f"{median} seconds"
        lines.append(f"Path: {path}, Success: {conclusions['success']}, Failure: {conclusions['failure']}, Action Required: {conclusions['action_required']}, Fail Rate: {fail_rate:.2f}%, Median Duration: {median_duration}, Total Runs: {sum(conclusions.values())}")
    return lines


def build_comparison(times, min_runs):
    """Return (path, ok count, fail count, median ok, median fail, ratio) per path, sorted by path."""
    comparison = []
    for path in sorted({path for path, _ in times}):
        ok = times.get((path, "success"), [])
        bad = times.get((path, "failure"), [])
        med_ok = statistics.median(ok) if len(ok) >= min_runs else None
        med_bad = statistics.median(bad) if len(bad) >= min_runs else None
        ratio = med_bad / med_ok if med_ok and med_bad is not None else None
        comparison.append((path, len(ok), len(bad), med_ok, med_bad, ratio))
    return comparison


def format_comparison_lines(comparison):
    """Return the success-vs-failure table as lines: a header (starting with a blank line), then one row per path."""
    def fmt(value, spec):
        return f"{value:{spec}}" if value is not None else "-"

    lines = [f"\n{'Path':55} {'OK runs':>8} {'Fail runs':>10} {'Med OK':>8} {'Med Fail':>9} {'Fail/OK':>8}"]
    for path, ok_count, bad_count, med_ok, med_bad, ratio in comparison:
        lines.append(f"{path:55} {ok_count:>8} {bad_count:>10} {fmt(med_ok, '8.2f'):>8} "
                     f"{fmt(med_bad, '9.2f'):>9} {fmt(ratio, '7.2f') + ('x' if ratio is not None else ''):>8}")
    return lines


def build_csv_text(comparison):
    """Return the CSV text (header plus one row per path); values with commas or quotes are quoted, missing values are empty."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for path, ok_count, bad_count, med_ok, med_bad, ratio in comparison:
        writer.writerow([path, ok_count, bad_count,
                         round(med_ok, 2) if med_ok is not None else "",
                         round(med_bad, 2) if med_bad is not None else "",
                         round(ratio, 2) if ratio is not None else ""])
    return buffer.getvalue()


def build_truncation_warning(total_count, fetched_count):
    """Return a warning if the API reported more runs than were fetched or the fetch hit API_RESULT_CAP, else None."""
    if total_count is not None and total_count > fetched_count:
        detail = f"GitHub reported {total_count:,} matching runs but returned only {fetched_count:,}"
    elif fetched_count >= API_RESULT_CAP:
        detail = f"GitHub returned {fetched_count:,} runs, which reaches its result cap"
    else:
        return None
    return (f"WARNING: {detail} (the API returns at most {API_RESULT_CAP:,} results per query); "
            f"the oldest runs in the window may be missing.")


def build_report_lines(totals, run_count, total_count, comparison):
    """Return the report lines printed after fetching, in order: totals, cap warning, per-path lines, long failures, table."""
    lines = [f"Oldest run date: {totals.oldest_date} Total runs retrieved: {run_count}"]
    warning = build_truncation_warning(total_count, run_count)
    if warning:
        lines.append(warning)
    lines.extend(format_conclusion_lines(totals.summary, totals.duration_times))
    lines.extend(totals.long_failures)
    lines.extend(format_comparison_lines(comparison))
    return lines


def describe_request_error(error):
    """Return the message to print for a failed request: rate-limit details or the error text."""
    if isinstance(error, requests.exceptions.HTTPError):
        r = error.response
        if r is not None and r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", 0))
            return f"Rate limit hit. Resets at epoch {reset}."
    return f"Request failed: {error}"


# --- Entry point ----------------------------------------------------------------

def main(repo=DEFAULT_REPO, output_path=OUTPUT_PATH, days=LOOKBACK_DAYS, min_runs=MIN_RUNS, now=None):
    """Fetch repo's recent runs, print the report, and write the CSV to output_path; return 0, or 1 if a request failed."""
    load_environment()
    token = get_token(os.environ)
    cutoff_date = compute_cutoff_date(now or datetime.now(), days)
    print(f"Cutoff date for runs: {cutoff_date}")
    session = create_retry_session()
    try:
        all_runs, total_count = fetch_all_runs(session, build_runs_url(repo, cutoff_date), build_headers(token))
        totals = analyze_runs(all_runs, LONG_FAILURE_SECONDS)
        comparison = build_comparison(totals.times, min_runs)
        for line in build_report_lines(totals, len(all_runs), total_count, comparison):
            print(line)
        write_text(output_path, build_csv_text(comparison))
    except requests.exceptions.RequestException as e:
        print(describe_request_error(e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
