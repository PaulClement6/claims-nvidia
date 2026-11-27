"""The safeguards of the reader (issue #125): placeholders and label checks.

Only the page text and the snippet are used: no model, no network. The page texts
are written the way pypdf gives them (a label on one line, its value on the next)
or as one line ("Invoice no.: 4471"), in English, German, French and Italian.
Free text fields (cause, diagnosis, offence, ...) are out of scope: no test here
claims that a meaning is verified.
"""

from datetime import date
from pathlib import Path

import pytest

from app.docintel.labels import PLACEHOLDERS, SAFEGUARDED, is_placeholder, verified

# ---------------------------------------------------------------- placeholders


@pytest.mark.parametrize(
    "text",
    [
        "Not answerable",
        "not answerable",
        "  NOT   ANSWERABLE ",
        "null",
        "NULL",
        "None",
        "n/a",
        "N/A",
        "N/A.",
        "n.a.",
        "k.A.",
        "-",
        "--",
        "—",
        "unknown",
        "Not found",
        "not available",
        "Keine Angabe",
        "unbekannt",
        "Nicht verfügbar",
        "inconnu",
        "Non disponible",
        "non communiqué",
        "sconosciuto",
        "non applicabile",
        '"n/a"',
        "'null'",
        "(not answerable)",
        "[unknown]",
        "not answerable.",
    ],
)
def test_a_placeholder_text_is_a_placeholder(text: str) -> None:
    assert is_placeholder(text)


@pytest.mark.parametrize(
    "value",
    [
        0,
        0.0,
        1840.0,
        True,
        False,
        None,
        date(2026, 8, 10),
        "",
        "   ",
        "0",
        "NA-4471",
        "Nadia Nunez",
        "Not answerable today?",
        "The refund is not answerable by us",
        "n/a-4471",
        "None of the above items",
        "Nullmann AG",
        "Unknown Street 5",
        "X",
        "N",
        "WPR-482913",
        "2026-08-10",
        "Anna Keller",
    ],
)
def test_a_real_value_is_not_a_placeholder(value: object) -> None:
    assert not is_placeholder(value)


def test_the_placeholders_are_normalised_texts() -> None:
    assert "not answerable" in PLACEHOLDERS and "null" in PLACEHOLDERS
    assert all(p == p.casefold() and p == p.strip() for p in PLACEHOLDERS)
    assert not any(len(p) == 1 and p.isalpha() for p in PLACEHOLDERS)


# ------------------------------------------------------------------ the fields


def test_the_safeguarded_fields_have_a_kind() -> None:
    assert set(SAFEGUARDED.values()) == {
        "id",
        "date",
        "amount",
        "organisation",
        "person",
    }
    for name in (
        "cause",
        "diagnosis",
        "offence",
        "item_description",
        "damage_description",
        "line_items",
        "repair_company",
        "merchant",
        "airline",
    ):
        assert name not in SAFEGUARDED


def test_a_field_out_of_scope_is_never_downgraded() -> None:
    assert verified("medical_report", "diagnosis", "sprained ankle", "sprained", "x")
    assert verified("repair_invoice", "repair_company", "Dr. Muster", "Dr. Muster", "")
    assert verified("police_report", "offence", "burglary", "Burglary", "Burglary")


# ------------------------------------------------------------------------- ids

INVOICE = (
    "Blaubach AG\nMarco Bianchi\nDate:\n2026-09-22\nInvoice no.:\nBSU-2026-1187\n"
    "Customer no.:\nK-30418\nInvoice\nJob: water damage\nTotal\nCHF 3,200.00\n"
)


def invoice_number(snippet: str, value: str, text: str = INVOICE) -> bool:
    return verified("repair_invoice", "invoice_number", value, snippet, text)


def test_an_invoice_number_with_its_label_above_is_verified() -> None:
    assert invoice_number("BSU-2026-1187", "BSU-2026-1187")


def test_an_invoice_number_with_its_label_in_the_snippet_is_verified() -> None:
    assert invoice_number("Invoice no.:\nBSU-2026-1187", "BSU-2026-1187")
    assert invoice_number("Invoice no.: BSU-2026-1187", "BSU-2026-1187")


def test_a_customer_number_is_not_an_invoice_number() -> None:
    assert not invoice_number("K-30418", "K-30418")


def test_a_value_with_no_label_is_not_verified() -> None:
    assert not verified("repair_invoice", "invoice_number", "4471", "4471", "x\n4471\n")
    assert not verified("repair_invoice", "invoice_number", "4471", "4471", "4471")


@pytest.mark.parametrize(
    "line",
    [
        "Invoice no.: 4471",
        "Invoice number: 4471",
        "Invoice No 4471",
        "Inv. No.: 4471",
        "Inv. no. 4471",
        "Rechnungsnummer: 4471",
        "Rechnung Nr. 4471",
        "Rechnungs-Nr.: 4471",
        "Facture n° 4471",
        "N° de facture: 4471",
        "Numéro de facture 4471",
        "Fattura n. 4471",
        "Numero fattura: 4471",
        "Invoice 4471",
    ],
)
def test_the_label_of_an_invoice_number_in_four_languages(line: str) -> None:
    text = f"Header\nSome Firm AG\n{line}\nTotal\nCHF 10.00\n"
    assert invoice_number("4471", "4471", text)
    assert invoice_number(line, "4471", text)


@pytest.mark.parametrize(
    "line",
    [
        "Patient no.: 4471",
        "Customer no.: 4471",
        "Kundennummer: 4471",
        "Numéro client: 4471",
        "Numero cliente: 4471",
        "Insured no.: 4471",
        "Order no.: 4471",
        "Job no.: 4471",
        "VAT no.: 4471",
        "UID: 4471",
        "IBAN: 4471",
        "Tel.: 4471",
        "Quote no.: 4471",
        "Booking reference: 4471",
        "Date: 4471",
    ],
)
def test_a_misleading_label_is_not_an_invoice_number(line: str) -> None:
    text = f"Header\n{line}\n"
    assert not invoice_number("4471", "4471", text)
    assert not invoice_number(line, "4471", text)


def test_a_label_on_the_line_above_is_the_label_even_with_a_separator() -> None:
    text = "Firm\nInvoice no.\nX-77\n"
    assert invoice_number("X-77", "X-77", text)


def test_a_snippet_with_several_labels_uses_the_nearest_one() -> None:
    text = "Customer no.: K-1\nInvoice no.: INV-9\nBooking reference: WPR-1\n"
    assert invoice_number("Customer no.: K-1\nInvoice no.: INV-9", "INV-9", text)
    assert not invoice_number("Customer no.: K-1", "K-1", text)
    both = "Invoice no.: INV-9\nBooking reference: WPR-1"
    assert not invoice_number(both, "WPR-1", text)
    one_line = "Customer no.: K-1 Invoice no.: INV-9"
    assert invoice_number(one_line, "INV-9", one_line)
    assert not invoice_number(one_line, "K-1", one_line)


def test_a_repeated_value_with_contradicting_labels_is_not_verified() -> None:
    text = "Invoice no.:\n7788\nCustomer no.:\n7788\n"
    assert not invoice_number("7788", "7788", text)


def test_a_repeated_value_with_one_label_and_one_bare_occurrence_is_verified() -> None:
    text = "Invoice no.:\n7788\nPayable within 30 days\n7788\n"
    assert invoice_number("7788", "7788", text)


def test_the_evidence_is_the_snippet_occurrence_not_any_label_on_the_page() -> None:
    """The value "K-30418" is never an invoice number, whatever else the page says."""
    text = "Invoice no.:\nBSU-1\nCustomer no.:\nK-30418\n"
    assert not invoice_number("K-30418", "K-30418", text)
    assert invoice_number("BSU-1", "BSU-1", text)


@pytest.mark.parametrize(
    "value",
    [
        "sent separately by e-mail",
        "follows by post",
        "pending",
        "Pending",
        "to follow",
        "will be sent later.",
        "see attachment",
        "TBD",
        "folgt per Post",
        "à suivre",
    ],
)
def test_prose_is_not_an_identifier(value: str) -> None:
    text = f"Booking reference:\n{value}\n"
    assert not verified("booking_confirmation", "booking_reference", value, value, text)


@pytest.mark.parametrize(
    "value",
    ["XKQZPL", "ABC-DEF", "ABCDEF-GH", "2026/INV/0042", "AB 12 CD", "WPR-482913", "A1"],
)
def test_an_identifier_with_a_label_is_verified_whatever_its_shape(value: str) -> None:
    text = f"Booking reference:\n{value}\n"
    assert verified("booking_confirmation", "booking_reference", value, value, text)


@pytest.mark.parametrize(
    ("name", "kind", "label"),
    [
        ("quote_number", "repair_quote", "Quote no.:"),
        ("quote_number", "repair_quote", "Offerte Nr.:"),
        ("quote_number", "repair_quote", "Numéro de devis:"),
        ("quote_number", "repair_quote", "Preventivo n.:"),
        ("booking_reference", "booking_confirmation", "Booking reference:"),
        ("booking_reference", "cancellation_confirmation", "Buchungsnummer:"),
        ("booking_reference", "booking_confirmation", "Réservation:"),
        ("booking_reference", "booking_confirmation", "Prenotazione n.:"),
        ("booking_reference", "booking_confirmation", "PNR:"),
        ("report_number", "police_report", "Report no.:"),
        ("report_number", "police_report", "Anzeige-Nr.:"),
        ("report_number", "police_report", "Rapport n°:"),
        ("report_number", "police_report", "Denuncia n.:"),
        ("report_reference", "luggage_report", "Report reference:"),
        ("report_reference", "luggage_report", "Referenz:"),
        ("report_reference", "luggage_report", "PIR:"),
        ("report_reference", "luggage_report", "Riferimento:"),
    ],
)
def test_the_label_of_each_identifier_field(name: str, kind: str, label: str) -> None:
    text = f"Firm\n{label}\nQ-5521-7\nTel.: 044\n"
    assert verified(kind, name, "Q-5521-7", "Q-5521-7", text)


def test_an_identifier_of_another_field_is_not_this_field() -> None:
    text = "Booking reference:\nWPR-1\nQuote no.:\nQ-2\n"
    assert not verified("repair_quote", "quote_number", "WPR-1", "WPR-1", text)
    assert not verified("booking_confirmation", "booking_reference", "Q-2", "Q-2", text)
    assert verified("repair_quote", "quote_number", "Q-2", "Q-2", text)


# ----------------------------------------------------------------------- dates

DOC = (
    "Firm AG\nDate:\n2026-09-22\nSite visits:\n2026-09-15 to 2026-09-21\n"
    "Valid until:\n2026-11-12\nDue date:\n2026-10-22\nDate of birth:\n1988-04-12\n"
)


def invoice_date(snippet: str, value: date, text: str = DOC) -> bool:
    return verified("repair_invoice", "invoice_date", value, snippet, text)


def test_a_generic_date_label_is_the_date_of_the_document() -> None:
    assert invoice_date("2026-09-22", date(2026, 9, 22))
    assert invoice_date("Date: 2026-09-22", date(2026, 9, 22))


@pytest.mark.parametrize(
    ("snippet", "value"),
    [
        ("2026-11-12", date(2026, 11, 12)),  # valid until
        ("2026-10-22", date(2026, 10, 22)),  # due date
        ("1988-04-12", date(1988, 4, 12)),  # birth
        ("2026-09-15", date(2026, 9, 15)),  # site visits
        ("2026-09-21", date(2026, 9, 21)),
    ],
)
def test_a_date_with_a_misleading_label_is_not_the_invoice_date(
    snippet: str, value: date
) -> None:
    assert not invoice_date(snippet, value)


@pytest.mark.parametrize(
    "line",
    [
        "Invoice date: 22.09.2026",
        "Date of invoice: 22.09.2026",
        "Rechnungsdatum: 22.09.2026",
        "Datum: 22.09.2026",
        "Date de facture: 22.09.2026",
        "Date: 22.09.2026",
        "Data fattura: 22.09.2026",
        "Data: 22.09.2026",
    ],
)
def test_the_label_of_an_invoice_date_in_four_languages(line: str) -> None:
    text = f"Firm\n{line}\n"
    assert invoice_date("22.09.2026", date(2026, 9, 22), text)
    assert invoice_date(line, date(2026, 9, 22), text)


def test_a_date_with_no_label_is_not_verified() -> None:
    text = "Bern,\n2026-08-03\nSome sentence\n"
    assert not invoice_date("2026-08-03", date(2026, 8, 3), text)


@pytest.mark.parametrize(
    ("name", "kind", "label"),
    [
        ("quote_date", "repair_quote", "Quote date:"),
        ("quote_date", "repair_quote", "Offertdatum:"),
        ("quote_date", "repair_quote", "Date:"),
        ("purchase_date", "receipt", "Date:"),
        ("purchase_date", "receipt", "Kaufdatum:"),
        ("purchase_date", "receipt", "Date d'achat:"),
        ("purchase_date", "receipt", "Data acquisto:"),
        ("cancellation_date", "cancellation_confirmation", "Cancellation date:"),
        ("cancellation_date", "cancellation_confirmation", "Cancelled on:"),
        ("cancellation_date", "cancellation_confirmation", "Storniert am:"),
        ("cancellation_date", "cancellation_confirmation", "Annulé le:"),
        ("cancellation_date", "cancellation_confirmation", "Annullato il:"),
        ("departure_date", "booking_confirmation", "Departure:"),
        ("departure_date", "booking_confirmation", "Abreise:"),
        ("departure_date", "booking_confirmation", "Départ:"),
        ("departure_date", "booking_confirmation", "Partenza:"),
        ("incident_date", "police_report", "Incident date:"),
        ("incident_date", "police_report", "Tatzeit:"),
        ("incident_date", "police_report", "Discovered:"),
        ("incident_date", "luggage_report", "Reported:"),
        ("incident_date", "police_report", "Date des faits:"),
        ("incident_date", "police_report", "Data del fatto:"),
        ("treatment_date", "medical_report", "Behandlung:"),
        ("treatment_date", "medical_report", "Date of treatment:"),
        ("treatment_date", "medical_report", "Consultation:"),
        ("treatment_date", "medical_report", "Data della visita:"),
        ("unfit_from", "medical_certificate", "Arbeitsunfähig ab:"),
        ("unfit_from", "medical_certificate", "Unfit from:"),
        ("unfit_from", "medical_certificate", "Incapacité de travail dès le:"),
        ("unfit_from", "medical_certificate", "Inabilità dal:"),
    ],
)
def test_the_label_of_each_date_field(name: str, kind: str, label: str) -> None:
    text = f"Firm AG\n{label}\n2026-03-04\nPayable within 30 days\n"
    assert verified(kind, name, date(2026, 3, 4), "2026-03-04", text)
    assert verified(kind, name, date(2026, 3, 4), f"{label} 2026-03-04", text)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("departure_date", "booking_confirmation"),
        ("incident_date", "police_report"),
        ("treatment_date", "medical_report"),
        ("unfit_from", "medical_certificate"),
    ],
)
def test_a_generic_date_label_is_not_enough_for_an_event_date(
    name: str, kind: str
) -> None:
    text = "Firm AG\nDate:\n2026-03-04\n"
    assert not verified(kind, name, date(2026, 3, 4), "2026-03-04", text)


def test_the_booking_date_is_not_the_departure() -> None:
    text = "Date:\n2026-06-02\nDeparture:\n2026-08-10\nReturn:\n2026-08-17\n"
    assert not verified(
        "booking_confirmation", "departure_date", date(2026, 6, 2), "2026-06-02", text
    )
    assert verified(
        "booking_confirmation", "departure_date", date(2026, 8, 10), "2026-08-10", text
    )
    assert not verified(
        "booking_confirmation", "departure_date", date(2026, 8, 17), "2026-08-17", text
    )


CERTIFICATE = (
    "Praxis am Lindenhof\nBern,\n2026-08-03\nMedical certificate\n"
    "I certify that I examined the patient Anna Keller (born 1988-04-12)\n"
    "on 2026-08-03 and found an acute illness.\n"
    "The patient is unfit to travel from 2026-08-03 until at least 2026-08-17.\n"
    "Bern, 2026-08-03\n"
)


def test_the_unfit_date_is_found_by_the_words_in_front_of_it() -> None:
    def check(snippet: str, value: date) -> bool:
        return verified(
            "medical_certificate", "unfit_from", value, snippet, CERTIFICATE
        )

    assert check("unfit to travel from 2026-08-03", date(2026, 8, 3))
    assert check("2026-08-03", date(2026, 8, 3))  # one of its occurrences has the label
    assert not check("until at least 2026-08-17", date(2026, 8, 17))
    assert not check("2026-08-17", date(2026, 8, 17))
    assert not check("(born 1988-04-12)", date(1988, 4, 12))
    assert not check("1988-04-12", date(1988, 4, 12))


def test_a_date_of_birth_is_never_an_event_date() -> None:
    text = "Patient: Lukas Meier (born 1991-03-08), examined on 2026-11-06.\n"
    assert verified(
        "medical_report", "treatment_date", date(2026, 11, 6), "2026-11-06", text
    )
    assert not verified(
        "medical_report", "treatment_date", date(1991, 3, 8), "1991-03-08", text
    )


# --------------------------------------------------------------------- amounts

TOTALS = (
    "Description\nQty\nUnit (CHF)\nAmount (CHF)\nReplace pipe\n1\n720.00\n720.00\n"
    "Subtotal\nCHF 2,960.00\nTotal (VAT included)\nCHF 3,200.00\n"
    "of which VAT 8.1%\nCHF 239.78\nDeposit\nCHF 500.00\nDiscount\n-40.00\n"
)


def total(snippet: str, value: float, text: str = TOTALS) -> bool:
    return verified("repair_invoice", "total", value, snippet, text)


def test_a_total_is_verified_by_its_label_above_or_in_the_snippet() -> None:
    assert total("CHF 3,200.00", 3200.0)
    assert total("3,200.00", 3200.0)
    assert total("Total (VAT included)\nCHF 3,200.00", 3200.0)
    assert total("Total CHF 3,200.00", 3200.0, "Total CHF 3,200.00\n")
    assert total("Total   CHF 3,200.00", 3200.0, "Item  1\nTotal   CHF 3,200.00\n")


@pytest.mark.parametrize(
    ("snippet", "value"),
    [
        ("CHF 2,960.00", 2960.0),  # subtotal
        ("CHF 239.78", 239.78),  # VAT
        ("CHF 500.00", 500.0),  # deposit
        ("-40.00", -40.0),  # discount
        ("720.00", 720.0),  # a line of the table
        ("1", 1.0),
    ],
)
def test_an_amount_with_another_meaning_is_not_the_total(
    snippet: str, value: float
) -> None:
    assert not total(snippet, value)


@pytest.mark.parametrize(
    "label",
    [
        "Total",
        "Total amount",
        "Total incl. VAT",
        "Total (VAT included)",
        "Grand total",
        "Amount due",
        "Gesamtbetrag",
        "Gesamttotal",
        "Rechnungsbetrag",
        "Total TTC",
        "Montant total",
        "Totale",
        "Totale fattura",
        "Importo totale",
    ],
)
def test_the_label_of_a_total_in_four_languages(label: str) -> None:
    text = f"Firm\n{label}\nCHF 99.50\nThank you\n"
    assert total("99.50", 99.5, text)
    assert total("CHF 99.50", 99.5, text)


@pytest.mark.parametrize(
    "label",
    [
        "Subtotal",
        "Zwischensumme",
        "Sous-total",
        "Subtotale",
        "VAT 8.1%",
        "MwSt.",
        "TVA 8.1%",
        "IVA",
        "Discount",
        "Rabatt",
        "Deposit",
        "Anzahlung",
        "Fee",
        "Service charge",
        "Gebuhr",
        "Total excl. VAT",
        "Previous balance",
    ],
)
def test_a_label_that_is_not_the_total(label: str) -> None:
    text = f"Firm\n{label}\nCHF 99.50\n"
    assert not total("99.50", 99.5, text)


@pytest.mark.parametrize(
    ("name", "kind", "label"),
    [
        ("trip_price", "booking_confirmation", "Total trip price"),
        ("trip_price", "booking_confirmation", "Reisepreis"),
        ("trip_price", "booking_confirmation", "Prix du voyage"),
        ("trip_price", "booking_confirmation", "Prezzo del viaggio"),
        ("trip_price", "booking_confirmation", "Trip price paid"),
        ("amount_paid", "receipt", "Total paid"),
        ("amount_paid", "receipt", "Total"),
        ("amount_paid", "receipt", "Betrag bezahlt"),
        ("amount_paid", "receipt", "Total payé"),
        ("amount_paid", "receipt", "Totale pagato"),
        ("refund_received", "cancellation_confirmation", "Refund to your card"),
        ("refund_received", "cancellation_confirmation", "Rückerstattung"),
        ("refund_received", "cancellation_confirmation", "Remboursement"),
        ("refund_received", "cancellation_confirmation", "Rimborso"),
    ],
)
def test_the_label_of_each_amount_field(name: str, kind: str, label: str) -> None:
    text = f"Firm AG\n{label}\nCHF 340.00\nKind regards\n"
    assert verified(kind, name, 340.0, "340.00", text)


def test_a_cancellation_fee_is_not_the_refund() -> None:
    text = (
        "Item\nTrip price paid\n1,840.00\n"
        "Cancellation fees (terms, art. 7)\n-1,500.00\n"
        "Refund to your card\n340.00\nThe refund of CHF 340.00 will be credited.\n"
    )
    assert verified(
        "cancellation_confirmation", "refund_received", 340.0, "340.00", text
    )
    assert not verified(
        "cancellation_confirmation", "refund_received", -1500.0, "-1,500.00", text
    )
    assert not verified(
        "cancellation_confirmation", "refund_received", 1840.0, "1,840.00", text
    )


def test_a_line_of_the_table_is_not_the_trip_price() -> None:
    text = "Return flights\n2\n420.00\n840.00\nTotal trip price\nCHF 1,840.00\n"
    assert verified("booking_confirmation", "trip_price", 1840.0, "CHF 1,840.00", text)
    assert not verified("booking_confirmation", "trip_price", 840.0, "840.00", text)
    assert not verified("booking_confirmation", "trip_price", 420.0, "420.00", text)


def test_a_cut_number_has_no_occurrence() -> None:
    text = "Total\nCHF 1,840.00\n"
    assert not verified("repair_invoice", "total", 840.0, "840.00", text)


def test_an_amount_in_a_table_row_has_no_label_from_the_row() -> None:
    text = "Replace pipe  1  720.00  720.00\nTotal  CHF 720.00\n"
    assert verified("repair_invoice", "total", 720.0, "Total  CHF 720.00", text)


# ----------------------------------------------------------------- organisation


def provider(value: str, text: str = "") -> bool:
    return verified("medical_invoice", "provider", value, value, text or value)


@pytest.mark.parametrize(
    "value",
    [
        "Praxis Dr. med. A. Muster",
        "Dr. Muster AG",
        "Gemeinschaftspraxis am See",
        "Medizinzentrum Seeblick AG",
        "Hausarztzentrum Seewinkel AG",
        "Gesundheitszentrum Limmatblick AG",
        "Praxis am Lindenhof",
        "Klinik Hirslanden",
        "Cabinet médical du Lac",
        "Studio medico Dott. Rossi",
        "Centre médical de Genève",
        "Hospital Zurich",
        "Ospedale Regionale",
        "Muster Zahnarztpraxis GmbH",
    ],
)
def test_a_clinic_or_a_practice_is_an_organisation(value: str) -> None:
    assert provider(value)


@pytest.mark.parametrize(
    "value",
    [
        "Dr. med. Corina Muster",
        "Dr. med. Corina Tschudi",
        "Dr. med. dent. Hans Meier",
        "Prof. Dr. med. Hans Meier",
        "Dr. Muster",
        "Dott.ssa Maria Rossi",
        "Dott. Luca Bianchi",
        "Dre Anne Dubois",
        "Docteur Jean Martin",
        "med. pract. Peter Frei",
        "PD Dr. med. Eva Roth",
        "Dr. med. B. Zingg",
    ],
)
def test_a_doctor_is_a_person_not_the_organisation(value: str) -> None:
    assert not provider(value)


def test_a_bare_name_after_a_person_label_is_a_person() -> None:
    text = "Medical report\nArzt:\nHans Muster\nDiagnosis: x\n"
    assert not provider("Hans Muster", text)
    assert not provider("Hans Muster", "Treating physician: Hans Muster\n")
    assert not provider("Hans Muster", "Medico: Hans Muster\n")
    assert not provider("Hans Muster", "Signature\nHans Muster\n")


def test_a_bare_name_with_no_title_and_no_person_label_is_kept_known_limit() -> None:
    """A sole trader may print only a name: nothing says it is a person."""
    assert provider("Hans Muster", "Hans Muster\nBahnhofstrasse 1\n")
    assert provider("Seeblick", "Seeblick\n")


def test_a_title_alone_on_an_organisation_is_not_a_reason() -> None:
    assert provider("Praxis Dr. med. A. Muster", "Praxis Dr. med. A. Muster\n")
    assert provider("Dr. Muster AG", "Arzt:\nDr. Muster AG\n")
    assert provider("Dr. Muster & Partner", "Dr. Muster & Partner\n")


def test_a_sole_trader_company_is_out_of_scope() -> None:
    assert verified("repair_invoice", "repair_company", "Dr. Muster", "Dr. Muster", "")
    assert verified(
        "receipt", "merchant", "Hans Muster", "Hans Muster", "Arzt: Hans Muster"
    )


# ---------------------------------------------------------------------- persons


@pytest.mark.parametrize(
    "value",
    [
        "Muster Elektro AG",
        "Tobler & Gerig Haustechnik AG",
        "Rietmann Bedachungen GmbH",
        "Zanetti Impianti SA",
        "Hausarztzentrum Seewinkel AG",
        "Praxis am Lindenhof",
        "Muster Sagl",
        "Muster Sàrl",
        "Muster Ltd",
    ],
)
def test_a_company_is_not_a_person_name(value: str) -> None:
    for name in ("customer_name", "patient_name", "traveller_name"):
        assert not verified("repair_invoice", name, value, value, value)


@pytest.mark.parametrize(
    "value",
    [
        "Anna Keller",
        "Irene Kaelin",
        "Jean-Pierre Dubois",
        "Maria de la Cruz",
        "Dr. Hans Meier",
        "Marco Bianchi",
        "Nadia Nunez",
        "O'Brien",
    ],
)
def test_a_person_name_is_kept(value: str) -> None:
    for name in ("customer_name", "patient_name", "traveller_name"):
        assert verified("repair_invoice", name, value, value, value)


# ------------------------------------------------------- text that sticks to a value


def test_a_label_inside_an_address_is_not_a_label() -> None:
    """ "rechnung@firma.example" holds the word "rechnung", but it is an e-mail."""
    text = "Tel. +41 44 555 04 30  |  rechnung@seeblick-medizin.example\n"
    assert not invoice_number(
        "seeblick-medizin.example", "seeblick-medizin.example", text
    )
    assert not verified("repair_quote", "quote_number", "q-1", "q-1", "offerte@q-1\n")
    assert not invoice_number("A-4471", "A-4471", "Invoice no. X-A-4471\n")


def test_a_number_followed_by_a_unit_is_not_an_amount() -> None:
    text = "The refund of CHF 340.00 will be credited within 10 working days.\n"
    assert not verified(
        "cancellation_confirmation", "refund_received", 10.0, "10", text
    )
    assert verified(
        "cancellation_confirmation", "refund_received", 340.0, "340.00", text
    )
    for tail in ("days", "nights", "%", "Tage", "jours", "giorni"):
        line = f"Refund: 5 {tail}\n"
        assert not verified(
            "cancellation_confirmation", "refund_received", 5.0, "5", line
        )
    assert verified(
        "cancellation_confirmation", "refund_received", 5.0, "5", "Refund: 5\n"
    )


def test_a_verb_after_the_amount_is_not_a_label_before_it() -> None:
    text = "The amount will be credited 340.00 later\n"
    assert not verified(
        "cancellation_confirmation", "refund_received", 340.0, "340.00", text
    )


# ------------------------------------------------- a label that names another thing


def test_a_bare_word_is_not_the_end_of_a_longer_label() -> None:
    cancelled = "Departure date:\n2026-08-10\n"
    assert not verified(
        "cancellation_confirmation",
        "cancellation_date",
        date(2026, 8, 10),
        "2026-08-10",
        cancelled,
    )
    text = "Sub total\nCHF 99.50\nNet total\nCHF 80.00\nTotal\nCHF 120.00\n"
    assert not total("99.50", 99.5, text)
    assert not total("80.00", 80.0, text)
    assert total("120.00", 120.0, text)


def test_a_line_that_has_its_own_value_is_not_the_label_of_the_next_line() -> None:
    text = "Arzt: Hans Muster\nPeter Frei\n"
    assert provider("Peter Frei", text)  # no label of its own: kept (known limit)
    assert not provider("Hans Muster", text)  # its own label is on its own line


def test_a_word_of_a_sentence_is_not_a_veto_but_a_label_is() -> None:
    text = (
        "Invoice no.:\n7788\nThank you for your order\n7788\n"
        "Vielen Dank fuer Ihren Auftrag\n7788\n"
    )
    assert invoice_number("7788", "7788", text)
    assert not invoice_number("7788", "7788", text + "Order no.:\n7788\n")
    assert not invoice_number("7788", "7788", text + "Auftragsnummer:\n7788\n")


@pytest.mark.parametrize("label", ["Delivery date", "Lieferdatum", "Site visits"])
def test_a_label_that_may_share_the_date_is_not_a_veto(label: str) -> None:
    """A delivery or a site visit can be on the day of the invoice."""
    text = f"Date:\n2026-09-22\n{label}:\n2026-09-22\n"
    assert invoice_date("2026-09-22", date(2026, 9, 22), text)


def test_totalbetrag_is_a_total() -> None:
    assert total("99.50", 99.5, "Totalbetrag\nCHF 99.50\n")


def test_case_and_spaces_of_the_page_do_not_count() -> None:
    text = "INVOICE   NO.:   ab-123 \n"
    assert invoice_number("ab-123", "AB-123", text)
    assert invoice_number("Invoice no.: AB-123", "ab-123", text)
    assert verified(
        "booking_confirmation",
        "booking_reference",
        "xkqzpl",
        "xkqzpl",
        "Booking Reference:  XKQZPL\n",
    )


def test_a_lower_case_word_with_vowels_is_prose_but_not_a_code() -> None:
    for word in ("pending", "later", "folgt"):
        text = f"Booking reference: {word}\n"
        assert not verified(
            "booking_confirmation", "booking_reference", word, word, text
        )


@pytest.mark.parametrize(
    "label", ["Client", "Numéro client", "Customer", "Kundennummer", "Patient"]
)
def test_a_name_alone_is_a_label_of_another_number(label: str) -> None:
    text = f"Invoice no.:\n7788\n{label}\n7788\n"
    assert not invoice_number("7788", "7788", text)


def test_a_name_in_the_middle_of_a_two_word_label_is_not_a_veto() -> None:
    text = "Invoice no.:\n7788\nCustomer service\n7788\n"
    assert invoice_number("7788", "7788", text)


# ----------------------------------------------- review fixes (#125, phase 2)
# F1: another value, or another party, between the keyword and the value


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("Invoice 1000, order 4471", "4471"),
        ("Rechnung F-100 Kunde K-9", "K-9"),
        ("Invoice for customer 4471", "4471"),
        ("Rechnung fuer Patient 4471", "4471"),
        ("Invoice for the order 4471", "4471"),
    ],
)
def test_another_value_or_party_between_the_keyword_and_the_value(
    line: str, value: str
) -> None:
    text = f"{line}\n"
    assert not invoice_number(value, value, text)
    assert not invoice_number(line, value, text)


def test_the_first_value_after_the_keyword_is_still_the_invoice_number() -> None:
    assert invoice_number("Invoice 1000", "1000", "Invoice 1000, order 4471\n")
    assert invoice_number("Rechnung F-100", "F-100", "Rechnung F-100 Kunde K-9\n")


def test_two_amounts_on_one_line_the_keyword_belongs_to_the_first() -> None:
    text = "Total CHF 100.00 / Selbstbehalt CHF 30.00\n"
    assert total("Total CHF 100.00", 100.0, text)
    assert not total("30.00", 30.0, text)
    assert not total("Total CHF 100.00 / Selbstbehalt CHF 30.00", 30.0, text)
    text = "Balance due 100.00 Paid 30.00\n"
    assert total("100.00", 100.0, text)
    assert not total("30.00", 30.0, text)


def test_a_percent_between_the_keyword_and_the_value_is_not_another_value() -> None:
    text = "x\nTotal 8.1% CHF 120.00\n"
    assert total("Total 8.1% CHF 120.00", 120.0, text)


@pytest.mark.parametrize(
    "line",
    [
        "{k} 2026-02-01 - 2026-03-01",
        "{k}: 2026-02-01 - 2026-03-01",
        "{k} from 2026-02-01 to 2026-03-01",
        "{k} 2026-02-01 bis 2026-03-01",
    ],
)
@pytest.mark.parametrize(
    ("name", "kind", "key"),
    [
        ("departure_date", "booking_confirmation", "Departure"),
        ("unfit_from", "medical_certificate", "Unfit from"),
        ("treatment_date", "medical_report", "Treatment"),
    ],
)
def test_the_second_date_of_a_range_is_not_the_date_of_the_label(
    name: str, kind: str, key: str, line: str
) -> None:
    text = line.format(k=key) + "\n"
    first, second = date(2026, 2, 1), date(2026, 3, 1)
    assert verified(kind, name, first, "2026-02-01", text)
    assert not verified(kind, name, second, "2026-03-01", text)
    assert not verified(kind, name, second, text.strip(), text)


# F2: "reported" is an incident word only as the whole label of a luggage report


@pytest.mark.parametrize(
    "label",
    [
        "Reported:",
        "Gemeldet:",
        "Signalé:",
        "Segnalato:",
        "Denunciato:",
        "REPORTED",
    ],
)
def test_a_bare_reported_label_is_the_incident_date_of_a_luggage_report(
    label: str,
) -> None:
    text = f"Baggage service\n{label}\n2026-05-14, at the arrival hall\n"
    value = date(2026, 5, 14)
    assert verified("luggage_report", "incident_date", value, "2026-05-14", text)
    snippet = f"{label} 2026-05-14"
    assert verified("luggage_report", "incident_date", value, snippet, text)


@pytest.mark.parametrize(
    "label",
    [
        "Date reported:",
        "Reported on:",
        "Reported on",
        "Gemeldet am:",
        "Datum gemeldet:",
        "Signalé le:",
        "Segnalato il:",
        "Reported by:",
    ],
)
@pytest.mark.parametrize("kind", ["luggage_report", "police_report"])
def test_reported_with_other_words_is_not_an_incident_date(
    label: str, kind: str
) -> None:
    text = f"Office\n{label}\n2026-05-14\n"
    value = date(2026, 5, 14)
    assert not verified(kind, "incident_date", value, "2026-05-14", text)
    assert not verified(kind, "incident_date", value, f"{label} 2026-05-14", text)


@pytest.mark.parametrize("label", ["Reported:", "Gemeldet:", "Segnalato:"])
def test_a_bare_reported_label_is_not_an_incident_date_of_other_documents(
    label: str,
) -> None:
    text = f"Office\n{label}\n2026-05-14\n"
    for kind in ("police_report", "damage_report", "medical_report"):
        value = date(2026, 5, 14)
        assert not verified(kind, "incident_date", value, "2026-05-14", text)


def test_a_bare_reported_label_is_only_for_the_incident_date() -> None:
    text = "Reported:\n2026-05-14\n"
    value = date(2026, 5, 14)
    assert not verified("luggage_report", "treatment_date", value, "2026-05-14", text)


# F3: text glued behind the value: the value is cut


@pytest.mark.parametrize(
    "page", ["Invoice no.: 4471-A\n", "Invoice no.: 4471/2\n", "Invoice no.:\n4471A\n"]
)
def test_a_value_cut_from_a_longer_identifier_has_no_label(page: str) -> None:
    assert not invoice_number("4471", "4471", page)
    assert not invoice_number("Invoice no.: 4471", "4471", page)


def test_the_whole_identifier_with_its_suffix_is_verified() -> None:
    assert invoice_number("4471-A", "4471-A", "Invoice no.: 4471-A\n")
    assert invoice_number("4471/2", "4471/2", "Invoice no.: 4471/2\n")


@pytest.mark.parametrize(
    "after", [" dated 2026-09-22", ",", ".", " - Zurich", ")", "\nDate:"]
)
def test_a_separator_behind_the_value_does_not_cut_it(after: str) -> None:
    assert invoice_number("4471", "4471", f"Invoice no.: 4471{after}\n")


# F4: general vocabulary


@pytest.mark.parametrize(
    "label",
    [
        "Total pages",
        "Total number of persons",
        "Total persons",
        "Total items",
        "Total quantity",
        "Total Anzahl Personen",
        "Total nombre de personnes",
        "Totale numero di persone",
        "Total no. of passengers",
    ],
)
def test_a_total_followed_by_a_count_noun_is_not_an_amount(label: str) -> None:
    text = f"Page 1\n{label}: 4\n"
    assert not total("4", 4.0, text)
    assert not total(f"{label}: 4", 4.0, text)


def test_a_total_price_for_persons_is_still_the_trip_price() -> None:
    text = "Total price for 2 persons: CHF 3,000.00\n"
    assert verified("booking_confirmation", "trip_price", 3000.0, "3,000.00", text)


@pytest.mark.parametrize(
    "label",
    [
        "Amount due",
        "Amount outstanding",
        "Outstanding amount",
        "Offener Betrag",
        "Betrag offen",
        "Montant dû",
        "Importo da pagare",
        "Balance due",
    ],
)
def test_an_amount_due_is_not_the_amount_paid(label: str) -> None:
    text = f"Receipt\n{label}: CHF 100.00\n"
    assert not verified("repair_invoice", "amount_paid", 100.0, "100.00", text)
    snippet = f"{label}: CHF 100.00"
    assert not verified("repair_invoice", "amount_paid", 100.0, snippet, text)


def test_an_amount_paid_is_still_the_amount_paid() -> None:
    for label in ("Amount paid", "Paid", "Bezahlt", "Montant payé", "Pagato"):
        text = f"Receipt\n{label}: CHF 100.00\n"
        assert verified("repair_invoice", "amount_paid", 100.0, "100.00", text), label


@pytest.mark.parametrize(
    "label",
    [
        "Refund requested",
        "Requested refund",
        "Refund claimed",
        "Rückerstattung beantragt",
        "Erstattung gefordert",
        "Remboursement demandé",
        "Rimborso richiesto",
    ],
)
def test_a_requested_refund_is_not_the_refund_received(label: str) -> None:
    text = f"Cancellation\n{label}: CHF 340.00\n"
    assert not verified(
        "cancellation_confirmation", "refund_received", 340.0, "340.00", text
    )


def test_a_refund_is_still_the_refund_received() -> None:
    for label in (
        "Refund",
        "Refund received",
        "Rückerstattung",
        "Remboursement",
        "Rimborso",
    ):
        text = f"Cancellation\n{label}: CHF 340.00\n"
        assert verified(
            "cancellation_confirmation", "refund_received", 340.0, "340.00", text
        ), label


@pytest.mark.parametrize(
    "label",
    [
        "Next visit",
        "Next examination",
        "Nächster Besuch",
        "Nächste Untersuchung",
        "Prochain examen",
        "Prossima visita",
    ],
)
def test_a_next_visit_is_not_the_treatment_date(label: str) -> None:
    text = f"Report\n{label}: 2026-04-01\n"
    value = date(2026, 4, 1)
    assert not verified("medical_report", "treatment_date", value, "2026-04-01", text)
    snippet = f"{label}: 2026-04-01"
    assert not verified("medical_report", "treatment_date", value, snippet, text)


# F5: the fallback of the criterion 5

REPORT = (
    "Medical centre AG\nThalwil,\n2026-10-25\nFile no.:\nP-1\nMedical report\n"
    "Patient: Lukas Meier (born 1991-03-08), examined on 2026-10-25.\n"
    "Thalwil, 2026-10-25\nDr. med. R. Haller\n"
)
POLICE = (
    "Police\nDate:\n2026-10-09\nReport no.:\nRPA-1\nDiscovered:\n"
    "2026-10-09, 07:30, when leaving for work\nBern, 2026-10-09\nWm T. Ammann\n"
)


@pytest.mark.parametrize(
    ("kind", "name", "snippet", "text"),
    [
        ("medical_report", "treatment_date", "Thalwil, 2026-10-25", REPORT),
        ("medical_report", "treatment_date", "Thalwil,\n2026-10-25", REPORT),
        ("medical_report", "treatment_date", "2026-10-25", REPORT),
        ("police_report", "incident_date", "Date: 2026-10-09", POLICE),
        (
            "police_report",
            "incident_date",
            "Datum: 2026-10-09",
            POLICE.replace("Date:", "Datum:"),
        ),
        (
            "police_report",
            "incident_date",
            "Data: 2026-10-09",
            POLICE.replace("Date:", "Data:"),
        ),
    ],
)
def test_a_neutral_snippet_falls_back_on_the_other_places_of_the_value(
    kind: str, name: str, snippet: str, text: str
) -> None:
    value = date.fromisoformat(snippet[-10:])
    assert verified(kind, name, value, snippet, text)


def test_the_fallback_needs_a_match_somewhere_on_the_page() -> None:
    text = "Medical centre AG\nThalwil, 2026-10-25\nDr. med. R. Haller\n"
    value = date(2026, 10, 25)
    assert not verified(
        "medical_report", "treatment_date", value, "Thalwil, 2026-10-25", text
    )


def test_the_fallback_is_blocked_by_a_veto_anywhere_on_the_page() -> None:
    value = date(2026, 10, 25)
    text = REPORT + "Date of birth: 2026-10-25\n"
    assert not verified(
        "medical_report", "treatment_date", value, "Thalwil, 2026-10-25", text
    )
    value = date(2026, 10, 9)
    text = POLICE + "Valid until: 2026-10-09\n"
    assert not verified(
        "police_report", "incident_date", value, "Date: 2026-10-09", text
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "Delivery: 2026-10-25",
        "Lieferung: 2026-10-25",
        "Invoice 2026-10-25",
        "Rechnung 2026-10-25",
        "Reference no. 2026-10-25",
        "Thalwil, Zurich: 2026-10-25",
    ],
)
def test_a_snippet_with_another_label_never_falls_back(snippet: str) -> None:
    text = REPORT + "Delivery: 2026-10-25\nLieferung: 2026-10-25\nInvoice 2026-10-25\n"
    text += (
        "Rechnung 2026-10-25\nReference no. 2026-10-25\nThalwil, Zurich: 2026-10-25\n"
    )
    value = date(2026, 10, 25)
    assert not verified("medical_report", "treatment_date", value, snippet, text)


def test_a_snippet_that_is_not_on_the_page_never_falls_back() -> None:
    value = date(2026, 10, 25)
    assert not verified(
        "medical_report", "treatment_date", value, "Bern, 2026-10-25", "x\n"
    )


def test_an_identifier_falls_back_only_with_a_label_and_no_veto() -> None:
    text = "Invoice no.:\n4471\nOrder no.:\n4471\n"
    assert not invoice_number("4471", "4471", text)  # a veto on the page
    text = "Customer service\n4471\nInvoice no.:\n4471\n"
    assert invoice_number("4471", "4471", text)


# Finalization of Phase 2 (Nikola, decisions 1 to 3): the fallback needs clear evidence
# and no other label of the value; the generic date does not depend on the layout.


def test_the_fallback_is_blocked_by_another_label_of_the_value() -> None:
    """ "Delivery" is no veto of a treatment date, but it is the label of something
    else: the page does not say clearly which date the snippet is."""
    value = date(2026, 10, 25)
    text = REPORT + "Delivery: 2026-10-25\n"
    assert not verified(
        "medical_report", "treatment_date", value, "Thalwil, 2026-10-25", text
    )


@pytest.mark.parametrize(
    "text",
    [
        POLICE,  # "Date:" on the line above the date
        POLICE.replace("Date:\n2026-10-09", "Date: 2026-10-09"),  # on the same line
        POLICE.replace("Date:\n2026-10-09", "Report no.: RPA-1 | Date: 2026-10-09"),
    ],
    ids=["above", "same-line", "after-separator"],
)
def test_a_generic_date_is_neutral_whatever_the_layout(text: str) -> None:
    value = date(2026, 10, 9)
    assert verified("police_report", "incident_date", value, "Date: 2026-10-09", text)


def test_a_generic_date_alone_never_verifies_an_event_date() -> None:
    """The fallback needs the same value under a label of the field: here the date
    of the report is not the day of the incident."""
    text = "Police\nDate: 2026-10-10\nDiscovered:\n2026-10-09, 07:30\n"
    value = date(2026, 10, 10)
    assert not verified(
        "police_report", "incident_date", value, "Date: 2026-10-10", text
    )


@pytest.mark.parametrize(
    "event", ["Date of loss: 2026-10-01", "Incident date: 2026-10-01"]
)
def test_reported_is_not_the_incident_when_the_page_names_the_incident(
    event: str,
) -> None:
    """Review F1: "Reported:" is the date of the incident on a baggage report only
    when the page gives no date of the incident itself."""
    text = f"Baggage irregularity report\nReported: 2026-10-09\n{event}\n"
    value = date(2026, 10, 9)
    assert not verified(
        "luggage_report", "incident_date", value, "Reported: 2026-10-09", text
    )
    text = "Baggage irregularity report\nReported: 2026-10-09\nFlight: AW 618\n"
    assert verified(
        "luggage_report", "incident_date", value, "Reported: 2026-10-09", text
    )


# The six adversarial false accepts of the review


def test_a_value_shown_with_its_own_label_stays_verified_when_repeated() -> None:
    """Not a false accept: the snippet is on the page with the label of the field, so
    the stored value is the value of that field, even if a customer number is the
    same. A bare snippet in the same case is downgraded (a veto at one place)."""
    text = "Invoice no.: 4471\nCustomer no.: 4471\n"
    assert invoice_number("Invoice no.: 4471", "4471", text)
    assert not invoice_number("4471", "4471", text)


@pytest.mark.parametrize("value", ["A", "7", "-"])
def test_an_identifier_of_one_character_is_not_verified(value: str) -> None:
    assert not invoice_number(f"Invoice no.: {value}", value, f"Invoice no.: {value}\n")


def test_an_identifier_of_two_characters_can_be_verified() -> None:
    assert invoice_number("Invoice no.: A7", "A7", "Invoice no.: A7\n")


@pytest.mark.parametrize(
    "line", ["Total 3 persons 30", "Total for 2 nights 30", "Gesamt 4 Artikel 30"]
)
def test_a_count_that_says_what_the_total_covers_keeps_the_total(line: str) -> None:
    """Not a false accept (review probe "persons then 30"): the count says what the
    total is for, the amount after it is the total. A count right after the keyword
    with its own value ("Total persons: 4") stays refused (test above)."""
    assert verified("repair_invoice", "total", 30.0, "30", line + "\n")


def test_a_total_with_its_currency_stays_verified() -> None:
    assert verified("repair_invoice", "total", 30.0, "30.00", "Total CHF 30.00\n")


@pytest.mark.parametrize(
    "label",
    [
        "Deposit paid",
        "Down payment",
        "Advance payment",
        "Anzahlung bezahlt",
        "Vorauszahlung",
        "Acompte payé",
        "Arrhes",
        "Acconto pagato",
        "Caparra",
        "Refund paid",
        "Rückerstattung",
        "Gutschrift",
        "Remboursement",
        "Rimborso",
    ],
)
def test_a_deposit_or_a_refund_is_not_the_amount_paid(label: str) -> None:
    text = f"{label}: 500.00\n"
    assert not verified("receipt", "amount_paid", 500.0, "500.00", text)


def test_the_total_paid_stays_verified() -> None:
    assert verified("receipt", "amount_paid", 500.0, "500.00", "Total paid: 500.00\n")


# F6: the source of the module is ASCII


def test_the_source_of_labels_is_ascii() -> None:
    path = Path(__file__).parent.parent / "app" / "docintel" / "labels.py"
    source = path.read_bytes().decode("utf-8")
    bad = [(i, c) for i, c in enumerate(source) if ord(c) > 127]
    assert not bad, bad[:5]
