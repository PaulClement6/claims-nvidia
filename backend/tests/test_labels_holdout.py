"""Held-out check of the reader safeguards (issue #125, phase 2).

Written from the specification only (the public names of `app.docintel.labels`,
the field definitions and the reader contract), on new layouts: a hotel folio, two
dental invoices (German), a garage invoice (Italian), a cancellation letter
(French), two police reports (German), an airline irregularity report, three
receipts, two quotes, a two-page invoice, three medical papers, two booking
documents. Each case builds a real PDF with a text layer, runs `OmniReader.extract`
with a stubbed model answer and checks the confidence (0.9 or 0.5), the value and
the source. A second group calls `labels.verified` and `labels.is_placeholder`
directly. No network: every model call goes to an httpx MockTransport.

Cases that the specification does not decide are NOT in this file (the report of the
worker lists them): "Folio No." as a booking reference, "Zahnarztpraxis" as an
organisation word, "Bar / Cash" as the amount paid, a reference inside a sentence of
prose with its date, "Total (excl. VAT)", "Amount due", a card line as the amount
paid, a lowercase identifier without digits, a bare name after "Medico:", a police
authority as a person, a prose date, an issue date as a VETO.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app.docintel import labels
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType

URL = "https://vlm.holdout.test/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "holdout-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-holdout")


class Stub:
    """Answers the requests in order: classify first, then one answer per page. A
    request beyond the planned answers, or to another URL, fails the test."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies: Iterator[dict[str, Any]] = iter(replies)
        self.count = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/chat/completions"
        self.count += 1
        reply = next(self.replies, None)
        assert reply is not None, "an unexpected model request"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}
        )

    def reader(self) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(client=client, sleep=lambda _s: None)


def build_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with a text layer: one string per line, top down; "" is a blank line."""
    out = BytesIO()
    pdf = canvas.Canvas(out)
    for lines in pages:
        pdf.setFont("Helvetica", 10)
        y = 800
        for line in lines:
            if line:
                pdf.drawString(50, y, line)
            y -= 14
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def page_answer(kind: str, fields: dict[str, tuple[Any, str]]) -> dict[str, Any]:
    """A model answer for a page: the given (value, snippet) per field, null for
    every other field, no line items."""
    schema = SCHEMAS[DocumentType(kind)]
    values: dict[str, Any] = {name: None for name in schema.model_fields}
    if "line_items" in values:
        values["line_items"] = []
    values.update({name: value for name, (value, _) in fields.items()})
    return {
        "values": values,
        "snippets": {name: snippet for name, (_, snippet) in fields.items()},
    }


def read(
    tmp_path: Path, kind: str, pages: list[list[str]], answers: list[dict[str, Any]]
) -> Any:
    path = tmp_path / "doc.pdf"
    path.write_bytes(build_pdf(pages))
    stub = Stub({"document_type": kind}, *answers)
    out = stub.reader().extract(
        Document(id="D-hold", file=path.name, pages=len(pages)),
        path.read_bytes(),
        "travel",
    )
    assert stub.count == 1 + len(pages)
    assert out.document_type == kind
    return out


# --------------------------------------------------------------------------------
# The layouts: (document type, pages of lines)
# --------------------------------------------------------------------------------

LAYOUTS: dict[str, tuple[str, list[list[str]]]] = {
    "folio": (
        "booking_confirmation",
        [
            [
                "Alpenrose Hotel & Spa AG",
                "Dorfstrasse 8, 3818 Grindelwald",
                "",
                "Guest Folio",
                "Guest: Mr. Ruedi Haldimann",
                "Folio No.: 88317",
                "Booking reference: HLF-77Q4T",
                "Arrival: 15.06.2027",
                "Departure: 18.06.2027",
                "Room 214, 3 nights",
                "Room charge 3 x 310.00   930.00",
                "Breakfast 3 x 24.00   72.00",
                "Deposit paid CHF 300.00",
                "Total price CHF 1'002.00",
            ]
        ],
    ),
    "dental": (
        "medical_invoice",
        [
            [
                "Praxis Dr. med. dent. C. Baumgartner",
                "Praxis am Rebberg, Rebbergstrasse 14, 8280 Kreuzlingen",
                "",
                "Rechnung",
                "Rechnungs-Nr.: ZB-2027-1187",
                "Rechnungsdatum: 09.04.2027",
                "Behandlungsdatum: 02.04.2027",
                "Patient: Lukas Meierhans",
                "Geburtsdatum: 14.09.1991",
                "Versichertennummer: 756.1234.5678.97",
                "Telefon: 071 555 01 42",
                "Kontrolle und Zahnreinigung   1   168.00",
                "Röntgen Einzelbild   2   44.50",
                "Zwischensumme   257.00",
                "MwSt   0.00",
                "Total CHF 257.00",
                "Anzahlung CHF 50.00",
            ]
        ],
    ),
    "dental_same_day": (
        "medical_invoice",
        [
            [
                "Rechnung Nr. 2027-0042",
                "Rechnungsdatum: 02.04.2027",
                "Behandlungsdatum: 02.04.2027",
                "Patient: Ines Kaufmann",
                "Total CHF 312.00",
            ]
        ],
    ),
    "garage": (
        "repair_invoice",
        [
            [
                "Autorimessa Ceresio SA",
                "Via Industria 12, 6900 Lugano",
                "P. IVA CHE-101.202.303 IVA",
                "Fattura n. 2027/0318",
                "Data: 21.05.2027",
                "Scadenza: 20.06.2027",
                "Cliente: Giovanni Bernasconi",
                "Targa: TI 224411",
                "Ordine n. 7731",
                "Telefono: 091 555 01 77",
                "Sostituzione pastiglie freni anteriori   1   240.00",
                "Manodopera   2.5 h   225.00",
                "Imponibile   465.00",
                "IVA 8.1%   37.67",
                "Acconto   100.00",
                "Totale da pagare CHF 402.67",
            ]
        ],
    ),
    "letter_fr": (
        "cancellation_confirmation",
        [
            [
                "Voyages Léman SA",
                "Rue du Lac 5, 1800 Vevey",
                "Vevey, le 16.03.2027",
                "Annulation de réservation",
                "Madame, Monsieur,",
                "Nous confirmons l'annulation de votre réservation ABX-2291 "
                "en date du 14.03.2027.",
                "Le montant remboursé s'élève à CHF 1'480.00.",
                "Frais de dossier retenus CHF 120.00.",
                "Référence de réservation : ABX-2291",
                "Date d'annulation : 14.03.2027",
                "Remboursement : CHF 1'480.00",
                "Avec nos meilleures salutations",
            ]
        ],
    ),
    "police_de": (
        "police_report",
        [
            [
                "Kantonspolizei Thurgau",
                "Rapport",
                "Rapport-Nr.: KAPO-27/88412",
                "Anzeigedatum: 03.05.2027",
                "Tatzeit: 02.05.2027, ca. 21.30 Uhr",
                "Tatort: Parkplatz Bahnhof Weinfelden",
                "Anzeigeerstatter: Rahel Zürcher",
                "Firma: Zürcher Transport GmbH",
                "Geburtsdatum: 12.07.1985",
                "Telefon: 071 555 02 30",
                "Delikt: Diebstahl aus Personenwagen",
                "Bearbeitende Beamtin: Wm Sandra Küng",
            ]
        ],
    ),
    "police_blank": (
        "police_report",
        [
            [
                "Rapport-Nr.: n.d.",
                "Rapport-Nr.: NIL-27/4471",
                "Anzeigeerstatter: Unbekannt",
                "Anzeigeerstatter: -",
                "Anzeigeerstatter: —",
                "Anzeigeerstatter: N/A",
                "Anzeigeerstatter: keine Angabe",
                "Anzeigeerstatter: non communiqué",
                "Anzeigeerstatter: (n/a)",
                "Anzeigeerstatter: Not available",
                "Anzeigeerstatter: Nils Null",
                "Delikt: Not answerable",
                "Delikt: Sachbeschädigung, keine Angabe zum Täter",
                "Tatzeit: 02.05.2027",
            ]
        ],
    ),
    "pir": (
        "luggage_report",
        [
            [
                "Nordwind Airlines Ltd. - Baggage Service Zurich",
                "Property Irregularity Report",
                "File reference: ZRH-NW-27-045511",
                "Passenger: Elena Marchetti",
                "Ticket number: 724 2209918345",
                "Flight: NW 412 on 23.07.2027",
                "Date of incident: 23.07.2027",
                "Date reported: 24.07.2027",
                "Date of birth: 02.02.1979",
                "Bag tag: NW 884420",
                "Phone: +41 44 555 01 90",
                "Description: black hard-shell suitcase, delayed",
                "Airline: Nordwind Airlines Ltd.",
            ]
        ],
    ),
    "receipt_long": (
        "receipt",
        [
            [
                "Dorfladen Müller & Söhne",
                "Hauptstrasse 3, 9042 Speicher",
                "Datum: 11.08.2027   14:32",
                "Kasse 2   Bon 004417",
                "",
                "Ruchbrot 500g   3.60",
                "Käse Appenzeller   7.90",
                "Milch 1L 4 Stk   6.60",
                "Äpfel Gala 1.2 kg   5.04",
                "Eier 10er   4.80",
                "Butter 250g   3.95",
                "Joghurt Natur 6 Stk   5.70",
                "Spaghetti 500g   2.40",
                "Tomaten 0.8 kg   4.72",
                "Kaffee gemahlen 250g   8.95",
                "Schokolade 2 Stk   4.60",
                "Mineralwasser 6 x 1.5L   6.60",
                "",
                "Subtotal   CHF 64.86",
                "Rabatt   -4.00",
                "TOTAL CHF 60.86",
                "Bar / Cash   CHF 70.00",
                "Rückgeld / Change   CHF 9.14",
                "MwSt 2.6%   1.54",
                "Garantie bis 11.08.2029",
                "Vielen Dank für Ihren Einkauf",
            ]
        ],
    ),
    "receipt_subtotal": (
        "receipt",
        [
            [
                "Café Zum Löwen",
                "Datum: 02.09.2027",
                "2 Cappuccino   9.00",
                "1 Gipfeli   2.50",
                "Subtotal   CHF 11.50",
                "Total   CHF 11.50",
                "Karte / Card   CHF 11.50",
            ]
        ],
    ),
    "receipt_card": (
        "receipt",
        [
            [
                "Papeterie Rütli",
                "Datum: 17.09.2027",
                "Ordner A4 breit   6.90",
                "Notizblock A5   3.20",
                "TOTAL CHF 10.10",
                "Karte / Card   CHF 10.10",
            ]
        ],
    ),
    "quote_de": (
        "repair_quote",
        [
            [
                "Schreinerei Hug GmbH",
                "Offerte",
                "",
                "Offerte-Nr.",
                "Q-2027-0093",
                "Offertdatum",
                "12.01.2027",
                "Gültig bis",
                "12.02.2027",
                "Kunde",
                "Anita Steffen",
                "Total CHF",
                "3'845.00",
            ]
        ],
    ),
    "quote_en": (
        "repair_quote",
        [
            [
                "Hartmann Auto Body Ltd",
                "Quote",
                "Quote no.: HQ-55102",
                "Quote date: 03.03.2027",
                "Valid until: 02.04.2027",
                "Customer: Pia Hartmann-Roth",
                "Order no.: 88-1204",
                "VAT no.: CHE-555.666.777",
                "Labour 6 h   720.00",
                "Paint materials   410.00",
                "VAT 8.1%   91.53",
                "Total CHF 1'221.53",
            ]
        ],
    ),
    "invoice_two_pages": (
        "repair_invoice",
        [
            [
                "Carrosserie Bachmann AG",
                "Rechnung Nr. B-27-3392",
                "Datum: 30.07.2027",
                "Kunde: Marco Fontana",
                "Karosserie Seitenteil ersetzen   1   1'640.00",
                "Lackierung   1   500.00",
                "Zwischensumme   2'140.00",
                "Übertrag auf Seite 2",
            ],
            [
                "Carrosserie Bachmann AG",
                "Rechnung Nr. B-27-3392",
                "Kunde: Marco Fontana",
                "Mietwagen 3 Tage   390.00",
                "Zwischensumme   2'530.00",
                "MwSt 8.1%   204.93",
                "Total CHF 2'734.93",
            ],
        ],
    ),
    "cert_de": (
        "medical_certificate",
        [
            [
                "Arztzeugnis",
                "Patient/in: Fabienne Riedweg",
                "Geburtsdatum: 05.04.1993",
                "Die Patientin ist wegen Krankheit arbeitsunfähig.",
                "Arbeitsunfähig ab: 12.06.2027",
                "Voraussichtlich bis: 26.06.2027",
                "Dr. med. Hans-Peter Gisler, Facharzt FMH",
                "Praxisstempel: Gruppenpraxis Seetal AG",
            ]
        ],
    ),
    "cert_fr": (
        "medical_certificate",
        [
            [
                "Cabinet médical Dre Gauthier",
                "Route de Lausanne 45, 1260 Nyon",
                "Certificat médical",
                "Patiente : Océane Duvoisin",
                "Date de naissance : 21.01.1990",
                "Incapacité de travail totale pour cause de maladie",
                "Début de l'incapacité : 03.07.2027",
                "Reprise du travail : 18.07.2027",
                "Date : 03.07.2027",
                "Dre Marianne Gauthier, médecin",
            ]
        ],
    ),
    "report_it": (
        "medical_report",
        [
            [
                "Studio medico Dott.ssa Elena Colombo",
                "Via Nassa 10, 6900 Lugano",
                "Referto medico",
                "Paziente: Davide Pellegrini",
                "Data nascita: 03.11.1984",
                "Data visita: 12.05.2027",
                "Data referto: 14.05.2027",
                "Diagnosi: Distorsione della caviglia destra",
                "Medico: Dott.ssa Elena Colombo",
            ]
        ],
    ),
    "booking_de": (
        "booking_confirmation",
        [
            [
                "Reisebüro Sonnenschein GmbH",
                "Buchungsbestätigung",
                "Buchungsnummer: SB-90417-Z",
                "Reisende: k.A.",
                "Abreise: 04.09.2027",
                "Rückreise: 11.09.2027",
                "Reisepreis: CHF 2'380.00",
                "Anzahlung: CHF 800.00",
                "Hinweis: Ihre Buchung ist noch ausstehend",
                "Status: pending",
                "Kundennummer: 4471882",
            ]
        ],
    ),
    "booking_overview": (
        "booking_confirmation",
        [
            [
                "Booking overview (agency copy)",
                "Booking reference: XKQZPL",
                "Booking reference: ABCDEF-GH",
                "Booking reference: pending",
                "Booking reference: to be confirmed by e-mail",
                "Booking reference: BK 2027 00417",
                "Booking reference: a1b2c3",
                "Booking reference: unavailable",
                "Booking reference: the number is on your ticket",
                "Booking reference: ausstehend",
            ]
        ],
    ),
}


@dataclass(frozen=True)
class Case:
    layout: str
    name: str
    value: Any
    snippet: str
    expect: float | None  # VERIFIED, UNVERIFIED, or None: the field is not stored
    page: int = 1

    def param(self) -> Any:
        label = f"{self.layout}|{self.name}|{self.snippet[:36]}|{self.expect}"
        return pytest.param(self, id=label)


def C(layout: str, name: str, value: Any, snippet: str, expect: float | None) -> Case:
    return Case(layout, name, value, snippet, expect)


V, U, X = VERIFIED, UNVERIFIED, None  # verified, unverified, not stored

CASES: list[Case] = [
    # ---- hotel folio
    C("folio", "booking_reference", "HLF-77Q4T", "Booking reference: HLF-77Q4T", V),
    C("folio", "booking_reference", "HLF-77Q4T", "HLF-77Q4T", V),
    C("folio", "traveller_name", "Ruedi Haldimann", "Guest: Mr. Ruedi Haldimann", V),
    C(
        "folio",
        "traveller_name",
        "Alpenrose Hotel & Spa AG",
        "Alpenrose Hotel & Spa AG",
        U,
    ),
    C("folio", "departure_date", "2027-06-18", "Departure: 18.06.2027", V),
    C("folio", "departure_date", "2027-06-18", "18.06.2027", V),
    C("folio", "departure_date", "2027-06-15", "Arrival: 15.06.2027", U),
    C("folio", "departure_date", "2027-06-15", "15.06.2027", U),
    C("folio", "trip_price", 1002.0, "Total price CHF 1'002.00", V),
    C("folio", "trip_price", 1002.0, "1'002.00", V),
    C("folio", "trip_price", 300.0, "Deposit paid CHF 300.00", U),
    C("folio", "trip_price", 930.0, "930.00", U),
    C("folio", "trip_price", 72.0, "Breakfast 3 x 24.00   72.00", U),
    # ---- dental invoice, German
    C("dental", "invoice_number", "ZB-2027-1187", "Rechnungs-Nr.: ZB-2027-1187", V),
    C("dental", "invoice_number", "ZB-2027-1187", "ZB-2027-1187", V),
    C(
        "dental",
        "invoice_number",
        "756.1234.5678.97",
        "Versichertennummer: 756.1234.5678.97",
        U,
    ),
    C("dental", "invoice_number", "071 555 01 42", "Telefon: 071 555 01 42", U),
    C("dental", "invoice_date", "2027-04-09", "Rechnungsdatum: 09.04.2027", V),
    C("dental", "invoice_date", "2027-04-09", "09.04.2027", V),
    C("dental", "invoice_date", "2027-04-02", "Behandlungsdatum: 02.04.2027", U),
    C("dental", "invoice_date", "1991-09-14", "Geburtsdatum: 14.09.1991", U),
    C(
        "dental",
        "provider",
        "Praxis Dr. med. dent. C. Baumgartner",
        "Praxis Dr. med. dent. C. Baumgartner",
        V,
    ),
    C("dental", "provider", "Praxis am Rebberg", "Praxis am Rebberg", V),
    C(
        "dental",
        "provider",
        "Dr. med. dent. C. Baumgartner",
        "Dr. med. dent. C. Baumgartner",
        U,
    ),
    C("dental", "patient_name", "Lukas Meierhans", "Patient: Lukas Meierhans", V),
    C("dental", "patient_name", "Praxis am Rebberg", "Praxis am Rebberg", U),
    C("dental", "total", 257.0, "Total CHF 257.00", V),
    C("dental", "total", 257.0, "Zwischensumme   257.00", U),
    C("dental", "total", 50.0, "Anzahlung CHF 50.00", U),
    C("dental", "total", 168.0, "Kontrolle und Zahnreinigung   1   168.00", U),
    C("dental", "total", 44.5, "Röntgen Einzelbild   2   44.50", U),
    C("dental", "total", 0.0, "MwSt   0.00", U),
    # ---- dental invoice, treatment on the invoice day
    C("dental_same_day", "invoice_number", "2027-0042", "Rechnung Nr. 2027-0042", V),
    C("dental_same_day", "invoice_date", "2027-04-02", "02.04.2027", V),
    C("dental_same_day", "invoice_date", "2027-04-02", "Rechnungsdatum: 02.04.2027", V),
    C(
        "dental_same_day",
        "invoice_date",
        "2027-04-02",
        "Behandlungsdatum: 02.04.2027",
        U,
    ),
    C("dental_same_day", "total", 312.0, "Total CHF 312.00", V),
    C("dental_same_day", "patient_name", "Ines Kaufmann", "Patient: Ines Kaufmann", V),
    # ---- garage invoice, Italian
    C("garage", "invoice_number", "2027/0318", "Fattura n. 2027/0318", V),
    C("garage", "invoice_number", "2027/0318", "2027/0318", V),
    C("garage", "invoice_number", "7731", "Ordine n. 7731", U),
    C("garage", "invoice_number", "TI 224411", "Targa: TI 224411", U),
    C("garage", "invoice_number", "CHE-101.202.303", "P. IVA CHE-101.202.303", U),
    C("garage", "invoice_number", "091 555 01 77", "Telefono: 091 555 01 77", U),
    C("garage", "invoice_date", "2027-05-21", "Data: 21.05.2027", V),
    C("garage", "invoice_date", "2027-05-21", "21.05.2027", V),
    C("garage", "invoice_date", "2027-06-20", "Scadenza: 20.06.2027", U),
    C("garage", "invoice_date", "2027-06-20", "20.06.2027", U),
    C("garage", "total", 402.67, "Totale da pagare CHF 402.67", V),
    C("garage", "total", 402.67, "402.67", V),
    C("garage", "total", 37.67, "IVA 8.1%   37.67", U),
    C("garage", "total", 465.0, "Imponibile   465.00", U),
    C("garage", "total", 100.0, "Acconto   100.00", U),
    C("garage", "total", 240.0, "240.00", U),
    C("garage", "total", 225.0, "Manodopera   2.5 h   225.00", U),
    C(
        "garage",
        "customer_name",
        "Giovanni Bernasconi",
        "Cliente: Giovanni Bernasconi",
        V,
    ),
    C("garage", "customer_name", "Autorimessa Ceresio SA", "Autorimessa Ceresio SA", U),
    C(
        "garage",
        "repair_company",
        "Autorimessa Ceresio SA",
        "Autorimessa Ceresio SA",
        V,
    ),
    # ---- cancellation letter, French
    C(
        "letter_fr",
        "booking_reference",
        "ABX-2291",
        "Référence de réservation : ABX-2291",
        V,
    ),
    C("letter_fr", "booking_reference", "ABX-2291", "ABX-2291", V),
    C(
        "letter_fr",
        "booking_reference",
        "annulation de votre réservation",
        "l'annulation de votre réservation ABX-2291",
        U,
    ),
    C(
        "letter_fr",
        "cancellation_date",
        "2027-03-14",
        "Date d'annulation : 14.03.2027",
        V,
    ),
    C("letter_fr", "cancellation_date", "2027-03-14", "14.03.2027", V),
    C("letter_fr", "cancellation_date", "2027-03-16", "Vevey, le 16.03.2027", U),
    C("letter_fr", "refund_received", 1480.0, "Remboursement : CHF 1'480.00", V),
    C("letter_fr", "refund_received", 120.0, "Frais de dossier retenus CHF 120.00", U),
    # ---- police report, German
    C("police_de", "report_number", "KAPO-27/88412", "Rapport-Nr.: KAPO-27/88412", V),
    C("police_de", "report_number", "KAPO-27/88412", "KAPO-27/88412", V),
    C("police_de", "report_number", "071 555 02 30", "Telefon: 071 555 02 30", U),
    C(
        "police_de",
        "incident_date",
        "2027-05-02",
        "Tatzeit: 02.05.2027, ca. 21.30 Uhr",
        V,
    ),
    C("police_de", "incident_date", "2027-05-02", "02.05.2027", V),
    C("police_de", "incident_date", "2027-05-03", "Anzeigedatum: 03.05.2027", U),
    C("police_de", "incident_date", "2027-05-03", "03.05.2027", U),
    C("police_de", "incident_date", "1985-07-12", "Geburtsdatum: 12.07.1985", U),
    C(
        "police_de",
        "customer_name",
        "Rahel Zürcher",
        "Anzeigeerstatter: Rahel Zürcher",
        V,
    ),
    C(
        "police_de",
        "customer_name",
        "Zürcher Transport GmbH",
        "Firma: Zürcher Transport GmbH",
        U,
    ),
    C(
        "police_de",
        "offence",
        "Diebstahl aus Personenwagen",
        "Delikt: Diebstahl aus Personenwagen",
        V,
    ),
    C("police_de", "offence", "Diebstahl", "Diebstahl", V),
    # ---- police report with blanks
    C("police_blank", "report_number", "n.d.", "Rapport-Nr.: n.d.", X),
    C("police_blank", "report_number", "NIL-27/4471", "Rapport-Nr.: NIL-27/4471", V),
    C("police_blank", "customer_name", "Unbekannt", "Anzeigeerstatter: Unbekannt", X),
    C("police_blank", "customer_name", "-", "Anzeigeerstatter: -", X),
    C("police_blank", "customer_name", "—", "Anzeigeerstatter: —", X),
    C("police_blank", "customer_name", "N/A", "Anzeigeerstatter: N/A", X),
    C(
        "police_blank",
        "customer_name",
        "keine Angabe",
        "Anzeigeerstatter: keine Angabe",
        X,
    ),
    C(
        "police_blank",
        "customer_name",
        "non communiqué",
        "Anzeigeerstatter: non communiqué",
        X,
    ),
    C("police_blank", "customer_name", "(n/a)", "Anzeigeerstatter: (n/a)", X),
    C(
        "police_blank",
        "customer_name",
        "Not available",
        "Anzeigeerstatter: Not available",
        X,
    ),
    C("police_blank", "customer_name", "Nils Null", "Anzeigeerstatter: Nils Null", V),
    C("police_blank", "offence", "Not answerable", "Delikt: Not answerable", X),
    C(
        "police_blank",
        "offence",
        "Sachbeschädigung, keine Angabe zum Täter",
        "Delikt: Sachbeschädigung, keine Angabe zum Täter",
        V,
    ),
    C("police_blank", "incident_date", "2027-05-02", "Tatzeit: 02.05.2027", V),
    # ---- airline irregularity report
    C(
        "pir",
        "report_reference",
        "ZRH-NW-27-045511",
        "File reference: ZRH-NW-27-045511",
        V,
    ),
    C("pir", "report_reference", "ZRH-NW-27-045511", "ZRH-NW-27-045511", V),
    C("pir", "report_reference", "724 2209918345", "Ticket number: 724 2209918345", U),
    C("pir", "report_reference", "NW 884420", "Bag tag: NW 884420", U),
    C("pir", "report_reference", "NW 412", "Flight: NW 412 on 23.07.2027", U),
    C("pir", "report_reference", "+41 44 555 01 90", "Phone: +41 44 555 01 90", U),
    C("pir", "incident_date", "2027-07-23", "Date of incident: 23.07.2027", V),
    C("pir", "incident_date", "2027-07-23", "23.07.2027", V),
    C("pir", "incident_date", "2027-07-24", "Date reported: 24.07.2027", U),
    C("pir", "incident_date", "1979-02-02", "Date of birth: 02.02.1979", U),
    C("pir", "incident_date", "2027-07-23", "Flight: NW 412 on 23.07.2027", U),
    C("pir", "traveller_name", "Elena Marchetti", "Passenger: Elena Marchetti", V),
    C(
        "pir",
        "traveller_name",
        "Nordwind Airlines Ltd.",
        "Airline: Nordwind Airlines Ltd.",
        U,
    ),
    C("pir", "airline", "Nordwind Airlines Ltd.", "Airline: Nordwind Airlines Ltd.", V),
    C(
        "pir",
        "airline",
        "Nordwind Airlines Ltd.",
        "Nordwind Airlines Ltd. - Baggage Service Zurich",
        V,
    ),
    # ---- long receipt
    C("receipt_long", "amount_paid", 60.86, "TOTAL CHF 60.86", V),
    C("receipt_long", "amount_paid", 60.86, "60.86", V),
    C("receipt_long", "amount_paid", 9.14, "Rückgeld / Change   CHF 9.14", U),
    C("receipt_long", "amount_paid", 64.86, "Subtotal   CHF 64.86", U),
    C("receipt_long", "amount_paid", -4.0, "Rabatt   -4.00", U),
    C("receipt_long", "amount_paid", 7.9, "Käse Appenzeller   7.90", U),
    C("receipt_long", "amount_paid", 7.9, "7.90", U),
    C("receipt_long", "amount_paid", 6.6, "Milch 1L 4 Stk   6.60", U),
    C("receipt_long", "amount_paid", 1.54, "MwSt 2.6%   1.54", U),
    C("receipt_long", "purchase_date", "2027-08-11", "Datum: 11.08.2027", V),
    C("receipt_long", "purchase_date", "2027-08-11", "11.08.2027", V),
    C("receipt_long", "purchase_date", "2027-08-11", "Datum: 11.08.2027   14:32", V),
    C("receipt_long", "purchase_date", "2029-08-11", "Garantie bis 11.08.2029", U),
    C(
        "receipt_long",
        "merchant",
        "Dorfladen Müller & Söhne",
        "Dorfladen Müller & Söhne",
        V,
    ),
    C("receipt_long", "item_description", "Ruchbrot 500g", "Ruchbrot 500g   3.60", V),
    # ---- receipts with a repeated amount
    C("receipt_subtotal", "amount_paid", 11.5, "11.50", U),
    C("receipt_subtotal", "purchase_date", "2027-09-02", "Datum: 02.09.2027", V),
    C("receipt_card", "amount_paid", 10.1, "TOTAL CHF 10.10", V),
    C("receipt_card", "amount_paid", 10.1, "10.10", V),
    C("receipt_card", "purchase_date", "2027-09-17", "17.09.2027", V),
    # ---- quote, German, labels above the values
    C("quote_de", "quote_number", "Q-2027-0093", "Q-2027-0093", V),
    C("quote_de", "quote_date", "2027-01-12", "12.01.2027", V),
    C("quote_de", "quote_date", "2027-02-12", "12.02.2027", U),
    C("quote_de", "total", 3845.0, "3'845.00", V),
    C("quote_de", "customer_name", "Anita Steffen", "Anita Steffen", V),
    C("quote_de", "repair_company", "Schreinerei Hug GmbH", "Schreinerei Hug GmbH", V),
    # ---- quote, English
    C("quote_en", "quote_number", "HQ-55102", "Quote no.: HQ-55102", V),
    C("quote_en", "quote_number", "88-1204", "Order no.: 88-1204", U),
    C("quote_en", "quote_number", "CHE-555.666.777", "VAT no.: CHE-555.666.777", U),
    C("quote_en", "quote_date", "2027-03-03", "Quote date: 03.03.2027", V),
    C("quote_en", "quote_date", "2027-04-02", "Valid until: 02.04.2027", U),
    C("quote_en", "quote_date", "2027-04-02", "02.04.2027", U),
    C("quote_en", "total", 1221.53, "Total CHF 1'221.53", V),
    C("quote_en", "total", 91.53, "VAT 8.1%   91.53", U),
    C("quote_en", "total", 720.0, "Labour 6 h   720.00", U),
    C("quote_en", "total", 410.0, "Paint materials   410.00", U),
    C(
        "quote_en",
        "customer_name",
        "Pia Hartmann-Roth",
        "Customer: Pia Hartmann-Roth",
        V,
    ),
    C(
        "quote_en",
        "customer_name",
        "Hartmann Auto Body Ltd",
        "Hartmann Auto Body Ltd",
        U,
    ),
    # ---- medical certificate, German
    C("cert_de", "patient_name", "Fabienne Riedweg", "Patient/in: Fabienne Riedweg", V),
    C(
        "cert_de",
        "patient_name",
        "Gruppenpraxis Seetal AG",
        "Praxisstempel: Gruppenpraxis Seetal AG",
        U,
    ),
    C("cert_de", "unfit_from", "2027-06-12", "Arbeitsunfähig ab: 12.06.2027", V),
    C("cert_de", "unfit_from", "2027-06-12", "12.06.2027", V),
    C("cert_de", "unfit_from", "2027-06-26", "Voraussichtlich bis: 26.06.2027", U),
    C("cert_de", "unfit_from", "1993-04-05", "Geburtsdatum: 05.04.1993", U),
    C("cert_de", "cause", "Krankheit", "wegen Krankheit", V),
    # ---- medical certificate, French
    C("cert_fr", "patient_name", "Océane Duvoisin", "Patiente : Océane Duvoisin", V),
    C(
        "cert_fr",
        "patient_name",
        "Cabinet médical Dre Gauthier",
        "Cabinet médical Dre Gauthier",
        U,
    ),
    C("cert_fr", "unfit_from", "2027-07-03", "Début de l'incapacité : 03.07.2027", V),
    C("cert_fr", "unfit_from", "2027-07-03", "03.07.2027", V),
    # Changed after the blind run (Nikola, 2026-11-24, decision 3: the generic date
    # does not depend on the layout): "Date :" is neutral and the same value stands
    # under "Debut de l'incapacite", with no other label, so the value is verified.
    C("cert_fr", "unfit_from", "2027-07-03", "Date : 03.07.2027", V),
    C("cert_fr", "unfit_from", "2027-07-18", "Reprise du travail : 18.07.2027", U),
    C("cert_fr", "unfit_from", "1990-01-21", "Date de naissance : 21.01.1990", U),
    C("cert_fr", "cause", "maladie", "pour cause de maladie", V),
    # ---- medical report, Italian
    C(
        "report_it",
        "patient_name",
        "Davide Pellegrini",
        "Paziente: Davide Pellegrini",
        V,
    ),
    C(
        "report_it",
        "patient_name",
        "Studio medico Dott.ssa Elena Colombo",
        "Studio medico Dott.ssa Elena Colombo",
        U,
    ),
    C("report_it", "treatment_date", "2027-05-12", "Data visita: 12.05.2027", V),
    C("report_it", "treatment_date", "2027-05-12", "12.05.2027", V),
    C("report_it", "treatment_date", "2027-05-14", "Data referto: 14.05.2027", U),
    C("report_it", "treatment_date", "1984-11-03", "Data nascita: 03.11.1984", U),
    C(
        "report_it",
        "provider",
        "Studio medico Dott.ssa Elena Colombo",
        "Studio medico Dott.ssa Elena Colombo",
        V,
    ),
    C(
        "report_it",
        "provider",
        "Dott.ssa Elena Colombo",
        "Medico: Dott.ssa Elena Colombo",
        U,
    ),
    C(
        "report_it",
        "diagnosis",
        "Distorsione della caviglia destra",
        "Diagnosi: Distorsione della caviglia destra",
        V,
    ),
    # ---- booking confirmation, German, with blanks
    C("booking_de", "booking_reference", "SB-90417-Z", "Buchungsnummer: SB-90417-Z", V),
    C(
        "booking_de",
        "booking_reference",
        "Buchung ist noch ausstehend",
        "Ihre Buchung ist noch ausstehend",
        U,
    ),
    C("booking_de", "booking_reference", "pending", "Status: pending", U),
    C("booking_de", "booking_reference", "4471882", "Kundennummer: 4471882", U),
    C("booking_de", "traveller_name", "k.A.", "Reisende: k.A.", X),
    C(
        "booking_de",
        "traveller_name",
        "Reisebüro Sonnenschein GmbH",
        "Reisebüro Sonnenschein GmbH",
        U,
    ),
    C("booking_de", "departure_date", "2027-09-04", "Abreise: 04.09.2027", V),
    C("booking_de", "departure_date", "2027-09-11", "Rückreise: 11.09.2027", U),
    C("booking_de", "trip_price", 2380.0, "Reisepreis: CHF 2'380.00", V),
    C("booking_de", "trip_price", 800.0, "Anzahlung: CHF 800.00", U),
    # ---- shape of an identifier (the label is right in every case)
    C(
        "booking_overview",
        "booking_reference",
        "XKQZPL",
        "Booking reference: XKQZPL",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "ABCDEF-GH",
        "Booking reference: ABCDEF-GH",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "BK 2027 00417",
        "Booking reference: BK 2027 00417",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "a1b2c3",
        "Booking reference: a1b2c3",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "pending",
        "Booking reference: pending",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "unavailable",
        "Booking reference: unavailable",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "ausstehend",
        "Booking reference: ausstehend",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "to be confirmed by e-mail",
        "Booking reference: to be confirmed by e-mail",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "the number is on your ticket",
        "Booking reference: the number is on your ticket",
        U,
    ),
]


def stored(out: Any) -> dict[str, Any]:
    return {f.name: f for f in out.fields}


@pytest.mark.parametrize("case", [c.param() for c in CASES])
def test_reader_keeps_or_downgrades_one_field(case: Case, tmp_path: Path) -> None:
    kind, pages = LAYOUTS[case.layout]
    answer = page_answer(kind, {case.name: (case.value, case.snippet)})
    out = read(tmp_path, kind, pages, [answer])
    fields = stored(out)
    if case.expect is None:
        assert fields == {}, "a placeholder must not be stored"
        return
    assert set(fields) == {case.name}, f"the field is not stored: {case.snippet!r}"
    got = fields[case.name]
    assert got.confidence == case.expect
    assert got.value == case.value
    assert got.source.document_id == "D-hold"
    assert got.source.page == case.page
    assert got.source.snippet == case.snippet.strip()


# --------------------------------------------------------------------------------
# Several pages
# --------------------------------------------------------------------------------

INVOICE = "invoice_two_pages"
FIRST = {
    "invoice_number": ("B-27-3392", "Rechnung Nr. B-27-3392"),
    "invoice_date": ("2027-07-30", "Datum: 30.07.2027"),
    "customer_name": ("Marco Fontana", "Kunde: Marco Fontana"),
}
TOTAL = {"total": (2734.93, "Total CHF 2'734.93")}


def two_pages(
    tmp_path: Path, first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    kind, pages = LAYOUTS[INVOICE]
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    return stored(out)


def test_total_on_page_two_is_verified_with_its_page(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, TOTAL)
    assert {n: f.confidence for n, f in fields.items()} == {
        "invoice_number": V,
        "invoice_date": V,
        "customer_name": V,
        "total": V,
    }
    assert fields["total"].source.page == 2
    assert fields["total"].source.snippet == "Total CHF 2'734.93"
    assert fields["invoice_number"].source.page == 1


def test_total_on_page_two_with_a_bare_snippet(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, {"total": (2734.93, "2'734.93")})
    assert fields["total"].confidence == V and fields["total"].source.page == 2


def test_subtotal_on_page_two_is_downgraded(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, {"total": (2530.0, "Zwischensumme   2'530.00")})
    assert fields["total"].confidence == U and fields["total"].value == 2530.0
    assert fields["total"].source.page == 2


def test_subtotal_on_page_one_and_total_on_page_two_stays_unverified(
    tmp_path: Path,
) -> None:
    first = {**FIRST, "total": (2140.0, "Zwischensumme   2'140.00")}
    fields = two_pages(tmp_path, first, TOTAL)
    assert fields["total"].confidence == U
    assert fields["total"].value == 2140.0 and fields["total"].source.page == 1
    assert fields["invoice_number"].confidence == V


@pytest.mark.parametrize(
    "placeholder", ["Not answerable", "n/a", "k.A.", "Unbekannt", "—", "non disponible"]
)
def test_placeholder_on_page_one_does_not_hide_the_real_value(
    placeholder: str, tmp_path: Path
) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [[*pages[0], f"Kunde: {placeholder}"], pages[1]]
    first = {"customer_name": (placeholder, f"Kunde: {placeholder}")}
    second = {"customer_name": ("Marco Fontana", "Kunde: Marco Fontana")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    got = stored(out)["customer_name"]
    assert got.confidence == V and got.source.page == 2
    assert got.value == "Marco Fontana"


@pytest.mark.parametrize("placeholder", ["n/a", "Nicht angegeben", "N.A.", "nil", "-"])
def test_placeholder_on_page_two_makes_no_disagreement(
    placeholder: str, tmp_path: Path
) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [pages[0], [*pages[1], f"Kunde: {placeholder}"]]
    first = {"customer_name": ("Marco Fontana", "Kunde: Marco Fontana")}
    second = {"customer_name": (placeholder, f"Kunde: {placeholder}")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    got = stored(out)["customer_name"]
    assert got.confidence == V and got.source.page == 1


def test_two_placeholders_store_nothing(tmp_path: Path) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [[*pages[0], "Kunde: n/a"], [*pages[1], "Kunde: Nicht angegeben"]]
    first = {"customer_name": ("n/a", "Kunde: n/a")}
    second = {"customer_name": ("Nicht angegeben", "Kunde: Nicht angegeben")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    assert "customer_name" not in stored(out)


def test_same_invoice_number_on_both_pages_stays_verified(tmp_path: Path) -> None:
    second = {"invoice_number": FIRST["invoice_number"]}
    fields = two_pages(tmp_path, FIRST, second)
    assert fields["invoice_number"].confidence == V
    assert fields["invoice_number"].source.page == 1


def test_photo_has_no_text_layer_and_stays_unverified(tmp_path: Path) -> None:
    """No text layer: the label check is not made, the path is unchanged (0.5)."""
    from PIL import Image

    image = Image.new("RGB", (400, 300), "white")
    png = BytesIO()
    image.save(png, "PNG")
    kind = "repair_invoice"
    answer = page_answer(kind, {"invoice_number": ("4471", "Invoice no.: 4471")})
    stub = Stub({"document_type": kind}, answer)
    out = stub.reader().extract(
        Document(id="D-hold", file="x.png", pages=1), png.getvalue(), "travel"
    )
    [field] = out.fields
    assert field.confidence == U and field.value == "4471"
    assert field.source.box is not None


# --------------------------------------------------------------------------------
# labels.verified, direct
# --------------------------------------------------------------------------------


def D(y: int, m: int, d: int) -> date:
    return date(y, m, d)


@dataclass(frozen=True)
class Direct:
    kind: str
    name: str
    value: Any
    snippet: str
    expect: bool
    text: str | None = None  # the page; the snippet alone when None

    def param(self) -> Any:
        shown = (self.text or self.snippet).replace("\n", "/")
        return pytest.param(
            self, id=f"{self.kind}|{self.name}|{shown[:48]}|{self.expect}"
        )


def Y(
    kind: str, name: str, value: Any, snippet: str, text: str | None = None
) -> Direct:
    return Direct(kind, name, value, snippet, True, text)


def N(
    kind: str, name: str, value: Any, snippet: str, text: str | None = None
) -> Direct:
    return Direct(kind, name, value, snippet, False, text)


RI, RQ, MI = "repair_invoice", "repair_quote", "medical_invoice"
BC, CC, PR, LR = (
    "booking_confirmation",
    "cancellation_confirmation",
    "police_report",
    "luggage_report",
)

IDS = [
    # the label names the field, in three languages and in any case
    Y(RI, "invoice_number", "4471", "Invoice no.: 4471"),
    Y(RI, "invoice_number", "4471", "INVOICE NO.: 4471"),
    Y(RI, "invoice_number", "4471", "rechnungs-nr.: 4471"),
    Y(RI, "invoice_number", "4471", "Rechnungsnummer 4471"),
    Y(RI, "invoice_number", "4471", "Fattura n. 4471"),
    Y(RI, "invoice_number", "4471", "Facture n° 4471"),
    Y(RI, "invoice_number", "4471", "Invoice number\n4471"),
    Y(MI, "invoice_number", "ZB-2027-1187", "Invoice no.: ZB-2027-1187"),
    # the label of the snippet occurrence: above, on the same line, after a separator
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.\n4471\n"),
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.\n\n4471\n"),
    Y(RI, "invoice_number", "4471", "4471", "Rechnungs-Nr.\t4471"),
    Y(RI, "invoice_number", "4471", "4471", "Kunde: Anna Berger | Rechnungs-Nr. 4471"),
    N(RI, "invoice_number", "9981", "9981", "Rechnungs-Nr. 4471 | Kunden-Nr. 9981"),
    Y(RI, "invoice_number", "4471", "4471", "Bestellnr. 77; Rechnungsnr. 4471"),
    N(RI, "invoice_number", "77", "77", "Bestellnr. 77; Rechnungsnr. 4471"),
    Y(RI, "invoice_number", "4471", "4471", "Auftrag Nr. 884   Rechnung Nr. 4471"),
    N(RI, "invoice_number", "884", "884", "Auftrag Nr. 884   Rechnung Nr. 4471"),
    # a label of another thing: a veto, even though the value is plausible
    N(RI, "invoice_number", "4471", "Customer no.: 4471"),
    N(MI, "invoice_number", "4471", "Patient no. 4471"),
    N(RI, "invoice_number", "4471", "Order number: 4471"),
    N(RI, "invoice_number", "4471", "Job no.: 4471"),
    N(RI, "invoice_number", "4471", "Tax number: 4471"),
    N(RI, "invoice_number", "4471", "VAT no. 4471"),
    N(RI, "invoice_number", "4471", "Insured no.: 4471"),
    N(RI, "invoice_number", "CHE-123.456.789", "UID: CHE-123.456.789"),
    N(
        RI,
        "invoice_number",
        "CH93 0076 2011 6238 5295 7",
        "IBAN: CH93 0076 2011 6238 5295 7",
    ),
    N(RI, "invoice_number", "0791234567", "Phone number: 0791234567"),
    N(RI, "invoice_number", "Q-1", "Quote no.: Q-1"),
    # the value repeated, or a label elsewhere on the page
    N(RI, "invoice_number", "4471", "4471", "Invoice no.: 5512\nOrder no.: 4471"),
    Y(RI, "invoice_number", "5512", "5512", "Invoice no.: 5512\nOrder no.: 4471"),
    N(
        RI,
        "invoice_number",
        "4471",
        "4471",
        "Rechnung\nRechnungs-Nr.: 5512\nKunde\n4471",
    ),
    N(RI, "invoice_number", "4471", "4471", "Invoice no.: 4471\nCustomer no.: 4471"),
    N(RI, "invoice_number", "4471", "4471", "Customer no.: 4471\nInvoice no.: 4471"),
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.: 4471\nPage 1 of 1"),
    # two labels in the snippet: the last one counts
    Y(
        BC,
        "booking_reference",
        "XJ-7790",
        "Customer no.: K-5521\nBooking reference: XJ-7790",
    ),
    N(
        BC,
        "booking_reference",
        "K-5521",
        "Customer no.: K-5521",
        "Customer no.: K-5521\nBooking reference: XJ-7790",
    ),
    # other id fields
    Y(BC, "booking_reference", "AB-12", "Booking reference: AB-12"),
    Y(BC, "booking_reference", "AB-12", "Buchungsreferenz: AB-12"),
    Y(BC, "booking_reference", "AB-12", "Buchungsnummer: AB-12"),
    Y(BC, "booking_reference", "AB-12", "Booking no.: AB-12"),
    N(BC, "booking_reference", "AB-12", "Customer no.: AB-12"),
    N(BC, "booking_reference", "AB-12", "Phone: AB-12"),
    Y(RQ, "quote_number", "Q-1", "Quote no.: Q-1"),
    Y(RQ, "quote_number", "Q-1", "Offerte-Nr.: Q-1"),
    N(RQ, "quote_number", "Q-1", "Order no.: Q-1"),
    N(RQ, "quote_number", "Q-1", "Customer no.: Q-1"),
    Y(PR, "report_number", "27/881", "Rapport-Nr.: 27/881"),
    Y(PR, "report_number", "27/881", "Report no.: 27/881"),
    Y(PR, "report_number", "27/881", "Rapport n°: 27/881"),
    N(PR, "report_number", "27/881", "Telefon: 27/881"),
    N(PR, "report_number", "27/881", "Customer no.: 27/881"),
    Y(LR, "report_reference", "ZRH-1", "File reference: ZRH-1"),
    Y(LR, "report_reference", "ZRH-1", "Report reference: ZRH-1"),
    N(LR, "report_reference", "ZRH-1", "Ticket number: ZRH-1"),
    N(LR, "report_reference", "ZRH-1", "Phone: ZRH-1"),
    # shape: the label is right, the value is prose or a word of the dictionary
    Y(RI, "invoice_number", "ABCDEF-GH", "Invoice no.: ABCDEF-GH"),
    Y(RI, "invoice_number", "XKQZPL", "Invoice no.: XKQZPL"),
    Y(RI, "invoice_number", "2027/0318", "Invoice no.: 2027/0318"),
    Y(RI, "invoice_number", "K-30418", "Invoice no.: K-30418"),
    N(RI, "invoice_number", "pending", "Invoice no.: pending"),
    N(RI, "invoice_number", "unavailable", "Invoice no.: unavailable"),
    N(
        RI,
        "invoice_number",
        "see attached document",
        "Invoice no.: see attached document",
    ),
    N(RI, "invoice_number", "to be confirmed", "Invoice no.: to be confirmed"),
    N(
        RI,
        "invoice_number",
        "will be sent by post",
        "Invoice no.: will be sent by post",
    ),
]

DATES = [
    # the own date of the document: the generic label is enough
    Y(RI, "invoice_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Datum 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Data: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Rechnungsdatum: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Invoice date: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Date: 2027-01-05"),
    Y(MI, "invoice_date", D(2027, 1, 5), "Datum: 05.01.2027"),
    Y(RQ, "quote_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(RQ, "quote_date", D(2027, 1, 5), "Quote date: 05.01.2027"),
    Y("receipt", "purchase_date", D(2027, 1, 5), "Datum: 05.01.2027"),
    Y("receipt", "purchase_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(CC, "cancellation_date", D(2027, 1, 5), "Datum: 05.01.2027"),
    Y(CC, "cancellation_date", D(2027, 1, 5), "Cancellation date: 05.01.2027"),
    Y(CC, "cancellation_date", D(2027, 1, 5), "Date d'annulation : 05.01.2027"),
    # a date of another meaning
    N(RI, "invoice_date", D(2027, 2, 5), "Due date: 05.02.2027"),
    N(RI, "invoice_date", D(2027, 2, 5), "Fällig am: 05.02.2027"),
    N(RI, "invoice_date", D(2027, 2, 5), "Valid until 05.02.2027"),
    N(RI, "invoice_date", D(2027, 2, 5), "Expiry date: 05.02.2027"),
    N(RI, "invoice_date", D(1980, 2, 5), "Date of birth: 05.02.1980"),
    N(RI, "invoice_date", D(1980, 2, 5), "Geburtsdatum: 05.02.1980"),
    N(RI, "invoice_date", D(2027, 2, 5), "Printed: 05.02.2027"),
    N(RQ, "quote_date", D(2027, 2, 5), "Valid until: 05.02.2027"),
    N(RQ, "quote_date", D(2027, 2, 5), "Gültig bis: 05.02.2027"),
    N("receipt", "purchase_date", D(2029, 2, 5), "Warranty until 05.02.2029"),
    N(CC, "cancellation_date", D(2027, 2, 5), "Valid until: 05.02.2027"),
    N(CC, "cancellation_date", D(1980, 2, 5), "Date of birth: 05.02.1980"),
    # no label at all
    N(RI, "invoice_date", D(2027, 1, 5), "05.01.2027"),
    N("receipt", "purchase_date", D(2027, 1, 5), "05.01.2027 14:32"),
    N(RQ, "quote_date", D(2027, 1, 5), "Zürich, 05.01.2027"),
    # the same day under two labels of the same document
    Y(
        MI,
        "invoice_date",
        D(2027, 4, 2),
        "02.04.2027",
        "Rechnungsdatum: 02.04.2027\nBehandlungsdatum: 02.04.2027",
    ),
    N(
        MI,
        "invoice_date",
        D(2027, 4, 2),
        "Behandlungsdatum: 02.04.2027",
        "Rechnungsdatum: 02.04.2027\nBehandlungsdatum: 02.04.2027",
    ),
    N(
        MI,
        "invoice_date",
        D(2027, 4, 2),
        "02.04.2027",
        "Fällig bis: 02.04.2027\nGültig bis: 02.04.2027",
    ),
    N(
        RI,
        "invoice_date",
        D(2027, 4, 2),
        "02.04.2027",
        "Rechnungsdatum: 02.04.2027\nDue date: 02.04.2027",
    ),
    # event dates need an event word
    Y(BC, "departure_date", D(2027, 7, 5), "Abreise 05.07.2027"),
    Y(BC, "departure_date", D(2027, 7, 5), "Departure date: 05.07.2027"),
    Y(BC, "departure_date", D(2027, 7, 5), "Départ : 05.07.2027"),
    Y(BC, "departure_date", D(2027, 7, 5), "Partenza: 05.07.2027"),
    Y(BC, "departure_date", D(2027, 7, 5), "Abflug: 05.07.2027"),
    N(BC, "departure_date", D(2027, 7, 5), "Date: 05.07.2027"),
    N(BC, "departure_date", D(2027, 7, 5), "Datum: 05.07.2027"),
    N(BC, "departure_date", D(2027, 7, 12), "Return: 12.07.2027"),
    N(BC, "departure_date", D(2027, 7, 12), "Rückflug: 12.07.2027"),
    N(BC, "departure_date", D(1980, 7, 12), "Geburtsdatum: 12.07.1980"),
    N(BC, "departure_date", D(2027, 7, 5), "05.07.2027"),
    Y(PR, "incident_date", D(2027, 6, 4), "Tatzeit: 04.06.2027"),
    Y(PR, "incident_date", D(2027, 6, 4), "Ereignis: 04.06.2027"),
    Y(PR, "incident_date", D(2027, 6, 4), "Incident date: 04.06.2027"),
    Y(PR, "incident_date", D(2027, 6, 4), "Data del fatto: 04.06.2027"),
    N(PR, "incident_date", D(2027, 6, 4), "Date: 04.06.2027"),
    N(PR, "incident_date", D(2027, 6, 5), "Anzeigedatum: 05.06.2027"),
    N(PR, "incident_date", D(1990, 6, 5), "Date of birth: 05.06.1990"),
    Y(LR, "incident_date", D(2027, 6, 4), "Date of incident: 04.06.2027"),
    Y(LR, "incident_date", D(2027, 6, 4), "Date of event: 04.06.2027"),
    N(LR, "incident_date", D(2027, 6, 5), "Date reported: 05.06.2027"),
    N(LR, "incident_date", D(2027, 6, 4), "Flight date: 04.06.2027"),
    Y("medical_report", "treatment_date", D(2027, 3, 1), "Behandlung: 01.03.2027"),
    Y(
        "medical_report",
        "treatment_date",
        D(2027, 3, 1),
        "Behandlungsdatum: 01.03.2027",
    ),
    Y("medical_report", "treatment_date", D(2027, 3, 1), "Untersuchung vom 01.03.2027"),
    Y(
        "medical_report",
        "treatment_date",
        D(2027, 3, 1),
        "Date de la consultation : 01.03.2027",
    ),
    Y("medical_report", "treatment_date", D(2027, 3, 1), "Data visita: 01.03.2027"),
    Y(
        "medical_report",
        "treatment_date",
        D(2027, 3, 1),
        "Date of treatment: 01.03.2027",
    ),
    N("medical_report", "treatment_date", D(2027, 3, 1), "Date : 01.03.2027"),
    N("medical_report", "treatment_date", D(2027, 3, 4), "Report date: 04.03.2027"),
    N("medical_report", "treatment_date", D(1990, 3, 4), "Geburtsdatum: 04.03.1990"),
    Y(
        "medical_certificate",
        "unfit_from",
        D(2027, 3, 1),
        "Arbeitsunfähig ab 01.03.2027",
    ),
    Y(
        "medical_certificate",
        "unfit_from",
        D(2027, 3, 1),
        "Unfit for work from: 01.03.2027",
    ),
    Y(
        "medical_certificate",
        "unfit_from",
        D(2027, 3, 1),
        "Incapacité de travail dès le 01.03.2027",
    ),
    Y(
        "medical_certificate",
        "unfit_from",
        D(2027, 3, 1),
        "Inabilità al lavoro dal 01.03.2027",
    ),
    N("medical_certificate", "unfit_from", D(2027, 3, 1), "Date: 01.03.2027"),
    N("medical_certificate", "unfit_from", D(2027, 3, 1), "Datum: 01.03.2027"),
    N("medical_certificate", "unfit_from", D(2027, 3, 15), "Valid until: 15.03.2027"),
    N("medical_certificate", "unfit_from", D(1990, 3, 15), "Date of birth: 15.03.1990"),
]

AMOUNTS = [
    Y(RI, "total", 99.0, "Total CHF 99.00"),
    Y(RI, "total", 99.0, "Total EUR 99.00"),
    Y(RI, "total", 99.0, "Total: 99.00 CHF"),
    Y(RI, "total", 99.0, "TOTAL 99.00"),
    Y(RI, "total", 1099.0, "TOTAL CHF 1'099.00"),
    Y(RI, "total", 99.0, "Totale da pagare CHF 99.00"),
    Y(RI, "total", 99.0, "Totale CHF 99.00"),
    Y(RI, "total", 99.0, "Gesamtbetrag: CHF 99.00"),
    Y(RI, "total", 99.0, "Rechnungsbetrag CHF 99.00"),
    Y(RI, "total", 99.0, "Montant total CHF 99.00"),
    Y(RI, "total", 99.0, "Total amount: 99.00"),
    Y(MI, "total", 99.0, "Total CHF 99.00"),
    Y(RQ, "total", 99.0, "Total CHF 99.00"),
    N(RI, "total", 99.0, "Subtotal: CHF 99.00"),
    N(RI, "total", 99.0, "VAT 8.1%: 99.00"),
    N(RI, "total", 99.0, "MwSt. 99.00"),
    N(RI, "total", 99.0, "TVA 99.00"),
    N(RI, "total", 99.0, "Deposit CHF 99.00"),
    N(RI, "total", 99.0, "Anzahlung CHF 99.00"),
    N(RI, "total", -99.0, "Discount -99.00"),
    N(RI, "total", 99.0, "Service fee 99.00"),
    N(RI, "total", 99.0, "Handling charge CHF 99.00"),
    N(RI, "total", 99.0, "99.00"),
    N(RI, "total", 99.0, "Paint 99.00"),
    N(RI, "total", 0.0, "Rabatt 0.00"),
    N(RI, "total", 99.0, "99.00", "Subtotal 99.00\nTotal 99.00"),
    Y(RI, "total", 99.0, "99.00", "Total CHF 99.00\nCard CHF 99.00"),
    N(RI, "total", 99.0, "99.00", "Total CHF 99.00\nDeposit CHF 99.00"),
    Y(RI, "total", 99.0, "99.00", "Total\n99.00"),
    N(RI, "total", 99.0, "99.00", "Position 1\n99.00"),
    Y(BC, "trip_price", 99.0, "Trip price: CHF 99.00"),
    Y(BC, "trip_price", 99.0, "Reisepreis CHF 99.00"),
    Y(BC, "trip_price", 99.0, "Total price CHF 99.00"),
    N(BC, "trip_price", 99.0, "Deposit CHF 99.00"),
    N(BC, "trip_price", 99.0, "Anzahlung CHF 99.00"),
    N(BC, "trip_price", 99.0, "Insurance premium CHF 99.00"),
    Y("receipt", "amount_paid", 20.0, "Amount paid CHF 20.00"),
    Y("receipt", "amount_paid", 20.0, "Total CHF 20.00"),
    N("receipt", "amount_paid", 5.0, "Change CHF 5.00"),
    N("receipt", "amount_paid", 5.0, "Rückgeld CHF 5.00"),
    N("receipt", "amount_paid", 20.0, "Subtotal CHF 20.00"),
    N("receipt", "amount_paid", 4.5, "Kaffee   4.50"),
    Y(CC, "refund_received", 400.0, "Refund: CHF 400.00"),
    Y(CC, "refund_received", 400.0, "Rückerstattung: CHF 400.00"),
    Y(CC, "refund_received", 400.0, "Remboursement CHF 400.00"),
    Y(CC, "refund_received", 400.0, "Rimborso CHF 400.00"),
    N(CC, "refund_received", 100.0, "Cancellation fee CHF 100.00"),
    N(CC, "refund_received", 100.0, "Stornogebühr CHF 100.00"),
    N(CC, "refund_received", 100.0, "Fee CHF 100.00"),
    N(CC, "refund_received", 400.0, "400.00"),
]

PERSON_LABELS = "Ärztin: ", "Doctor: ", "Médecin: ", "Zahnarzt: ", "Signed: "

PROVIDERS = [
    # an organisation
    Y(MI, "provider", "Praxis Dr. med. A. Muster", "Praxis Dr. med. A. Muster"),
    Y(MI, "provider", "Dr. Muster AG", "Dr. Muster AG"),
    Y(MI, "provider", "Gruppenpraxis Wil", "Gruppenpraxis Wil"),
    Y(MI, "provider", "Klinik Seeblick", "Klinik Seeblick"),
    Y(MI, "provider", "Hôpital de Nyon", "Hôpital de Nyon"),
    Y(MI, "provider", "Cabinet Dre Gauthier", "Cabinet Dre Gauthier"),
    Y(MI, "provider", "Ospedale Regionale di Lugano", "Ospedale Regionale di Lugano"),
    Y(MI, "provider", "Studio Dentistico Dott. Rossi", "Studio Dentistico Dott. Rossi"),
    Y(
        MI,
        "provider",
        "Zentrum für Zahnmedizin Dr. Weber",
        "Zentrum für Zahnmedizin Dr. Weber",
    ),
    Y(MI, "provider", "Medical Center Bern", "Medical Center Bern"),
    Y(MI, "provider", "Centre Santé Lausanne", "Centre Santé Lausanne"),
    Y(MI, "provider", "Dental Care Ltd", "Dental Care Ltd"),
    Y(MI, "provider", "Seeblick Physio", "Seeblick Physio"),
    Y("medical_report", "provider", "Praxis am Bahnhof", "Praxis am Bahnhof"),
    # one person with a title
    N(MI, "provider", "Dr. med. Hanspeter Gerber", "Dr. med. Hanspeter Gerber"),
    N(MI, "provider", "Dr. Muster", "Dr. Muster"),
    N(
        MI,
        "provider",
        "Prof. Dr. med. Beat Rothenfluh",
        "Prof. Dr. med. Beat Rothenfluh",
    ),
    N(MI, "provider", "PD Dr. med. Lukas Imhof", "PD Dr. med. Lukas Imhof"),
    N(MI, "provider", "med. pract. Sabine Roth", "med. pract. Sabine Roth"),
    N(MI, "provider", "Dott.ssa Elena Colombo", "Dott.ssa Elena Colombo"),
    N(MI, "provider", "Dott. Marco Rossi", "Dott. Marco Rossi"),
    N(MI, "provider", "Dre Marianne Gauthier", "Dre Marianne Gauthier"),
    N(MI, "provider", "Docteur Pierre Aebi", "Docteur Pierre Aebi"),
    N(MI, "provider", "Dr. med. J. P. Hess", "Dr. med. J. P. Hess"),
    N(
        "medical_report",
        "provider",
        "Dr. med. Hanspeter Gerber",
        "Dr. med. Hanspeter Gerber",
    ),
    # a name with no title: only a person label makes it a person
    Y(MI, "provider", "Sabine Roth", "Sabine Roth"),
    Y(MI, "provider", "Sabine Roth", "Sabine Roth", "Rechnung\nSabine Roth\nZürich"),
    *[
        N(MI, "provider", "Sabine Roth", "Sabine Roth", f"Rechnung\n{label}Sabine Roth")
        for label in PERSON_LABELS
    ],
    N(
        MI,
        "provider",
        "Mary Ann Keller",
        "Mary Ann Keller",
        "Physician: Mary Ann Keller",
    ),
    N(MI, "provider", "Pierre Aebi", "Pierre Aebi", "Unterschrift: Pierre Aebi"),
    N(MI, "provider", "Marco Rossi", "Marco Rossi", "Medico: Marco Rossi"),
    N(MI, "provider", "Tobias Frei", "Tobias Frei", "Dentist: Tobias Frei"),
]

PERSONS = [
    Y(MI, "patient_name", "Lukas Meierhans", "Lukas Meierhans"),
    Y(MI, "patient_name", "Anna-Maria von Arx", "Patient: Anna-Maria von Arx"),
    Y(MI, "patient_name", "Hans Müller-Frey", "Hans Müller-Frey"),
    Y(RI, "customer_name", "Seamus O'Brien", "Kunde: Seamus O'Brien"),
    Y(BC, "traveller_name", "Mr. Ruedi Haldimann", "Guest: Mr. Ruedi Haldimann"),
    N(MI, "patient_name", "Praxis am Rebberg", "Praxis am Rebberg"),
    N(RI, "customer_name", "Muster Treuhand AG", "Kunde: Muster Treuhand AG"),
    N(RI, "customer_name", "Dr. Muster GmbH", "Dr. Muster GmbH"),
    N(RI, "customer_name", "Garage Rossi Sagl", "Garage Rossi Sagl"),
    N(RI, "customer_name", "Hotel Bellevue SA", "Hotel Bellevue SA"),
    N(BC, "traveller_name", "Alpenrose Hotel & Spa AG", "Alpenrose Hotel & Spa AG"),
    N(BC, "traveller_name", "Reisebüro Nova GmbH", "Reisebüro Nova GmbH"),
    N(LR, "traveller_name", "Nordwind Airlines Ltd.", "Nordwind Airlines Ltd."),
    N(PR, "customer_name", "Bernasconi Sàrl", "Bernasconi Sàrl"),
    N(PR, "customer_name", "Medizinisches Zentrum Wil", "Medizinisches Zentrum Wil"),
    N(MI, "patient_name", "Klinik Hirslanden", "Klinik Hirslanden"),
    N(MI, "patient_name", "Clinique Santé Plus", "Clinique Santé Plus"),
    N(MI, "patient_name", "Studio Legale Rossi", "Studio Legale Rossi"),
]

OUT_OF_SCOPE = [
    Y("receipt", "merchant", "Hans Muster", "Hans Muster"),
    Y("receipt", "merchant", "Dorfladen AG", "Total CHF 99.00"),
    Y(RI, "repair_company", "Dr. med. Hans Muster", "Dr. med. Hans Muster"),
    Y(RQ, "repair_company", "Autorimessa Ceresio SA", "Autorimessa Ceresio SA"),
    Y(
        LR,
        "airline",
        "Nordwind Airlines Ltd.",
        "Date: 05.01.2027 Nordwind Airlines Ltd.",
    ),
    Y(PR, "offence", "Diebstahl", "Telefon: Diebstahl"),
    Y("medical_certificate", "cause", "Krankheit", "Geburtsdatum: Krankheit"),
    Y("medical_report", "diagnosis", "Distorsione", "Total CHF 99.00 Distorsione"),
    Y("receipt", "item_description", "Ruchbrot", "Ruchbrot"),
    Y(
        "damage_photo",
        "damage_description",
        "Delle sulla portiera",
        "Delle sulla portiera",
    ),
]

DIRECT = [*IDS, *DATES, *AMOUNTS, *PROVIDERS, *PERSONS, *OUT_OF_SCOPE]


@pytest.mark.parametrize("case", [c.param() for c in DIRECT])
def test_verified_direct(case: Direct) -> None:
    text = case.text if case.text is not None else case.snippet
    assert (
        labels.verified(case.kind, case.name, case.value, case.snippet, text)
        is case.expect
    )


def test_the_safeguarded_fields_are_the_ones_of_the_specification() -> None:
    expected = {
        **dict.fromkeys(
            [
                "invoice_number",
                "quote_number",
                "booking_reference",
                "report_number",
                "report_reference",
            ],
            "id",
        ),
        **dict.fromkeys(
            [
                "invoice_date",
                "quote_date",
                "purchase_date",
                "cancellation_date",
                "departure_date",
                "incident_date",
                "treatment_date",
                "unfit_from",
            ],
            "date",
        ),
        **dict.fromkeys(
            ["total", "trip_price", "amount_paid", "refund_received"], "amount"
        ),
        "provider": "organisation",
        **dict.fromkeys(["patient_name", "customer_name", "traveller_name"], "person"),
    }
    assert dict(labels.SAFEGUARDED) == expected


@pytest.mark.parametrize(
    "name",
    [
        "cause",
        "diagnosis",
        "offence",
        "item_description",
        "damage_description",
        "line_items",
        "repair_company",
        "merchant",
        "airline",
    ],
)
def test_out_of_scope_fields_are_not_safeguarded(name: str) -> None:
    assert name not in labels.SAFEGUARDED


# --------------------------------------------------------------------------------
# labels.is_placeholder, direct
# --------------------------------------------------------------------------------

PLACEHOLDER_TRUE = [
    "Not answerable",
    "not answerable",
    "NOT ANSWERABLE",
    "Unanswerable",
    "null",
    "NULL",
    "None",
    "N/A",
    "n/a",
    "N.A.",
    "n.a.",
    "-",
    "--",
    "—",
    "Unknown",
    "UNKNOWN",
    "Not found",
    "Not available",
    "Not applicable",
    "Not specified",
    "Not provided",
    "Not given",
    "Not stated",
    "Nil",
    "No value",
    "Empty",
    "k.A.",
    "K.A.",
    "keine Angabe",
    "Keine Angabe",
    "Unbekannt",
    "nicht verfügbar",
    "Nicht angegeben",
    "inconnu",
    "Non disponible",
    "non applicable",
    "n.d.",
    "N.D.",
    "Non communiqué",
    "sconosciuto",
    "non disponibile",
    "Non applicabile",
    "  Not   Applicable  ",
    "\tUnknown\n",
    '"n/a"',
    "'Unknown'",
    "[n/a]",
    "(unknown)",
    "(n/a)",
    "N/A.",
    "Unknown.",
    "(not available)",
]

PLACEHOLDER_FALSE: list[object] = [
    "N/A 4471",
    "ID: n/a",
    "Unknown Pass AG",
    "none of the above",
    "Not available in stock",
    "Unknown, Peter",
    "Nilufar Sharipova",
    "Nullmatt AG",
    "NIL-27/4471",
    "n.a.-77",
    "A",
    "X",
    "K",
    "-4471",
    "4471",
    "XKQZPL",
    "Nicolas Null",
    "Keine Angabe zur Tat",
    "Anna Keller",
    "Unbekannt Reisen AG",
    None,
    0,
    0.0,
    1,
    12.5,
    -4.0,
    False,
    True,
    date(2027, 1, 5),
    [],
    ["n/a"],
    {"value": "n/a"},
]


@pytest.mark.parametrize(
    "value", [pytest.param(v, id=repr(v)) for v in PLACEHOLDER_TRUE]
)
def test_is_placeholder_true(value: str) -> None:
    assert labels.is_placeholder(value) is True


@pytest.mark.parametrize(
    "value", [pytest.param(v, id=repr(v)) for v in PLACEHOLDER_FALSE]
)
def test_is_placeholder_false(value: object) -> None:
    assert labels.is_placeholder(value) is False


def test_placeholders_are_normalised_and_have_no_single_letter() -> None:
    assert labels.PLACEHOLDERS
    for text in labels.PLACEHOLDERS:
        assert text == text.casefold() and text == " ".join(text.split())
        assert len(text) > 1 or not text.isalnum()
