"""Text between Slack and the agent: history lines in, mrkdwn out."""

from __future__ import annotations

import re

SLACK_TEXT_LIMIT = 3500

_USER_REF = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_REF = re.compile(r"<#[CG][A-Z0-9]+\|([^>]*)>")
_SPECIAL_REF = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_LINK_WITH_LABEL = re.compile(r"<((?:https?|mailto):[^|>]+)\|([^>]+)>")
_LINK = re.compile(r"<((?:https?|mailto):[^>]+)>")


def user_ids_in(text: str) -> set[str]:
    return set(_USER_REF.findall(text or ""))


def slack_to_plain(text: str, names: dict[str, str]) -> str:
    """Slack markup to readable text: mentions become @names, links become ``label (url)``."""
    text = _USER_REF.sub(lambda m: "@" + names.get(m.group(1), m.group(1)), text or "")
    text = _CHANNEL_REF.sub(lambda m: "#" + m.group(1), text)
    text = _SPECIAL_REF.sub(lambda m: "@" + m.group(1), text)
    text = _LINK_WITH_LABEL.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = _LINK.sub(lambda m: m.group(1), text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def history_line(name: str, text: str) -> str:
    """``<display name>: <text>``, with continuation lines indented so speakers stay apart."""
    return f"{name}: " + text.replace("\n", "\n  ")


_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![*\w])")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE = re.compile(r"~~(.+?)~~")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_QUOTE = re.compile(r"^&gt;", re.MULTILINE)


def markdown_to_mrkdwn(text: str) -> str:
    """Convert the common Markdown the model writes into Slack mrkdwn; code stays as it is."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    code: list[str] = []

    def stash(match: re.Match[str]) -> str:
        code.append(match.group(0))
        return f"\x00{len(code) - 1}\x00"

    text = _CODE.sub(stash, text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _ITALIC.sub(r"_\1_", text)
    text = _BOLD.sub(lambda m: f"*{m.group(1) or m.group(2)}*", text)
    text = _STRIKE.sub(r"~\1~", text)
    text = _MD_LINK.sub(r"<\2|\1>", text)
    text = _HEADING.sub(r"*\1*", text)
    text = _QUOTE.sub(">", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: code[int(m.group(1))], text)


def split_message(text: str, limit: int = SLACK_TEXT_LIMIT) -> list[str]:
    """Split at paragraph or line breaks so each chunk fits in one Slack message."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest or not chunks:
        chunks.append(rest)
    return chunks
