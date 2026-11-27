# ruff: noqa: E501
"""Independent synthetic tests of the reader safeguards (issue #125, phase 2).

Written from the shared specification and from the field definitions only. Every
document here is invented (new companies, new layouts, new label wordings) and no
test knows the implementation. Each table row has a short reason id.

Rows that the specification left open were decided at the end of phase 2
(2026-11-24): the rows the reader passes are plain tests now. The rows it does not
pass carry `xfail(strict=True, reason="known limit: ...")`: the reader gives 0.5
there (a person checks the field), which is safe; strict, so a change shows.

No network: the reader runs on an httpx MockTransport that raises on any request
that the test did not announce.
"""

import json
from collections.abc import Iterator
from datetime import date
from io import BytesIO
from typing import Any

import httpx2 as httpx
import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app.docintel.labels import PLACEHOLDERS, SAFEGUARDED, is_placeholder, verified
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType, ExtractedField

D = date(2027, 5, 12)
DS = "12.05.2027"
HEAD = "Wydenmoos Gebäudetechnik AG | Bachstrasse 12 | 8499 Sternenberg"
FOOT = "Seite 1 von 1"


def known_limit(reason: str) -> Any:
    return pytest.mark.xfail(strict=True, reason=f"known limit (0.5): {reason}")


# Marginal wordings that the reader does not take as the label of the field: the field
# gets 0.5 and a person checks it. A bare "Price" can be the price of one person.
KNOWN_LIMITS = {"Case number", "Credit", "Fatt. n.", "Price", "Preis", "Prix", "Prezzo"}


# ---------------------------------------------------------------------------
# 1. Placeholders
# ---------------------------------------------------------------------------

PLACEHOLDER_YES = [
    ("N/A", "en-na-upper"),
    ("n/a", "en-na-lower"),
    ("  N/A  ", "en-na-spaces"),
    ("(n/a)", "en-na-brackets"),
    ("'n/a'", "en-na-single-quotes"),
    ('"N/A"', "en-na-double-quotes"),
    ("n/a.", "en-na-dot"),
    ("N.A.", "en-n-dot-a-upper"),
    ("n.a.", "en-n-dot-a-lower"),
    ("-", "dash"),
    ("--", "double-dash"),
    ("—", "em-dash"),
    ("NULL", "en-null-upper"),
    ("None", "en-none"),
    ("nil", "en-nil"),
    ("Unknown", "en-unknown"),
    ("UNKNOWN", "en-unknown-upper"),
    ("[unknown]", "en-unknown-brackets"),
    ("not found", "en-not-found"),
    ("Not   Available", "en-not-available-spaces"),
    ("not\tavailable", "en-not-available-tab"),
    ("NOT APPLICABLE", "en-not-applicable"),
    ("not specified", "en-not-specified"),
    ("Not provided", "en-not-provided"),
    ("not given", "en-not-given"),
    ("not stated", "en-not-stated"),
    ("no value", "en-no-value"),
    ("Empty", "en-empty"),
    ("unanswerable", "en-unanswerable"),
    ("not answerable", "en-not-answerable"),
    ("Not Answerable.", "en-not-answerable-dot"),
    ("(not available)", "en-not-available-brackets"),
    ("k.a.", "de-ka-lower"),
    ("K.A.", "de-ka-upper"),
    ("Keine Angabe", "de-keine-angabe"),
    ("keine  angabe", "de-keine-angabe-spaces"),
    ("UNBEKANNT", "de-unbekannt-upper"),
    ("Nicht verfügbar", "de-nicht-verfuegbar"),
    ("NICHT VERFÜGBAR", "de-nicht-verfuegbar-upper"),
    ("nicht angegeben", "de-nicht-angegeben"),
    ("inconnu", "fr-inconnu"),
    ("Inconnu.", "fr-inconnu-dot"),
    ("non disponible", "fr-non-disponible"),
    ("Non Applicable", "fr-non-applicable"),
    ("n.d.", "fr-nd-lower"),
    ("N.D.", "fr-nd-upper"),
    ("non communiqué", "fr-non-communique"),
    ("Non Communiqué", "fr-non-communique-title"),
    ("sconosciuto", "it-sconosciuto"),
    ("Non disponibile", "it-non-disponibile"),
    ("non applicabile", "it-non-applicabile"),
    ("n/a\n", "en-na-newline"),
]

PLACEHOLDER_NO: list[tuple[Any, str]] = [
    (0, "int-zero"),
    (0.0, "float-zero"),
    (1, "int-one"),
    (12.5, "float"),
    (True, "bool-true"),
    (False, "bool-false"),
    (None, "none-object"),
    (date(2027, 5, 12), "date"),
    (["n/a"], "list-with-placeholder"),
    (b"n/a", "bytes"),
    ("0", "string-zero"),
    ("NA-2207", "identifier-with-na"),
    ("Nala Andrin", "name-with-na-letters"),
    ("Unknown Pleasures Records GmbH", "merchant-starting-with-unknown"),
    ("Please contact n/a by phone", "sentence-with-na-inside"),
    ("Rechnung n/a 4471", "id-with-na-inside"),
    ("see n/a below", "sentence-na-middle"),
    ("Nilsson", "surname-nil-prefix"),
    ("Nil Anderson", "name-nil-first-word"),
    ("None of the above", "sentence-starting-none"),
    ("Not available until Monday", "sentence-not-available"),
    ("Nicht angegeben am 3. Mai", "de-sentence"),
    ("Non disponible depuis hier", "fr-sentence"),
    ("Inconnu SA", "company-inconnu"),
    ("Empty Spaces Ltd", "company-empty"),
    ("Nullbach AG", "company-null-prefix"),
    ("N", "single-letter-n"),
    ("A", "single-letter-a"),
    ("x", "single-letter-x"),
    ("k", "single-letter-k"),
    ("NA", "two-letter-code-not-listed"),
    ("-5", "negative-number-text"),
    ("2027-05-12", "iso-date-text"),
    ("--- see below", "dashes-and-text"),
]


@pytest.mark.parametrize(
    ("value", "why"), PLACEHOLDER_YES, ids=[r for _, r in PLACEHOLDER_YES]
)
def test_placeholder_texts_are_placeholders(value: str, why: str) -> None:
    assert is_placeholder(value) is True


@pytest.mark.parametrize(
    ("value", "why"), PLACEHOLDER_NO, ids=[r for _, r in PLACEHOLDER_NO]
)
def test_real_values_are_not_placeholders(value: Any, why: str) -> None:
    assert is_placeholder(value) is False


def test_placeholders_is_a_frozenset_of_normalised_texts() -> None:
    assert isinstance(PLACEHOLDERS, frozenset) and PLACEHOLDERS
    for item in PLACEHOLDERS:
        assert isinstance(item, str)
        assert item == item.casefold(), item
        assert item == " ".join(item.split()), item
        assert not (len(item) == 1 and item.isalpha()), item  # never a single letter
        assert item.strip(), item


def test_every_listed_placeholder_is_recognised() -> None:
    for item in PLACEHOLDERS:
        assert is_placeholder(item) is True, item


def test_a_placeholder_word_inside_a_longer_text_is_never_a_placeholder() -> None:
    for item in PLACEHOLDERS:
        if any(ch.isalpha() for ch in item):
            assert is_placeholder(f"{item} Brunnmatt AG") is False, item


# ---------------------------------------------------------------------------
# 2. Labels in four languages, four places
# ---------------------------------------------------------------------------

WHERE = ("same", "above", "inside", "pipe")


def place(label: str, shown: str, where: str, money: bool) -> tuple[str, str]:
    """(snippet, page text) with the label before the value, in one of four ways."""
    cur = "CHF " if money else ""
    if where == "same":  # the label before the value, on the same line
        return shown, f"{HEAD}\n{label}: {cur}{shown}\n{FOOT}"
    if where == "above":  # the label on the line above the value
        return shown, f"{HEAD}\n{label}\n{cur}{shown}\n{FOOT}"
    if where == "inside":  # the label is inside the snippet
        snippet = f"{label} {cur}{shown}"
        return snippet, f"{HEAD}\n{snippet}\n{FOOT}"
    # the label after a separator
    return shown, f"{HEAD} | {label} {cur}{shown} | {FOOT}"


# (document type, field, value, shown, labels, marginal labels)
Entry = tuple[str, str, Any, str, list[str], list[str]]
ENTRIES: list[Entry] = [
    (
        "repair_invoice",
        "invoice_number",
        "RN-48217",
        "RN-48217",
        [
            "Invoice no.",
            "Invoice number",
            "Invoice No",
            "Invoice #",
            "Rechnung Nr.",
            "Rechnung Nr",
            "Rechnungsnummer",
            "Rechnungs-Nr.",
            "Facture n°",
            "Facture N°",
            "Numéro de facture",
            "N° de facture",
            "Fattura n.",
            "Fattura n°",
            "Numero fattura",
            "Numero della fattura",
        ],
        ["Inv. no.", "Rg.-Nr.", "Fatt. n."],
    ),
    (
        "medical_invoice",
        "invoice_number",
        "MR-90113",
        "MR-90113",
        ["Invoice no.", "Rechnungsnummer", "Facture n°", "Fattura n."],
        [],
    ),
    (
        "repair_quote",
        "quote_number",
        "OFF-30915",
        "OFF-30915",
        [
            "Quote no.",
            "Quote number",
            "Quotation no.",
            "Quotation number",
            "Offerte Nr.",
            "Offertnummer",
            "Angebot Nr.",
            "Angebotsnummer",
            "Devis n°",
            "Devis N°",
            "Numéro de devis",
            "N° de devis",
            "Preventivo n.",
            "Numero preventivo",
            "Numero del preventivo",
        ],
        ["Offer no.", "Estimate no.", "Offerta n."],
    ),
    (
        "booking_confirmation",
        "booking_reference",
        "V8KD2M",
        "V8KD2M",
        [
            "Booking reference",
            "Booking ref.",
            "Booking number",
            "Booking no.",
            "Reservation number",
            "Buchungsreferenz",
            "Buchungsnummer",
            "Buchungs-Nr.",
            "Reservationsnummer",
            "Reservierungsnummer",
            "Référence de réservation",
            "Numéro de réservation",
            "N° de réservation",
            "Riferimento prenotazione",
            "Numero di prenotazione",
            "Codice di prenotazione",
            "PNR",
        ],
        ["Confirmation code", "Confirmation number"],
    ),
    (
        "cancellation_confirmation",
        "booking_reference",
        "C4TW9L",
        "C4TW9L",
        [
            "Booking reference",
            "Buchungsnummer",
            "Numéro de réservation",
            "Numero di prenotazione",
        ],
        [],
    ),
    (
        "police_report",
        "report_number",
        "2027-PV-6143",
        "2027-PV-6143",
        [
            "Report no.",
            "Report number",
            "Police report no.",
            "Protokoll-Nr.",
            "Protokollnummer",
            "Rapportnummer",
            "Rapport Nr.",
            "Numéro de rapport",
            "Rapport n°",
            "Procès-verbal n°",
            "N° de procès-verbal",
            "Rapporto n.",
            "Verbale n.",
            "Numero di verbale",
        ],
        ["Case number", "Anzeige-Nr."],
    ),
    (
        "luggage_report",
        "report_reference",
        "BRNLX48820",
        "BRNLX48820",
        ["Report reference"],
        ["Reference no.", "PIR no.", "Referenz", "Référence", "Riferimento"],
    ),
    (
        "repair_invoice",
        "invoice_date",
        D,
        DS,
        [
            "Invoice date",
            "Date of invoice",
            "Date",
            "Rechnungsdatum",
            "Datum",
            "Datum der Rechnung",
            "Date de facture",
            "Date de la facture",
            "Data fattura",
            "Data della fattura",
            "Data",
        ],
        [],
    ),
    (
        "medical_invoice",
        "invoice_date",
        D,
        DS,
        ["Invoice date", "Rechnungsdatum", "Date de facture", "Data fattura"],
        [],
    ),
    (
        "repair_quote",
        "quote_date",
        D,
        DS,
        [
            "Quote date",
            "Date of quote",
            "Quotation date",
            "Date",
            "Datum",
            "Angebotsdatum",
            "Offertdatum",
            "Date du devis",
            "Date de devis",
            "Data preventivo",
            "Data del preventivo",
            "Data",
        ],
        [],
    ),
    (
        "receipt",
        "purchase_date",
        D,
        DS,
        [
            "Purchase date",
            "Date of purchase",
            "Date",
            "Datum",
            "Kaufdatum",
            "Einkaufsdatum",
            "Date d'achat",
            "Data di acquisto",
            "Data",
        ],
        [],
    ),
    (
        "cancellation_confirmation",
        "cancellation_date",
        D,
        DS,
        [
            "Cancellation date",
            "Date of cancellation",
            "Date",
            "Datum",
            "Stornierungsdatum",
            "Date d'annulation",
            "Data di annullamento",
        ],
        [],
    ),
    (
        "booking_confirmation",
        "departure_date",
        D,
        DS,
        [
            "Departure",
            "Departure date",
            "Date of departure",
            "Abreise",
            "Abreisedatum",
            "Abflug",
            "Abflugdatum",
            "Départ",
            "Date de départ",
            "Partenza",
            "Data di partenza",
            "Data partenza",
        ],
        [],
    ),
    (
        "medical_report",
        "treatment_date",
        D,
        DS,
        [
            "Treatment date",
            "Date of treatment",
            "Examined on",
            "Date of consultation",
            "Consultation",
            "Behandlungsdatum",
            "Behandlung am",
            "Untersuchung vom",
            "Untersuchungsdatum",
            "Datum der Behandlung",
            "Date du traitement",
            "Date de consultation",
            "Visite du",
            "Data del trattamento",
            "Data della visita",
            "Data visita",
        ],
        ["Treated on"],
    ),
    (
        "police_report",
        "incident_date",
        D,
        DS,
        [
            "Incident date",
            "Date of incident",
            "Date of event",
            "Event date",
            "Tatzeit",
            "Ereignisdatum",
            "Datum des Ereignisses",
            "Date des faits",
            "Date du fait",
            "Date de l'incident",
            "Data del fatto",
            "Data dell'evento",
            "Data dell'incidente",
        ],
        ["Tatdatum"],
    ),
    (
        "luggage_report",
        "incident_date",
        D,
        DS,
        ["Incident date", "Date of event"],
        ["Datum des Vorfalls"],
    ),
    (
        "medical_certificate",
        "unfit_from",
        D,
        DS,
        [
            "Unfit from",
            "Unfit for work from",
            "Incapacity from",
            "Arbeitsunfähig ab",
            "Arbeitsunfähig seit",
            "AUF ab",
            "Incapacité de travail dès le",
            "Incapacité dès",
            "Inabilità al lavoro dal",
            "Inabilità dal",
        ],
        [],
    ),
    (
        "repair_invoice",
        "total",
        4820.5,
        "4'820.50",
        [
            "Total",
            "Total amount",
            "Grand total",
            "Total to pay",
            "Gesamtbetrag",
            "Gesamttotal",
            "Totalbetrag",
            "Montant total",
            "Total TTC",
            "Total à payer",
            "Totale",
            "Importo totale",
            "Totale complessivo",
            "Totale fattura",
        ],
        ["Rechnungsbetrag", "Endbetrag", "Amount due"],
    ),
    (
        "repair_quote",
        "total",
        4820.5,
        "4'820.50",
        ["Total", "Gesamtbetrag", "Totale", "Montant total"],
        [],
    ),
    (
        "medical_invoice",
        "total",
        4820.5,
        "4'820.50",
        ["Total", "Gesamtbetrag", "Totale", "Montant total"],
        [],
    ),
    (
        "receipt",
        "amount_paid",
        89.0,
        "89.00",
        [
            "Amount paid",
            "Total paid",
            "Paid",
            "Betrag bezahlt",
            "Bezahlt",
            "Montant payé",
            "Payé",
            "Importo pagato",
            "Pagato",
            "Totale pagato",
        ],
        ["Total", "Summe", "Totale", "Gesamtbetrag"],
    ),
    (
        "booking_confirmation",
        "trip_price",
        3450.0,
        "3'450.00",
        [
            "Total price",
            "Trip price",
            "Travel price",
            "Reisepreis",
            "Gesamtpreis",
            "Prix total",
            "Prix du voyage",
            "Prezzo totale",
            "Prezzo del viaggio",
        ],
        ["Price", "Preis", "Prix", "Prezzo", "Total"],
    ),
    (
        "cancellation_confirmation",
        "refund_received",
        1210.0,
        "1,210.00",
        [
            "Refund",
            "Refund amount",
            "Refund received",
            "Refunded",
            "Rückerstattung",
            "Erstattungsbetrag",
            "Rückzahlung",
            "Remboursement",
            "Montant remboursé",
            "Rimborso",
            "Importo rimborsato",
        ],
        ["Gutschrift", "Credit"],
    ),
]


def label_params() -> list[Any]:
    out: list[Any] = []
    for doc, name, value, shown, core, marginal in ENTRIES:
        money = isinstance(value, float)
        for where in WHERE:
            for label in core:
                out.append(
                    pytest.param(
                        doc, name, value, label, shown, where, money,
                        id=f"{doc}|{name}|{label}|{where}",
                    )
                )  # fmt: skip
            for label in marginal:
                out.append(
                    pytest.param(
                        doc, name, value, label, shown, where, money,
                        marks=(
                            known_limit(f"marginal wording {label!r}")
                            if label in KNOWN_LIMITS
                            else ()
                        ),
                        id=f"{doc}|{name}|{label}|{where}|marginal",
                    )
                )  # fmt: skip
    return out


@pytest.mark.parametrize(
    ("doc", "name", "value", "label", "shown", "where", "money"), label_params()
)
def test_the_right_label_before_the_value_verifies(
    doc: str, name: str, value: Any, label: str, shown: str, where: str, money: bool
) -> None:
    snippet, text = place(label, shown, where, money)
    assert verified(doc, name, value, snippet, text) is True


def test_a_currency_before_the_value_is_not_a_label() -> None:
    text = f"{HEAD}\nTotal CHF 89.00\n{FOOT}"
    assert verified("receipt", "amount_paid", 89.0, "Total CHF 89.00", text) is True
    for cur in ("CHF", "EUR", "USD", "€", "$"):
        shown = f"Gesamtbetrag {cur} 120.00"
        assert verified(
            "repair_invoice", "total", 120.0, "120.00", f"{HEAD}\n{shown}"
        ), cur


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param("CHF 1'280.00", id="chf"),
        pytest.param("Fr. 1'280.00", id="fr"),
    ],
)  # fmt: skip
def test_a_snippet_that_starts_with_a_currency_still_sees_the_label(
    snippet: str,
) -> None:
    # Rule 2a leaves nothing once the currency is removed; rule 2b then looks at the
    # same line before the occurrence. Real model snippets are often "CHF 1'280.00".
    text = f"{HEAD}\nGesamtbetrag {snippet}\n{FOOT}"
    assert verified("repair_invoice", "total", 1280.0, snippet, text) is True


# ---------------------------------------------------------------------------
# 3. Unknown and missing labels
# ---------------------------------------------------------------------------

BARE: list[tuple[str, str, Any, str]] = [
    ("repair_invoice", "invoice_number", "RN-48217", "RN-48217"),
    ("repair_quote", "quote_number", "OFF-30915", "OFF-30915"),
    ("booking_confirmation", "booking_reference", "V8KD2M", "V8KD2M"),
    ("police_report", "report_number", "2027-PV-6143", "2027-PV-6143"),
    ("luggage_report", "report_reference", "BRNLX48820", "BRNLX48820"),
    ("repair_invoice", "invoice_date", D, DS),
    ("repair_quote", "quote_date", D, DS),
    ("booking_confirmation", "departure_date", D, DS),
    ("medical_report", "treatment_date", D, DS),
    ("police_report", "incident_date", D, DS),
    ("receipt", "purchase_date", D, DS),
    ("cancellation_confirmation", "cancellation_date", D, DS),
    ("medical_certificate", "unfit_from", D, DS),
    ("repair_invoice", "total", 4820.5, "4'820.50"),
    ("receipt", "amount_paid", 89.0, "89.00"),
    ("booking_confirmation", "trip_price", 3450.0, "3'450.00"),
    ("cancellation_confirmation", "refund_received", 1210.0, "1,210.00"),
]
HEADINGS = ["Bemerkungen", "Remarks", "Remarque", "Annotazioni"]


def unlabelled_params() -> list[Any]:
    out: list[Any] = []
    for doc, name, value, shown in BARE:
        money = isinstance(value, float)
        cur = "CHF " if money else ""
        layouts = {
            "alone-first-line": f"{cur}{shown}\n{FOOT}",
            "alone-after-letterhead": f"{HEAD}\n{cur}{shown}\n{FOOT}",
            "alone-after-blank-lines": f"{HEAD}\n\n\n{cur}{shown}\n{FOOT}",
        }
        for heading in HEADINGS:
            layouts[f"heading-same-{heading}"] = f"{HEAD}\n{heading}: {cur}{shown}"
            layouts[f"heading-above-{heading}"] = f"{HEAD}\n{heading}\n{cur}{shown}"
        for lid, text in layouts.items():
            out.append(pytest.param(doc, name, value, shown, text, id=f"{name}|{lid}"))
    return out


@pytest.mark.parametrize(
    ("doc", "name", "value", "snippet", "text"), unlabelled_params()
)
def test_a_value_with_no_label_or_an_unrelated_label_is_not_verified(
    doc: str, name: str, value: Any, snippet: str, text: str
) -> None:
    assert verified(doc, name, value, snippet, text) is False


GENERIC_FOR_EVENTS = [
    ("booking_confirmation", "departure_date", ["Date", "Datum", "Data"]),
    ("police_report", "incident_date", ["Date", "Datum"]),
    ("medical_report", "treatment_date", ["Date", "Datum", "Data"]),
    ("medical_certificate", "unfit_from", ["Date", "Datum"]),
    ("luggage_report", "incident_date", ["Datum"]),
]


@pytest.mark.parametrize(
    ("doc", "name", "label", "where"),
    [
        (doc, name, label, where)
        for doc, name, labels in GENERIC_FOR_EVENTS
        for label in labels
        for where in WHERE
    ],
)
def test_a_generic_date_label_does_not_verify_an_event_date(
    doc: str, name: str, label: str, where: str
) -> None:
    snippet, text = place(label, DS, where, False)
    assert verified(doc, name, D, snippet, text) is False


@pytest.mark.parametrize(
    ("doc", "name", "label"),
    [
        ("repair_invoice", "invoice_date", "Metadata"),
        ("repair_invoice", "invoice_date", "Last updated"),
        ("repair_invoice", "invoice_date", "Update"),
        ("repair_quote", "quote_date", "Database"),
        ("repair_invoice", "total", "Subtotal"),
        ("repair_invoice", "total", "Zwischentotal"),
    ],
)
def test_a_label_word_inside_another_word_is_not_a_label(
    doc: str, name: str, label: str
) -> None:
    value: Any = 120.0 if name == "total" else D
    shown = "120.00" if name == "total" else DS
    text = f"{HEAD}\n{label}: {shown}"
    assert verified(doc, name, value, shown, text) is False


# ---------------------------------------------------------------------------
# 4. Repeated values
# ---------------------------------------------------------------------------

REPEATED: list[tuple[str, str, Any, str, str, bool, str]] = [
    # contradicting labels, the snippet is the bare value: no label wins
    (
        "repair_invoice", "invoice_number", "7731442",
        "Customer no. 7731442\nPayment terms 30 days\nInvoice no. 7731442",
        "7731442", False, "en-customer-then-invoice",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Invoice no. 7731442\nPayment terms 30 days\nCustomer no. 7731442",
        "7731442", False, "en-invoice-then-customer",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Kunden-Nr. 7731442\nZahlbar in 30 Tagen\nRechnung Nr. 7731442",
        "7731442", False, "de-customer-invoice",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "N° client 7731442\nPayable à 30 jours\nFacture N° 7731442",
        "7731442", False, "fr-customer-invoice",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Cliente n. 7731442\nPagabile a 30 giorni\nFattura n. 7731442",
        "7731442", False, "it-customer-invoice",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Rechnung Nr. 7731442\nAuftrags-Nr. 7731442\nKunden-Nr. 7731442",
        "7731442", False, "de-one-match-two-vetoes",
    ),
    (
        "repair_quote", "quote_number", "5051",
        "Kunden-Nr. 5051\nOfferte Nr. 5051",
        "5051", False, "de-quote-customer",
    ),
    (
        "booking_confirmation", "booking_reference", "V8KD2M",
        "Customer ref. V8KD2M\nBooking reference V8KD2M",
        "V8KD2M", False, "en-booking-customer",
    ),
    (
        "police_report", "report_number", "2027-PV-6143",
        "Report no. 2027-PV-6143\nPhone 2027-PV-6143",
        "2027-PV-6143", False, "en-report-phone",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Invoice date: {DS}\nDue date: {DS}", DS, False, "en-invoice-due",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Rechnungsdatum: {DS}\nZahlbar bis: {DS}", DS, False, "de-invoice-due",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Date de facture : {DS}\nÉchéance : {DS}", DS, False, "fr-invoice-due",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Data fattura {DS}\nScadenza {DS}", DS, False, "it-invoice-due",
    ),
    (
        "repair_quote", "quote_date", D,
        f"Quote date: {DS}\nValid until: {DS}", DS, False, "en-quote-valid",
    ),
    (
        "repair_quote", "quote_date", D,
        f"Angebotsdatum: {DS}\nGültig bis: {DS}", DS, False, "de-quote-valid",
    ),
    (
        "police_report", "incident_date", D,
        f"Incident date: {DS}\nDate of birth: {DS}", DS, False, "en-incident-birth",
    ),
    (
        "medical_report", "treatment_date", D,
        f"Treatment date: {DS}\nPrint date: {DS}", DS, False, "en-treatment-print",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Total 120.00\nSubtotal 120.00", "120.00", False, "en-total-subtotal",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Gesamtbetrag 120.00\nZwischensumme 120.00", "120.00", False, "de-total-sub",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Total 120.00\nAnzahlung 120.00", "120.00", False, "de-total-deposit",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Totale 120.00\nIVA 120.00", "120.00", False, "it-total-vat",
    ),
    (
        "booking_confirmation", "trip_price", 3450.0,
        "Total price 3'450.00\nDeposit 3'450.00", "3'450.00", False, "en-price-deposit",
    ),
    # one matching label and bare or unrelated occurrences: it stands
    (
        "repair_invoice", "invoice_number", "7731442",
        "Rechnung Nr. 7731442\nVielen Dank für Ihren Auftrag\n7731442",
        "7731442", True, "de-match-plus-bare-line",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Invoice no. 7731442\nDelivery note 7731442\nPage 1 of 1 7731442",
        "7731442", True, "en-match-plus-unrelated",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Rechnung Nr. 7731442\nBetreff\n7731442\nRef.\n7731442",
        "7731442", True, "de-match-plus-two-unrelated",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Total 120.00\nPos. 3: 120.00", "120.00", True, "en-total-plus-line",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Invoice date: {DS}\nDelivery: {DS}", DS, True, "en-date-plus-delivery",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Datum: {DS}\nLieferung: {DS}", DS, True, "de-date-plus-delivery",
    ),
    # the snippet carries its own label: it pins its occurrence
    (
        "repair_invoice", "invoice_number", "7731442",
        "Customer no. 7731442\nInvoice no. 7731442",
        "Invoice no. 7731442", True, "snippet-pins-the-match",
    ),
    (
        "repair_invoice", "invoice_number", "7731442",
        "Customer no. 7731442\nInvoice no. 7731442",
        "Customer no. 7731442", False, "snippet-pins-the-veto",
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("doc", "name", "value", "text", "snippet", "expected", "why"),
    [pytest.param(*row, id=row[-1]) for row in REPEATED],
)
def test_repeated_values_follow_their_labels(
    doc: str,
    name: str,
    value: Any,
    text: str,
    snippet: str,
    expected: bool,
    why: str,
) -> None:
    assert verified(doc, name, value, snippet, f"{HEAD}\n{text}") is expected


# ---------------------------------------------------------------------------
# 5. Misleading neighbouring labels
# ---------------------------------------------------------------------------

TWO_COLUMNS: list[tuple[str, str, bool, str]] = [
    # (line, value, expected, id): the value of an invoice number
    ("Rechnung Nr. 5512    Kunden-Nr. 9904", "5512", True, "de-4sp-invoice"),
    ("Rechnung Nr. 5512    Kunden-Nr. 9904", "9904", False, "de-4sp-customer"),
    ("Rechnung Nr. 5512 | Kunden-Nr. 9904", "5512", True, "de-pipe-invoice"),
    ("Rechnung Nr. 5512 | Kunden-Nr. 9904", "9904", False, "de-pipe-customer"),
    ("Rechnung Nr. 5512\tKunden-Nr. 9904", "5512", True, "de-tab-invoice"),
    ("Rechnung Nr. 5512\tKunden-Nr. 9904", "9904", False, "de-tab-customer"),
    ("Kunden-Nr. 9904    Rechnung Nr. 5512", "5512", True, "de-reversed-invoice"),
    ("Kunden-Nr. 9904    Rechnung Nr. 5512", "9904", False, "de-reversed-customer"),
    ("Invoice no. 4471; Order no. 9981", "4471", True, "en-semicolon-invoice"),
    ("Invoice no. 4471; Order no. 9981", "9981", False, "en-semicolon-order"),
    ("Facture N° 3381    N° client 7720", "3381", True, "fr-invoice"),
    ("Facture N° 3381    N° client 7720", "7720", False, "fr-customer"),
    ("Fattura n. 8802   Cliente n. 1167", "8802", True, "it-invoice"),
    ("Fattura n. 8802   Cliente n. 1167", "1167", False, "it-customer"),
    ("Customer no. 1167    Invoice no. 8802", "1167", False, "en-reversed-customer"),
    ("Customer no. 1167    Invoice no. 8802", "8802", True, "en-reversed-invoice"),
    ("Invoice no. 4471 | Job no. 3305 | Patient no. 6120", "3305", False, "en-job"),
    ("Invoice no. 4471 | Job no. 3305 | Patient no. 6120", "6120", False, "en-patient"),
    ("Invoice no. 4471 | Job no. 3305 | Patient no. 6120", "4471", True, "en-3col"),
    ("Rechnung Nr. 4471 | MwSt-Nr. 8802031", "8802031", False, "de-vat"),
    ("Rechnung Nr. 4471 | Auftrags-Nr. 3305", "3305", False, "de-order"),
    ("Rechnung Nr. 4471 | Patienten-Nr. 6120", "6120", False, "de-patient"),
    ("Rechnung Nr. 4471 | UID CHE-000.111.222", "CHE-000.111.222", False, "uid"),
    ("Invoice no. 4471    Tel. 0315550142", "0315550142", False, "tel"),
    ("Invoice no. 4471    Phone 0315550142", "0315550142", False, "phone"),
    ("Invoice no. 4471    Tax no. 4478812", "4478812", False, "tax"),
    ("Invoice no. 4471    IBAN CH0012345678901234567", "CH0012345678901234567", False, "iban"),
    ("Facture N° 3381    N° de commande 5590", "5590", False, "fr-order"),
    ("Fattura n. 8802   Ordine n. 5590", "5590", False, "it-order"),
    ("Fattura n. 8802   P. IVA 03392120125", "03392120125", False, "it-vat"),
    ("Fattura n. 8802   Assicurato n. 7710", "7710", False, "it-insured"),
    ("Rechnung Nr. 4471  Versicherten-Nr. 7710", "7710", False, "de-insured"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("line", "value", "expected", "why"),
    [pytest.param(*row, id=row[-1]) for row in TWO_COLUMNS],
)
def test_the_label_nearest_the_value_decides_on_a_crowded_line(
    line: str, value: str, expected: bool, why: str
) -> None:
    text = f"{HEAD}\n{line}\n{FOOT}"
    assert verified("repair_invoice", "invoice_number", value, value, text) is expected


VETO_LABELS: list[tuple[str, str, list[str]]] = [
    (
        "repair_quote",
        "quote_date",
        [
            "Valid until", "Valid to", "Gültig bis", "Gültigkeit", "Valable jusqu'au",
            "Valido fino al", "Valida fino al", "Due date", "Zahlbar bis",
            "Date d'échéance", "Scadenza", "Date of birth", "Geburtsdatum",
            "Date de naissance", "Data di nascita", "Print date", "Druckdatum",
            "Gedruckt am", "Imprimé le", "Data di stampa", "Expiry date", "Ablaufdatum",
        ],
    ),
    (
        "repair_invoice",
        "invoice_date",
        [
            "Due date", "Fälligkeitsdatum", "Zahlbar bis", "Date d'échéance",
            "Scadenza", "Payment due", "Valid until", "Print date", "Druckdatum",
            "Date of birth", "Geburtsdatum", "Expiry date",
        ],
    ),
    (
        "medical_report",
        "treatment_date",
        [
            "Date of birth", "Geburtsdatum", "Date de naissance", "Data di nascita",
            "Print date", "Druckdatum", "Gedruckt am", "Date d'impression",
            "Data di stampa",
        ],
    ),
    (
        "police_report",
        "incident_date",
        ["Date of birth", "Geburtsdatum", "Print date", "Druckdatum", "Valid until"],
    ),
    (
        "booking_confirmation",
        "departure_date",
        [
            "Return", "Rückreise", "Retour", "Ritorno", "Booking date",
            "Buchungsdatum", "Date of birth", "Geburtsdatum", "Print date",
        ],
    ),
    (
        "cancellation_confirmation",
        "cancellation_date",
        ["Date of birth", "Departure date", "Print date", "Due date"],
    ),
    (
        "receipt",
        "purchase_date",
        ["Date of birth", "Warranty until", "Garantie bis", "Print date"],
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("doc", "name", "label", "where"),
    [
        (doc, name, label, where)
        for doc, name, labels in VETO_LABELS
        for label in labels
        for where in ("same", "above", "inside", "pipe")
    ],
)
def test_a_date_under_a_label_of_another_date_does_not_verify(
    doc: str, name: str, label: str, where: str
) -> None:
    snippet, text = place(label, DS, where, False)
    assert verified(doc, name, D, snippet, text) is False


AMOUNT_VETOES: list[tuple[str, str, list[str]]] = [
    (
        "repair_invoice",
        "total",
        [
            "Subtotal", "Sub-total", "Sub total", "Zwischensumme", "Zwischentotal",
            "Sous-total", "Subtotale", "MwSt", "MwSt.", "VAT", "TVA", "IVA",
            "Mehrwertsteuer", "Anzahlung", "Deposit", "Acompte", "Acconto",
            "Discount", "Rabatt", "Remise", "Sconto", "Fee", "Gebühr", "Frais",
            "Balance before", "Previous balance",
        ],
    ),
    (
        "booking_confirmation",
        "trip_price",
        [
            "Deposit", "Anzahlung", "Acompte", "Acconto", "Discount", "Rabatt",
            "Booking fee", "Buchungsgebühr", "Frais de dossier", "VAT", "MwSt",
        ],
    ),
    (
        "cancellation_confirmation",
        "refund_received",
        [
            "Cancellation fee", "Stornogebühr", "Stornokosten", "Frais d'annulation",
            "Penale", "Fee", "Gebühr", "Deposit", "Anzahlung",
        ],
    ),
    (
        "receipt",
        "amount_paid",
        ["Subtotal", "Zwischensumme", "VAT", "MwSt", "Discount", "Rabatt", "Sconto"],
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("doc", "name", "label", "where"),
    [
        (doc, name, label, where)
        for doc, name, labels in AMOUNT_VETOES
        for label in labels
        for where in WHERE
    ],
)
def test_an_amount_under_a_label_of_another_amount_does_not_verify(
    doc: str, name: str, label: str, where: str
) -> None:
    snippet, text = place(label, "120.00", where, True)
    assert verified(doc, name, 120.0, snippet, text) is False


NEAREST_LABEL: list[tuple[str, str, Any, str, str, bool, str]] = [
    (
        "repair_invoice", "invoice_number", "5512",
        "Rechnungsnummer\nBemerkung 5512", "5512", False, "same-line-label-wins",
    ),
    (
        "repair_invoice", "invoice_number", "5512",
        "Kundennummer\n5512", "5512", False, "veto-label-above",
    ),
    (
        "repair_invoice", "invoice_number", "5512",
        "Customer no.\n5512", "5512", False, "en-veto-label-above",
    ),
    (
        "repair_invoice", "invoice_number", "5512",
        "Numéro de client\n5512", "5512", False, "fr-veto-label-above",
    ),
    (
        "repair_invoice", "invoice_number", "5512",
        "Numero cliente\n5512", "5512", False, "it-veto-label-above",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Total\nZwischensumme 120.00", "120.00", False, "total-above-sub-same-line",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Zwischensumme\nCHF 120.00", "120.00", False, "sub-above-currency-line",
    ),
    (
        "repair_invoice", "total", 120.0,
        "Total\nCHF 120.00", "120.00", True, "total-above-currency-line",
    ),
    (
        "repair_quote", "quote_date", D,
        f"Valid until\n{DS}", DS, False, "valid-until-above",
    ),
    (
        "repair_quote", "quote_date", D,
        f"Quote date\n{DS}", DS, True, "quote-date-above",
    ),
    (
        "repair_invoice", "invoice_date", D,
        f"Rechnungsdatum\nLieferung: {DS}", DS, False, "same-line-label-wins-date",
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("doc", "name", "value", "text", "snippet", "expected", "why"),
    [pytest.param(*row, id=row[-1]) for row in NEAREST_LABEL],
)
def test_only_the_nearest_label_counts(
    doc: str,
    name: str,
    value: Any,
    text: str,
    snippet: str,
    expected: bool,
    why: str,
) -> None:
    assert verified(doc, name, value, snippet, f"{HEAD}\n{text}") is expected


# ---------------------------------------------------------------------------
# 6. Identifier formats
# ---------------------------------------------------------------------------

LEGIT_IDS: list[tuple[str, str, str, str, str]] = [
    ("booking_confirmation", "booking_reference", "Booking reference", "QWZRTP", "letters-only"),
    ("booking_confirmation", "booking_reference", "Buchungsnummer", "PLMNQR", "letters-only-de"),
    ("booking_confirmation", "booking_reference", "Booking reference", "ZYXWVU-JK", "letters-hyphen"),
    ("police_report", "report_number", "Report no.", "HX-KLM", "letters-hyphen-short"),
    ("repair_invoice", "invoice_number", "Invoice no.", "2027/INV/0031", "slashes"),
    ("repair_invoice", "invoice_number", "Rechnung Nr.", "5512", "plain-number"),
    ("repair_invoice", "invoice_number", "Invoice no.", "INV.2027.0031-A", "dots-and-hyphen"),
    ("booking_confirmation", "booking_reference", "Booking ref.", "ZX 44 BB", "spaced-groups"),
    ("repair_quote", "quote_number", "Quote no.", "Q 7781", "letter-space-digits"),
    ("repair_quote", "quote_number", "Numero preventivo", "PV/27/0412", "slashes-it"),
    ("luggage_report", "report_reference", "Report reference", "BRNLX48820", "letters-digits"),
    ("police_report", "report_number", "Numéro de rapport", "K-17", "short"),
    ("repair_invoice", "invoice_number", "Facture n°", "FA-2027-00418", "fr-format"),
]  # fmt: skip

PROSE = [
    ("will be sent by e-mail", "en-sentence"),
    ("to be confirmed", "en-three-lowercase"),
    ("pending", "en-single-lowercase-word"),
    ("folgt per Post", "de-three-tokens"),
    ("Wird per E-Mail zugestellt", "de-sentence-capital"),
    ("en attente de confirmation", "fr-sentence"),
    ("sarà comunicato via e-mail", "it-sentence"),
    ("Number will follow, thanks.", "sentence-punctuation"),
    ("will follow.", "sentence-dot"),
]
PROSE_TWO_WORDS = [
    ("à confirmer", "fr-two-lowercase-words"),
    ("da definire", "it-two-lowercase-words"),
    ("wird nachgereicht", "de-two-lowercase-words"),
]
ID_COMBOS = [
    ("repair_invoice", "invoice_number", "Invoice no."),
    ("booking_confirmation", "booking_reference", "Buchungsnummer"),
    ("police_report", "report_number", "Numéro de rapport"),
    ("repair_quote", "quote_number", "Numero preventivo"),
    ("luggage_report", "report_reference", "Report reference"),
]


@pytest.mark.parametrize(
    ("doc", "name", "label", "value", "why"),
    [pytest.param(*row, id=row[-1]) for row in LEGIT_IDS],
)
@pytest.mark.parametrize("where", WHERE)
def test_an_unusual_identifier_under_the_right_label_verifies(
    doc: str, name: str, label: str, value: str, why: str, where: str
) -> None:
    snippet, text = place(label, value, where, False)
    assert verified(doc, name, value, snippet, text) is True


def prose_params() -> list[Any]:
    out: list[Any] = []
    for combo in ID_COMBOS:
        for phrase, why in PROSE:
            out.append(pytest.param(*combo, phrase, id=f"{combo[1]}|{why}"))
        for phrase, why in PROSE_TWO_WORDS:
            out.append(
                pytest.param(
                    *combo,
                    phrase,
                    id=f"{combo[1]}|{why}",
                )
            )
    return out


@pytest.mark.parametrize(("doc", "name", "label", "value"), prose_params())
@pytest.mark.parametrize("where", ("same", "inside"))
def test_prose_under_the_right_label_is_not_an_identifier(
    doc: str, name: str, label: str, value: str, where: str
) -> None:
    snippet, text = place(label, value, where, False)
    assert verified(doc, name, value, snippet, text) is False


# ---------------------------------------------------------------------------
# 7. Doctors, practices, companies
# ---------------------------------------------------------------------------


def provider_text(value: str, extra: str = "") -> str:
    return f"{value}\nBahnhofplatz 3, 8001 Zürich\n{extra}Patient: Rahel Gantenbein"


DOCTORS = [
    "Dr. med. Ursina Waldvogel",
    "Dr. Marco Bellinelli",
    "Prof. Dr. Jonas Etterlin",
    "Prof. Dr. med. Annelise Hofer-Brand",
    "PD Dr. med. Irene Blattner",
    "med. pract. Jan Holenstein",
    "Dr. med. dent. K. Brülhart",
    "Dr. A. B. Rohner",
    "Dott.ssa Carla Ferrante",
    "Dott. Ruggero Lenti",
    "Dre Aline Fortis",
    "Docteur Sylvain Barraud",
    "Dr. med. Hanna Kellerhals",
]
PRACTICES = [
    ("Arztpraxis Dr. med. L. Brenner", "arztpraxis-with-doctor"),
    ("Dr. Brenner Medical AG", "legal-form-and-medical"),
    ("Cabinet médical Dre Aline Fortis", "cabinet-with-doctor"),
    ("Studio medico Dott. Ruggero Lenti", "studio-with-doctor"),
    ("Praxis Dr. med. S. Hunziker", "praxis-with-doctor"),
    ("Dr. Kaelin AG", "doctor-name-and-ag"),
    ("Dr. Orsini Sagl", "doctor-name-and-sagl"),
    ("Gruppenpraxis Dr. med. Koller", "gruppenpraxis"),
    ("Clinique des Chênes SA", "clinique-sa"),
    ("Klinik am See", "klinik"),
    ("Centro Medico Lugano Sud", "centro-medico"),
    ("Permanence Gare Plus", "permanence"),
    ("Medical Center Aarewald", "medical-center"),
    ("Spital Oberland", "spital"),
    ("Hôpital du Jorat", "hopital"),
    ("Ospedale Regionale Mendrisio", "ospedale"),
    ("Ambulatorio Dott.ssa Greta Pallanza", "ambulatorio-with-doctor"),
    ("Zentrum für Gesundheit Seetal", "zentrum-gesundheit"),
    ("Seeblick Medizin", "medizin-word"),
    ("Hanna Kellerhals Ltd", "name-and-ltd"),
]
PERSON_LABELS = [
    "Ärztin", "Arzt", "Médecin", "Medico", "Physician", "Doctor", "Dentist",
    "Zahnarzt", "Signed", "Signature", "Unterschrift",
]  # fmt: skip


@pytest.mark.parametrize("doc", ["medical_report", "medical_invoice"])
@pytest.mark.parametrize("name", DOCTORS)
def test_a_titled_person_is_not_a_provider(doc: str, name: str) -> None:
    assert verified(doc, "provider", name, name, provider_text(name)) is False


@pytest.mark.parametrize("doc", ["medical_report", "medical_invoice"])
@pytest.mark.parametrize(("name", "why"), PRACTICES, ids=[p[1] for p in PRACTICES])
def test_an_organisation_is_a_provider_even_with_a_doctor_name_in_it(
    doc: str, name: str, why: str
) -> None:
    assert verified(doc, "provider", name, name, provider_text(name)) is True


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Zahnarztpraxis Dr. Imhof", id="zahnarztpraxis"),
        pytest.param("Kinderarztpraxis Sonnhalde", id="kinderarztpraxis"),
    ],
)  # fmt: skip
def test_a_compound_with_an_organisation_word_is_a_provider(name: str) -> None:
    assert (
        verified("medical_report", "provider", name, name, provider_text(name)) is True
    )


@pytest.mark.parametrize("label", PERSON_LABELS)
@pytest.mark.parametrize("name", ["Mirella Zogg", "Oskar Lindeblad", "Elio Santacroce"])
def test_a_bare_name_after_a_person_label_is_not_a_provider(
    label: str, name: str
) -> None:
    text = f"Rechnung\n{label}: {name}\nBahnhofplatz 3, 8001 Zürich"
    assert verified("medical_report", "provider", name, name, text) is False


@pytest.mark.parametrize(
    "label",
    [pytest.param(label, id=label) for label in ("Physician", "Ärztin", "Médecin")],
)
def test_a_bare_name_under_a_person_label_on_the_line_above(label: str) -> None:
    name = "Oskar Lindeblad"
    text = f"Rechnung\n{label}\n{name}\nBahnhofplatz 3, 8001 Zürich"
    assert verified("medical_report", "provider", name, name, text) is False


def test_a_bare_name_with_no_title_and_no_label_stays_verified() -> None:
    # documented limit: nothing says that it is a person
    name = "Hannes Zumbühl"
    text = provider_text(name)
    assert verified("medical_report", "provider", name, name, text) is True


def test_a_person_label_elsewhere_does_not_make_another_name_a_person() -> None:
    text = "Physician: Oskar Lindeblad\nHannes Zumbühl\nBahnhofplatz 3, 8001 Zürich"
    assert (
        verified("medical_report", "provider", "Hannes Zumbühl", "Hannes Zumbühl", text)
        is True
    )


def test_an_organisation_after_a_person_label_is_still_an_organisation() -> None:
    name = "Klinik Hirschen"
    text = f"Rechnung\nSigned: {name}\nBahnhofplatz 3"
    assert verified("medical_report", "provider", name, name, text) is True


def test_an_organisation_needs_no_label() -> None:
    name = "Klinik Hirschen"
    assert verified("medical_invoice", "provider", name, name, name) is True


COMPANIES = [
    ("patient_name", "medical_certificate", "Fenstertech Riedmatt GmbH"),
    ("patient_name", "medical_certificate", "Klinik Sonnenberg"),
    ("patient_name", "medical_report", "Hôpital du Jorat"),
    ("patient_name", "medical_invoice", "Ospedale Regionale Mendrisio"),
    ("customer_name", "repair_invoice", "Brunner & Söhne Bau AG"),
    ("customer_name", "repair_invoice", "Café Sonnenhof Sàrl"),
    ("customer_name", "repair_quote", "Elettro Kamber Sagl"),
    ("customer_name", "police_report", "Praxis Ammann"),
    ("customer_name", "repair_invoice", "Studio Legale Moretti"),
    ("customer_name", "repair_invoice", "Centro Sportivo Lugano"),
    ("customer_name", "repair_invoice", "Müller SA"),
    ("traveller_name", "booking_confirmation", "Nordlicht Reisen Ltd"),
    ("traveller_name", "luggage_report", "Reisebüro Nord GmbH"),
    ("traveller_name", "booking_confirmation", "Alpenwind Reisen AG"),
]
PEOPLE = [
    ("patient_name", "medical_certificate", "Livia Hartmeier"),
    ("patient_name", "medical_report", "Jean-Pierre Dufaux"),
    ("patient_name", "medical_invoice", "Anna-Lena von Matt"),
    ("patient_name", "medical_report", "Dr. Silvan Grob"),
    ("customer_name", "repair_invoice", "Corinne Aebischer"),
    ("customer_name", "police_report", "Agnes Aggeler"),
    ("customer_name", "repair_invoice", "Salvatore Sagliano"),
    ("customer_name", "repair_quote", "Mélanie Vauthey"),
    ("traveller_name", "booking_confirmation", "Lena Fankhauser"),
    ("traveller_name", "luggage_report", "Gianni Terragni"),
]


@pytest.mark.parametrize(("name", "doc", "value"), COMPANIES)
def test_a_company_is_not_a_person(name: str, doc: str, value: str) -> None:
    assert verified(doc, name, value, value, f"{HEAD}\n{value}\nSeite 1") is False


@pytest.mark.parametrize(("name", "doc", "value"), PEOPLE)
def test_a_person_needs_no_label(name: str, doc: str, value: str) -> None:
    assert verified(doc, name, value, value, f"{HEAD}\n{value}\nSeite 1") is True


# ---------------------------------------------------------------------------
# 8. Out of scope fields
# ---------------------------------------------------------------------------

OUT_OF_SCOPE: list[tuple[str, str, str, str, str]] = [
    ("medical_certificate", "cause", "illness", "Date of birth: 12.05.1990\nillness", "bad-context"),
    ("medical_certificate", "cause", "accident", "Kunden-Nr. accident", "veto-like-label"),
    ("medical_report", "diagnosis", "Influenza", "Influenza", "bare"),
    ("medical_report", "diagnosis", "Influenza", "Rechnungsnummer: Influenza", "id-label"),
    ("police_report", "offence", "Theft", "Print date: Theft", "date-veto-label"),
    ("police_report", "offence", "Theft", "Theft", "bare"),
    ("receipt", "item_description", "Down jacket", "Subtotal Down jacket", "amount-veto"),
    ("receipt", "item_description", "Down jacket", "Down jacket", "bare"),
    ("damage_photo", "damage_description", "cracked windscreen", "x", "snippet-not-on-text"),
    ("repair_invoice", "repair_company", "Hans Muster", "Hans Muster", "sole-trader-name"),
    ("repair_invoice", "repair_company", "Tobler Fensterbau", "Patient: Tobler Fensterbau", "patient-label"),
    ("receipt", "merchant", "Unknown Pleasures Records GmbH", "Unknown Pleasures Records GmbH", "unknown-merchant"),
    ("receipt", "merchant", "Dr. Fritz Stamm", "Dr. Fritz Stamm", "person-like-merchant"),
    ("luggage_report", "airline", "Aero Valmont", "Aero Valmont", "airline"),
    ("luggage_report", "airline", "Aero Valmont", "Customer no. Aero Valmont", "airline-bad-label"),
    ("repair_invoice", "line_items", "[]", "x", "line-items"),
    ("repair_invoice", "description", "Rohrreinigung", "Subtotal Rohrreinigung", "line-description"),
    ("repair_invoice", "quantity", "2", "Tel. 2", "line-quantity"),
]  # fmt: skip

EXPECTED_KINDS = {
    "invoice_number": "id",
    "quote_number": "id",
    "booking_reference": "id",
    "report_number": "id",
    "report_reference": "id",
    "invoice_date": "date",
    "quote_date": "date",
    "departure_date": "date",
    "treatment_date": "date",
    "incident_date": "date",
    "purchase_date": "date",
    "cancellation_date": "date",
    "unfit_from": "date",
    "total": "amount",
    "trip_price": "amount",
    "amount_paid": "amount",
    "refund_received": "amount",
    "provider": "organisation",
    "patient_name": "person",
    "customer_name": "person",
    "traveller_name": "person",
}
OUT_OF_SCOPE_NAMES = {
    "cause",
    "diagnosis",
    "offence",
    "item_description",
    "damage_description",
    "line_items",
    "repair_company",
    "merchant",
    "airline",
    "description",
    "quantity",
    "unit_price",
    "amount",
}


@pytest.mark.parametrize(
    ("doc", "name", "value", "text", "why"),
    [pytest.param(*row, id=f"{row[1]}|{row[-1]}") for row in OUT_OF_SCOPE],
)
def test_an_out_of_scope_field_is_never_downgraded(
    doc: str, name: str, value: Any, text: str, why: str
) -> None:
    snippet = value if isinstance(value, str) else "x"
    assert verified(doc, name, value, snippet, text) is True


def test_the_safeguarded_fields_and_their_kinds() -> None:
    assert dict(SAFEGUARDED) == EXPECTED_KINDS


def test_out_of_scope_fields_are_not_safeguarded() -> None:
    assert not OUT_OF_SCOPE_NAMES & set(SAFEGUARDED)


def test_every_safeguarded_field_exists_in_a_schema() -> None:
    known = {n for schema in SCHEMAS.values() for n in schema.model_fields}
    assert set(SAFEGUARDED) <= known


def test_every_schema_field_outside_the_scope_gives_true() -> None:
    for kind, schema in SCHEMAS.items():
        for name in schema.model_fields:
            if name not in SAFEGUARDED:
                value: Any = [] if name == "line_items" else "anything"
                assert verified(kind.value, name, value, "anything", "other") is True


# ---------------------------------------------------------------------------
# 10. Snippet association
# ---------------------------------------------------------------------------

T1 = f"Invoice date: {DS}\nDelivery: {DS}"
T2 = f"Invoice date: {DS}\nValid until: {DS}"
ASSOCIATION: list[tuple[str, str, Any, str, str, bool, str]] = [
    # (doc, field, value, snippet, text, expected, id)
    (
        "repair_invoice", "invoice_date", D, f"Invoice date: {DS}", T1, True,
        "label-in-snippet-wins-over-other-occurrence",
    ),
    (
        "repair_invoice", "invoice_date", D, DS, T1, True,
        # bare snippet, two occurrences: one MATCH, one NONE (a delivery date is
        # not a veto): the rule is >= 1 MATCH and 0 VETO
        "bare-one-match-one-none",
    ),
    (
        "repair_invoice", "invoice_date", D, f"Delivery: {DS}", T1, False,
        # the label on the page elsewhere does not count: the occurrence is the
        # one of the snippet
        "snippet-with-other-label",
    ),
    (
        "repair_invoice", "invoice_date", D, DS, T2, False,
        # one occurrence is a VETO: the bare value is ambiguous
        "bare-match-and-veto",
    ),
    (
        "repair_invoice", "invoice_date", D, f"Invoice date: {DS}", T2, True,
        "snippet-pins-match-despite-veto-elsewhere",
    ),
    (
        "repair_invoice", "invoice_date", D, f"Valid until: {DS}", T2, False,
        "snippet-pins-veto",
    ),
    (
        "medical_invoice", "invoice_date", D, DS,
        f"Behandlungsdatum: {DS}\nRechnungsdatum: {DS}", True,
        # a label of another legitimate date (treatment) is not a veto
        "treatment-date-label-is-not-a-veto",
    ),
    (
        "repair_invoice", "total", 250.0, "250.00",
        "Total: 250.00\nSubtotal: 1,250.00", True,
        # "250.00" inside "1,250.00" is not an occurrence (whole numbers)
        "whole-numbers-only",
    ),
    (
        "repair_invoice", "total", 250.0, "250.00",
        "Subtotal: 250.00\nTotal: 1,250.00", False,
        "whole-numbers-only-veto-remains",
    ),
    (
        "repair_invoice", "invoice_number", "AB-4471", "AB-4471",
        "Invoice   No.:\n   ab-4471", True,
        "case-and-spaces-are-normalised",
    ),
    (
        "repair_invoice", "invoice_number", "4471", "Invoice no.: 4471",
        "Invoice no.:      4471", True,
        "spaces-collapse-in-the-snippet-match",
    ),
    (
        "repair_invoice", "invoice_number", "4471", "invoice NO.: 4471",
        "Invoice no.: 4471", True,
        "snippet-case-differs-from-the-page",
    ),
    (
        "booking_confirmation", "booking_reference", "PLQ7MX",
        "Customer no.: K-1\nBooking reference: PLQ7MX",
        "Customer no.: K-1\nBooking reference: PLQ7MX", True,
        "several-labels-in-the-snippet-the-last-counts",
    ),
    (
        "booking_confirmation", "booking_reference", "K-1",
        "Customer no.: K-1\nBooking reference: PLQ7MX",
        "Customer no.: K-1\nBooking reference: PLQ7MX", False,
        "several-labels-in-the-snippet-value-after-the-first",
    ),
    (
        "repair_invoice", "invoice_number", "4471", "Invoice no. 4471",
        "Customer no. 4471\nInvoice no. 4471\nCustomer no. 4471", True,
        "snippet-pins-one-occurrence-among-three",
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("doc", "name", "value", "snippet", "text", "expected", "why"),
    [pytest.param(*row, id=row[-1]) for row in ASSOCIATION],
)
def test_the_label_belongs_to_the_occurrence_of_the_snippet(
    doc: str,
    name: str,
    value: Any,
    snippet: str,
    text: str,
    expected: bool,
    why: str,
) -> None:
    assert verified(doc, name, value, snippet, f"{HEAD}\n{text}") is expected


# ---------------------------------------------------------------------------
# 9. The reader, on real PDFs and a real scan, with a stubbed transport
# ---------------------------------------------------------------------------

URL = "https://vlm.synthetic.test/v1"
Op = tuple[int, int, str]


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "dummy-key-for-the-stub")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "stub-model")


class Transport:
    """Answers each request with the next reply (a JSON dict). Any request that the
    test did not announce raises."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies: Iterator[dict[str, Any]] = iter(replies)
        self.requests: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/chat/completions"
        self.requests.append(json.loads(request.content))
        try:
            reply = next(self.replies)
        except StopIteration:
            raise AssertionError(
                "an unexpected request reached the transport"
            ) from None
        content = json.dumps(reply)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    def reader(self) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(client=client, sleep=lambda seconds: None)


def pdf(*pages: list[Op]) -> bytes:
    """A real PDF with a text layer: one string per operation."""
    buffer = BytesIO()
    page = canvas.Canvas(buffer)
    for ops in pages:
        page.setFont("Helvetica", 11)
        for x, y, text in ops:
            page.drawString(x, y, text)
        page.showPage()
    page.save()
    return buffer.getvalue()


def scan() -> bytes:
    """A photo of a page: pixels only, no text layer."""
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(
        ["Holzwerk Pfenninger GmbH", "Rechnung Nr. 5512", "Gesamtbetrag 1'280.00"]
    ):
        draw.text((60, 80 + 40 * i), line, fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def answer(kind: str, **given: tuple[Any, str]) -> dict[str, Any]:
    """The page answer of the model: every key of the schema, null when not given."""
    fields = SCHEMAS[DocumentType(kind)].model_fields
    values: dict[str, Any] = {n: None for n in fields}
    if "line_items" in values:
        values["line_items"] = []
    snippets: dict[str, str] = {}
    for name, (value, snippet) in given.items():
        values[name] = value.isoformat() if isinstance(value, date) else value
        snippets[name] = snippet
    return {"values": values, "snippets": snippets}


def read(data: bytes, kind: str, *answers: dict[str, Any]) -> dict[str, ExtractedField]:
    transport = Transport({"document_type": kind}, *answers)
    document = Document(id="D-synth", file="x", pages=max(1, len(answers)))
    out = transport.reader().extract(document, data, "synthetic")
    assert out.document_type == kind
    assert len(transport.requests) == 1 + len(answers)  # classify, then each page
    return {f.name: f for f in out.fields}


def check_field(
    data: bytes, kind: str, name: str, value: Any, snippet: str, expected: float
) -> None:
    fields = read(data, kind, answer(kind, **{name: (value, snippet)}))
    assert name in fields, "a checked field must be stored, never dropped"
    stored = fields[name]
    assert stored.confidence == expected
    assert stored.value == (value.isoformat() if isinstance(value, date) else value)
    assert stored.source.document_id == "D-synth"
    assert stored.source.page == 1
    assert stored.source.snippet == snippet.strip()


# (a) a two-column header block, German
LAYOUT_A = pdf(
    [
        (50, 770, "Holzwerk Pfenninger GmbH"),
        (50, 755, "Saegereistrasse 9, 6217 Kottwil"),
        (50, 720, "Rechnung Nr. 5512"),
        (300, 720, "Kunden-Nr. 9904"),
        (50, 700, "Rechnungsdatum 03.06.2027     Zahlbar bis 03.07.2027"),
        (50, 670, "Kunde: Corinne Aebischer"),
        (50, 640, "Gesamtbetrag CHF 1'280.00"),
        (50, 625, "Anzahlung CHF 400.00"),
        (50, 600, "Referenz: n/a"),
        (50, 585, "Auftrag: unbekannt"),
    ]
)
# (b1) a table of cells, each label cell above its value cell, Italian
LAYOUT_B1 = pdf(
    [
        (50, 770, "Vetrerie Calanchini SA"),
        (50, 755, "Via Ronco 14, 6900 Lugano"),
        (50, 720, "Numero preventivo"),
        (220, 720, "PV-30871"),
        (50, 700, "Data preventivo"),
        (220, 700, "08.06.2027"),
        (50, 680, "Valido fino al"),
        (220, 680, "08.07.2027"),
        (50, 660, "Totale"),
        (220, 660, "2'300.00"),
        (50, 640, "Cliente"),
        (220, 640, "Gianni Terragni"),
    ]
)
# (b2) column headers, then the values two lines below their own header
LAYOUT_B2 = pdf(
    [
        (50, 770, "Autospritzwerk Kunz AG"),
        (50, 700, "Quote no."),
        (220, 700, "Date"),
        (380, 700, "Total"),
        (50, 685, "QT-5507"),
        (220, 685, "11.06.2027"),
        (380, 685, "980.00"),
    ]
)
# (b3) a header row and a value row, one string each
LAYOUT_B3 = pdf(
    [
        (50, 770, "Autospritzwerk Kunz AG"),
        (50, 700, "Quote no.     Date     Total"),
        (50, 685, "QT-5508     12.06.2027     1'450.00"),
    ]
)
# (c) value before label
LAYOUT_C = pdf(
    [
        (50, 770, "Alpenwind Reisen AG"),
        (50, 740, "V8KD2M   Booking reference"),
        (50, 725, "14.09.2027   Departure date"),
        (50, 710, "3'450.00   Total price"),
        (50, 680, "Lena Fankhauser"),
    ]
)
# (d) a French invoice
LAYOUT_D = pdf(
    [
        (50, 770, "Atelier Mécanique Dubuis Sàrl"),
        (50, 755, "Route des Vignes 3, 1196 Gland"),
        (50, 720, "Facture N° 2027-0418"),
        (50, 705, "N° client : CL-5527"),
        (50, 690, "Date de facture : 14.06.2027"),
        (50, 675, "Date d'échéance : 14.07.2027"),
        (50, 660, "Client : Mélanie Vauthey"),
        (50, 630, "Sous-total CHF 2'900.00"),
        (50, 615, "TVA 8.1 % CHF 234.90"),
        (50, 600, "Montant total : CHF 3'134.90"),
    ]
)
# (e) an Italian medical invoice
LAYOUT_E = pdf(
    [
        (50, 770, "Ambulatorio Medico Santa Lucia"),
        (50, 755, "Via Besso 22, 6900 Lugano"),
        (50, 720, "Fattura n. 311/27"),
        (50, 705, "Data fattura 21.06.2027"),
        (50, 690, "Paziente: Rocco Bernasconi"),
        (50, 675, "Medico: Dott.ssa Greta Pallanza"),
        (50, 650, "Totale CHF 187.40"),
    ]
)

V, U = VERIFIED, UNVERIFIED
READER_CASES: list[tuple[str, bytes, str, str, Any, str, float, str]] = [
    # (layout, pdf, kind, field, value, snippet, expected confidence, id)
    ("A", LAYOUT_A, "repair_invoice", "invoice_number", "5512", "5512", V, "a-invoice-bare"),
    ("A", LAYOUT_A, "repair_invoice", "invoice_number", "5512", "Rechnung Nr. 5512", V, "a-invoice-label"),
    ("A", LAYOUT_A, "repair_invoice", "invoice_number", "9904", "9904", U, "a-customer-no-as-invoice"),
    ("A", LAYOUT_A, "repair_invoice", "invoice_number", "9904", "Kunden-Nr. 9904", U, "a-customer-no-with-label"),
    ("A", LAYOUT_A, "repair_invoice", "invoice_date", date(2027, 6, 3), "03.06.2027", V, "a-invoice-date"),
    ("A", LAYOUT_A, "repair_invoice", "invoice_date", date(2027, 7, 3), "03.07.2027", U, "a-due-date-as-invoice-date"),
    ("A", LAYOUT_A, "repair_invoice", "total", 1280.0, "1'280.00", V, "a-total-bare"),
    ("A", LAYOUT_A, "repair_invoice", "total", 1280.0, "Gesamtbetrag CHF 1'280.00", V, "a-total-label"),
    ("A", LAYOUT_A, "repair_invoice", "total", 400.0, "400.00", U, "a-deposit-as-total"),
    ("A", LAYOUT_A, "repair_invoice", "total", 400.0, "Anzahlung CHF 400.00", U, "a-deposit-with-label"),
    ("A", LAYOUT_A, "repair_invoice", "customer_name", "Corinne Aebischer", "Corinne Aebischer", V, "a-customer"),
    ("A", LAYOUT_A, "repair_invoice", "customer_name", "Holzwerk Pfenninger GmbH", "Holzwerk Pfenninger GmbH", U, "a-company-as-customer"),
    ("A", LAYOUT_A, "repair_invoice", "repair_company", "Holzwerk Pfenninger GmbH", "Holzwerk Pfenninger GmbH", V, "a-company-out-of-scope"),
    ("B1", LAYOUT_B1, "repair_quote", "quote_number", "PV-30871", "PV-30871", V, "b1-quote-number"),
    ("B1", LAYOUT_B1, "repair_quote", "quote_date", date(2027, 6, 8), "08.06.2027", V, "b1-quote-date"),
    ("B1", LAYOUT_B1, "repair_quote", "quote_date", date(2027, 7, 8), "08.07.2027", U, "b1-valid-until-as-quote-date"),
    ("B1", LAYOUT_B1, "repair_quote", "total", 2300.0, "2'300.00", V, "b1-total"),
    ("B1", LAYOUT_B1, "repair_quote", "customer_name", "Gianni Terragni", "Gianni Terragni", V, "b1-customer"),
    ("B1", LAYOUT_B1, "repair_quote", "customer_name", "Vetrerie Calanchini SA", "Vetrerie Calanchini SA", U, "b1-company-as-customer"),
    # the cell of a value is two lines under its own header: the line above is another
    # cell, so no label stands before the value: unverified, never dropped
    ("B2", LAYOUT_B2, "repair_quote", "quote_number", "QT-5507", "QT-5507", U, "b2-quote-number-under-total"),
    ("B2", LAYOUT_B2, "repair_quote", "quote_date", date(2027, 6, 11), "11.06.2027", U, "b2-quote-date"),
    ("B2", LAYOUT_B2, "repair_quote", "total", 980.0, "980.00", U, "b2-total"),
    ("C", LAYOUT_C, "booking_confirmation", "booking_reference", "V8KD2M", "V8KD2M", U, "c-reference-bare"),
    ("C", LAYOUT_C, "booking_confirmation", "booking_reference", "V8KD2M", "V8KD2M   Booking reference", U, "c-reference-label-after"),
    ("C", LAYOUT_C, "booking_confirmation", "departure_date", date(2027, 9, 14), "14.09.2027", U, "c-date-bare"),
    ("C", LAYOUT_C, "booking_confirmation", "departure_date", date(2027, 9, 14), "14.09.2027   Departure date", U, "c-date-label-after"),
    ("C", LAYOUT_C, "booking_confirmation", "trip_price", 3450.0, "3'450.00", U, "c-price-bare"),
    ("C", LAYOUT_C, "booking_confirmation", "trip_price", 3450.0, "3'450.00   Total price", U, "c-price-label-after"),
    ("C", LAYOUT_C, "booking_confirmation", "traveller_name", "Lena Fankhauser", "Lena Fankhauser", V, "c-traveller"),
    ("C", LAYOUT_C, "booking_confirmation", "traveller_name", "Alpenwind Reisen AG", "Alpenwind Reisen AG", U, "c-company-as-traveller"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_number", "2027-0418", "Facture N° 2027-0418", V, "d-invoice-label"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_number", "2027-0418", "2027-0418", V, "d-invoice-bare"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_number", "CL-5527", "N° client : CL-5527", U, "d-client-no-with-label"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_number", "CL-5527", "CL-5527", U, "d-client-no-bare"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_date", date(2027, 6, 14), "14.06.2027", V, "d-invoice-date-bare"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_date", date(2027, 6, 14), "Date de facture : 14.06.2027", V, "d-invoice-date-label"),
    ("D", LAYOUT_D, "repair_invoice", "invoice_date", date(2027, 7, 14), "14.07.2027", U, "d-due-date-bare"),
    ("D", LAYOUT_D, "repair_invoice", "total", 3134.9, "3'134.90", V, "d-total-bare"),
    ("D", LAYOUT_D, "repair_invoice", "total", 3134.9, "Montant total : CHF 3'134.90", V, "d-total-label"),
    ("D", LAYOUT_D, "repair_invoice", "total", 2900.0, "2'900.00", U, "d-subtotal-bare"),
    ("D", LAYOUT_D, "repair_invoice", "total", 2900.0, "Sous-total CHF 2'900.00", U, "d-subtotal-label"),
    ("D", LAYOUT_D, "repair_invoice", "total", 234.9, "234.90", U, "d-vat-as-total"),
    ("D", LAYOUT_D, "repair_invoice", "customer_name", "Mélanie Vauthey", "Mélanie Vauthey", V, "d-customer"),
    ("D", LAYOUT_D, "repair_invoice", "customer_name", "Atelier Mécanique Dubuis Sàrl", "Atelier Mécanique Dubuis Sàrl", U, "d-company-as-customer"),
    ("D", LAYOUT_D, "repair_invoice", "repair_company", "Atelier Mécanique Dubuis Sàrl", "Atelier Mécanique Dubuis Sàrl", V, "d-company-out-of-scope"),
    ("E", LAYOUT_E, "medical_invoice", "invoice_number", "311/27", "Fattura n. 311/27", V, "e-invoice-label"),
    ("E", LAYOUT_E, "medical_invoice", "invoice_number", "311/27", "311/27", V, "e-invoice-bare"),
    ("E", LAYOUT_E, "medical_invoice", "invoice_date", date(2027, 6, 21), "Data fattura 21.06.2027", V, "e-date-label"),
    ("E", LAYOUT_E, "medical_invoice", "total", 187.4, "Totale CHF 187.40", V, "e-total-label"),
    ("E", LAYOUT_E, "medical_invoice", "total", 187.4, "187.40", V, "e-total-bare"),
    ("E", LAYOUT_E, "medical_invoice", "provider", "Ambulatorio Medico Santa Lucia", "Ambulatorio Medico Santa Lucia", V, "e-practice"),
    ("E", LAYOUT_E, "medical_invoice", "provider", "Dott.ssa Greta Pallanza", "Dott.ssa Greta Pallanza", U, "e-doctor-as-provider"),
    ("E", LAYOUT_E, "medical_invoice", "patient_name", "Rocco Bernasconi", "Rocco Bernasconi", V, "e-patient"),
    ("E", LAYOUT_E, "medical_invoice", "patient_name", "Ambulatorio Medico Santa Lucia", "Ambulatorio Medico Santa Lucia", U, "e-practice-as-patient"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("layout", "data", "kind", "name", "value", "snippet", "expected", "why"),
    [pytest.param(*row, id=row[-1]) for row in READER_CASES],
)
def test_the_reader_keeps_the_right_fields_verified_and_downgrades_the_rest(
    layout: str,
    data: bytes,
    kind: str,
    name: str,
    value: Any,
    snippet: str,
    expected: float,
    why: str,
) -> None:
    check_field(data, kind, name, value, snippet, expected)


B3_CASES = [
    ("quote_number", "QT-5508", "QT-5508"),
    ("quote_date", date(2027, 6, 12), "12.06.2027"),
    ("total", 1450.0, "1'450.00"),
]


@pytest.mark.parametrize(
    ("name", "value", "snippet"),
    [
        pytest.param(
            *case,
            marks=known_limit(
                "a line above that holds several labels (column headers)"
            ),
            id=f"b3-{case[0]}",
        )
        for case in B3_CASES
    ],
)
def test_a_header_row_with_several_labels_above_a_value_row(
    name: str, value: Any, snippet: str
) -> None:
    check_field(LAYOUT_B3, "repair_quote", name, value, snippet, VERIFIED)


def test_a_misassigned_field_keeps_its_value_and_its_source() -> None:
    fields = read(
        LAYOUT_A,
        "repair_invoice",
        answer(
            "repair_invoice",
            invoice_number=("9904", "Kunden-Nr. 9904"),
            invoice_date=(date(2027, 7, 3), "03.07.2027"),
            total=(400.0, "Anzahlung CHF 400.00"),
            customer_name=("Corinne Aebischer", "Corinne Aebischer"),
        ),
    )
    assert {n: f.confidence for n, f in fields.items()} == {
        "invoice_number": UNVERIFIED,
        "invoice_date": UNVERIFIED,
        "total": UNVERIFIED,
        "customer_name": VERIFIED,
    }
    assert fields["invoice_number"].value == "9904"
    assert fields["invoice_date"].value == "2027-07-03"
    assert fields["total"].value == 400.0
    assert fields["invoice_number"].source.snippet == "Kunden-Nr. 9904"


@pytest.mark.parametrize(
    ("name", "value", "snippet"),
    [
        ("invoice_number", "n/a", "Referenz: n/a"),
        ("customer_name", "unbekannt", "unbekannt"),
        ("customer_name", "n/a", "n/a"),
        ("repair_company", "N/A", "n/a"),
    ],
)
def test_a_placeholder_is_never_stored(name: str, value: str, snippet: str) -> None:
    fields = read(
        LAYOUT_A, "repair_invoice", answer("repair_invoice", **{name: (value, snippet)})
    )
    assert name not in fields


def test_a_placeholder_is_skipped_while_the_real_fields_stay() -> None:
    fields = read(
        LAYOUT_A,
        "repair_invoice",
        answer(
            "repair_invoice",
            invoice_number=("5512", "Rechnung Nr. 5512"),
            customer_name=("unbekannt", "unbekannt"),
        ),
    )
    assert set(fields) == {"invoice_number"}
    assert fields["invoice_number"].confidence == VERIFIED


def two_pages(first: str, second: str) -> bytes:
    return pdf(
        [
            (50, 770, "Holzwerk Pfenninger GmbH"),
            (50, 740, "Rechnung Nr. 5512"),
            (50, 720, f"Kunde: {first}"),
        ],
        [
            (50, 770, "Holzwerk Pfenninger GmbH"),
            (50, 740, "Rechnung Nr. 5512"),
            (50, 720, f"Kunde: {second}"),
        ],
    )


def page_answer(customer: str) -> dict[str, Any]:
    return answer(
        "repair_invoice",
        invoice_number=("5512", "Rechnung Nr. 5512"),
        customer_name=(customer, customer),
    )


def test_a_placeholder_on_page_one_does_not_hide_the_real_value_of_page_two() -> None:
    fields = read(
        two_pages("unbekannt", "Corinne Aebischer"),
        "repair_invoice",
        page_answer("unbekannt"),
        page_answer("Corinne Aebischer"),
    )
    customer = fields["customer_name"]
    assert customer.value == "Corinne Aebischer"
    assert customer.source.page == 2
    assert customer.confidence == VERIFIED  # no disagreement was made by a placeholder
    assert fields["invoice_number"].confidence == VERIFIED


def test_a_placeholder_on_page_two_does_not_disturb_the_real_value_of_page_one() -> (
    None
):
    fields = read(
        two_pages("Corinne Aebischer", "n/a"),
        "repair_invoice",
        page_answer("Corinne Aebischer"),
        page_answer("n/a"),
    )
    customer = fields["customer_name"]
    assert customer.value == "Corinne Aebischer"
    assert customer.source.page == 1
    assert customer.confidence == VERIFIED


def test_placeholders_on_every_page_store_nothing() -> None:
    fields = read(
        two_pages("n/a", "unbekannt"),
        "repair_invoice",
        page_answer("n/a"),
        page_answer("unbekannt"),
    )
    assert "customer_name" not in fields
    assert fields["invoice_number"].confidence == VERIFIED


def test_a_scan_gives_unverified_for_every_field_and_stores_no_placeholder() -> None:
    kind = "repair_invoice"
    fields = read(
        scan(),
        kind,
        answer(
            kind,
            invoice_number=("5512", "Rechnung Nr. 5512"),
            customer_name=("Corinne Aebischer", "Corinne Aebischer"),
            total=(1280.0, "Gesamtbetrag 1'280.00"),
            invoice_date=(date(2027, 6, 3), "03.06.2027"),
            repair_company=("n/a", "n/a"),
        ),
    )
    assert set(fields) == {"invoice_number", "customer_name", "total", "invoice_date"}
    for field in fields.values():
        assert field.confidence == UNVERIFIED
        assert field.source.box is not None  # the whole page, a person checks it


def test_no_text_layer_means_no_label_check_even_for_a_wrong_assignment() -> None:
    kind = "repair_invoice"
    fields = read(
        scan(),
        kind,
        answer(kind, invoice_number=("9904", "Kunden-Nr. 9904")),
    )
    assert fields["invoice_number"].confidence == UNVERIFIED
    assert fields["invoice_number"].value == "9904"
