#!/usr/bin/env python3
"""A heavy, multi-tab recruiting lead tracker and its exact answers.

Writes `Recruit Lead Tracker 2026.xlsx` (three monthly lead tabs with the same
columns, one past the 5,000-row bound, plus a team tab and a profiles tab) and
`questions.json`: each question with the answer worked out here, from the same
rows, so a reply is checked against arithmetic rather than read by eye.

Usage: .venv/bin/python evaluation/recruit_e2e/generate.py
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent
rnd = random.Random(20261105)

HEADER = ["Recruit", "Profile Name", "Dev Attempting", "Lead Status", "PH Name", "Lead Type",
          "BDE", "Role", "Job Link", "Applied Via", "Also Applied Via (O)", "Date", "Status"]
MONTHS = [("Oct 2026", 2026, 10, 3500), ("Nov 2026", 2026, 11, 6200), ("Dec 2026", 2026, 12, 2800)]

RECRUITERS = ["Tanvi Shah", "Ishaan Gupta", "Meghna Rao", "Farah Khan", "Dev Anand Pillai"]
BDES = ["Aman Verma", "Neha Joshi", "Karan Malhotra", "Pooja Iyer",
        "Rahul Bansal", "Simran Kaur", "Vivek Nair", "Ritu Saxena"]
LEADS = {"Aman Verma": "Neha Joshi", "Karan Malhotra": "Neha Joshi", "Pooja Iyer": "Neha Joshi",
         "Rahul Bansal": "Simran Kaur", "Vivek Nair": "Simran Kaur", "Ritu Saxena": "Simran Kaur",
         "Neha Joshi": "Neha Joshi", "Simran Kaur": "Simran Kaur"}
PROFILES = ["Daniel Reed", "Sophia Turner", "Liam Carter", "Olivia Hayes", "Ethan Brooks",
            "Ava Mitchell", "Noah Bennett", "Mia Collins", "Lucas Ward", "Emma Foster",
            "Mason Gray", "Chloe Russell", "Logan Price", "Grace Howard", "Ryan Cooper",
            "Zoe Parker", "Owen Hughes", "Lily Sanders", "Caleb Morris", "Ella Watson",
            "Jack Bailey", "Aria Kelly", "Henry Ross", "Nora Bell", "Wyatt Cox"]
STACKS = ["Java", "React", "Python", "Node.js", ".NET", "DevOps", "Data Engineering", "QA Automation"]
DEVS = ["Arjun Mehta", "Sneha Patil", "Rohit Sharma", "Kavya Reddy", "Nikhil Jain",
        "Ananya Das", "Varun Kapoor", "Pallavi Singh", "Siddharth Rao", "Isha Menon",
        "Manish Yadav", "Deepika Nair", "Harsh Vardhan", "Priyanka Bose", "Aditya Kulkarni"]
ROLES = ["Senior Java Developer", "React Developer", "Full Stack Engineer", "Python Developer",
         "Node.js Developer", ".NET Developer", "DevOps Engineer", "Data Engineer",
         "QA Automation Engineer", "Cloud Engineer", "Backend Engineer", "Frontend Engineer",
         "Machine Learning Engineer", "Salesforce Developer", "Mobile Developer (React Native)",
         "Site Reliability Engineer", "Golang Developer", "Scala Data Engineer",
         "Technical Lead - Java", "Angular Developer"]
STAGES = [("Applied", 30), ("No Response", 22), ("Screening", 14), ("Interview Scheduled", 9),
          ("Technical Round", 8), ("Client Round", 5), ("Offer", 3), ("Rejected", 9)]
CLOSED = {"Offer", "Rejected", "No Response"}
TYPES = [("Contract", 45), ("Full Time", 30), ("C2H", 18), ("Freelance", 7)]
CHANNELS = [("LinkedIn", 34), ("Dice", 20), ("Indeed", 18), ("Upwork", 10),
            ("Company Website", 12), ("Referral", 6)]

_A = ["Blue", "North", "Silver", "Bright", "Iron", "Cedar", "Quantum", "Apex", "Nova", "Summit",
      "Harbor", "Pixel", "Vertex", "Lumen", "Orbit", "Granite", "Maple", "Crest", "Pioneer", "Atlas"]
_B = ["peak", "wind", "line", "stone", "field", "gate", "bridge", "works", "path", "point"]
_C = ["Systems", "Labs", "Technologies", "Solutions", "Analytics", "Software", "Health", "Capital"]
COMPANIES = sorted({f"{a}{b} {c}" for a in _A for b in _B for c in _C})
rnd.shuffle(COMPANIES)
COMPANIES = COMPANIES[:420]


def pick(weighted):
    names, weights = zip(*weighted)
    return rnd.choices(names, weights=weights)[0]


def leads_for(year: int, month: int, n: int, start_id: int) -> list[dict]:
    rows = []
    first = date(year, month, 1)
    days = ((date(year + (month == 12), month % 12 + 1, 1)) - first).days
    for i in range(n):
        profile = rnd.choice(PROFILES)
        stage = pick(STAGES)
        channel = pick(CHANNELS)
        also = "" if rnd.random() < 0.7 else rnd.choice([c for c, _ in CHANNELS if c != channel])
        company = rnd.choice(COMPANIES)
        status = "Closed" if stage in CLOSED else ("On Hold" if rnd.random() < 0.08 else "Open")
        rows.append({
            "Recruit": rnd.choice(RECRUITERS), "Profile Name": profile,
            "Dev Attempting": rnd.choice(DEVS) if stage not in ("Applied", "No Response") else "",
            "Lead Status": stage, "PH Name": company, "Lead Type": pick(TYPES),
            "BDE": rnd.choice(BDES), "Role": rnd.choice(ROLES),
            "Job Link": f"https://jobs.example.com/{company.lower().replace(' ', '-')}/{start_id + i}",
            "Applied Via": channel, "Also Applied Via (O)": also,
            "Date": first + timedelta(days=rnd.randrange(days)), "Status": status,
        })
    return rows


def main() -> None:
    book = Workbook()
    book.remove(book.active)
    tabs: dict[str, list[dict]] = {}
    next_id = 50001
    for title, year, month, n in MONTHS:
        rows = leads_for(year, month, n, next_id)
        next_id += n
        tabs[title] = rows
        sheet = book.create_sheet(title)
        sheet.append(HEADER)
        for r in rows:
            sheet.append([r[h] for h in HEADER])
    team = book.create_sheet("BDE Team")
    team.append(["BDE", "Team Lead", "Location", "Email", "Joined"])
    for i, b in enumerate(BDES):
        team.append([b, LEADS[b], ["Pune", "Noida", "Bengaluru"][i % 3],
                     b.lower().replace(" ", ".") + "@talentbridge.example", date(2023 + i % 3, 1 + i, 3)])
    profiles = book.create_sheet("Profiles")
    profiles.append(["Profile Name", "Primary Stack", "Experience (yrs)", "Managed By", "Active"])
    for i, p in enumerate(PROFILES):
        profiles.append([p, STACKS[i % len(STACKS)], 3 + (i * 7) % 11, BDES[i % len(BDES)],
                         "No" if i % 9 == 4 else "Yes"])
    book.save(OUT / "Recruit Lead Tracker 2026.xlsx")

    allrows = [r for rows in tabs.values() for r in rows]
    nov = tabs["Nov 2026"]
    q: list[dict] = []

    def add(mode, question, expect, **extra):
        q.append({"n": len(q) + 1, "mode": mode, "question": question, "expect": expect, **extra})

    # --- single rows -----------------------------------------------------
    for r in (allrows[17], nov[4321], tabs["Dec 2026"][99]):
        add("ask", f"What is the lead status for {r['Job Link']}?",
            f"{r['Lead Status']} ({r['Profile Name']} at {r['PH Name']}, {r['Role']}, BDE {r['BDE']})",
            must=[r["Lead Status"]])
    r = nov[5555]  # past the 5,000-row bound of one table
    add("ask", f"Who is the BDE on {r['Job Link']}?", r["BDE"], must=[r["BDE"]])
    # --- one person ------------------------------------------------------
    p = "Daniel Reed"
    mine = [r for r in allrows if r["Profile Name"] == p]
    add("ask", f"How many companies has {p} applied to?",
        f"{len({r['PH Name'] for r in mine})} different companies ({len(mine)} applications)",
        must=[str(len({r['PH Name'] for r in mine}))])
    add("ask", f"How many applications has {p} made in total?", str(len(mine)), must=[str(len(mine))])
    mine_nov = [r for r in nov if r["Profile Name"] == p]
    add("ask", f"How many leads did {p} have in November?", str(len(mine_nov)),
        must=[str(len(mine_nov))])
    off = sorted({r["PH Name"] for r in mine if r["Lead Status"] == "Offer"})
    add("ask", f"Which companies gave {p} an offer?", ", ".join(off) or "none", must=off)
    stage = Counter(r["Lead Status"] for r in mine)
    add("ask", f"Break down {p}'s leads by lead status",
        "; ".join(f"{k}: {v}" for k, v in stage.most_common()), must=[str(v) for v in stage.values()])
    dev = "Kavya Reddy"
    roles = Counter(r["Role"] for r in allrows if r["Dev Attempting"] == dev)
    add("ask", f"Which roles has {dev} attempted, and how many times each?",
        "; ".join(f"{k}: {v}" for k, v in roles.most_common(5)) + " ...",
        must=[roles.most_common(1)[0][0]])
    add("ask", f"How many interviews has {dev} attempted?",
        str(sum(roles.values())), must=[str(sum(roles.values()))])
    # --- a BDE -----------------------------------------------------------
    b = "Karan Malhotra"
    bl = [r for r in allrows if r["BDE"] == b]
    add("ask", f"How many leads has {b} generated?", str(len(bl)), must=[str(len(bl))])
    add("ask", f"How many leads did {b} generate in December?",
        str(sum(r["BDE"] == b for r in tabs["Dec 2026"])),
        must=[str(sum(r["BDE"] == b for r in tabs["Dec 2026"]))])
    won = sum(r["Lead Status"] == "Offer" for r in bl)
    add("ask", f"What share of {b}'s leads reached an offer?",
        f"{won} of {len(bl)} = {won / len(bl) * 100:.1f}%", must=[f"{won / len(bl) * 100:.1f}"])
    per_bde = Counter(r["BDE"] for r in allrows if r["Lead Status"] == "Offer")
    top, n_top = per_bde.most_common(1)[0]
    add("ask", "Which BDE has the most offers?", f"{top} ({n_top})", must=[top, str(n_top)])
    add("ask", f"Who is {b}'s team lead and what is their email?",
        f"{LEADS[b]}; {b.lower().replace(' ', '.')}@talentbridge.example", must=[LEADS[b]])
    # --- channels ---------------------------------------------------------
    li = sum(r["Applied Via"] == "LinkedIn" for r in allrows)
    add("ask", "How many applications went through LinkedIn?", str(li), must=[str(li)])
    both = sum(r["Applied Via"] == "LinkedIn" and r["Also Applied Via (O)"] == "Dice" for r in allrows)
    add("ask", "How many applications were made via LinkedIn and also via Dice?", str(both),
        must=[str(both)])
    ch_nov = Counter(r["Applied Via"] for r in nov)
    add("ask", "Which channel brought the most applications in November?",
        f"{ch_nov.most_common(1)[0][0]} ({ch_nov.most_common(1)[0][1]})",
        must=[ch_nov.most_common(1)[0][0], str(ch_nov.most_common(1)[0][1])])
    # --- whole tracker ----------------------------------------------------
    add("ask", "How many leads are in the tracker?", str(len(allrows)), must=[str(len(allrows))])
    open_ = sum(r["Status"] == "Open" for r in allrows)
    add("ask", "How many leads are still open?", str(open_), must=[str(open_)])
    offers = sum(r["Lead Status"] == "Offer" for r in allrows)
    add("ask", "How many offers did we get across October to December?", str(offers),
        must=[str(offers)])
    comp = Counter(r["PH Name"] for r in allrows)
    c, nc = comp.most_common(1)[0]
    add("ask", "Which company have we applied to the most?", f"{c} ({nc})", must=[c, str(nc)])
    cl = [r for r in allrows if r["PH Name"] == c]
    add("ask", f"Which profiles applied to {c}?",
        ", ".join(sorted({r['Profile Name'] for r in cl})),
        must=sorted({r["Profile Name"] for r in cl})[:3])
    add("ask", "How many C2H leads are there in November?",
        str(sum(r["Lead Type"] == "C2H" for r in nov)),
        must=[str(sum(r["Lead Type"] == "C2H" for r in nov))])
    # --- refusals ---------------------------------------------------------
    add("ask", "How many companies has Jordan Blake applied to?",
        "No such profile; says so", refuse=True)
    add("ask", "How many leads did we have in March 2026?",
        "Not in the data (Oct-Dec 2026); no zero, no guess", refuse=True)
    add("ask", "What salary was Daniel Reed offered?", "Not in the data", refuse=True)
    # --- follow-ups -------------------------------------------------------
    add("ask", f"How many leads has {b} generated? -> and how many of them reached an offer?",
        f"{len(bl)}; then {won}", must=[str(won)], follow=True)
    # --- harder: several typed values, values the sheet lacks, lists ----------
    def rows(**kw):
        return [r for r in allrows if all(r[k] == v for k, v in kw.items())]

    n = len(rows(**{"Profile Name": "Sophia Turner", "Applied Via": "Dice"}))
    n_dec = sum(r["Profile Name"] == "Sophia Turner" and r["Applied Via"] == "Dice"
                for r in tabs["Dec 2026"])
    add("ask", "How many Dice applications did Sophia Turner make in December?", str(n_dec),
        must=[str(n_dec)])
    n = len(rows(**{"BDE": "Aman Verma", "Lead Type": "C2H", "Applied Via": "LinkedIn"}))
    add("ask", "How many C2H leads did Aman Verma get via LinkedIn?", str(n), must=[str(n)])
    n = len(rows(**{"Profile Name": "Liam Carter", "Lead Status": "Offer", "Applied Via": "Indeed"}))
    add("ask", "How many offers did Liam Carter get through Indeed?", str(n), must=[str(n)])
    mine = rows(**{"Profile Name": "Olivia Hayes"})
    tr = sum(r["Lead Status"] == "Technical Round" for r in mine)
    add("ask", "What share of Olivia Hayes's applications reached the technical round?",
        f"{tr} of {len(mine)} = {tr / len(mine) * 100:.1f}%", must=[f"{tr / len(mine) * 100:.1f}"])
    role = Counter(r["Role"] for r in rows(**{"Profile Name": "Ethan Brooks"})).most_common(1)[0]
    add("ask", "Which role has Ethan Brooks applied to most?", f"{role[0]} ({role[1]})",
        must=[role[0], str(role[1])])
    comp = {r["PH Name"] for r in nov if r["BDE"] == "Neha Joshi"}
    add("ask", "How many different companies did Neha Joshi's leads go to in November?",
        str(len(comp)), must=[str(len(comp))])
    n = len(rows(**{"Lead Type": "Full Time", "Status": "Open"}))
    add("ask", "How many Full Time leads are open?", str(n), must=[str(n)])
    n = sum(r["Lead Status"] == "Rejected" for r in tabs["Oct 2026"])
    add("ask", "How many leads were rejected in October?", str(n), must=[str(n)])
    offers = sorted(r["PH Name"] for r in rows(**{"Profile Name": "Mia Collins",
                                                  "Lead Status": "Offer"}))
    add("ask", "List Mia Collins's offers with the company and role",
        "; ".join(offers), must=offers[:4])
    rec = Counter(r["Recruit"] for r in allrows if r["Lead Status"] == "Offer").most_common(1)[0]
    add("ask", "Which recruiter handled the most offers?", f"{rec[0]} ({rec[1]})",
        must=[rec[0], str(rec[1])])
    n = len(rows(**{"Profile Name": "Logan Price", "Applied Via": "Upwork"}))
    add("ask", "Total applications by Logan Price via Upwork", str(n), must=[str(n)])
    add("ask", "How many applications did Sophia Turner make via Monster?",
        "Monster is not a channel in the tracker; says so", refuse=True)
    add("ask", "How many leads does Priya Malhotra have?", "No such person; says so", refuse=True)
    n = len(rows(**{"Dev Attempting": "Rohit Sharma", "Lead Status": "Client Round"}))
    add("ask", "How many client rounds has Rohit Sharma attempted?", str(n), must=[str(n)])
    n = sum(r["BDE"] == "Vivek Nair" and r["Status"] == "On Hold" for r in nov)
    add("ask", "How many of Vivek Nair's November leads are on hold?", str(n), must=[str(n)])

    # --- charts -------------------------------------------------------------
    oct_via = Counter(r["Applied Via"] for r in tabs["Oct 2026"]).most_common(1)[0]
    add("chart", "Leads per month by applied via", f"stacked bar Oct-Dec x channel, Oct {oct_via}",
        must=[str(oct_via[1])])
    add("chart", "Offers by BDE", f"bar per BDE, {top} highest ({n_top})", must=[top])
    add("chart", f"Daniel Reed's leads by lead status", "bar per stage", must=[str(stage.most_common(1)[0][1])])
    add("chart", "Leads by lead type in December", "bar 4 types",
        must=[str(Counter(r['Lead Type'] for r in tabs['Dec 2026']).most_common(1)[0][1])])
    # 1 Nov 2026 is a Sunday: its week holds only that day once October is
    # filtered out, a figure the unfiltered chart never shows.
    nov_first = sum(r["Date"] == date(2026, 11, 1) for r in allrows)
    add("chart", "Weekly leads in November", f"weeks of Nov only, first week {nov_first}",
        must=[str(nov_first)])

    (OUT / "questions.json").write_text(json.dumps(q, indent=1, default=str))
    print(f"{len(allrows)} lead rows in {len(tabs)} tabs; {len(q)} questions")


if __name__ == "__main__":
    main()
