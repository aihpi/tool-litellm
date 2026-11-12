import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.key_expiry import Periods, parse_period_ends, plan_changes, run
from app.litellm_client import LiteLLMClient

BERLIN = ZoneInfo("Europe/Berlin")
# Spring 1 Jan to 31 Mar, Summer to 30 Jun, Autumn to 30 Sep, Winter to 31 Dec.
PERIODS = Periods(ends=tuple(parse_period_ends("03-31,06-30,09-30,12-31")), tz=BERLIN)
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def at(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=BERLIN)


def end(y, m, d):
    return datetime(y, m, d, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("created", "expires"),
    [
        (at(2026, 11, 12), end(2027, 1, 1)),  # Winter 2026: works through 31 Dec
        (at(2026, 12, 31, 23), end(2027, 1, 1)),
        (at(2027, 1, 1, 0), end(2027, 4, 1)),  # new key on 1 Jan: Spring 2027, through 31 Mar
        (at(2027, 3, 31, 23), end(2027, 4, 1)),
        (at(2027, 4, 1), end(2027, 7, 1)),  # got access on 1 April: through Summer
        (at(2027, 6, 30), end(2027, 7, 1)),  # 30-day month
        (at(2027, 9, 30), end(2027, 10, 1)),
        (at(2027, 10, 1), end(2028, 1, 1)),
    ],
)
def test_period_of_a_key(created, expires):
    assert PERIODS.expiry_for(created) == expires


def test_old_keys_get_the_running_period_not_a_past_one():
    old = datetime(2025, 2, 1, tzinfo=timezone.utc)
    assert PERIODS.key_expiry(old, NOW) == end(2027, 1, 1)
    assert PERIODS.key_expiry(None, NOW) == end(2027, 1, 1)


def test_bad_period_spec():
    with pytest.raises(ValueError):
        parse_period_ends("13-01")
    with pytest.raises(ValueError):
        parse_period_ends(" , ")


def test_plan_only_touches_keys_ending_too_late_or_never():
    jan_1 = end(2027, 1, 1)
    keys = [
        {"token": "h-never", "key_alias": "never", "expires": None},
        {"token": "h-late", "key_alias": "late", "expires": "2027-03-01T00:00:00Z"},
        {"token": "h-early", "key_alias": "early", "expires": "2026-12-01T00:00:00+00:00"},
        {"token": "h-exact", "key_alias": "exact", "expires": jan_1.astimezone(timezone.utc).isoformat()},
        {"token": "h-expired", "key_alias": "expired", "expires": "2026-10-01T00:00:00Z"},
        {"key_alias": "no token"},
    ]
    changes = plan_changes(keys, now=NOW, periods=PERIODS)
    assert [c.label for c in changes] == ["never", "late"]
    assert all(c.new_expires == jan_1 for c in changes)


def test_plan_uses_each_keys_creation_date():
    jan_2 = at(2027, 1, 2, 3)
    keys = [
        {"token": "h-new", "key_alias": "new", "expires": None, "created_at": "2027-01-01T08:00:00Z"},
        {"token": "h-old", "key_alias": "old", "expires": None, "created_at": "2026-06-01T08:00:00Z"},
    ]
    targets = {c.label: c.new_expires for c in plan_changes(keys, now=jan_2, periods=PERIODS)}
    assert targets == {"new": end(2027, 4, 1), "old": end(2027, 4, 1)}


def make_client(keys, calls):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if request.url.path == "/key/list":
            return httpx.Response(200, json={"keys": keys, "total_pages": 1})
        if request.url.path == "/key/update":
            return httpx.Response(200, json={"key": body["key"]})
        return httpx.Response(404)

    return LiteLLMClient("http://litellm.test", "sk-admin", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_dry_run_changes_nothing():
    calls = []
    client = make_client([{"token": "h-never", "expires": None}], calls)
    changes = await run(client, team_id="kisz-default", periods=PERIODS, apply=False, now=NOW)
    assert len(changes) == 1
    assert [c[1] for c in calls] == ["/key/list"]
    assert calls[0][2]["team_id"] == "kisz-default"


@pytest.mark.asyncio
async def test_apply_sets_the_remaining_seconds_by_hashed_token():
    calls = []
    client = make_client([{"token": "h-never", "expires": None}], calls)
    await run(client, team_id="kisz-default", periods=PERIODS, apply=True, now=NOW)
    (update,) = [c for c in calls if c[1] == "/key/update"]
    expected = int((end(2027, 1, 1) - NOW).total_seconds())
    assert update[3] == {"key": "h-never", "duration": f"{expected}s"}


def test_key_made_on_the_last_day_after_the_run_is_stopped_not_extended():
    """No one keeps a key into the new period by creating it on 31 December after the daily run."""
    created = at(2026, 12, 31, 10)
    run_time = at(2027, 1, 1, 0).replace(minute=5)
    assert PERIODS.key_expiry(created, run_time) == run_time
    (change,) = plan_changes([{"token": "h", "key_alias": "late", "expires": None, "created_at": created.isoformat()}], now=run_time, periods=PERIODS)
    assert change.new_expires == run_time


def test_key_already_ended_on_time_is_left_alone():
    created = at(2026, 12, 20)
    run_time = at(2027, 1, 1, 0).replace(minute=5)
    keys = [{"token": "h", "expires": end(2027, 1, 1).isoformat(), "created_at": created.isoformat()}]
    assert plan_changes(keys, now=run_time, periods=PERIODS) == []
