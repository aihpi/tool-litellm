from __future__ import annotations

import asyncio

import pytest
from app.responder import ThrottledMessage
from app.slack_format import history_line, markdown_to_mrkdwn, slack_to_plain, split_message
from conftest import Clock, FakeSlack


def test_markdown_becomes_slack_mrkdwn_but_code_is_left_alone():
    text = "# Plan\n**Bold** and *italic* with [docs](https://x.test/a?b=1&c=2)\n- one\n* two\n`a**b**`\n```\n**raw** <tag>\n```"
    assert markdown_to_mrkdwn(text) == (
        "*Plan*\n*Bold* and _italic_ with <https://x.test/a?b=1&amp;c=2|docs>\n• one\n• two\n`a**b**`\n```\n**raw** &lt;tag&gt;\n```"
    )


def test_markdown_blockquote_and_comparison_signs():
    assert markdown_to_mrkdwn("> quoted\n1 < 2 & 3 > 2") == "> quoted\n1 &lt; 2 &amp; 3 &gt; 2"


def test_slack_markup_becomes_plain_text_with_names():
    text = "<@U1> see <#C1|workflows>, <https://x.test|the doc> and <https://y.test> &amp; <!here>"
    assert (
        slack_to_plain(text, {"U1": "Anna"})
        == "@Anna see #workflows, the doc (https://x.test) and https://y.test & @here"
    )


def test_history_line_indents_continuation_lines():
    assert history_line("Anna", "one\ntwo") == "Anna: one\n  two"


def test_split_message_prefers_paragraph_breaks():
    text = "a" * 10 + "\n\n" + "b" * 10 + "\n" + "c" * 25
    assert split_message(text, limit=24) == ["a" * 10, "b" * 10, "c" * 24, "c"]
    assert split_message("") == [""]


@pytest.mark.asyncio
async def test_updates_are_throttled_and_only_the_newest_text_is_sent():
    slack = FakeSlack()
    clock = Clock(0.0)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    message = ThrottledMessage(
        slack, channel="C", ts="1.0", thread_ts="0.5", min_interval=1.5, clock=clock, sleep=sleep
    )
    message.set("first")
    await message._task
    clock.now = 0.5
    message.set("second")
    message.set("third")
    message.set("third")
    await message._task

    assert [c["text"] for c in slack.called("chat_update")] == ["first", "third"]
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_finish_cancels_the_pending_edit_and_posts_overflow_in_the_thread():
    slack = FakeSlack()
    clock = Clock(0.0)
    message = ThrottledMessage(
        slack, channel="C", ts="1.0", thread_ts="0.5", min_interval=1.5, clock=clock, sleep=asyncio.sleep
    )
    message.set("progress")
    await message._task
    message.set("more progress")  # would wait 1.5 s
    await message.finish("x" * 3500 + "\n\n" + "y" * 10)

    assert [c["text"] for c in slack.called("chat_update")] == ["progress", "x" * 3500]
    assert slack.called("chat_postMessage") == [{"channel": "C", "thread_ts": "0.5", "text": "y" * 10}]
