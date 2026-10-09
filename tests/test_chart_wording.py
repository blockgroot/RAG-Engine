"""Every chart says what its numbers ARE, in words built from the data.

"130 Salary (₹ lakh)" was right and unreadable: the unit was the column's
whole header and nothing said a bar was a SUM over a team's rows.
"""

from __future__ import annotations

import pytest

from app.insights import describe, query, registry

SALARY = {"name": "Salary (₹ lakh)", "type": "number", "unit": "₹"}


@pytest.mark.parametrize("value,unit,shown", [
    (130, "₹ lakh", "₹130 lakh"), (2000, "$", "$2,000"), (12.5, "%", "12.5%"),
    (1, "tasks", "1 task"), (4, "tasks", "4 tasks"), (7, "", "7"),
    (3.25, "days (median)", "3.25 days (median)"),
])
def test_a_value_is_written_the_way_people_write_it(value, unit, shown):
    assert describe.format_value(value, unit) == shown


def test_a_header_bracket_is_the_unit_and_never_part_of_the_title():
    assert describe.split_header("Salary (₹ lakh)") == ("Salary", "₹ lakh")
    assert describe.split_header("Revenue") == ("Revenue", "")
    assert describe.table_unit("sum", SALARY) == "₹ lakh"
    assert describe.table_unit("sum", {"name": "Revenue", "unit": "₹"}) == "₹"
    assert describe.table_unit("count", None) == "rows"
    assert describe.table_title("sum", SALARY, "Team", None) == "Total Salary by Team"
    assert describe.table_title("average", SALARY, "Location", "Team") == (
        "Average Salary by Location and Team")
    assert describe.table_title("count", None, "Team", None) == "Number of rows by Team"


def test_a_table_chart_says_what_each_bar_adds_up():
    assert describe.table_explain(
        "sum", SALARY, group="Team", split=None, period="month", by_date=False,
        chart="bar", rows=10, total=266, unit="₹ lakh",
    ) == "Each bar adds up Salary for the rows of each Team. 10 rows, ₹266 lakh in total."
    assert describe.table_explain(
        "count", None, group="Start Month", split="Team", period="month", by_date=True,
        chart="line", rows=10, total=10, unit="rows",
    ) == "Each point counts the table's rows dated in each month, split by Team. 10 rows."


@pytest.mark.parametrize("key,measure,group,title_part,explain", [
    ("issues_completed", None, None, None, "Each point shows how many tasks in each week."),
    ("drive_docs_changed", "people", None, "number of different people",
     "Each point shows how many different people in each week."),
    ("prs_merged", "average_additions", "person", "average lines added",
     "Each bar shows the average lines added for each person."),
    ("issue_cycle_time", None, "team", None,
     "Each bar shows the median time from filed to done (in days) for each team."),
    ("pr_lead_time", "longest", None, "longest (in days)",
     "Each point shows the longest time from raised to merged (in days) in each week."),
])
def test_every_connector_says_what_its_measure_is(key, measure, group, title_part, explain):
    metric = registry.get(key)
    chosen = query.measures_for(metric)[measure or query.default_measure(metric)]
    assert describe.activity_measure(metric, chosen) == title_part
    assert describe.activity_explain(metric, chosen, group=group, split=None, period="week",
                                     chart="bar" if group else "line") == explain
