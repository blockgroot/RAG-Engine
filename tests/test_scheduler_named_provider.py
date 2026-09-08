"""Naming the app in the prompt picks it — the error message said to."""

from __future__ import annotations

from app.api.schedulers import _named_provider

AVAILABLE = ["google", "linear", "notion"]


def test_the_named_app_wins():
    # The report this came from: "...the ones remaining in linear" classified
    # as unknown, because the only classifier was a cosine probe over chunks
    # and the word "linear" never reached it.
    assert (
        _named_provider(
            "Generate a report for issue being raised and completed and the "
            "ones remaining in linear",
            AVAILABLE,
        )
        == "linear"
    )


def test_drive_aliases():
    assert _named_provider("what changed in google drive", AVAILABLE) == "google"
    assert _named_provider("new files in Drive this week", AVAILABLE) == "google"


def test_two_named_apps_fall_through_to_the_probe():
    # A guess between them would install a standing wrong report.
    assert _named_provider("changes in notion and linear", AVAILABLE) is None


def test_an_unconnected_app_is_not_offered():
    assert _named_provider("linear issues", ["google", "notion"]) is None


def test_no_name_means_no_decision():
    assert _named_provider("summarise what the team discussed", AVAILABLE) is None


def test_a_word_inside_another_word_does_not_count():
    # Substring matching would make "linearity" or "notional" pick a service.
    assert _named_provider("track the linearity of our burndown", AVAILABLE) is None
    assert _named_provider("a notional budget review", AVAILABLE) is None
