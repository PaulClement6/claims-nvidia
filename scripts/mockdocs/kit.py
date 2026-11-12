"""The scenarios of the kit, the hard versions, and `generate`, which writes it all.

A scenario is one folder of `data/mock/` with the documents of one claim, the account
and the policy it is played on, and one or more stories a visitor types. The expected
signals and outcome of each story are checked by `backend/tests/test_document_kit.py`
against the real workflows: when a rule changes, the test says which story moved.

To add a scenario: write the document functions (`documents.py`), add a `Scenario`
to SCENARIOS, run the generator, commit the new files, read `docs/MOCK-DATA.md`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import documents as k
from . import hard, legacy, photos

Maker = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class Story:
    """What a visitor types in one message, and what the pipeline must answer."""

    text: str
    incident_date: str
    amount: float
    cause: str
    signals: list[str]
    outcome: str


@dataclass(frozen=True)
class Scenario:
    folder: str
    customer_id: str
    policy: str
    title: str
    documents: list[tuple[str, Maker]]  # in the order a visitor uploads them
    stories: list[Story]


SCENARIOS = [
    Scenario(
        "claim-1-travel",
        "C-1001",
        "TRV-2026-0417",
        "Trip cancelled because of illness",
        [
            ("booking-confirmation.pdf", legacy.booking_confirmation),
            ("cancellation-confirmation.pdf", legacy.cancellation_confirmation),
            ("medical-certificate.pdf", legacy.medical_certificate),
        ],
        [
            Story(
                "My trip was cancelled because I was ill. It began on 3 August 2026 "
                "and I claim CHF 1,500.",
                "2026-08-03",
                1500,
                "illness",
                [],
                "straight_through",
            )
        ],
    ),
    Scenario(
        "claim-2-property",
        "C-1002",
        "HH-2024-0933",
        "Water damage in the kitchen",
        [
            ("repair-invoice.pdf", legacy.repair_invoice),
            ("damage-photo-1.jpg", legacy.damage_photo_1),
            ("damage-photo-2.jpg", legacy.damage_photo_2),
        ],
        [
            Story(
                "Water damage in the kitchen on 14 September 2026. I claim CHF 3,200.",
                "2026-09-14",
                3200,
                "water",
                [],
                "handler_review",
            )
        ],
    ),
    Scenario(
        "claim-3-health",
        "C-1003",
        "ACC-2026-2210",
        "Sprained ankle after a fall at a station",
        [
            ("medical-invoice.pdf", legacy.medical_invoice),
            ("medical-report.pdf", legacy.medical_report),
        ],
        [
            Story(
                "I sprained my ankle in an accident on 25 October 2026. "
                "I claim CHF 4,450.",
                "2026-10-25",
                4450,
                "accident",
                ["C001", "R003", "R004", "R005", "T001", "T002", "T003"],
                "investigation",
            )
        ],
    ),
    Scenario(
        "kit-anna-luggage",
        "C-1001",
        "TRV-2026-0417",
        "Luggage lost on a trip to Porto",
        [
            ("booking-confirmation.pdf", k.booking_confirmation_porto),
            ("receipt-trolley.pdf", k.receipt_trolley),
            ("receipt-jacket-shoes.pdf", k.receipt_jacket_shoes),
            ("luggage-report.pdf", k.luggage_report),
        ],
        [
            Story(
                "My luggage was lost on arrival in Porto on 14 May 2026. "
                "I claim CHF 787.",
                "2026-05-14",
                787,
                "luggage",
                [],
                "straight_through",
            )
        ],
    ),
    Scenario(
        "kit-anna-theft",
        "C-1001",
        "HH-2025-1182",
        "Burglary: forced front door",
        [
            ("police-report.pdf", k.police_report),
            ("repair-quote.pdf", k.repair_quote_door),
            ("receipt-lock-set.pdf", k.receipt_lock_set),
            ("receipt-door-frame.pdf", k.receipt_door_frame),
            ("damage-photo.jpg", photos.door_photo),
        ],
        [
            Story(
                "Burglary at my flat on 9 October 2026: the front door was forced. "
                "I claim CHF 2,640.",
                "2026-10-09",
                2640,
                "theft",
                [],
                "handler_review",
            )
        ],
    ),
    Scenario(
        "kit-marco-travel",
        "C-1002",
        "TRV-2026-0721",
        "Trip to Sicily cancelled",
        [
            ("booking-confirmation.pdf", k.booking_confirmation_sicily),
            ("cancellation-confirmation.pdf", k.cancellation_confirmation_sicily),
        ],
        [
            Story(
                "I had to cancel my trip to Sicily on 12 July 2026 because of a "
                "family emergency. I claim CHF 2,300.",
                "2026-07-12",
                2300,
                "cancellation",
                [],
                "straight_through",
            ),
            Story(  # the full trip price: not price minus refund
                "I had to cancel my trip to Sicily on 12 July 2026 because of a "
                "family emergency. I claim CHF 3,200.",
                "2026-07-12",
                3200,
                "cancellation",
                ["C003"],
                "handler_review",
            ),
        ],
    ),
    Scenario(
        "kit-marco-inflated",
        "C-1002",
        "HH-2024-0933",
        "Bathroom flooded by a leaking pipe",
        [
            ("repair-invoice.pdf", k.repair_invoice_inflated),
            ("damage-photo.jpg", photos.floor_photo),
        ],
        [
            Story(
                "A leaking pipe flooded my bathroom on 3 November 2026. "
                "I claim CHF 4,740.",
                "2026-11-03",
                4740,
                "water",
                ["T001", "T002", "T003"],
                "investigation",
            )
        ],
    ),
    Scenario(
        "kit-lukas-clean",
        "C-1003",
        "ACC-2026-2210",
        "Bike fall, hurt wrist",
        [
            ("medical-invoice.pdf", k.medical_invoice_clean),
            ("medical-report.pdf", k.medical_report_clean),
        ],
        [
            Story(
                "I fell off my bike on 6 November 2026 and hurt my wrist. "
                "I claim CHF 840.",
                "2026-11-06",
                840,
                "accident",
                [],
                "straight_through",
            )
        ],
    ),
    Scenario(
        "kit-lukas-household",
        "C-1003",
        "HH-2026-0102",
        "Broken living-room window",
        [("repair-quote.pdf", k.repair_quote_window)],
        [
            Story(
                "A football broke the living-room window on 14 September 2026. "
                "I claim CHF 1,260.",
                "2026-09-14",
                1260,
                "breakage",
                ["R001"],
                "handler_review",
            )
        ],
    ),
]

# (source document, variant, angle of the scan in degrees): six scans, three photos.
HARD = [
    ("claim-2-property/repair-invoice.pdf", "scan", 1.2),
    ("kit-lukas-clean/medical-invoice.pdf", "scan", -1.7),
    ("claim-1-travel/booking-confirmation.pdf", "scan", 1.5),
    ("claim-1-travel/medical-certificate.pdf", "scan", -1.1),
    ("kit-anna-theft/police-report.pdf", "scan", 1.9),
    ("kit-anna-luggage/receipt-jacket-shoes.pdf", "scan", -1.4),
    ("claim-2-property/repair-invoice.pdf", "photo", 0),
    ("kit-anna-luggage/receipt-jacket-shoes.pdf", "photo", 0),
    ("kit-anna-luggage/luggage-report.pdf", "photo", 0),
]
HARD_FOLDER = "hard"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def make_hard(out: Path, expected: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Writes the hard versions; returns their lines of the index."""
    (out / HARD_FOLDER).mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (source, variant, angle) in enumerate(HARD):
        folder, name = source.split("/")
        stem = f"{folder}-{Path(name).stem}-{variant}"
        target = out / HARD_FOLDER / (stem + (".pdf" if variant == "scan" else ".jpg"))
        if variant == "scan":
            hard.scan(out / source, target, seed=10 + i, angle=angle)
        else:
            hard.photo(out / source, target, seed=10 + i)
        write_json(
            target.with_suffix(".expected.json"),
            {**expected[source], "variant": variant, "source_document": source},
        )
        lines.append(
            {
                "file": f"{HARD_FOLDER}/{target.name}",
                "variant": variant,
                "source_document": source,
                "document_type": expected[source]["document_type"],
            }
        )
    return lines


def generate(out: Path) -> None:
    expected: dict[str, dict[str, Any]] = {}
    scenarios = []
    for s in SCENARIOS:
        (out / s.folder).mkdir(parents=True, exist_ok=True)
        docs = []
        for name, make in s.documents:
            made = make(out / s.folder / name)
            write_json((out / s.folder / name).with_suffix(".expected.json"), made)
            expected[f"{s.folder}/{name}"] = made
            docs.append({"file": name, "document_type": made["document_type"]})
        scenarios.append(
            {
                "folder": s.folder,
                "customer_id": s.customer_id,
                "policy": s.policy,
                "title": s.title,
                "documents": docs,
                "stories": [
                    {
                        "text": t.text,
                        "incident_date": t.incident_date,
                        "amount": t.amount,
                        "cause": t.cause,
                        "signals": t.signals,
                        "outcome": t.outcome,
                    }
                    for t in s.stories
                ],
            }
        )
    write_json(
        out / "index.json", {"scenarios": scenarios, "hard": make_hard(out, expected)}
    )
