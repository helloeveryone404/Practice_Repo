import pytest
import datetime
from workflow_run_stats import build_headers, build_runs_url, compute_cutoff_date, get_token, get_duration

def test_build_headers():
    token = "test_token"
    headers = build_headers(token)
    assert headers["Authorization"] == "Bearer test_token"

def test_build_runs_url():
    repo = "test_repo"
    cutoff_date = "2023-01-01T00:00:00Z"
    url = build_runs_url(repo, cutoff_date)
    assert url == "https://api.github.com/repos/test_repo/actions/runs?branch=main&per_page=100&created=>=2023-01-01T00:00:00Z"

@pytest.mark.parametrize("now, days, expected", [
    pytest.param(datetime.datetime(2023, 1, 10, 0, 0, 0), 90, "2022-10-12", id='Year_Boundary'),
    pytest.param(datetime.datetime(2024, 4, 15, 12, 0, 0), 90, "2024-01-16", id='Leap_Year'),
    pytest.param(datetime.datetime(2023, 4, 15, 12, 0, 0), 90, "2023-01-15", id='Regular_Year'),
    pytest.param(datetime.datetime(2025, 4, 1, 0, 0, 0), 30, "2025-03-02", id='Beginning_of_Day'),
    pytest.param(datetime.datetime(2025, 4, 1, 23, 59, 59), 30, "2025-03-02", id='End_of_Day'),
    ],
)
def test_compute_cutoff_date(now, days, expected):
    assert compute_cutoff_date(now, days) == expected

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