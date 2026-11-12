"""Ends every key of one team at the end of the current period: `python -m app.key_expiry`.

LiteLLM's per-team key duration is an Enterprise feature, so this does the same for free from outside: run it on a
schedule (a Kubernetes CronJob, daily) and every key of the team that has no end date, or one later than its
period allows, is set to end with its period (see `Periods`). Keys that already end earlier are left alone, as are
keys of every other team. Spend and usage stay when a key expires.

Environment:
  LITELLM_BASE_URL          LiteLLM inside the cluster, e.g. http://litellm-service.litellm.svc:4000
  LITELLM_KEY_POLICY_KEY    admin key allowed to call /key/list and /key/update (falls back to LITELLM_MASTER_KEY)
  KEY_EXPIRY_TEAM_ID        team whose keys get the period end (default: kisz-default)
  KEY_EXPIRY_PERIOD_ENDS    last day of each period as month-day, comma-separated (default: 03-31,06-30,09-30,12-31)
  KEY_EXPIRY_TIMEZONE       keys end at 00:00 on the day after the last day, in this time zone (default: Europe/Berlin)
  KEY_EXPIRY_APPLY          "true" to change keys; anything else only prints what would change (default)
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.litellm_client import LiteLLMClient

# A key ending this close after the period end counts as already right (LiteLLM computes its own "now").
_TOLERANCE = timedelta(minutes=10)
# A key from a period that ended less than this long ago is stopped, not moved into the running period.
_JUST_ENDED = timedelta(days=7)


def parse_period_ends(spec: str) -> list[tuple[int, int]]:
    ends = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        month, day = (int(part) for part in item.split("-", 1))
        datetime(2024, month, day)  # validates the date (2024 is a leap year, so 02-29 passes)
        ends.append((month, day))
    if not ends:
        raise ValueError("KEY_EXPIRY_PERIOD_ENDS is empty")
    return sorted(set(ends))


def _parse_expires(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Change:
    token: str
    label: str
    old_expires: datetime | None
    new_expires: datetime


@dataclass(frozen=True)
class Periods:
    """Periods that end on fixed dates each year (e.g. 31 March, 30 June, 30 September, 31 December).

    A key works through its period's last day and stops at 00:00 the day after. Whoever continues creates a new
    key from that day on, which then belongs to the new period.
    """

    ends: tuple[tuple[int, int], ...]
    tz: ZoneInfo

    def expiry_for(self, moment: datetime) -> datetime:
        local = moment.astimezone(self.tz).date()
        for year in (local.year, local.year + 1):
            for month, day in self.ends:
                try:
                    end = date(year, month, day)
                except ValueError:  # 02-29 in a non-leap year
                    continue
                if local <= end:
                    return datetime.combine(end + timedelta(days=1), time(0), tzinfo=self.tz)
        raise ValueError("no period end found")  # unreachable with at least one valid end

    def key_expiry(self, created_at: datetime | None, now: datetime) -> datetime:
        """By creation date. A key whose period ended in the last week (made on a period's last day, after that
        day's run) stops now instead of running into the new period. Older keys without an end date, which only
        exist before this job first runs, get the period that is running now."""
        if created_at is not None:
            target = self.expiry_for(created_at)
            if target > now:
                return target
            if now - target < _JUST_ENDED:
                return now
        return self.expiry_for(now)


def plan_changes(keys: Sequence[dict[str, Any]], *, now: datetime, periods: Periods) -> list[Change]:
    changes = []
    for key in keys:
        token = key.get("token")
        if not isinstance(token, str) or not token:
            continue
        expires = _parse_expires(key.get("expires"))
        target = periods.key_expiry(_parse_expires(key.get("created_at")), now)
        if expires is not None and (expires <= now or expires <= target + _TOLERANCE):
            continue  # already expired, or already ends by its period end
        label = key.get("key_alias") or key.get("key_name") or f"key {token[:8]}"
        changes.append(Change(token=token, label=str(label), old_expires=expires, new_expires=target))
    return changes


async def run(
    client: LiteLLMClient,
    *,
    team_id: str,
    periods: Periods,
    apply: bool,
    now: datetime | None = None,
) -> list[Change]:
    now = now or datetime.now(timezone.utc)
    period_end = periods.expiry_for(now)
    changes = plan_changes(await client.list_team_keys(team_id), now=now, periods=periods)
    for change in changes:
        old = change.old_expires.isoformat() if change.old_expires else "never"
        print(f"{'set' if apply else 'would set'} {change.label}: {old} -> {change.new_expires.isoformat()}")
        if apply:
            seconds = max(int((change.new_expires - now).total_seconds()), 60)
            await client.set_key_duration(change.token, f"{seconds}s")
    print(f"team {team_id}: keys made now end {period_end.isoformat()}, {len(changes)} key(s) {'updated' if apply else 'to update'}")
    return changes


def main() -> int:
    base_url = os.environ["LITELLM_BASE_URL"]
    admin_key = os.environ.get("LITELLM_KEY_POLICY_KEY") or os.environ["LITELLM_MASTER_KEY"]
    client = LiteLLMClient(base_url, admin_key, timeout=60.0)
    periods = Periods(
        ends=tuple(parse_period_ends(os.environ.get("KEY_EXPIRY_PERIOD_ENDS", "03-31,06-30,09-30,12-31"))),
        tz=ZoneInfo(os.environ.get("KEY_EXPIRY_TIMEZONE", "Europe/Berlin")),
    )
    asyncio.run(
        run(
            client,
            team_id=os.environ.get("KEY_EXPIRY_TEAM_ID", "kisz-default"),
            periods=periods,
            apply=os.environ.get("KEY_EXPIRY_APPLY", "").lower() == "true",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
