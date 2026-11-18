from __future__ import annotations

import httpx
import pytest
from app.files import (
    MAX_IMAGES,
    Download,
    FileFetchError,
    Image,
    SlackFileFetcher,
    collect,
    decode_text,
)
from conftest import FakeFetcher, slack_file

PNG = b"\x89PNG\r\n\x1a\nfake"


def url(f: dict) -> str:
    return f["url_private_download"]


@pytest.mark.asyncio
async def test_images_and_text_files_are_read_and_other_files_get_a_note():
    chart = slack_file("F1", "chart.png", "image/png", size=len(PNG))
    data = slack_file("F2", "data.csv", "text/csv")
    slides = slack_file("F3", "slides.pdf", "application/pdf", pretty_type="PDF")
    fetcher = FakeFetcher(
        {url(chart): Download(PNG, "image/png", True), url(data): Download(b"a,b\n1,2\n", "text/csv", True)}
    )

    result = await collect([({"files": [chart, data, slides]}, "Felix")], fetcher)

    assert result.images == [Image(name="chart.png", mime_type="image/png", data=PNG)]
    assert result.text_block() == "[File data.csv, attached by Felix]\na,b\n1,2\n\n[End of data.csv]"
    assert result.notes == {
        "F1": "[image chart.png: attached after the conversation]",
        "F2": "[file data.csv: its text is shown before the conversation]",
        "F3": "[file slides.pdf: PDF files cannot be read here]",
    }
    assert fetcher.urls == [url(chart), url(data)]


@pytest.mark.asyncio
async def test_only_the_newest_images_are_attached():
    files = [slack_file(f"F{i}", f"img{i}.png", "image/png") for i in range(MAX_IMAGES + 1)]
    fetcher = FakeFetcher({url(f): Download(PNG, "image/png", True) for f in files})

    # Newest message first: its image counts first.
    result = await collect([({"files": [f]}, "Felix") for f in files], fetcher)

    assert [i.name for i in result.images] == [f"img{i}.png" for i in range(MAX_IMAGES)]
    assert "left out" in result.notes[f"F{MAX_IMAGES}"]


@pytest.mark.asyncio
async def test_a_large_or_unusual_image_uses_the_slack_thumbnail():
    photo = slack_file("F1", "photo.heic", "image/heic", size=3_000_000, thumb_1024="https://files.slack.com/t1024.jpg")
    huge = slack_file("F2", "huge.png", "image/png", size=50_000_000, thumb_720="https://files.slack.com/t720.png")
    no_thumb = slack_file("F3", "big.png", "image/png", size=50_000_000)
    fetcher = FakeFetcher(
        {
            "https://files.slack.com/t1024.jpg": Download(b"jpeg", "image/jpeg", True),
            "https://files.slack.com/t720.png": Download(PNG, "image/png", True),
        }
    )

    result = await collect([({"files": [photo, huge, no_thumb]}, "Felix")], fetcher)

    assert [(i.name, i.mime_type) for i in result.images] == [("photo.heic", "image/jpeg"), ("huge.png", "image/png")]
    assert result.notes["F3"] == "[image big.png: too large to read]"


@pytest.mark.asyncio
async def test_a_slack_sign_in_page_is_not_taken_for_the_file():
    chart = slack_file("F1", "chart.png", "image/png")
    notes = slack_file("F2", "notes.txt", "text/plain")
    sign_in = Download(b"<html>Sign in</html>", "text/html", True)
    fetcher = FakeFetcher({url(chart): sign_in, url(notes): sign_in})

    result = await collect([({"files": [chart, notes]}, "Felix")], fetcher)

    assert result.images == [] and result.texts == []
    assert result.notes == {
        "F1": "[image chart.png: could not be downloaded]",
        "F2": "[file notes.txt: could not be downloaded]",
    }


@pytest.mark.asyncio
async def test_failed_hidden_and_external_files_get_a_note():
    broken = slack_file("F1", "a.png", "image/png")
    deleted = slack_file("F2", "b.txt", "text/plain", mode="tombstone")
    external = slack_file("F3", "c.txt", "text/plain", file_access="check_file_info")
    fetcher = FakeFetcher({url(broken): FileFetchError("HTTP 404")})

    result = await collect([({"files": [broken, deleted, external]}, "Felix")], fetcher)

    assert result.notes == {
        "F1": "[image a.png: could not be downloaded]",
        "F2": "[file b.txt: not available to you]",
        "F3": "[file c.txt: not available to you]",
    }
    assert fetcher.urls == [url(broken)]


@pytest.mark.asyncio
async def test_long_text_is_cut_and_marked():
    log = slack_file("F1", "log.txt", "text/plain")
    fetcher = FakeFetcher({url(log): Download(b"x" * 300_000, "text/plain", True)})

    result = await collect([({"files": [log]}, "Felix")], fetcher)

    assert result.texts[0].truncated
    assert len(result.texts[0].text) == 30_000
    assert result.text_block().endswith("[The file goes on; only its start is shown.]\n[End of log.txt]")


@pytest.mark.asyncio
async def test_without_a_fetcher_every_file_gets_a_note():
    result = await collect([({"files": [slack_file("F1", "a.png", "image/png")]}, "Felix")], None)
    assert result.notes == {"F1": "[image a.png: could not be downloaded]"}


def test_text_decoding():
    assert decode_text("Grüße".encode()) == "Grüße"
    assert decode_text("﻿a;b".encode()) == "a;b"
    assert decode_text("Größe".encode("cp1252")) == "Größe"
    # A download cut in the middle of a character.
    assert decode_text("Grüße".encode()[:3]) == "Gr"


@pytest.mark.asyncio
async def test_the_fetcher_sends_the_token_to_slack_only_and_stops_at_the_limit():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 100, headers={"content-type": "text/plain; charset=utf-8"})

    fetcher = SlackFileFetcher("xoxb-test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    full = await fetcher.fetch("https://files.slack.com/a.txt", max_bytes=1000)
    cut = await fetcher.fetch("https://files.slack.com/a.txt", max_bytes=10)

    assert full == Download(b"x" * 100, "text/plain", True)
    assert cut.data == b"x" * 10 and not cut.complete
    assert seen[0].headers["authorization"] == "Bearer xoxb-test"
    with pytest.raises(FileFetchError):
        await fetcher.fetch("https://files.slack.com/missing", max_bytes=10)
    for bad in ("https://evil.example/a.png", "http://files.slack.com/a.png", "https://slack.com.evil.example/a"):
        with pytest.raises(FileFetchError):
            await fetcher.fetch(bad, max_bytes=10)
    assert len(seen) == 3
