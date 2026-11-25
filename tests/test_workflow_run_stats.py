import runpy
import types
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests
import workflow_run_stats
from workflow_run_stats import (
    RunTotals,
    add_conclusion,
    add_success_failure_time,
    analyze_runs,
    build_comparison,
    build_csv_text,
    build_headers,
    build_jobs_url,
    build_long_failure_message,
    build_report_lines,
    build_runs_url,
    build_truncation_warning,
    compute_cutoff_date,
    describe_request_error,
    drop_failures_without_jobs,
    format_comparison_lines,
    format_conclusion_lines,
    get_duration,
    get_token,
    update_oldest_date,
    write_text,
)

HEADERS = {"Authorization": "Bearer test-token"}
JOBS_URL_1111 = "https://api.github.com/repos/test/test-repo/actions/runs/1111/jobs?filter=all&per_page=100"


def test_load_environment_calls_load_dotenv(monkeypatch):
    calls = []
    monkeypatch.setattr(workflow_run_stats, "load_dotenv", lambda: calls.append("called"))

    workflow_run_stats.load_environment()

    assert calls == ["called"]


@pytest.mark.parametrize("prefix", ["http://", "https://"])
def test_create_retry_session_mounts_retry_adapter(prefix):
    session = workflow_run_stats.create_retry_session()

    retries = session.get_adapter(prefix + "example.test").max_retries

    assert isinstance(session, requests.Session)
    assert retries.total == 5
    assert retries.backoff_factor == 1
    assert list(retries.status_forcelist) == [429, 500, 502, 503, 504]
    assert list(retries.allowed_methods) == ["GET"]
    assert retries.raise_on_status is False



def make_page(data, next_url=None):
    links = {"next": {"url": next_url}} if next_url else {}
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data, links=links)


def make_paged_session(pages):
    """Return (session, calls); session.get returns the page registered for the requested url."""
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return pages[url]

    return SimpleNamespace(get=fake_get), calls


def test_fetch_all_runs_follows_next_links_and_keeps_first_total_count(capsys):
    pages = {
        "https://example.test/page1": make_page(
            {"total_count": 3, "workflow_runs": [{"id": 1}, {"id": 2}]}, "https://example.test/page2"
        ),
        # total_count differs on page 2 on purpose; only the first one counts
        "https://example.test/page2": make_page({"total_count": 99, "workflow_runs": [{"id": 3}]}),
    }
    session, calls = make_paged_session(pages)

    runs, total_count = workflow_run_stats.fetch_all_runs(session, "https://example.test/page1", HEADERS)

    assert runs == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert total_count == 3
    assert calls == [
        ("https://example.test/page1", HEADERS, (3.05, 10)),
        ("https://example.test/page2", HEADERS, (3.05, 10)),
    ]
    assert capsys.readouterr().out == "Total so far: 2\nTotal so far: 3\n"


def test_fetch_all_runs_total_count_missing_returns_none():
    session, _ = make_paged_session({"https://example.test/p": make_page({"workflow_runs": [{"id": 1}]})})

    runs, total_count = workflow_run_stats.fetch_all_runs(session, "https://example.test/p", HEADERS)

    assert runs == [{"id": 1}]
    assert total_count is None


def test_fetch_all_runs_stops_at_result_cap_even_with_next_link():
    full_page = [{"id": i} for i in range(workflow_run_stats.API_RESULT_CAP)]
    pages = {
        "https://example.test/page1": make_page(
            {"total_count": 5000, "workflow_runs": full_page}, "https://example.test/page2"
        ),
    }
    session, calls = make_paged_session(pages)

    runs, total_count = workflow_run_stats.fetch_all_runs(session, "https://example.test/page1", HEADERS)

    assert len(runs) == 1000
    assert total_count == 5000
    assert [call[0] for call in calls] == ["https://example.test/page1"]


def test_fetch_all_runs_empty_url_makes_no_request():
    session, calls = make_paged_session({})

    runs, total_count = workflow_run_stats.fetch_all_runs(session, "", HEADERS)

    assert (runs, total_count) == ([], None)
    assert calls == []


def test_fetch_all_runs_propagates_request_error():
    def raise_error():
        raise requests.exceptions.HTTPError("boom")

    session = SimpleNamespace(get=lambda url, headers, timeout: SimpleNamespace(raise_for_status=raise_error))

    with pytest.raises(requests.exceptions.HTTPError, match="boom"):
        workflow_run_stats.fetch_all_runs(session, "https://example.test/page1", HEADERS)



LONG_FAILURE_RUN = {
    "id": 1111,
    "conclusion": "failure",
    "run_started_at": "2021-01-03T00:00:00Z",
    "updated_at": "2021-01-03T00:01:00Z",  # 60 seconds, above the limit of 30
}
SUCCESS_RUN = {
    "id": 3333,
    "conclusion": "success",
    "run_started_at": "2021-01-03T00:00:00Z",
    "updated_at": "2021-01-03T00:02:00Z",  # 120 seconds
}
SHORT_FAILURE_RUN = {
    "id": 4444,
    "conclusion": "failure",
    "run_started_at": "2021-01-03T00:00:00Z",
    "updated_at": "2021-01-03T00:00:10Z",  # 10 seconds, below the limit
}
BOUNDARY_FAILURE_RUN = {
    "id": 5555,
    "conclusion": "failure",
    "run_started_at": "2021-01-03T00:00:00Z",
    "updated_at": "2021-01-03T00:00:30Z",  # exactly 30 seconds, not above the limit
}
NO_TIMESTAMPS_FAILURE_RUN = {"id": 6666, "conclusion": "failure"}
UNFINISHED_RUN = {
    "id": 7777,
    "conclusion": None,
    "run_started_at": "2021-01-03T00:00:00Z",
    "updated_at": "2021-01-03T00:05:00Z",
}


@pytest.mark.parametrize(
    "total_count, runs, long_failure_seconds, expected_kept_runs, expected_skip_count, expected_calls",
    [
        pytest.param(0, [LONG_FAILURE_RUN], 30, [], 1, [JOBS_URL_1111], id="skipped_no_jobs"),
        pytest.param(1, [LONG_FAILURE_RUN], 30, [LONG_FAILURE_RUN], 0, [JOBS_URL_1111], id="kept_has_jobs"),
        pytest.param(0, [SUCCESS_RUN], 30, [SUCCESS_RUN], 0, [], id="success_run_no_request"),
        pytest.param(0, [SHORT_FAILURE_RUN], 30, [SHORT_FAILURE_RUN], 0, [], id="short_failure_no_request"),
        pytest.param(0, [BOUNDARY_FAILURE_RUN], 30, [BOUNDARY_FAILURE_RUN], 0, [], id="boundary_equal_no_request"),
        pytest.param(0, [NO_TIMESTAMPS_FAILURE_RUN], 30, [NO_TIMESTAMPS_FAILURE_RUN], 0, [], id="no_timestamps_no_request"),
        pytest.param(0, [UNFINISHED_RUN], 30, [UNFINISHED_RUN], 0, [], id="unfinished_no_request"),
        pytest.param(0, [], 30, [], 0, [], id="no_runs"),
    ],
)
def test_drop_failures_without_jobs(
    total_count, runs, long_failure_seconds, expected_kept_runs, expected_skip_count, expected_calls
):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"total_count": total_count},
        )

    session = SimpleNamespace(get=fake_get)

    kept_runs, skipped_count = drop_failures_without_jobs(
        session, HEADERS, runs, "test/test-repo", long_failure_seconds
    )

    assert kept_runs == expected_kept_runs
    assert skipped_count == expected_skip_count
    assert calls == [(url, HEADERS, (3.05, 10)) for url in expected_calls]


def test_drop_failures_without_jobs_mixed_runs_keeps_order():
    # One fake session answers differently per run: run 1111 has no jobs, run 8888 has jobs.
    other_failure = {**LONG_FAILURE_RUN, "id": 8888}
    totals = {1111: 0, 8888: 4}

    def fake_get(url, headers, timeout):
        run_id = int(url.split("/runs/")[1].split("/")[0])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"total_count": totals[run_id]},
        )

    kept_runs, skipped_count = drop_failures_without_jobs(
        SimpleNamespace(get=fake_get),
        HEADERS,
        [SUCCESS_RUN, LONG_FAILURE_RUN, other_failure],
        "test/test-repo",
        30,
    )

    assert kept_runs == [SUCCESS_RUN, other_failure]
    assert skipped_count == 1


def test_drop_failures_without_jobs_propagates_request_error():
    def fake_get(url, headers, timeout):
        def raise_error():
            raise requests.exceptions.HTTPError("boom")

        return SimpleNamespace(raise_for_status=raise_error)

    with pytest.raises(requests.exceptions.HTTPError, match="boom"):
        drop_failures_without_jobs(
            SimpleNamespace(get=fake_get), HEADERS, [LONG_FAILURE_RUN], "test/test-repo", 30
        )


def test_write_text(tmp_path):
    tmp_file = tmp_path/'out.csv'
    write_text(tmp_file, 'test_text_first')
    result1 = tmp_file.read_text(encoding="utf-8")
    assert result1 == "test_text_first"
    write_text(tmp_file, 'test_text_second')
    result2 = tmp_file.read_text(encoding="utf-8")
    assert result2 == "test_text_second"


def test_get_token_returns_token():
    env = {"GITHUB_TOKEN": "test_token"}
    assert get_token(env) == "test_token"

@pytest.mark.parametrize("env", [
    pytest.param({}, id='Missing_Token'),
    pytest.param({"GITHUB_TOKEN": ""}, id='Empty_Token'),
])
def test_get_token_raises_error(env):
    with pytest.raises(ValueError, match="GITHUB_TOKEN environment variable is not set"):
        get_token(env)


def test_build_headers():
    token = "test_token"
    headers = build_headers(token)
    assert headers["Authorization"] == "Bearer test_token"


@pytest.mark.parametrize("now, days, expected", [
    pytest.param(datetime(2023, 1, 10, 0, 0, 0), 90, "2022-10-12", id='Year_Boundary'),
    pytest.param(datetime(2024, 4, 15, 12, 0, 0), 90, "2024-01-16", id='Leap_Year'),
    pytest.param(datetime(2023, 4, 15, 12, 0, 0), 90, "2023-01-15", id='Regular_Year'),
    pytest.param(datetime(2025, 4, 1, 0, 0, 0), 30, "2025-03-02", id='Beginning_of_Day'),
    pytest.param(datetime(2025, 4, 1, 23, 59, 59), 30, "2025-03-02", id='End_of_Day'),
    ],
)
def test_compute_cutoff_date(now, days, expected):
    assert compute_cutoff_date(now, days) == expected


def test_build_runs_url():
    repo = "test_repo"
    cutoff_date = "2023-01-01T00:00:00Z"
    url = build_runs_url(repo, cutoff_date)
    assert url == "https://api.github.com/repos/test_repo/actions/runs?branch=main&per_page=100&created=>=2023-01-01T00:00:00Z"


def test_build_jobs_url():
    repo = "test-owner/test_repo"
    run_id = 12345
    url = build_jobs_url(repo, run_id)
    assert url == "https://api.github.com/repos/test-owner/test_repo/actions/runs/12345/jobs?filter=all&per_page=100"


@pytest.mark.parametrize("conclusion, start_time, end_time, expected_duration", [
    pytest.param("success", "2023-01-01T00:00:00Z", "2023-01-01T00:05:00Z", 300, id='Success'),
    pytest.param("failure", "2023-01-01T00:00:00Z", "2023-01-01T00:10:00Z", 600, id='Failure'),
    pytest.param("", "2023-01-01T00:00:00Z", "2023-01-01T00:15:00Z", 900, id='Empty_Conclusion'),
])
def test_get_duration_success(conclusion, start_time, end_time, expected_duration):
    run = {
        "conclusion": conclusion,
        "run_started_at": start_time,
        "updated_at": end_time
    }
    assert get_duration(run) == expected_duration

@pytest.mark.parametrize("run", [
    pytest.param({"conclusion": None, "run_started_at": "2023-01-01T00:00:00Z", "updated_at": "2023-01-01T00:05:00Z"}, id='None_Conclusion'),
    pytest.param({"conclusion": "success", "updated_at": "2023-01-01T00:05:00Z"}, id="Missing_Start_Key"),
    pytest.param({"conclusion": "success", "run_started_at": "", "updated_at": "2023-01-01T00:05:00Z"}, id='Success_Empty_Start_Time'),
    pytest.param({"conclusion": "success", "run_started_at": None, "updated_at": "2023-01-01T00:05:00Z"}, id='Success_None_Start_Time'),
    pytest.param({"conclusion": "success", "run_started_at": "2023-01-01T00:00:00Z",}, id='Missing_End_Time'),
    pytest.param({"conclusion": "success", "run_started_at": "2023-01-01T00:00:00Z", "updated_at": ""}, id='Success_Empty_End_Time'),
    pytest.param({"conclusion": "success", "run_started_at": "2023-01-01T00:00:00Z", "updated_at": None}, id='Success_None_End_Time'),
])
def test_get_duration_returns_none(run):
    assert get_duration(run) is None


@pytest.mark.parametrize("start, run, expected", [
    pytest.param(None, {"created_at": "2023-01-01T00:00:00Z"}, datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC), id='First_Date_Is_Set'),
    pytest.param("2022-01-01T00:00:00Z", {"created_at": "2023-01-01T00:00:00Z"}, datetime(2022, 1, 1, 0, 0, 0, tzinfo=UTC), id='Later_Date_Is_Ignored'),
    pytest.param("2023-01-01T00:00:00Z", {"created_at": "2022-01-01T00:00:00Z"}, datetime(2022, 1, 1, 0, 0, 0, tzinfo=UTC), id='Earlier_Date_Is_Updated'),
    pytest.param("2023-01-01T00:00:00Z", {"created_at": None}, datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC), id='Created_At_Is_None'),
    pytest.param("2023-01-01T00:00:00Z", {}, datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC), id='Created_At_Key_Missing'),
    pytest.param("2023-01-01T00:00:00Z", {"created_at": ""}, datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC), id='Created_At_Empty_String'),
    pytest.param(None, {}, None, id='None_Start_And_Missing_Created_At'),
])
def test_update_oldest_date(start, run, expected):
    totals = RunTotals()
    if start is not None:
        totals.oldest_date = datetime.fromisoformat(start)
    update_oldest_date(totals, run)
    assert totals.oldest_date == expected


@pytest.mark.parametrize("duration, conclusion, expected_duration, expected_conclusion", [
    pytest.param(300, "success", {"a.yml": [300]}, {"a.yml": {"success": 1}}, id='Both_Recorded'),
    pytest.param(None, None, {}, {}, id='Neither_Recorded'),
    pytest.param(600, None, {"a.yml": [600]}, {}, id='Only_Duration_Recorded'),
    pytest.param(None, "success", {}, {"a.yml": {"success": 1}}, id='Only_Conclusion_Recorded'),
    pytest.param(300, "failure", {"a.yml": [300]}, {"a.yml": {"failure": 1}}, id='Failure_Conclusion_Recorded'),
])
def test_add_conclusion(duration, conclusion, expected_duration, expected_conclusion):
    totals = RunTotals()
    run = {"path": "a.yml", "conclusion": conclusion}
    add_conclusion(totals, run, duration)
    assert dict(totals.duration_times) == expected_duration
    assert dict(totals.summary) == expected_conclusion

def test_add_conclusion_accumulation():
    totals = RunTotals()
    run1 = {"path": "a.yml", "conclusion": "success"}
    run2 = {"path": "a.yml", "conclusion": "failure"}
    run3 = {"path": "b.yml", "conclusion": "success"}
    add_conclusion(totals, run1, 300)
    add_conclusion(totals, run2, 400)
    add_conclusion(totals, run3, 500)
    assert dict(totals.duration_times) == {"a.yml": [300, 400], "b.yml": [500]}
    assert dict(totals.summary) == {"a.yml": {"success": 1, "failure": 1}, "b.yml": {"success": 1}}


def test_build_long_failure_message():
    run = {"path": "a.yml", "id": "123", "html_url": "https://example.com", "created_at": "2023-01-01T00:00:00Z", "updated_at": "2023-01-01T00:05:00Z", "head_sha": "98765", "run_started_at": "2023-01-01T00:01:00Z"}
    duration = 125
    message = build_long_failure_message(run, duration)
    assert message == "Failure: a.yml with duration 2.08 minutes, run ID: 123, URL: https://example.com, created at: 2023-01-01T00:00:00Z, updated at: 2023-01-01T00:05:00Z, head_sha: 98765, run_started_at: 2023-01-01T00:01:00Z"


@pytest.mark.parametrize("conclusion, duration, path, expected_message, expected_times", [
    pytest.param(None, 90, "a.yml", [], {}, id="None_conclusion"),
    pytest.param("success", None, "a.yml", [], {}, id="None_duration"),
    pytest.param("success", 15, "a.yml", [], {('a.yml', 'success'): [0.25]}, id="Success_duration_under"),
    pytest.param("success", 60, "a.yml", [], {('a.yml', 'success'): [1.0]}, id="Success_duration_over"),
    pytest.param("failure", 15, "a.yml", [], {('a.yml', 'failure'): [0.25]}, id="Failure_duration_under"),
    pytest.param("failure", 30, "a.yml", [], {('a.yml', 'failure'): [0.5]}, id="Equal_to_long_failure_seconds"),
    pytest.param("pending", 15, "a.yml", [], {}, id="Pending_conclusion"),
    pytest.param("failure", 90, "a.yml", ['Failure: a.yml with duration 1.50 minutes, run ID: 6754, URL: https://another_example.com, created at: 2024-01-01T00:00:00Z, updated at: 2024-01-01T00:05:00Z, head_sha: 38765, run_started_at: 2024-01-01T00:01:00Z'], {('a.yml', 'failure'): [1.5]}, id="Long_failure_message")
])
def test_add_success_failure_time(conclusion, duration, path, expected_message, expected_times):
    totals = RunTotals()
    run = {"conclusion":conclusion, "path":path, "id":6754, "html_url": "https://another_example.com", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:05:00Z", "head_sha": "38765", "run_started_at": "2024-01-01T00:01:00Z"}
    long_failure_seconds = 30
    add_success_failure_time(totals, run, duration, long_failure_seconds)
    assert totals.long_failures == expected_message
    assert dict(totals.times) == expected_times

def test_add_success_failure_time_accumulation():
    totals = RunTotals()
    run1 = {"conclusion":"failure", "path":"z.yml", "id":4567, "html_url":"https://one_more_example.com", "created_at":"2024-07-01T00:00:00Z", "updated_at": "2024-07-01T00:05:00Z", "head_sha": "387659", "run_started_at":"2024-07-01T00:01:00Z"}
    run2 = {"conclusion":"failure", "path":"z.yml", "id":4577, "html_url":"https://one_more_example.com", "created_at":"2025-07-01T00:00:00Z", "updated_at": "2025-07-01T00:05:00Z", "head_sha": "387650", "run_started_at":"2025-07-01T00:01:00Z"}
    add_success_failure_time(totals, run1, 15, 30)
    add_success_failure_time(totals, run2, 60, 30)
    assert dict(totals.times) == {('z.yml', 'failure'):[0.25,1.0]}
    assert totals.long_failures == ['Failure: z.yml with duration 1.00 minutes, run ID: 4577, URL: https://one_more_example.com, created at: 2025-07-01T00:00:00Z, updated at: 2025-07-01T00:05:00Z, head_sha: 387650, run_started_at: 2025-07-01T00:01:00Z']


def make_run(path, conclusion, created, started, updated, run_id):
    return {"path": path, "conclusion": conclusion, "id": run_id, "html_url": "https://example.com",
            "created_at": created, "run_started_at": started, "updated_at": updated, "head_sha": "abc"}

def test_analyze_runs():
    runs = [
        make_run("a.yml", "success", "2024-01-02T00:00:00Z", "2024-01-02T00:00:00Z", "2024-01-02T00:01:00Z", 1),
        make_run("a.yml", "failure", "2024-01-03T00:00:00Z", "2024-01-03T00:00:00Z", "2024-01-03T00:02:00Z", 2),
        make_run("b.yml", None, "2023-12-31T00:00:00Z", None, "2023-12-31T00:00:30Z", 3),
    ]
    totals = analyze_runs(runs, 100)
    assert totals.oldest_date == datetime(2023, 12, 31, tzinfo=UTC)
    assert dict(totals.summary) == {"a.yml": {"success": 1, "failure": 1}}
    assert dict(totals.duration_times) == {"a.yml": [60.0, 120.0]}
    assert dict(totals.times) == {("a.yml", "success"): [1.0], ("a.yml", "failure"): [2.0]}
    assert totals.long_failures == [
        "Failure: a.yml with duration 2.00 minutes, run ID: 2, URL: https://example.com, created at: 2024-01-03T00:00:00Z, updated at: 2024-01-03T00:02:00Z, head_sha: abc, run_started_at: 2024-01-03T00:00:00Z"
    ]


def test_format_conclusion_lines():
    summary = {
        "b.yml": Counter(success=3, failure=1, action_required=2),
        "c.yml": Counter(action_required=1),
        "a.yml": Counter(success=2, failure=2),
    }
    duration_times = {
        "b.yml": [30, 60, 120],
        "a.yml": [120, 240],
    }
    lines = format_conclusion_lines(summary, duration_times)
    assert lines == [
        "Path: a.yml, Success: 2, Failure: 2, Action Required: 0, Fail Rate: 50.00%, Median Duration: 3.00 minutes, Total Runs: 4",
        "Path: b.yml, Success: 3, Failure: 1, Action Required: 2, Fail Rate: 25.00%, Median Duration: 60 seconds, Total Runs: 6",
        "Path: c.yml, Success: 0, Failure: 0, Action Required: 1, Fail Rate: 0.00%, Median Duration: n/a, Total Runs: 1",
    ]


def test_build_comparison():
    times = {
        ("c.yml", "failure"): [4, 4, 4],
        ("a.yml", "success"): [1, 2, 3],
        ("a.yml", "failure"): [4, 6, 8],
        ("b.yml", "success"): [1, 2],
        ("b.yml", "failure"): [5, 5, 5],
        ("d.yml", "success"): [0, 0, 0],
        ("d.yml", "failure"): [3, 3, 3],
    }
    assert build_comparison(times, 3) == [
        ("a.yml", 3, 3, 2, 6, 3.0),
        ("b.yml", 2, 3, None, 5, None),
        ("c.yml", 0, 3, None, 4, None),
        ("d.yml", 3, 3, 0, 3, None),
    ]


def test_format_comparison_lines():
    comparison = [
        ("a.yml", 10, 4, 2.5, 7.5, 3.0),
        ("b.yml", 2, 0, None, None, None),
    ]
    lines = format_comparison_lines(comparison)
    assert lines == [
        "\nPath                                                     OK runs  Fail runs   Med OK  Med Fail  Fail/OK",
        "a.yml                                                         10          4     2.50      7.50    3.00x",
        "b.yml                                                          2          0        -         -        -",
    ]

def test_format_comparison_lines_empty():
    assert format_comparison_lines([]) == [
        "\nPath                                                     OK runs  Fail runs   Med OK  Med Fail  Fail/OK"
    ]


@pytest.mark.parametrize("comparison, expected",[
    pytest.param([("a.yml", 10, 4, 2.5, 7.5, 3.14149),("b.yml", 2, 0, 2.334, 5.789, 6.781)], "Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\na.yml,10,4,2.5,7.5,3.14\nb.yml,2,0,2.33,5.79,6.78\n", id="All_values"),
    pytest.param([("a.yml", 10, None, 2.478, None, 3.14149),("b.yml", 2, 0, 2.964, 2.333, 3.45)], "Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\na.yml,10,,2.48,,3.14\nb.yml,2,0,2.96,2.33,3.45\n", id="None_in_row_1"),
    pytest.param([("a.yml", 10, 7, 5.111, 4.598, 3.14159),("b.yml", None, 0, None, 2.333, None)], "Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\na.yml,10,7,5.11,4.6,3.14\nb.yml,,0,,2.33,\n", id="None_in_row_2"),
    pytest.param([("a.yml", None, None, None, None, None),("b.yml", None, None, None, None, None)], "Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\na.yml,,,,,\nb.yml,,,,,\n", id="Path_only_no_values"),
    pytest.param([("a,b.yml", 3, 7, 3.48, 4.20, 6.77),("b,c,e.yml", 5, 9, 3.88, 5.699, 0)], 'Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\n"a,b.yml",3,7,3.48,4.2,6.77\n"b,c,e.yml",5,9,3.88,5.7,0\n', id="Comma_in_path"),
    pytest.param([],"Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\n", id="Empty_list")
])
def test_build_csv_text(comparison, expected):
    assert build_csv_text(comparison) == expected


@pytest.mark.parametrize("total_count, fetched_count, expected", [
    pytest.param(None, 100, None, id="Total_count_is_none"),
    pytest.param(200, 100, "WARNING: GitHub reported 200 matching runs but returned only 100 (the API returns at most 1,000 results per query); the oldest runs in the window may be missing.", id="Total_count_greater"),
    pytest.param(1000, 1000, "WARNING: GitHub returned 1,000 runs, which reaches its result cap (the API returns at most 1,000 results per query); the oldest runs in the window may be missing.", id="API_result_cap"),
    pytest.param(100, 200, None, id="Fetched_count_greater"),
    pytest.param(1500, 1000, "WARNING: GitHub reported 1,500 matching runs but returned only 1,000 (the API returns at most 1,000 results per query); the oldest runs in the window may be missing.", id="Total_greater_and_over_limit"),
    pytest.param(None, 1000, "WARNING: GitHub returned 1,000 runs, which reaches its result cap (the API returns at most 1,000 results per query); the oldest runs in the window may be missing.", id="None_total_fetched_hit_cap"),
    pytest.param(999, 999, None, id="Boundry_test")
])
def test_build_truncation_warning(total_count, fetched_count, expected):
    message = build_truncation_warning(total_count, fetched_count)
    assert message == expected


def make_totals():
    totals = RunTotals()
    totals.oldest_date = datetime(2024, 1, 1, tzinfo=UTC)
    totals.summary["a.yml"].update({"success": 2, "failure": 1})
    totals.duration_times["a.yml"] = [60, 120, 180]
    totals.long_failures.append("Failure: a.yml (long failure message)")
    return totals

@pytest.mark.parametrize("run_count, total_count, skipped_count, expected_warning, expected_skipped", [
    pytest.param(500, 500, 0, [], [], id="No_warning_no_skipped"),
    pytest.param(
        1000, 1500, 2,
        ["WARNING: GitHub reported 1,500 matching runs but returned only 1,000 (the API returns at most 1,000 results per query); the oldest runs in the window may be missing."],
        ["Skipped 2 failed runs with no jobs"],
        id="Warning_and_skipped",
    ),
])
def test_build_report_lines(run_count, total_count, skipped_count, expected_warning, expected_skipped):
    totals = make_totals()
    comparison = [("a.yml", 2, 1, None, None, None)]
    lines = build_report_lines(totals, run_count, total_count, comparison, skipped_count)
    assert lines == (
        [f"Oldest run date: 2024-01-01 00:00:00+00:00 Total runs retrieved: {run_count}"]
        + expected_warning
        + ["Path: a.yml, Success: 2, Failure: 1, Action Required: 0, Fail Rate: 33.33%, Median Duration: 2.00 minutes, Total Runs: 3"]
        + ["Failure: a.yml (long failure message)"]
        + expected_skipped
        + format_comparison_lines(comparison)
    )


@pytest.mark.parametrize("message, status_code, headers, expected",[
    pytest.param("403 Client Error: Forbidden", 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"}, "Rate limit hit. Resets at epoch 1700000000.", id="Rate_limit"),
    pytest.param("403 Client Error: Forbidden", 403, {"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "1700000000"}, "Request failed: 403 Client Error: Forbidden", id="403_not_rate_limit"),
    pytest.param("500 Internal Server Error", 500, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"}, "Request failed: 500 Internal Server Error", id="500_status"),
    pytest.param("403 Client Error: Forbidden", 403, {"X-RateLimit-Remaining": "0"}, "Rate limit hit. Resets at epoch 0.", id="Missing_reset_key")
])
def test_describe_request_error(message, status_code, headers, expected):
    fake_response = types.SimpleNamespace(status_code=status_code, headers=headers)
    error = requests.exceptions.HTTPError(message, response=fake_response)
    test = describe_request_error(error)
    assert test == expected

def test_describe_request_error_no_response():
    error = requests.exceptions.HTTPError("ConnectionError", response=None)
    assert describe_request_error(error) == "Request failed: ConnectionError"

def test_describe_request_error_non_HTTP():
    error = requests.exceptions.ConnectionError("boom", response=None)
    assert describe_request_error(error) == "Request failed: boom"



MAIN_RUNS = [
    {  # success, 120 seconds
        "id": 1, "path": "ci.yml", "conclusion": "success", "created_at": "2026-11-01T00:00:00Z",
        "run_started_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:02:00Z",
        "html_url": "https://example.test/1", "head_sha": "aaa",
    },
    {  # failure, 60 seconds
        "id": 2, "path": "ci.yml", "conclusion": "failure", "created_at": "2026-11-02T00:00:00Z",
        "run_started_at": "2026-11-02T00:00:00Z", "updated_at": "2026-11-02T00:01:00Z",
        "html_url": "https://example.test/2", "head_sha": "bbb",
    },
    {  # failure longer than a day with no jobs, so it gets dropped
        "id": 3, "path": "ci.yml", "conclusion": "failure", "created_at": "2026-11-03T00:00:00Z",
        "run_started_at": "2026-11-03T00:00:00Z", "updated_at": "2026-11-05T00:00:00Z",
        "html_url": "https://example.test/3", "head_sha": "ccc",
    },
]


@pytest.fixture
def main_env(monkeypatch):
    """Token set, .env loading and the real session replaced; returns the list of requested urls."""
    urls = []
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(workflow_run_stats, "load_environment", lambda: None)

    def fake_get(url, headers, timeout):
        urls.append(url)
        assert headers == {"Authorization": "Bearer test-token"}
        if "/jobs" in url:
            data = {"total_count": 0}
        else:
            data = {"total_count": 3, "workflow_runs": MAIN_RUNS}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data, links={})

    monkeypatch.setattr(workflow_run_stats, "create_retry_session", lambda: SimpleNamespace(get=fake_get))
    return urls


def test_main_prints_report_and_writes_csv(main_env, tmp_path, capsys):
    output_path = tmp_path / "out.csv"

    result = workflow_run_stats.main(
        repo="test/test-repo", output_path=str(output_path), days=90, min_runs=1, now=datetime(2026, 11, 25)
    )

    assert result == 0
    assert main_env == [
        "https://api.github.com/repos/test/test-repo/actions/runs?branch=main&per_page=100&created=>=2026-08-27",
        "https://api.github.com/repos/test/test-repo/actions/runs/3/jobs?filter=all&per_page=100",
    ]
    out_lines = capsys.readouterr().out.splitlines()
    assert out_lines[0] == "Cutoff date for runs: 2026-08-27"
    assert "Skipped 1 failed runs with no jobs" in out_lines
    assert any(line.startswith("Path: ci.yml, Success: 1, Failure: 1") for line in out_lines)
    assert output_path.read_text(encoding="utf-8") == (
        "Path,OK runs,Fail runs,Med OK,Med Fail,Fail/OK\n"
        "ci.yml,1,1,2.0,1.0,0.5\n"
    )


def test_main_returns_1_and_prints_message_when_request_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(workflow_run_stats, "load_environment", lambda: None)

    def failing_get(url, headers, timeout):
        raise requests.exceptions.HTTPError("boom")

    monkeypatch.setattr(workflow_run_stats, "create_retry_session", lambda: SimpleNamespace(get=failing_get))
    output_path = tmp_path / "out.csv"

    result = workflow_run_stats.main(output_path=str(output_path))

    out_lines = capsys.readouterr().out.splitlines()
    assert result == 1
    assert out_lines[0].startswith("Cutoff date for runs: ")
    assert out_lines[-1] == "Request failed: boom"
    assert not output_path.exists()


def test_main_raises_when_token_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(workflow_run_stats, "load_environment", lambda: None)

    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        workflow_run_stats.main()


def test_script_exits_with_the_return_code_of_main(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.chdir(tmp_path)
    # The script runs again as __main__, so patch the libraries it imports, not its own names.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)

    def failing_get(self, url, **kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests.Session, "get", failing_get)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(workflow_run_stats.__file__, run_name="__main__")

    assert exit_info.value.code == 1