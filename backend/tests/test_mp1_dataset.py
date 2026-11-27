"""The MP1 set (issue #126): eight documents of two or three pages, to compare one model
call per page with one call per document.

It lives in `data/mock/mp1/`, flat, with an answer key per document and a manifest. It
is not part of the kit (`demo.NOT_KIT`). The tests below are written from the frozen
specification (`backend/.dev-data/issue-126/MP1-SPEC.md`), not from the generator: the
table `SPEC` is typed by hand, and every test compares the committed files with it.
The checks are functions that return a list of problems (`problems`, `scan_problems`,
`source_problems`, `reader_problems`): the same function serves the positive tests (no
problem) and the negative tests (the problem is named), so a check that cannot fail
would be seen.
"""

import copy
import functools
import importlib
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops
from pypdf import PdfReader

from app import demo
from app.docintel import evaluate, labels, omni
from app.docintel.compare import BadAnswerKey
from app.docintel.fixture import FixtureReader
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType
from tests.reader_contract import MOCK
from tests.test_omni_reader import Stub

ROOT = MOCK.parents[1]
MP1 = MOCK / "mp1"
NEW_SETS = frozenset({"n1", "mp1"})  # the folders of data/mock that are not the kit


@pytest.fixture(autouse=True)
def _omni_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings of the reader; the stub answers at this URL (no network)."""
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", "https://vlm.test/v1")
    monkeypatch.setenv("VLM_MODEL", "omni-test")


# ===================================================== the specification, by hand
# One row per field: (name, value, key page, key snippet, accepted pages). An accepted
# page is (page, evidence text printed on it, the label text that stands before the
# value there). The key page is always one of them. The tuples come from section 5 of
# the specification, never from a file of the set.


@dataclass(frozen=True)
class Accepted:
    page: int
    evidence: str
    context: str  # label and value as the page prints them, in this order


@dataclass(frozen=True)
class Fld:
    name: str
    value: Any
    page: int
    snippet: str | None  # None for an absent field
    accepted: tuple[Accepted, ...] = ()


def many(evidence: str, contexts: dict[int, str]) -> tuple[Accepted, ...]:
    return tuple(Accepted(p, evidence, c) for p, c in sorted(contexts.items()))


# description, quantity, unit price, amount, page (a cancellation line has no
# quantity and no unit price)
Line = tuple[str, int | None, float | None, float, int]
FOOT = "Page {} of {}"
HEAD_REPAIR = ("Work", "Qty", "Unit (CHF)", "Amount (CHF)")
HEAD_MEDICAL = ("Service", "Qty", "Unit (CHF)", "Amount (CHF)")
HEAD_CANCELLATION = ("Description", "Amount (CHF)")


@dataclass(frozen=True)
class Spec:
    stem: str
    type: str
    pages: int
    kind: str  # "text" or "scan"
    fields: tuple[Fld, ...]
    lines: tuple[Line, ...]
    printed: tuple[tuple[str, ...], ...]  # per page: texts that the page prints
    headers: dict[int, tuple[str, bool]]  # page -> (running header, one string?)
    favour: str
    scenarios: tuple[str, ...]
    absent: dict[str, str]


def pair(label: str, value: str) -> tuple[str, str]:
    return (label, value)


S01 = "mp1-01-repair-invoice-carried-forward"
S02 = "mp1-02-medical-invoice-continued"
S03 = "mp1-03-cancellation-confirmation-scan"
S04 = "mp1-04-repair-quote-terms"
S05 = "mp1-05-repair-invoice-service-rows"
S06 = "mp1-06-medical-report-scan"
S07 = "mp1-07-booking-confirmation-no-price"
S08 = "mp1-08-police-report-two-dates"

INV01 = "Invoice no.: WB-26-0714"
BOOK03 = "Booking reference: MJ-512086"
QUOTE04 = "Quote no.: FA/26/0451"
HEAD05 = "Kessler & Brunold Heizservice AG | Invoice no.: KB-26-1893 | Date: 2026-11-05"
HEAD06 = "Medical report | Patient: Mauro Tresch"
HEAD08 = "Report no.: LPU-2026-04193 | Reporting person: Dominik Haefeli"

SPEC: tuple[Spec, ...] = (
    Spec(
        S01,
        "repair_invoice",
        3,
        "text",
        (
            Fld(
                "invoice_number",
                "WB-26-0714",
                1,
                "WB-26-0714",
                many("WB-26-0714", {1: INV01, 2: INV01, 3: INV01}),
            ),
            Fld("invoice_date", "2026-10-08", 1, "2026-10-08"),
            Fld("repair_company", "Wydler Badbau AG", 1, "Wydler Badbau AG"),
            Fld("total", 6766.0, 3, "CHF 6,766.00"),
            Fld("customer_name", "Melina Zbinden", 1, "Melina Zbinden"),
        ),
        (
            ("Demolition of old bathroom tiles, per m2", 14, 38.0, 532.0, 1),
            ("Disposal of rubble, per container", 1, 265.0, 265.0, 1),
            ("Waterproofing membrane, per m2", 14, 26.5, 371.0, 1),
            ("Floor drain, stainless steel", 1, 189.0, 189.0, 1),
            ("Plumber, per hour", 11, 98.0, 1078.0, 1),
            ("Wall tiles 30x60, per m2", 22, 54.8, 1205.6, 2),
            ("Tile adhesive, bag 25 kg", 6, 31.9, 191.4, 2),
            ("Tiler, per hour", 19, 92.0, 1748.0, 2),
            ("Silicone joints, per metre", 28, 4.5, 126.0, 2),
            ("Shower screen, glass 8 mm", 1, 655.0, 655.0, 2),
            ("Mixer tap, wall mounted", 1, 327.0, 327.0, 3),
            ("Travel and small material, flat", 1, 78.0, 78.0, 3),
        ),
        (
            (
                "Wydler Badbau AG",
                "Gewerbering 14, 8580 Amriswil",
                "Tel. +41 71 555 38 17",
                "Melina Zbinden",
                "Rebhalde 22",
                "8500 Frauenfeld",
                "Switzerland",
                "Invoice no.:",
                "WB-26-0714",
                "Date:",
                "2026-10-08",
                "Work done:",
                "2026-09-28 to 2026-10-02",
                "Invoice",
                *HEAD_REPAIR,
                "Subtotal page 1, carried forward",
                "2,435.00",
                "Page 1 of 3",
            ),
            (
                INV01,
                *HEAD_REPAIR,
                "Brought forward from page 1",
                "2,435.00",
                "Carried forward to page 3",
                "6,361.00",
                "Page 2 of 3",
            ),
            (
                INV01,
                *HEAD_REPAIR,
                "Brought forward from page 2",
                "6,361.00",
                "Total",
                "CHF 6,766.00",
                "of which VAT 8.1%",
                "506.98",
                "Payable within 30 days.",
                "Page 3 of 3",
            ),
        ),
        {2: (INV01, False), 3: (INV01, False)},
        "document",
        (
            "number_and_date_on_page_1",
            "total_on_last_page",
            "table_over_page_break",
            "repeated_header",
            "carried_forward_subtotal",
            "value_on_two_pages",
        ),
        {},
    ),
    Spec(
        S02,
        "medical_invoice",
        2,
        "text",
        (
            Fld("invoice_number", "RMC-40718", 1, "RMC-40718"),
            Fld("invoice_date", "2026-09-17", 1, "2026-09-17"),
            Fld("provider", "Rheinau Health Centre AG", 1, "Rheinau Health Centre AG"),
            Fld("patient_name", "Valentin Egloff", 1, "Valentin Egloff"),
            Fld("total", 788.0, 2, "CHF 788.00"),
        ),
        (
            ("General consultation, 30 min", 1, 118.5, 118.5, 1),
            ("X-ray, left knee, 2 views", 1, 84.2, 84.2, 1),
            ("Ultrasound, knee joint", 1, 96.8, 96.8, 1),
            ("Manual therapy, 20 min", 4, 51.75, 207.0, 1),
            ("Knee brace, size M", 1, 139.0, 139.0, 2),
            ("Follow-up consultation with written report", 1, 142.5, 142.5, 2),
        ),
        (
            (
                "Rheinau Health Centre AG",
                "Inselweg 9, 8462 Rheinau",
                "Tel. +41 52 555 41 26",
                "Valentin Egloff",
                "Kirchgasse 6",
                "8212 Neuhausen",
                "Switzerland",
                "Invoice no.:",
                "RMC-40718",
                "Invoice date:",
                "2026-09-17",
                "Patient no.:",
                "PN-90352",
                "Treatment:",
                "2026-08-31 to 2026-09-11",
                "Invoice",
                "Patient:",
                "Cost estimate of 2026-08-28: CHF 650.00",
                *HEAD_MEDICAL,
                "Continued on page 2",
                "Page 1 of 2",
            ),
            ("Total", "CHF 788.00", "Payable within 30 days.", "Page 2 of 2"),
        ),
        {},
        "document",
        (
            "number_and_date_on_page_1",
            "total_on_last_page",
            "table_over_page_break",
            "misleading_candidate",
        ),
        {},
    ),
    Spec(
        S03,
        "cancellation_confirmation",
        3,
        "scan",
        (
            Fld(
                "booking_reference",
                "MJ-512086",
                1,
                "MJ-512086",
                many("MJ-512086", {1: BOOK03, 2: BOOK03, 3: BOOK03}),
            ),
            Fld("cancellation_date", "2026-10-30", 1, "2026-10-30"),
            Fld("refund_received", 1893.0, 3, "CHF 1,893.00"),
        ),
        (
            ("Trip price", None, None, 2860.0, 2),
            ("Cancellation charge (30%)", None, None, -858.0, 2),
            ("Insurance premium, not refundable", None, None, -64.0, 2),
            ("Handling fee", None, None, -45.0, 2),
        ),
        (
            (
                "Meridian Journeys SA",
                "Avenue des Alpes 21, 1820 Montreux",
                "Tel. +41 21 555 73 08",
                "Beatrice Zurfluh",
                "Weidstrasse 9",
                "9630 Wattwil",
                "Switzerland",
                "Cancellation confirmation",
                "Booking reference:",
                "MJ-512086",
                "Cancelled on:",
                "2026-10-30",
                "Departure was planned:",
                "2026-12-12",
                "Trip:",
                "Cyclades, 9 nights, 2 persons",
                "Booking amount:",
                "CHF 2,860.00",
                "The calculation is on page 2, the refund on page 3.",
                "Page 1 of 3",
            ),
            (BOOK03, "Calculation", *HEAD_CANCELLATION, "Page 2 of 3"),
            (
                BOOK03,
                "Refund:",
                "CHF 1,893.00",
                "The amount is credited within 10 working days.",
                "Montreux, 2026-10-31",
                "Page 3 of 3",
            ),
        ),
        {2: (BOOK03, False), 3: (BOOK03, False)},
        "neutral",
        (
            "scan",
            "number_and_date_on_page_1",
            "total_on_last_page",
            "repeated_header",
            "misleading_candidate",
            "value_on_two_pages",
        ),
        {},
    ),
    Spec(
        S04,
        "repair_quote",
        3,
        "text",
        (
            Fld(
                "quote_number",
                "FA/26/0451",
                1,
                "FA/26/0451",
                many("FA/26/0451", {1: QUOTE04, 2: QUOTE04, 3: QUOTE04}),
            ),
            Fld("quote_date", None, 1, None),
            Fld("repair_company", "Stalder Fassaden GmbH", 1, "Stalder Fassaden GmbH"),
            Fld("total", 8488.5, 2, "CHF 8,488.50"),
            Fld(
                "customer_name",
                "Gottfried Wirz",
                1,
                "Gottfried Wirz",
                many(
                    "Gottfried Wirz",
                    {
                        1: "Gottfried Wirz",
                        2: "Accepted by the customer: Gottfried Wirz",
                    },
                ),
            ),
        ),
        (
            ("Scaffolding, erect and remove, per m2", 180, 14.2, 2556.0, 1),
            ("High-pressure cleaning of facade, per m2", 165, 6.8, 1122.0, 1),
            ("Crack repair, per metre", 42, 17.5, 735.0, 1),
            ("Base coat, mineral, per m2", 165, 9.4, 1551.0, 1),
            ("Top coat, silicate, per m2", 165, 15.3, 2524.5, 1),
        ),
        (
            (
                "Stalder Fassaden GmbH",
                "Schuetzenweg 8, 6210 Sursee",
                "Tel. +41 41 555 62 91",
                "Gottfried Wirz",
                "Eichmattstrasse 4",
                "6020 Emmenbruecke",
                "Switzerland",
                "Quote no.:",
                "FA/26/0451",
                "Site visit:",
                "2026-10-06",
                "Quote",
                *HEAD_REPAIR,
                "Total and options: see page 2",
                "Page 1 of 3",
            ),
            (
                QUOTE04,
                "Total of the quote",
                "CHF 8,488.50",
                "Optional work, not in the total above",
                "Replace window sills, 12 pieces",
                "1,275.00",
                "Total with options",
                "CHF 9,763.50",
                "Valid until 2026-12-18",
                "Accepted by the customer: Gottfried Wirz",
                "Page 2 of 3",
            ),
            (
                QUOTE04,
                "General terms of business (edition 2026-02-15)",
                "1. A deposit of 30% is due within 10 days of the order.",
                "2. Reminder fee: CHF 30.00. Interest on late payment: 5% per year.",
                "3. Price changes above CHF 500.00 or 5% need a written agreement.",
                "4. Liability is limited to CHF 50,000.00 per event.",
                "5. Complaints: +41 41 555 62 91, reference AGB-2026-03.",
                "Page 3 of 3",
            ),
        ),
        {2: (QUOTE04, False), 3: (QUOTE04, False)},
        "page",
        (
            "terms_page",
            "repeated_header",
            "misleading_candidate",
            "absent_field",
            "value_on_two_pages",
        ),
        {"quote_date": "lure"},
    ),
    Spec(
        S05,
        "repair_invoice",
        2,
        "text",
        (
            Fld(
                "invoice_number",
                "KB-26-1893",
                1,
                "KB-26-1893",
                many(
                    "KB-26-1893",
                    {1: "Invoice no.: KB-26-1893", 2: "Invoice no.: KB-26-1893"},
                ),
            ),
            Fld(
                "invoice_date",
                "2026-11-05",
                1,
                "2026-11-05",
                many(
                    "2026-11-05",
                    {1: "Date: 2026-11-05", 2: "Date: 2026-11-05"},
                ),
            ),
            Fld(
                "repair_company",
                "Kessler & Brunold Heizservice AG",
                1,
                "Kessler & Brunold Heizservice AG",
                many(
                    "Kessler & Brunold Heizservice AG",
                    {
                        1: "Kessler & Brunold Heizservice AG",
                        2: "Kessler & Brunold Heizservice AG | Invoice no.:",
                    },
                ),
            ),
            Fld("total", 1885.5, 2, "CHF 1,885.50"),
            Fld("customer_name", "Bruno Steinemann", 1, "Bruno Steinemann"),
        ),
        (
            ("Boiler service, flat 1.01", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 1.02", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 1.03", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 1.04", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 1.05", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 1.06", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 2.01", 1, 148.0, 148.0, 1),
            ("Boiler service, flat 2.02", 1, 148.0, 148.0, 2),
            (
                "Boiler service, flat 2.03, with new circulation pump",
                1,
                312.5,
                312.5,
                2,
            ),
            ("Boiler service, flat 2.04", 1, 148.0, 148.0, 2),
            ("Boiler service, flat 2.05", 1, 148.0, 148.0, 2),
            ("Call-out fee, second visit", 2, 46.5, 93.0, 2),
        ),
        (
            (
                "Kessler & Brunold Heizservice AG",
                "Werkhofstrasse 6, 8610 Uster",
                "Tel. +41 44 555 29 64",
                "Bruno Steinemann",
                "Hofackerweg 3",
                "8620 Wetzikon",
                "Switzerland",
                "Invoice no.:",
                "KB-26-1893",
                "Date:",
                "2026-11-05",
                "Invoice",
                *HEAD_REPAIR,
                "Continued on page 2",
                "Page 1 of 2",
            ),
            (HEAD05, *HEAD_REPAIR, "Total", "CHF 1,885.50", "Page 2 of 2"),
        ),
        {2: (HEAD05, True)},
        "page",
        (
            "total_on_last_page",
            "table_over_page_break",
            "repeated_header",
            "value_on_two_pages",
        ),
        {},
    ),
    Spec(
        S06,
        "medical_report",
        2,
        "scan",
        (
            Fld(
                "patient_name",
                "Mauro Tresch",
                1,
                "Mauro Tresch",
                many(
                    "Mauro Tresch",
                    {1: "Patient: Mauro Tresch", 2: "Patient: Mauro Tresch"},
                ),
            ),
            Fld("diagnosis", "lumbar disc herniation", 2, "lumbar disc herniation"),
            Fld("treatment_date", "2026-10-27", 1, "2026-10-27"),
            Fld(
                "provider",
                "Thurauen Health Centre AG",
                1,
                "Thurauen Health Centre AG",
                many(
                    "Thurauen Health Centre AG",
                    {1: "Thurauen Health Centre AG", 2: "Thurauen Health Centre AG"},
                ),
            ),
        ),
        (),
        (
            (
                "Thurauen Health Centre AG",
                "Seeblickweg 2, 9620 Lichtensteig",
                "Tel. +41 71 555 90 13",
                "Medical report",
                "Patient:",
                "Mauro Tresch (born 1991-06-02)",
                "Examined on:",
                "2026-10-27",
                "Referred by:",
                "Dr. med. Flurin Camenzind on 2026-10-13",
                "History: low back pain for three weeks, radiating into the left leg.",
                "Findings: reduced flexion of the lumbar spine, straight leg raise "
                "positive on the left.",
                "Continued on page 2",
                "Page 1 of 2",
            ),
            (
                HEAD06,
                "Diagnosis:",
                "lumbar disc herniation",
                "Therapy: physiotherapy, nine sessions, and pain medication for ten "
                "days.",
                "Lichtensteig, 2026-10-29",
                "Dr. med. Sonja Kappeler, specialist in rheumatology",
                "Thurauen Health Centre AG",
                "Page 2 of 2",
            ),
        ),
        {2: (HEAD06, True)},
        "page",
        ("scan", "repeated_header", "misleading_candidate", "value_on_two_pages"),
        {},
    ),
    Spec(
        S07,
        "booking_confirmation",
        2,
        "text",
        (
            Fld(
                "booking_reference",
                "FBT-317405",
                1,
                "FBT-317405",
                many(
                    "FBT-317405",
                    {
                        1: "Booking reference: FBT-317405",
                        2: "Booking reference: FBT-317405",
                    },
                ),
            ),
            Fld("traveller_name", "Ottilie Burkhalter", 1, "Ottilie Burkhalter"),
            Fld("departure_date", "2027-01-16", 1, "2027-01-16"),
            Fld("trip_price", None, 1, None),
        ),
        (),
        (
            (
                "Firnblick Travel AG",
                "Pilatusweg 11, 6370 Stans",
                "Tel. +41 41 555 17 82",
                "Ottilie Burkhalter",
                "Schwandenweg 5",
                "6060 Sarnen",
                "Switzerland",
                "Booking confirmation",
                "Booking reference:",
                "FBT-317405",
                "Booked on:",
                "2026-09-03",
                "Customer no.:",
                "C-48271",
                "Traveller:",
                "Trip:",
                "Wellness week in the Engadin, 7 nights, half board",
                "Departure:",
                "2027-01-16",
                "Page 1 of 2",
            ),
            (
                "Booking reference: FBT-317405",
                "Payments",
                "Return: 2027-01-23",
                "Deposit received on 2026-09-05: CHF 430.00",
                "Next instalment, due 2026-12-01: CHF 600.00",
                "Further instalments follow. The invoice with the total price "
                "follows by post.",
                "Page 2 of 2",
            ),
        ),
        {2: ("Booking reference: FBT-317405", False)},
        "neutral",
        (
            "repeated_header",
            "terms_page",
            "misleading_candidate",
            "absent_field",
            "value_on_two_pages",
        ),
        {"trip_price": "lure"},
    ),
    Spec(
        S08,
        "police_report",
        2,
        "text",
        (
            Fld(
                "report_number",
                "LPU-2026-04193",
                1,
                "LPU-2026-04193",
                many(
                    "LPU-2026-04193",
                    {
                        1: "Report no.: LPU-2026-04193",
                        2: "Report no.: LPU-2026-04193",
                    },
                ),
            ),
            Fld("incident_date", "2026-11-10", 2, "2026-11-10"),
            Fld(
                "customer_name",
                "Dominik Haefeli",
                1,
                "Dominik Haefeli",
                many(
                    "Dominik Haefeli",
                    {
                        1: "Reporting person: Dominik Haefeli",
                        2: "Reporting person: Dominik Haefeli",
                    },
                ),
            ),
            Fld("offence", "Theft", 2, "Theft"),
        ),
        (),
        (
            (
                "Linthgebiet Police",
                "Station Uznach, Rickenstrasse 12, 8730 Uznach",
                "Tel. +41 55 555 46 30",
                "Police report",
                "Report no.:",
                "LPU-2026-04193",
                "Date of report:",
                "2026-11-11",
                "Officer:",
                "Sgt. M. Gubler",
                "Reporting person:",
                "Dominik Haefeli (born 1979-02-23)",
                "Address:",
                "Mattenweg 18, 8730 Uznach",
                "Reported by telephone on 2026-11-11, 09:15.",
                "Page 1 of 2",
            ),
            (
                HEAD08,
                "Statement",
                "The theft took place on 2026-11-10 between 06:15 and 07:20.",
                "Witness: Sibylle Nyffeler saw a blue van at 06:50.",
                "Offence:",
                "Theft",
                "Stolen item: e-bike, value stated CHF 3,190.00",
                "Signed: Dominik Haefeli",
                "Uznach, 2026-11-11",
                "Page 2 of 2",
            ),
        ),
        {2: (HEAD08, True)},
        "document",
        ("repeated_header", "misleading_candidate", "value_on_two_pages"),
        {},
    ),
)
BY_STEM = {s.stem: s for s in SPEC}
STEMS = [s.stem for s in SPEC]
TEXT_STEMS = [s.stem for s in SPEC if s.kind == "text"]
SCAN_STEMS = [s.stem for s in SPEC if s.kind == "scan"]
EXPECTED_COUNTS = {
    "documents": 8,
    "pages": 19,
    "text_documents": 6,
    "scan_documents": 2,
    "text_pages": 14,
    "scan_pages": 5,
    "fields_scored": 35,
    "fields_present": 33,
    "fields_absent": 2,
    "fields_repeated": 12,
    "line_items": 39,
    "line_items_text": 35,
    "line_items_scan": 4,
}
# The sums of the specification (section 5), as text so that no float is involved
PAGE_SUMS = {
    S01: {1: "2435.00", 2: "3926.00", 3: "405.00"},
    S02: {1: "506.50", 2: "281.50"},
    S03: {2: "1893.00"},  # the four lines, with their signs
    S04: {1: "8488.50"},
    S05: {1: "1036.00", 2: "849.50"},
}
TOTALS = {S01: "6766.00", S02: "788.00", S04: "8488.50", S05: "1885.50"}
# the pages that print the column head of their table (section 5)
HEAD_PAGES = {S01: {1, 2, 3}, S02: {1}, S04: {1}, S05: {1, 2}, S07: set(), S08: set()}


def line_dict(line: Line) -> dict[str, Any]:
    description, quantity, unit, amount, _ = line
    if quantity is None:
        return {"description": description, "amount": amount}
    return {
        "description": description,
        "quantity": quantity,
        "unit_price": unit,
        "amount": amount,
    }


def expected_key(spec: Spec) -> dict[str, Any]:
    """The answer key of the specification, built from the table."""
    fields = []
    for f in spec.fields:
        row: dict[str, Any] = {
            "name": f.name,
            "value": f.value,
            "confidence": 1.0,
            "page": f.page,
        }
        if f.value is not None:
            row["snippet"] = f.snippet
        if f.accepted:
            row["accepted_pages"] = [a.page for a in f.accepted]
            row["page_snippets"] = {str(a.page): a.evidence for a in f.accepted}
        fields.append(row)
    key: dict[str, Any] = {
        "document_type": spec.type,
        "fields": fields,
        "line_items": [line_dict(x) for x in spec.lines],
        "line_pages": [x[4] for x in spec.lines],
        "pages": spec.pages,
    }
    if spec.kind == "scan":
        key["variant"] = "scan"
    return key


# ================================================================== the checks


def flat(text: str) -> str:
    """No white space: pypdf may join or split the strings of one line."""
    return "".join(text.split())


def has(text: str, part: str) -> bool:
    return flat(part) in flat(text)


def page_texts(pdf: Path) -> list[str]:
    """The text layer of each page, as the reader sees it."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return [p.extract_text() or "" for p in PdfReader(pdf).pages]


def typed(name: str, value: Any) -> Any:
    """The value as the reader gives it to the label check: a date is a date."""
    if labels.SAFEGUARDED.get(name) == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def field_problems(kind: str, f: dict[str, Any], texts: list[str]) -> list[str]:
    name, value = f["name"], f["value"]
    if value is None:
        extra = {"snippet", "accepted_pages", "page_snippets"} & set(f)
        return [f"{name}: an absent field has {sorted(extra)}"] if extra else []
    out: list[str] = []
    pages = f.get("accepted_pages") or [f["page"]]
    evidence = f.get("page_snippets") or {str(f["page"]): f["snippet"]}
    if evidence.get(str(f["page"])) != f["snippet"]:
        out.append(f"{name}: the evidence of the key page is not the snippet")
    for p in pages:
        text = evidence.get(str(p))
        if not 1 <= p <= len(texts) or text is None:
            out.append(f"{name}: page {p} does not exist or has no evidence")
            continue
        page = texts[p - 1]
        if not omni._shown(text, page):
            out.append(f"{name}: evidence {text!r} is not shown on page {p}")
        elif not omni._holds(text, value):
            out.append(f"{name}: evidence {text!r} does not hold the value")
        elif not labels.verified(kind, name, typed(name, value), text, page):
            out.append(f"{name}: the label check does not verify {text!r} on page {p}")
    printing = [n for n, t in enumerate(texts, 1) if omni._holds(t, value)]
    if printing and printing[0] != f["page"]:
        out.append(
            f"{name}: the first page that prints the value is {printing[0]}, "
            f"the key says {f['page']}"
        )
    if set(printing) != set(pages):
        out.append(
            f"{name}: the value is printed on pages {printing}, "
            f"the key accepts {sorted(pages)}"
        )
    return out


def line_problems(key: dict[str, Any], texts: list[str]) -> list[str]:
    lines, pages = key["line_items"], key["line_pages"]
    if len(pages) != len(lines):
        return [f"line_pages: {len(pages)} entries for {len(lines)} lines"]
    out: list[str] = []
    if pages != sorted(pages):
        out.append("line_pages is not in page order")
    last: dict[int, int] = {}
    for i, (line, p) in enumerate(zip(lines, pages, strict=True), 1):
        if not 1 <= p <= len(texts):
            out.append(f"line {i}: page {p} does not exist")
            continue
        if not omni._on_page(line, texts[p - 1]):
            out.append(f"line {i}: not on the text of page {p}")
            continue
        description = flat(line["description"])
        out += [
            f"line {i}: its description is also on page {q}"
            for q, t in enumerate(texts, 1)
            if q != p and description in flat(t)
        ]
        position = flat(texts[p - 1]).find(description)
        if position < last.get(p, -1):
            out.append(f"line {i}: not in the order of the lines of page {p}")
        last[p] = position
    return out


def arithmetic_problems(key: dict[str, Any]) -> list[str]:
    out: list[str] = []
    total = Decimal(0)
    for i, line in enumerate(key["line_items"], 1):
        amount = Decimal(str(line["amount"]))
        if "quantity" in line:
            product = Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"]))
            if product != amount:
                out.append(f"line {i}: quantity times unit price is {product}")
        total += amount
    by_name = {f["name"]: f["value"] for f in key["fields"]}
    target = by_name.get("total", by_name.get("refund_received"))
    if key["line_items"] and target is not None and total != Decimal(str(target)):
        out.append(f"sum: the lines add up to {total}, the document gives {target}")
    return out


def problems(pdf: Path, key: dict[str, Any]) -> list[str]:
    """What is wrong when this key is held against a PDF with a text layer."""
    texts = page_texts(pdf)
    out: list[str] = []
    if len(texts) != key["pages"]:
        out.append(f"page count: {len(texts)} in the file, {key['pages']} in the key")
    out += [f"page {n}: no text layer" for n, t in enumerate(texts, 1) if not t.strip()]
    for f in key["fields"]:
        out += field_problems(key["document_type"], f, texts)
    return out + line_problems(key, texts) + arithmetic_problems(key)


def scan_problems(pdf: Path, key: dict[str, Any]) -> list[str]:
    """What is wrong when this PDF is held against the key of a scan."""
    reader = PdfReader(pdf)
    out: list[str] = []
    if len(reader.pages) != key["pages"]:
        out.append(f"page count: {len(reader.pages)} in the file, {key['pages']}")
    for n, page in enumerate(reader.pages, 1):
        if (page.extract_text() or "").strip():
            out.append(f"page {n}: has a text layer")
        if len(page.images) != 1:
            out.append(f"page {n}: {len(page.images)} images, one expected")
        else:
            image = page.images[0].image
            assert image is not None
            if not is_grey(image):
                out.append(f"page {n}: the image is not grey")
            if not 1.40 < image.height / image.width < 1.43:
                out.append(f"page {n}: the image is not A4")
    if b"Exif" in pdf.read_bytes():
        out.append("Exif data in the file")
    return out


def is_grey(image: Image.Image) -> bool:
    """One grey level, or three equal channels."""
    if image.mode == "L":
        return True
    red, green, blue = image.convert("RGB").split()
    return not (
        ImageChops.difference(red, green).getbbox()
        or ImageChops.difference(green, blue).getbbox()
    )


def source_problems(key: dict[str, Any], pages: list[list[str]]) -> list[str]:
    """The key against the printed strings of each page, in drawing order (what
    `mp1.build` returns). A snippet or an evidence text is a part of ONE string; a
    cell of a table is a string of its own."""
    out: list[str] = []
    if len(pages) != key["pages"]:
        out.append(f"page count: {len(pages)} source pages, {key['pages']} in the key")
        return out

    def part_of_one(text: str, p: int) -> bool:
        return 1 <= p <= len(pages) and any(text in s for s in pages[p - 1])

    def a_cell(text: str, p: int) -> bool:
        return 1 <= p <= len(pages) and any(s.strip() == text for s in pages[p - 1])

    for f in key["fields"]:
        if f["value"] is None:
            continue
        evidence = f.get("page_snippets") or {str(f["page"]): f["snippet"]}
        for p, text in sorted(evidence.items(), key=lambda kv: int(kv[0])):
            if not part_of_one(text, int(p)):
                out.append(f"{f['name']}: {text!r} is not in one string of page {p}")
    for i, (line, p) in enumerate(
        zip(key["line_items"], key["line_pages"], strict=True), 1
    ):
        for name, value in line.items():
            if isinstance(value, str):
                cell = value
            elif name == "quantity":
                cell = str(value)
            else:
                cell = f"{value:,.2f}"  # 2 decimals, "," for thousands, the sign
            if not a_cell(cell, p):
                out.append(f"line {i}: the {name} {cell!r} is not a cell of page {p}")
    return out


# ===================================================== the files of the set
def key_of(stem: str) -> dict[str, Any]:
    key: dict[str, Any] = json.loads((MP1 / f"{stem}.expected.json").read_text("utf-8"))
    return key


def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((MP1 / "manifest.json").read_text("utf-8"))
    return data


def counts_of(keys: dict[str, dict[str, Any]], kinds: dict[str, str]) -> dict[str, int]:
    """Every count of section 1, computed from keys (a mapping stem -> key) and the
    kind of each stem."""
    fields = [f for k in keys.values() for f in k["fields"]]
    lines = {s: len(k["line_items"]) for s, k in keys.items()}
    return {
        "documents": len(keys),
        "pages": sum(k["pages"] for k in keys.values()),
        "text_documents": sum(v == "text" for v in kinds.values()),
        "scan_documents": sum(v == "scan" for v in kinds.values()),
        "text_pages": sum(k["pages"] for s, k in keys.items() if kinds[s] == "text"),
        "scan_pages": sum(k["pages"] for s, k in keys.items() if kinds[s] == "scan"),
        "fields_scored": len(fields),
        "fields_present": sum(f["value"] is not None for f in fields),
        "fields_absent": sum(f["value"] is None for f in fields),
        "fields_repeated": sum(
            f["value"] is not None and len(f.get("accepted_pages", [])) > 1
            for f in fields
        ),
        "line_items": sum(lines.values()),
        "line_items_text": sum(n for s, n in lines.items() if kinds[s] == "text"),
        "line_items_scan": sum(n for s, n in lines.items() if kinds[s] == "scan"),
    }


@functools.cache
def mp1_module() -> Any:
    """The generator of the set (loaded when a test asks for it)."""
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("mockdocs.mp1")


@pytest.fixture(scope="module")
def printed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[list[str]]]:
    """What the generator prints on each page of each document (a fresh run)."""
    made: dict[str, list[list[str]]] = mp1_module().build(
        tmp_path_factory.mktemp("mp1-printed")
    )
    return made


# ================================================================ 1 the contents


def test_the_folder_holds_the_eight_documents_their_keys_and_the_manifest_only() -> (
    None
):
    names = sorted(p.name for p in MP1.iterdir())
    assert names == sorted(
        [f"{s}.pdf" for s in STEMS]
        + [f"{s}.expected.json" for s in STEMS]
        + ["manifest.json"]
    )
    assert len(STEMS) == len(set(STEMS)) == 8


def test_the_counts_of_the_table_of_the_specification_are_those_of_section_1() -> None:
    keys = {s.stem: expected_key(s) for s in SPEC}
    kinds = {s.stem: s.kind for s in SPEC}
    assert counts_of(keys, kinds) == EXPECTED_COUNTS


def test_the_counts_of_section_1_are_computed_from_the_committed_keys() -> None:
    keys = {s: key_of(s) for s in STEMS}
    kinds = {s.stem: s.kind for s in SPEC}
    assert counts_of(keys, kinds) == EXPECTED_COUNTS


def test_the_page_count_of_each_document_is_the_one_of_the_specification() -> None:
    assert [len(PdfReader(MP1 / f"{s}.pdf").pages) for s in STEMS] == [
        3,
        2,
        3,
        3,
        2,
        2,
        2,
        2,
    ]
    assert [key_of(s)["pages"] for s in STEMS] == [3, 2, 3, 3, 2, 2, 2, 2]


def test_the_manifest_counts_equal_the_counts_of_the_keys() -> None:
    keys = {s: key_of(s) for s in STEMS}
    kinds = {d["file"].removesuffix(".pdf"): d["kind"] for d in manifest()["documents"]}
    assert manifest()["counts"] == counts_of(keys, kinds) == EXPECTED_COUNTS


def test_the_manifest_tells_what_the_set_is() -> None:
    m = manifest()
    assert set(m) == {
        "set",
        "version",
        "issue",
        "purpose",
        "language",
        "documents",
        "counts",
    }
    assert (m["set"], m["version"], m["issue"], m["language"]) == ("mp1", 1, 126, "en")
    assert isinstance(m["purpose"], str) and m["purpose"].strip()
    assert [d["file"] for d in m["documents"]] == [f"{s}.pdf" for s in STEMS]


@pytest.mark.parametrize("stem", STEMS)
def test_the_manifest_lists_each_document_as_the_specification_says(stem: str) -> None:
    spec = BY_STEM[stem]
    [entry] = [d for d in manifest()["documents"] if d["file"] == f"{stem}.pdf"]
    present = [f for f in spec.fields if f.value is not None]
    wanted: dict[str, Any] = {
        "file": f"{stem}.pdf",
        "document_type": spec.type,
        "kind": spec.kind,
        "pages": spec.pages,
        "fields_present": len(present),
        "fields_absent": len(spec.fields) - len(present),
        "line_items": len(spec.lines),
        "repeated": {
            f.name: [a.page for a in f.accepted] for f in present if f.accepted
        },
        "absent": spec.absent,
        "expected_to_favour": spec.favour,
        "scenarios": list(spec.scenarios),
    }
    if spec.kind == "scan":
        pages = entry.pop("source_text_sha256")
        assert len(pages) == spec.pages
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in pages)
        assert len(set(pages)) == spec.pages  # no two pages share a text
    assert entry == wanted


def test_the_kinds_are_six_text_documents_and_two_scans() -> None:
    kinds = [d["kind"] for d in manifest()["documents"]]
    assert {k: kinds.count(k) for k in set(kinds)} == {"text": 6, "scan": 2}
    assert SCAN_STEMS == [S03, S06]


# =========================================================== 2 the answer keys


@pytest.mark.parametrize("stem", STEMS)
def test_each_answer_key_equals_the_table_of_the_specification(stem: str) -> None:
    key, want = key_of(stem), expected_key(BY_STEM[stem])
    assert set(key) == set(want)  # variant only for the two scans
    for name in ("document_type", "pages", "line_pages", "line_items"):
        assert key[name] == want[name], name
    assert key.get("variant") == want.get("variant")
    got = {f["name"]: f for f in key["fields"]}
    assert len(got) == len(key["fields"]) == len(want["fields"])
    assert got == {f["name"]: f for f in want["fields"]}


@pytest.mark.parametrize("stem", STEMS)
def test_the_types_of_the_values_of_a_key_are_the_ones_of_the_kit(stem: str) -> None:
    key = key_of(stem)
    for line in key["line_items"]:
        if "quantity" in line:
            assert type(line["quantity"]) is int, line
        for name in ("unit_price", "amount"):
            assert name not in line or type(line[name]) in (int, float), line
    for f in key["fields"]:
        assert type(f["page"]) is int and f["confidence"] == 1.0
        assert f["value"] is None or type(f["value"]) in (str, float), f
        if f["value"] is None:
            assert f["page"] == 1 and "snippet" not in f  # the form of N1
    assert all(type(p) is int for p in key["line_pages"])


def test_the_two_absent_fields_are_the_lures_of_the_specification() -> None:
    absent = {
        (s, f["name"]) for s in STEMS for f in key_of(s)["fields"] if f["value"] is None
    }
    assert absent == {(S04, "quote_date"), (S07, "trip_price")}
    assert {n: v for d in manifest()["documents"] for n, v in d["absent"].items()} == {
        "quote_date": "lure",
        "trip_price": "lure",
    }


# ============================================================== 3 the arithmetic


def test_each_priced_line_is_quantity_times_unit_price() -> None:
    n = 0
    for spec in SPEC:
        for _description, quantity, unit, amount, _ in spec.lines:
            if quantity is not None:
                n += 1
                assert Decimal(quantity) * Decimal(str(unit)) == Decimal(str(amount))
    assert n == 35  # the cancellation lines have no quantity
    assert not [s for s in STEMS if arithmetic_problems(key_of(s))]


def test_the_sums_per_page_and_the_totals_are_those_of_the_specification() -> None:
    for spec in SPEC:
        key = key_of(spec.stem)
        by_page: dict[int, Decimal] = {}
        for line, page in zip(key["line_items"], key["line_pages"], strict=True):
            by_page[page] = by_page.get(page, Decimal(0)) + Decimal(str(line["amount"]))
        assert by_page == {
            p: Decimal(v) for p, v in PAGE_SUMS.get(spec.stem, {}).items()
        }, spec.stem
        fields = {f["name"]: f["value"] for f in key["fields"]}
        if spec.stem in TOTALS:
            assert Decimal(str(fields["total"])) == Decimal(TOTALS[spec.stem])
            assert sum(by_page.values()) == Decimal(TOTALS[spec.stem])


def test_the_carried_forward_amounts_of_the_first_invoice_add_up() -> None:
    key = key_of(S01)
    lines = list(zip(key["line_items"], key["line_pages"], strict=True))
    page1 = sum(Decimal(str(x["amount"])) for x, p in lines if p == 1)
    page2 = sum(Decimal(str(x["amount"])) for x, p in lines if p == 2)
    assert page1 == Decimal("2435.00")  # "carried forward" at the end of page 1
    assert page1 + page2 == Decimal("6361.00")  # "carried forward" at the end of page 2
    assert page1 + page2 + Decimal("405.00") == Decimal("6766.00")
    # the VAT line of the total: 8.1% of the net amount, inside the total
    assert round(
        Decimal("6766.00") * Decimal("0.081") / Decimal("1.081"), 2
    ) == Decimal("506.98")
    # the lines of the carry forward are not line items
    assert not [x for x, _ in lines if "forward" in x["description"].lower()]


def test_the_quote_total_and_the_total_with_the_option() -> None:
    key = key_of(S04)
    net = sum(Decimal(str(x["amount"])) for x in key["line_items"])
    assert net == Decimal("8488.50")
    assert net + Decimal("1275.00") == Decimal("9763.50")  # the option, not a line
    assert [x["amount"] for x in key["line_items"]].count(1275.0) == 0


def test_the_refund_is_the_four_lines_of_the_cancellation_added_up() -> None:
    key = key_of(S03)
    amounts = [Decimal(str(x["amount"])) for x in key["line_items"]]
    assert amounts == [Decimal(a) for a in ("2860.00", "-858.00", "-64.00", "-45.00")]
    assert sum(amounts) == Decimal("1893.00")
    assert Decimal("2860.00") * Decimal("0.30") == Decimal("858.00")
    assert {f["name"]: f["value"] for f in key["fields"]}["refund_received"] == 1893.0


# ======================================================= 4 the text PDFs, as read


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_each_text_pdf_passes_every_check_against_its_key(stem: str) -> None:
    assert problems(MP1 / f"{stem}.pdf", key_of(stem)) == []


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_every_page_of_a_text_pdf_has_a_text_layer(stem: str) -> None:
    texts = page_texts(MP1 / f"{stem}.pdf")
    assert len(texts) == BY_STEM[stem].pages
    assert all(t.strip() for t in texts)


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_each_text_pdf_prints_the_texts_of_the_specification_on_each_page(
    stem: str,
) -> None:
    spec, texts = BY_STEM[stem], page_texts(MP1 / f"{stem}.pdf")
    missing = [
        (n, s)
        for n, (strings, text) in enumerate(zip(spec.printed, texts, strict=True), 1)
        for s in strings
        if not has(text, s)
    ]
    assert missing == []


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_the_foot_line_is_the_last_text_of_each_page_and_a_header_the_first(
    stem: str,
) -> None:
    spec, texts = BY_STEM[stem], page_texts(MP1 / f"{stem}.pdf")
    for n, text in enumerate(texts, 1):
        assert flat(text).endswith(flat(FOOT.format(n, spec.pages))), (stem, n)
        if n in spec.headers:
            assert flat(text).startswith(flat(spec.headers[n][0])), (stem, n)


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_the_letterhead_is_on_page_1_only(stem: str) -> None:
    texts = page_texts(MP1 / f"{stem}.pdf")
    assert "Tel." in texts[0]
    assert not [n for n, t in enumerate(texts[1:], 2) if "Tel." in t]


def test_page_2_of_the_medical_invoice_is_a_bare_table_page() -> None:
    page2 = page_texts(MP1 / f"{S02}.pdf")[1]
    for text in (
        "Rheinau",
        "Invoice no",
        "RMC-40718",
        "Invoice date",
        "2026-09-17",
        "Patient",
        "Service",
        "Qty",
        "Unit (CHF)",
        "Amount (CHF)",
    ):
        assert text not in page2, text
    assert has(page2, "Knee brace, size M") and has(page2, "CHF 788.00")


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_a_page_with_lines_repeats_the_column_head_unless_the_specification_says_not(
    stem: str,
) -> None:
    texts = page_texts(MP1 / f"{stem}.pdf")
    head = HEAD_MEDICAL if stem == S02 else HEAD_REPAIR
    with_head = HEAD_PAGES[stem]
    for n, text in enumerate(texts, 1):
        assert all(has(text, h) for h in head) == (n in with_head), (stem, n)
        assert any(has(text, h) for h in head[1:]) == (n in with_head), (stem, n)


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_the_value_of_a_field_is_on_the_accepted_pages_and_on_no_other_page(
    stem: str,
) -> None:
    texts = page_texts(MP1 / f"{stem}.pdf")
    for f in BY_STEM[stem].fields:
        if f.value is None:
            continue
        pages = [a.page for a in f.accepted] or [f.page]
        printing = [n for n, t in enumerate(texts, 1) if omni._holds(t, f.value)]
        assert printing == pages, (stem, f.name)


def test_a_value_printed_on_one_page_only_is_not_accepted_on_another_page() -> None:
    one_page = [
        (S01, "total", 3),
        (S01, "invoice_date", 1),
        (S01, "repair_company", 1),
        (S01, "customer_name", 1),
        (S02, "total", 2),
        (S02, "provider", 1),
        (S04, "total", 2),
        (S04, "repair_company", 1),
        (S05, "total", 2),
        (S05, "customer_name", 1),
        (S07, "departure_date", 1),
        (S08, "incident_date", 2),
        (S08, "offence", 2),
    ]
    for stem, name, page in one_page:
        key = next(f for f in key_of(stem)["fields"] if f["name"] == name)
        assert key["page"] == page and "accepted_pages" not in key, (stem, name)
        texts = page_texts(MP1 / f"{stem}.pdf")
        others = [
            n
            for n, t in enumerate(texts, 1)
            if n != page and omni._holds(t, key["value"])
        ]
        assert others == [], (stem, name)


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_the_label_of_a_repeated_value_stands_before_it_on_each_accepted_page(
    stem: str,
) -> None:
    texts = page_texts(MP1 / f"{stem}.pdf")
    for f in BY_STEM[stem].fields:
        for a in f.accepted:
            assert has(texts[a.page - 1], a.context), (stem, f.name, a.page, a.context)
            assert a.evidence in a.context
            assert has(texts[a.page - 1], a.evidence)


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_every_repeated_safeguarded_field_verifies_on_every_accepted_page(
    stem: str,
) -> None:
    spec, texts = BY_STEM[stem], page_texts(MP1 / f"{stem}.pdf")
    checked = 0
    for f in spec.fields:
        for a in f.accepted:
            if f.name not in labels.SAFEGUARDED:
                continue
            checked += 1
            assert labels.verified(
                spec.type, f.name, typed(f.name, f.value), a.evidence, texts[a.page - 1]
            ), (stem, f.name, a.page)
    # one check per accepted page: the number (3 pages) and the name (2) of the quote,
    # the number and the date (2 each) of the invoice KB, ...
    assert checked == {S01: 3, S04: 5, S05: 4, S07: 2, S08: 4}.get(stem, 0)


def test_the_carry_forward_lines_print_the_amounts_of_the_specification() -> None:
    p1, p2, p3 = page_texts(MP1 / f"{S01}.pdf")
    assert has(p1, "Subtotal page 1, carried forward") and has(p1, "2,435.00")
    assert has(p2, "Brought forward from page 1") and has(p2, "2,435.00")
    assert has(p2, "Carried forward to page 3") and has(p2, "6,361.00")
    assert has(p3, "Brought forward from page 2") and has(p3, "6,361.00")
    assert (
        has(p3, "CHF 6,766.00") and has(p3, "of which VAT 8.1%") and has(p3, "506.98")
    )
    assert "6,766.00" not in p1 + p2 and "6,361.00" not in p1


PHONE = re.compile(r"\+41 \d\d 555 \d\d \d\d")


def test_every_phone_number_has_the_kit_pattern_and_no_placeholder_is_printed() -> None:
    seen = 0
    for stem in TEXT_STEMS:
        for text in page_texts(MP1 / f"{stem}.pdf"):
            flat_text = " ".join(text.split())
            assert flat_text.count("+41") == len(PHONE.findall(flat_text)), stem
            seen += flat_text.count("+41")
            for placeholder in ("IBAN CH00", "UID CHE-000", "CHE-000", "CH00 0000"):
                assert placeholder not in flat_text, (stem, placeholder)
    assert seen == 6 + 1  # six letterheads and the complaints line of the quote


# ======================================================================= 6 lures
# (stem, field, value, snippet, page, text on the page that makes it a lure)
LURES: list[tuple[str, str, Any, str, int, str]] = [
    (S01, "total", 2435.0, "2,435.00", 1, "subtotal, carried forward"),
    (S01, "total", 2435.0, "2,435.00", 2, "brought forward"),
    (S01, "total", 6361.0, "6,361.00", 2, "carried forward"),
    (S01, "total", 6361.0, "6,361.00", 3, "brought forward"),
    (S01, "invoice_date", "2026-09-28", "2026-09-28", 1, "work done, from"),
    (S01, "invoice_date", "2026-10-02", "2026-10-02", 1, "work done, to"),
    (S02, "invoice_number", "PN-90352", "PN-90352", 1, "patient number"),
    (S02, "total", 650.0, "650.00", 1, "cost estimate"),
    (S02, "total", 650.0, "CHF 650.00", 1, "cost estimate"),
    (S02, "invoice_date", "2026-08-28", "2026-08-28", 1, "date of the estimate"),
    (S04, "quote_date", "2026-10-06", "2026-10-06", 1, "site visit"),
    (S04, "quote_date", "2026-12-18", "2026-12-18", 2, "valid until"),
    (S04, "quote_date", "2026-02-15", "2026-02-15", 3, "edition of the terms"),
    (S07, "trip_price", 430.0, "430.00", 2, "deposit"),
    (S07, "trip_price", 430.0, "CHF 430.00", 2, "deposit"),
    (S07, "trip_price", 600.0, "600.00", 2, "instalment"),
    (S07, "trip_price", 600.0, "CHF 600.00", 2, "instalment"),
    (S07, "departure_date", "2027-01-23", "2027-01-23", 2, "return"),
    (S07, "booking_reference", "C-48271", "C-48271", 1, "customer number"),
    (S08, "incident_date", "2026-11-11", "2026-11-11", 1, "date of report"),
    (S08, "incident_date", "2026-11-11", "2026-11-11", 2, "signature date"),
]


@pytest.mark.parametrize(
    ("stem", "name", "value", "snippet", "page", "why"),
    LURES,
    ids=[f"{s[:6]}-{n}-{p}-{w}" for s, n, _, sn, p, w in LURES],
)
def test_a_lure_does_not_verify_as_the_field_it_looks_like(
    stem: str, name: str, value: Any, snippet: str, page: int, why: str
) -> None:
    text = page_texts(MP1 / f"{stem}.pdf")[page - 1]
    assert omni._shown(snippet, text), "the lure is not on the page"  # a real lure
    assert not labels.verified(
        BY_STEM[stem].type, name, typed(name, value), snippet, text
    ), why


def test_the_total_with_options_verifies_as_the_total_a_known_limit_of_the_check() -> (
    None
):
    """Section 7 of the specification: the label check cannot tell `Total with
    options CHF 9,763.50` from the total of the quote. It is pinned as true, so that
    a change of the check, or of the wording, is seen."""
    text = page_texts(MP1 / f"{S04}.pdf")[1]
    assert omni._shown("CHF 9,763.50", text) and has(text, "Total with options")
    assert labels.verified("repair_quote", "total", 9763.5, "CHF 9,763.50", text)
    # the right total of the quote is verified too
    assert labels.verified("repair_quote", "total", 8488.5, "CHF 8,488.50", text)


# ======================================================================== 7 scans


@pytest.mark.parametrize("stem", SCAN_STEMS)
def test_a_scan_has_no_text_layer_one_image_per_page_and_no_exif(stem: str) -> None:
    pdf = MP1 / f"{stem}.pdf"
    assert scan_problems(pdf, key_of(stem)) == []
    assert all(not t.strip() for t in page_texts(pdf))
    assert [len(p.images) for p in PdfReader(pdf).pages] == [1] * BY_STEM[stem].pages
    assert key_of(stem)["variant"] == "scan"


def test_the_five_pages_of_the_scans_are_five_images() -> None:
    assert sum(len(PdfReader(MP1 / f"{s}.pdf").pages) for s in SCAN_STEMS) == 5


@pytest.mark.parametrize("stem", SCAN_STEMS)
def test_the_source_of_a_scan_prints_the_texts_of_the_specification_on_each_page(
    stem: str, printed: dict[str, list[list[str]]]
) -> None:
    spec, pages = BY_STEM[stem], printed[f"{stem}.pdf"]
    missing = [
        (n, s)
        for n, (strings, source) in enumerate(zip(spec.printed, pages, strict=True), 1)
        for s in strings
        if not has("\n".join(source), s)
    ]
    assert missing == []


# ============================================================ 9 isolation


def test_the_kit_is_still_35_documents_and_holds_no_file_of_n1_or_mp1() -> None:
    assert demo.NOT_KIT == {"n1", "mp1"}
    found = demo._kit()
    assert len(found) == 35
    assert all(folder not in NEW_SETS for folder, _ in found)
    assert not {name for _, name in found} & {p.name for p in MP1.iterdir()}
    harness = evaluate.kit()
    assert len(harness) == 35
    assert not any(d.id.split("/")[0] in NEW_SETS for d in harness)


def test_n1_is_still_nine_documents_and_mp1_is_eight() -> None:
    assert "mp1" in evaluate.SETS and "n1" in evaluate.SETS
    assert len(evaluate.kit("n1")) == 9
    docs = evaluate.kit("mp1")
    assert [d.id for d in docs] == sorted(f"mp1/{s}.pdf" for s in STEMS)
    assert [d.key_error for d in docs] == [None] * 8
    assert all(d.key is not None for d in docs)
    kinds = {d.id: d.kind for d in docs}
    assert sorted(kinds.values()) == ["clean"] * 6 + ["scan"] * 2
    assert {k for k, v in kinds.items() if v == "scan"} == {
        f"mp1/{s}.pdf" for s in SCAN_STEMS
    }


@pytest.mark.parametrize("stem", STEMS)
def test_the_fixture_reader_stores_exactly_the_present_fields_with_the_key_page(
    stem: str,
) -> None:
    key, pdf = key_of(stem), MP1 / f"{stem}.pdf"
    document = Document(id="D-mp1", file=pdf.name, pages=key["pages"])
    reader = FixtureReader()
    assert reader.classify(document, pdf.read_bytes()) == key["document_type"]
    out = reader.extract(document, pdf.read_bytes(), "unknown")
    present = {f["name"]: f for f in key["fields"] if f["value"] is not None}
    absent = {f["name"] for f in key["fields"] if f["value"] is None}
    assert {f.name: f.value for f in out.fields} == {
        n: f["value"] for n, f in present.items()
    }
    assert not absent & {f.name for f in out.fields}
    for f in out.fields:
        assert f.source.document_id == "D-mp1"
        assert f.source.page == present[f.name]["page"]
        assert f.source.snippet == present[f.name]["snippet"]
    assert out.line_items == key["line_items"]
    assert out.document_type == key["document_type"]


# ========================================================= 10 independence
WORD = re.compile(r"[0-9a-zà-ÿ]+(?:[.,:/'-][0-9a-zà-ÿ]+)*")
YEAR = re.compile(r"20[23]\d")
UID = "UID CHE-000.000.000"  # the placeholders that the kit generator prints
IBAN = "IBAN CH00 0000 0000 0000 0000 0"
SMALL_NUMBER = re.compile(r"\d{1,3}(?:\.\d)?")
# THE GENERIC WORDS THAT THE CHECK ALLOWS. Every one is an English word of a form or of
# a line of a bill (never a person, a company, a street, a town or an identifier). They
# may be in the kit or in N1; every other capitalised word may not. "Reto" is not here.
FORM_WORDS = {
    "A", "Accepted", "Address", "AG", "Amount", "Boiler", "Booking", "Calculation",
    "Call-out", "Cancellation", "Cancelled", "CHF", "Cost", "Customer", "Date",
    "Departure", "Description", "Diagnosis", "Disposal", "Dr", "Examined", "Findings",
    "Floor", "Follow-up", "General", "GmbH", "Health", "History", "Insurance",
    "Invoice", "M", "Medical", "Mixer", "Next", "Offence", "Officer", "Optional",
    "Page", "Patient", "Payable", "Police", "Price", "Qty", "Quote", "Refund",
    "Replace", "Report", "Reported", "Reporting", "Return", "SA", "Service", "Signed",
    "Site", "Statement", "Station", "Switzerland", "Tel", "The", "Theft", "Tile",
    "Top", "Total", "Travel", "Traveller", "Treatment", "Trip", "Ultrasound", "Unit",
    "VAT", "Valid", "Wall", "Work", "X-ray",
}  # fmt: skip
# More lower-case words, for the runs of three words (the stop words of English and
# the words of a phrase of a form: "within 10 working days", "of the quote").
RUN_WORDS = {
    "a", "above", "and", "at", "board", "by", "credited", "days", "due", "fee",
    "follows", "for", "from", "half", "in", "is", "left", "nights", "of", "on", "per",
    "persons", "post", "ten", "to", "week", "which", "with", "within", "working",
}  # fmt: skip
GENERIC = {w.casefold() for w in FORM_WORDS} | RUN_WORDS


@functools.cache
def corpus() -> tuple[frozenset[str], frozenset[tuple[str, str, str]]]:
    """The words and the runs of three words of everything that MP1 must not share:
    the kit and N1 (JSON and PDF text of data/mock, outside MP1), the generators
    of the kit and of N1, docs/MOCK-DATA.md, and the texts that go to the model
    (the worked example and the descriptions of the fields)."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    texts: list[str] = []
    for p in sorted(MOCK.rglob("*")):
        if p.relative_to(MOCK).parts[0] == "mp1":
            continue
        if p.suffix == ".json":
            texts.append(p.read_text("utf-8"))
        elif p.suffix == ".pdf":
            texts += [pg.extract_text() or "" for pg in PdfReader(p).pages]
    mockdocs = sorted((ROOT / "scripts" / "mockdocs").glob("*.py"))
    # the generators that existed before MP1: a later file (the pilot documents)
    # checks its own independence and must not be able to break a frozen set
    later = ("mp1", "paged", "pilot")
    texts += [p.read_text("utf-8") for p in mockdocs if not p.name.startswith(later)]
    texts.append((ROOT / "docs" / "MOCK-DATA.md").read_text("utf-8"))
    texts.append(omni.EXAMPLE)  # the example of the prompt, with its answer
    texts += list(omni.DESCRIPTIONS.values())
    words: set[str] = set()
    runs: set[tuple[str, str, str]] = set()
    for t in texts:
        tokens = WORD.findall(t.casefold())
        words.update(tokens)
        runs.update(zip(tokens, tokens[1:], tokens[2:], strict=False))
    return frozenset(words), frozenset(runs)


def name_problems(strings: list[str]) -> list[str]:
    """Capitalised words (a person, a company, a street, a town) that are in the kit,
    in N1 or in the prompt, apart from the generic words of a form."""
    words, _ = corpus()
    found = {
        n
        for s in strings
        for n in re.findall(r"\b[A-ZÄÖÜ][\w-]*", s)
        if n not in FORM_WORDS and n.casefold() in words
    }
    return [f"name {n!r} is in the kit, in N1 or in the prompt" for n in sorted(found)]


def number_problems(strings: list[str]) -> list[str]:
    """Numbers (an identifier, a date, an amount, a time) of 4 characters or more that
    are in the kit, in N1 or in the prompt. A year alone is allowed, as in N1."""
    words, _ = corpus()
    found = {
        w
        for s in strings
        for w in WORD.findall(s.casefold())
        if len(w) >= 4 and any(c.isdigit() for c in w) and not YEAR.fullmatch(w)
        if w in words
    }
    return [
        f"number {w!r} is in the kit, in N1 or in the prompt" for w in sorted(found)
    ]


def run_problems(strings: list[str]) -> list[str]:
    """Runs of three words that are in the kit, in N1 or in the prompt, unless every
    word of the run is generic (`GENERIC`) or a small number."""
    _, runs = corpus()
    found = set()
    for s in strings:
        tokens = WORD.findall(s.casefold())
        for run in zip(tokens, tokens[1:], tokens[2:], strict=False):
            generic = all(w in GENERIC or SMALL_NUMBER.fullmatch(w) for w in run)
            if run in runs and not generic:
                found.add(" ".join(run))
    return [
        f"the run {r!r} is in the kit, in N1 or in the prompt" for r in sorted(found)
    ]


def independence_problems(strings: list[str]) -> list[str]:
    return name_problems(strings) + number_problems(strings) + run_problems(strings)


def test_the_check_of_independence_can_find_a_name_a_number_and_a_run() -> None:
    words, _ = corpus()
    assert {"lindenhof", "brunner", "jonas", "tobler"} <= words  # kit and N1 names
    assert name_problems(["Jonas Meier"])
    assert name_problems(["Reto Camenzind"])  # a first name of the kit is found too
    assert number_problems(["Invoice QM-30741 | Date 09.03.2026"])
    assert number_problems(["2026-11-04"])  # a date of the kit
    assert run_problems(["Panoramic image 2 x 23.45 46.90"])
    assert run_problems(["Dental check-up 1 x 64.20"])
    assert independence_problems(["Wydler Badbau AG, Gewerbering 14"]) == []
    assert independence_problems(["of the order, within 10 days"]) == []  # generic


def spec_strings() -> list[str]:
    """Every text that the specification prints, and the cells of each line."""
    strings = [s for spec in SPEC for page in spec.printed for s in page]
    for spec in SPEC:
        for description, quantity, unit, amount, _ in spec.lines:
            strings += [description, f"{amount:,.2f}"]
            if unit is not None:
                strings += [str(quantity), f"{unit:,.2f}"]
    return strings


def test_the_names_of_the_specification_are_not_in_the_kit_n1_or_the_prompt() -> None:
    assert name_problems(spec_strings()) == []


def test_the_numbers_of_the_specification_are_not_in_the_kit_n1_or_the_prompt() -> None:
    assert number_problems(spec_strings()) == []


def test_the_runs_of_the_specification_are_not_in_the_kit_n1_or_the_prompt() -> None:
    assert run_problems(spec_strings()) == []


def all_strings(printed: dict[str, list[list[str]]]) -> list[str]:
    return [s for pages in printed.values() for page in pages for s in page]


def test_no_name_of_mp1_is_in_the_kit_n1_or_the_prompt(
    printed: dict[str, list[list[str]]],
) -> None:
    assert sorted(printed) == sorted(f"{s}.pdf" for s in STEMS)
    assert name_problems(all_strings(printed)) == []


def test_no_number_of_mp1_is_in_the_kit_n1_or_the_prompt(
    printed: dict[str, list[list[str]]],
) -> None:
    assert number_problems(all_strings(printed)) == []


def test_no_run_of_three_words_of_mp1_is_in_the_kit_n1_or_the_prompt(
    printed: dict[str, list[list[str]]],
) -> None:
    assert run_problems(all_strings(printed)) == []


def test_no_placeholder_of_the_kit_generator_is_printed(
    printed: dict[str, list[list[str]]],
) -> None:
    for string in all_strings(printed):
        assert UID not in string and IBAN not in string, string
        assert "CHE-000" not in string and "CH00" not in string, string
        assert "IBAN" not in string and "UID" not in string, string


# ====================================================== 11 a perfect reader


def page_answers(key: dict[str, Any]) -> list[dict[str, Any]]:
    """The answer of a perfect model, page by page, built from the key."""
    names = SCHEMAS[DocumentType(key["document_type"])].model_fields
    answers = []
    for n in range(1, key["pages"] + 1):
        values: dict[str, Any] = {
            name: [] if name == "line_items" else None for name in names
        }
        snippets: dict[str, str] = {}
        for f in key["fields"]:
            if f["value"] is not None and f["page"] == n:
                values[f["name"]] = f["value"]
                snippets[f["name"]] = f["snippet"]
        if "line_items" in names:
            values["line_items"] = [
                dict(line)
                for line, p in zip(key["line_items"], key["line_pages"], strict=True)
                if p == n
            ]
        answers.append({"values": values, "snippets": snippets})
    return answers


def reader_problems(pdf: Path, key: dict[str, Any], scan: bool) -> list[str]:
    """What the existing `OmniReader` (page mode, stub of the model) stores for a
    perfect model, held against the key."""
    stub = Stub({"document_type": key["document_type"]}, *page_answers(key))
    document = Document(id="D-omni", file=pdf.name, pages=key["pages"])
    out = stub.reader().extract(document, pdf.read_bytes(), "unknown")
    found: list[str] = []
    if len(stub.requests) != 1 + key["pages"]:
        found.append(f"{len(stub.requests)} requests for {key['pages']} pages")
    present = {f["name"]: f for f in key["fields"] if f["value"] is not None}
    stored = {f.name: f for f in out.fields}
    if set(stored) != set(present):
        found.append(f"stored {sorted(stored)}, present {sorted(present)}")
    for name, f in stored.items():
        want = present.get(name)
        if want is None:
            continue
        if f.value != want["value"]:
            found.append(f"{name}: value {f.value!r}, key {want['value']!r}")
        if f.source.page != want["page"]:
            found.append(f"{name}: page {f.source.page}, key {want['page']}")
        if scan:
            if f.confidence != omni.UNVERIFIED:
                found.append(f"{name}: confidence {f.confidence}, a scan gives 0.5")
            if f.source.box is None or f.source.snippet is not None:
                found.append(f"{name}: a scan needs a box and no snippet")
        else:
            if f.confidence != omni.VERIFIED:
                found.append(f"{name}: confidence {f.confidence}, not VERIFIED")
            if f.source.snippet != want["snippet"] or f.source.box is not None:
                found.append(f"{name}: the snippet of the source is not the key one")
    wanted_lines = [] if scan else key["line_items"]
    if out.line_items != wanted_lines:
        found.append(f"line items: {out.line_items!r} instead of {wanted_lines!r}")
    return found


@pytest.mark.parametrize("stem", STEMS)
def test_a_perfect_model_gives_the_key_through_the_existing_reader_in_page_mode(
    stem: str,
) -> None:
    scan = BY_STEM[stem].kind == "scan"
    assert reader_problems(MP1 / f"{stem}.pdf", key_of(stem), scan) == []


def test_the_reader_refuses_the_lines_of_a_scan_and_stores_no_absent_field() -> None:
    key = key_of(S03)
    assert key["line_items"]  # the model gave four lines on page 2
    stub = Stub({"document_type": key["document_type"]}, *page_answers(key))
    document = Document(id="D-omni", file="x", pages=3)
    out = stub.reader().extract(document, (MP1 / f"{S03}.pdf").read_bytes(), "unknown")
    assert out.line_items == []
    assert {f.name for f in out.fields} == {
        "booking_reference",
        "cancellation_date",
        "refund_received",
    }


# =============================================================== 12 negatives
# Each negative test holds a copy of a key of the table (never a committed file)
# against a committed PDF, and names the problem that the check must find.


def bad(stem: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    key = copy.deepcopy(expected_key(BY_STEM[stem]))
    change(key)
    return key


def field(key: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [f for f in key["fields"] if f["name"] == name]
    assert isinstance(found, dict)
    return found


def move_snippet(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page"] = 2  # the number is on page 1


def accept_wrong_page(key: dict[str, Any]) -> None:
    f = field(key, "invoice_number")
    f["accepted_pages"] = [1, 2]
    f["page_snippets"] = {"1": "RMC-40718", "2": "RMC-40718"}  # page 2 has no number


def wrong_evidence(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page_snippets"]["2"] = "WB-26-9999"


def swap_lines(key: dict[str, Any]) -> None:
    lines = key["line_items"]
    lines[0], lines[1] = lines[1], lines[0]


def wrong_line_page(key: dict[str, Any]) -> None:
    key["line_pages"][0] = 2


def break_the_sum(key: dict[str, Any]) -> None:
    key["line_items"][2]["unit_price"] = key["line_items"][2]["amount"] = 69.8


def break_a_product(key: dict[str, Any]) -> None:
    key["line_items"][3]["amount"] = 208.0


def wrong_pages(key: dict[str, Any]) -> None:
    key["pages"] = 3


def a_lure_as_key(key: dict[str, Any]) -> None:
    f = field(key, "invoice_number")
    f["value"] = f["snippet"] = "PN-90352"


def wrong_first_page(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page"] = 2


def a_value_missing_on_its_page(key: dict[str, Any]) -> None:
    field(key, "total")["page"] = 1  # CHF 788.00 is on page 2


def absent_with_snippet(key: dict[str, Any]) -> None:
    field(key, "quote_date")["snippet"] = "2026-10-06"


NEGATIVES: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    (S02, move_snippet, "is not shown on page 2"),
    (S02, accept_wrong_page, "is not shown on page 2"),
    (S01, wrong_evidence, "is not shown on page 2"),
    (S01, swap_lines, "not in the order of the lines of page 1"),
    (S01, wrong_line_page, "line 1: not on the text of page 2"),
    (S02, break_the_sum, "sum: the lines add up"),
    (S02, break_a_product, "quantity times unit price"),
    (S02, wrong_pages, "page count"),
    (S02, a_lure_as_key, "the label check does not verify"),
    (S01, wrong_first_page, "the first page that prints the value is 1"),
    (S02, a_value_missing_on_its_page, "is not shown on page 1"),
    (S04, absent_with_snippet, "an absent field has"),
]


@pytest.mark.parametrize(
    ("stem", "change", "problem"),
    NEGATIVES,
    ids=[f"{s[:6]}-{c.__name__}" for s, c, _ in NEGATIVES],
)
def test_the_check_of_a_text_pdf_names_each_mistake_of_a_key(
    stem: str, change: Callable[[dict[str, Any]], None], problem: str
) -> None:
    assert problems(MP1 / f"{stem}.pdf", expected_key(BY_STEM[stem])) == []
    found = problems(MP1 / f"{stem}.pdf", bad(stem, change))
    assert any(problem in p for p in found), found


def test_a_text_pdf_in_place_of_a_scan_is_found_by_the_check_of_the_scans() -> None:
    key = expected_key(BY_STEM[S03])
    found = scan_problems(MP1 / f"{S02}.pdf", key)  # the invoice has 2 pages, 3 wanted
    assert any("has a text layer" in p for p in found), found
    assert any("page count" in p for p in found), found
    same_pages = scan_problems(MP1 / f"{S01}.pdf", key)  # 3 pages, as the scan
    assert any("page 1: has a text layer" in p for p in same_pages), same_pages
    assert any("images, one expected" in p for p in same_pages), same_pages


def test_a_scan_with_the_wrong_page_count_is_found() -> None:
    key = bad(S03, lambda k: k.update(pages=2))
    found = scan_problems(MP1 / f"{S03}.pdf", key)
    assert any("page count" in p for p in found), found


def test_a_perfect_reader_check_names_a_wrong_key_or_a_wrong_kind() -> None:
    key = expected_key(BY_STEM[S02])
    assert reader_problems(MP1 / f"{S02}.pdf", key, scan=False) == []
    found = reader_problems(MP1 / f"{S02}.pdf", key, scan=True)  # a text PDF as a scan
    assert any("a scan gives 0.5" in p for p in found), found
    wrong = bad(S02, lambda k: field(k, "total").update(page=1))
    assert reader_problems(MP1 / f"{S02}.pdf", wrong, scan=False) != []


def test_the_check_of_the_source_names_a_snippet_or_a_cell_that_is_not_printed() -> (
    None
):
    pages = [["Invoice no.:", "RMC-40718", "Page 1 of 2"], ["Total", "CHF 788.00"]]
    key: dict[str, Any] = {
        "pages": 2,
        "fields": [
            {
                "name": "total",
                "value": 788.0,
                "page": 2,
                "snippet": "CHF 788.00",
            }
        ],
        "line_items": [],
        "line_pages": [],
    }
    assert source_problems(key, pages) == []
    key["fields"][0]["page"] = 1
    key["fields"][0]["snippet"] = "CHF 789.00"
    assert any(
        "is not in one string of page 1" in p for p in source_problems(key, pages)
    )
    key["line_items"] = [{"description": "X", "amount": 1.0}]
    key["line_pages"] = [1]
    assert any("not a cell of page 1" in p for p in source_problems(key, pages))
    assert any("page count" in p for p in source_problems(key, pages[:1]))


# ----------------------------------------------- the new rules of `_check_key`


def check(key: Any) -> None:
    evaluate._check_key(key)


def base_key() -> dict[str, Any]:
    return copy.deepcopy(expected_key(BY_STEM[S01]))


def test_a_key_with_accepted_pages_and_page_snippets_is_valid() -> None:
    for spec in SPEC:
        check(expected_key(spec))  # the whole table, as the harness reads it


def test_a_key_with_none_of_the_new_attributes_is_valid_as_today() -> None:
    key = base_key()
    for f in key["fields"]:
        f.pop("accepted_pages", None)
        f.pop("page_snippets", None)
    for extra in ("line_pages", "pages", "variant"):
        key.pop(extra, None)
    check(key)
    for kit_key in sorted(MOCK.glob("claim-*/*.expected.json"))[:3]:
        check(json.loads(kit_key.read_text("utf-8")))


def set_accepted(value: Any) -> Callable[[dict[str, Any]], None]:
    """Only `accepted_pages` is bad: page_snippets follows it, so that the rule of
    page_snippets is not the one that fires."""

    def change(key: dict[str, Any]) -> None:
        f = field(key, "invoice_number")
        f["accepted_pages"] = value
        if isinstance(value, list):
            f["page_snippets"] = {
                str(int(p))
                if isinstance(p, bool | int | float)
                else str(p): "WB-26-0714"
                for p in value
            }

    return change


def set_snippets(value: Any) -> Callable[[dict[str, Any]], None]:
    def change(key: dict[str, Any]) -> None:
        field(key, "invoice_number")["page_snippets"] = value

    return change


def drop(attribute: str) -> Callable[[dict[str, Any]], None]:
    def change(key: dict[str, Any]) -> None:
        del field(key, "invoice_number")[attribute]

    return change


def snippet_for_page_2(text: Any) -> Callable[[dict[str, Any]], None]:
    def change(key: dict[str, Any]) -> None:
        field(key, "invoice_number")["page_snippets"]["2"] = text

    return change


def null_with_accepted(key: dict[str, Any]) -> None:
    f = field(key, "total")
    f["value"] = None
    f.pop("snippet")
    f["accepted_pages"] = [1, 3]
    f["page_snippets"] = {"1": "x", "3": "x"}


def key_snippet_differs(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page_snippets"]["1"] = "WB-26-0715"


def bool_page_with_its_own_text_key(key: dict[str, Any]) -> None:
    """`True` as a page, with the text key that `str(True)` gives: only the rule
    "a bool is not a page" refuses it."""
    f = field(key, "invoice_number")
    f["accepted_pages"] = [True, 2]
    f["page_snippets"] = {"True": "WB-26-0714", "2": "WB-26-0714"}


def null_with_accepted_and_a_snippet(key: dict[str, Any]) -> None:
    """An absent field with a snippet and evidence that agree with each other: only
    the rule "an absent field has no accepted page" refuses it."""
    f = field(key, "total")
    f["value"] = None
    f["snippet"] = "x"
    f["accepted_pages"] = [1, f["page"]]
    f["page_snippets"] = {"1": "x", str(f["page"]): "x"}


BAD_KEYS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("a bool page with its own text key", bool_page_with_its_own_text_key),
    ("an absent field with evidence that agrees", null_with_accepted_and_a_snippet),
    ("accepted_pages is text", set_accepted("1,2,3")),
    ("accepted_pages is a number", set_accepted(2)),
    ("accepted_pages has one page", set_accepted([1])),
    ("accepted_pages is empty", set_accepted([])),
    ("accepted_pages has a repeat", set_accepted([1, 1, 2])),
    ("accepted_pages has a zero", set_accepted([0, 1])),
    ("accepted_pages has a negative", set_accepted([-1, 1])),
    ("accepted_pages has a bool", set_accepted([True, 2])),
    ("accepted_pages has two bools", set_accepted([False, True])),
    ("accepted_pages has a float", set_accepted([1.0, 2.0])),
    ("accepted_pages has text pages", set_accepted(["1", "2"])),
    ("accepted_pages has a null", set_accepted([1, None])),
    ("accepted_pages is not sorted", set_accepted([3, 1, 2])),
    ("accepted_pages lacks the page", set_accepted([2, 3])),
    ("page_snippets without accepted_pages", drop("accepted_pages")),
    ("accepted_pages without page_snippets", drop("page_snippets")),
    ("page_snippets is a list", set_snippets(["WB-26-0714", "WB-26-0714"])),
    ("page_snippets is text", set_snippets("WB-26-0714")),
    (
        "page_snippets lacks a page",
        set_snippets({"1": "WB-26-0714", "2": "WB-26-0714"}),
    ),
    (
        "page_snippets has another page",
        set_snippets({str(p): "WB-26-0714" for p in (1, 2, 3, 4)}),
    ),
    (
        "page_snippets has number keys",
        set_snippets({p: "WB-26-0714" for p in (1, 2, 3)}),
    ),
    ("page_snippets has an empty text", snippet_for_page_2("")),
    ("page_snippets has a blank text", snippet_for_page_2("   ")),
    ("page_snippets has a number", snippet_for_page_2(714)),
    ("page_snippets has a null", snippet_for_page_2(None)),
    ("page_snippets of the key page is not the snippet", key_snippet_differs),
    ("a null value with accepted_pages", null_with_accepted),
]


@pytest.mark.parametrize(
    ("why", "change"), BAD_KEYS, ids=[w.replace(" ", "-") for w, _ in BAD_KEYS]
)
def test_a_bad_form_of_the_new_attributes_is_a_bad_answer_key(
    why: str, change: Callable[[dict[str, Any]], None]
) -> None:
    check(base_key())  # the key before the change is valid
    key = base_key()
    change(key)
    with pytest.raises(BadAnswerKey):
        check(key)


def test_a_key_of_an_absent_field_with_accepted_pages_is_a_key_error_of_the_kit_report(
    tmp_path: Path,
) -> None:
    key = bad(S04, lambda k: field(k, "quote_date").update(accepted_pages=[1, 2]))
    (tmp_path / "x.expected.json").write_text(json.dumps(key), "utf-8")
    (tmp_path / "x.pdf").write_bytes(b"%PDF-1.4")
    found, error = evaluate._answer_key(tmp_path / "x.pdf")
    assert found is None and error is not None and "BadAnswerKey" in error
    good = expected_key(BY_STEM[S04])
    (tmp_path / "x.expected.json").write_text(json.dumps(good), "utf-8")
    assert evaluate._answer_key(tmp_path / "x.pdf") == (good, None)


def test_the_harness_reports_the_key_error_of_a_document_of_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "mock" / "mp1"
    folder.mkdir(parents=True)
    pdf = MP1 / f"{S01}.pdf"
    (folder / pdf.name).write_bytes(pdf.read_bytes())
    key = expected_key(BY_STEM[S01])
    field(key, "invoice_number")["accepted_pages"] = [1]  # one page: a bad key
    (folder / f"{S01}.expected.json").write_text(json.dumps(key), "utf-8")
    (folder / "manifest.json").write_text(
        json.dumps({"documents": [{"file": pdf.name, "kind": "text"}]}), "utf-8"
    )
    monkeypatch.setenv("CLAIMS_DATA", str(tmp_path))
    [doc] = evaluate.kit("mp1")
    assert doc.key is None and doc.key_error and "BadAnswerKey" in doc.key_error
