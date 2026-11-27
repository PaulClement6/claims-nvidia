"""Safeguards of the reader (issue #125): placeholders and label checks.

A snippet that is on the page proves that the text is there, not that it is the
right field: a customer number can be read as an invoice number. For a page with a
text layer, `verified` looks at the words in front of the value, where the snippet
is on the page, and says if they fit the field. Missing, doubtful or contradicting
evidence gives False: the reader keeps the value and its source and stores it at
UNVERIFIED, so a person looks at it. Nothing is dropped and no value is changed.

Scope. Identifiers, dates and amounts need a label. The provider and the person
fields have NO label check: only the form of the name is read. A provider must not
read as one person, and a person field must not read as a company. So a doctor can
pass as the patient, and a patient or an insurer as the provider. Free text fields
(cause, diagnosis, offence, item_description, damage_description) and the lines are
OUT of scope: they are never downgraded here and never claimed as verified by
meaning. The same holds for repair_company, merchant and airline (a sole trader is
a legitimate company).

The words are general business vocabulary in English, never the words of one
document. The 44 mock documents are in English, so no document measures another
language: a label in German, French or Italian is unknown and gives 0.5. A label is
the text just before the value, and the nearest keyword of it decides: it fits the
field (match), it names another thing (veto), or it is unknown (none). Accents and
case do not count.

Known limits (each one gives 0.5 or keeps today's behaviour; none drops a value):
- Persons and organisations are told apart by their words only: a title ("Dr. med.")
  with one to four names and no word of an organisation reads as one person, so a
  practice that is named only after its doctor gets 0.5. A bare name with no title
  and no person label ("Corina Muster") stays at 0.9 as a provider.
- A label the lists do not know (a bare "Price", "Amount" or "Sum", "Total labour")
  gives 0.5, and so does a table whose labels are column headers above a row of
  values.
- A value that the page shows with the label of the field is verified even when the
  same text also stands under another label: the stored value is the value of that
  field. A bare snippet gets 0.5 when the same text also stands under a label of
  something else (a veto); an unknown label there does not block it.
- A scan or a photo has no text layer: nothing is checked, the reader gives 0.5.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Final

# field -> kind. The only fields that are checked; every other field is out of scope.
SAFEGUARDED: Final[dict[str, str]] = {
    "invoice_number": "id",
    "quote_number": "id",
    "booking_reference": "id",
    "report_number": "id",
    "report_reference": "id",
    "invoice_date": "date",
    "quote_date": "date",
    "purchase_date": "date",
    "cancellation_date": "date",
    "departure_date": "date",
    "incident_date": "date",
    "treatment_date": "date",
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


def _fold(text: str) -> str:
    """Case, accents and runs of spaces do not count."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join(plain.split())


# ------------------------------------------------------------------ placeholders

_PLACEHOLDER_TEXTS = (
    # English
    "not answerable, unanswerable, null, none, n/a, n.a., nil, unknown, not found, "
    "not available, not applicable, not specified, not provided, not given, "
    "not stated, not mentioned, not shown, not visible, not legible, illegible, "
    "unreadable, no value, no data, no information, empty, "
    # a dash alone
    "-, --, ---, \u2014, \u2013, \u2212"
)
_OUTER = " \t\r\n\"'`\u00b4\u2018\u2019\u201c\u201d\u201e\u00ab\u00bb()[]{}<>.,;:!*_"


def _placeholder_form(text: str) -> str:
    return " ".join(text.casefold().split()).strip(_OUTER)


# The normalised texts (casefold, outer spaces, quotes, brackets and dots stripped),
# with and without accents. A text of one letter is never one of them.
PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    form
    for raw in _PLACEHOLDER_TEXTS.split(", ")
    for form in (_placeholder_form(raw), _fold(_placeholder_form(raw)))
)


def is_placeholder(value: object) -> bool:
    """True only for a str whose whole normalised text is in PLACEHOLDERS. Never a
    substring: "Not answerable today?" and "NA-4471" are values. Numbers (0, 0.0),
    dates and booleans are values."""
    if not isinstance(value, str):
        return False
    form = _placeholder_form(value)
    return form in PLACEHOLDERS or _fold(form) in PLACEHOLDERS


# ----------------------------------------------------------------------- words
# Every pattern is accent-free and lower case (the label is folded the same way).


@dataclass(frozen=True)
class _Rules:
    match: tuple[re.Pattern[str], ...]
    veto: tuple[re.Pattern[str], ...]
    short: tuple[re.Pattern[str], ...] = ()  # veto only when the label is 1-2 words
    anywhere: tuple[re.Pattern[str], ...] = ()  # veto wherever it stands in the label


def _compile(words: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"(?<![a-z0-9])(?:{w})(?![a-z0-9])") for w in words)


def _rules(
    match: tuple[str, ...],
    veto: tuple[str, ...],
    short: tuple[str, ...] = (),
    anywhere: tuple[str, ...] = (),
) -> _Rules:
    return _Rules(_compile(match), _compile(veto), _compile(short), _compile(anywhere))


# Identifiers: one group of words per kind of number. A field matches its own group
# and vetoes the others.
_ID_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "invoice": (
        r"invoice\w*",
        r"inv",
        r"bill",
    ),
    "quote": (
        r"quot\w*",
        r"offer\w*",
        r"estimate",
    ),
    "booking": (
        r"booking\w*",
        r"reservation\w*",
        r"reserv\w*",
        r"pnr",
        r"record locator",
        r"confirmation (?:no|nr|num\w*|code)",
        r"confirmation",
    ),
    "report": (
        r"reports?",
        r"journal\w*",
        r"case (?:no|nr|num\w*|n\W)",
    ),
    "luggage": (
        r"pir",
        r"property irregularity",
        r"irregularity",
        r"baggage (?:report|file)",
        r"file reference",
        r"reference\w*",
        r"ref",
        r"claim (?:no|nr|num\w*|ref\w*)",
    ),
}
# Things that are never an invoice, quote, booking or report number.
_ID_OTHER: Final[tuple[str, ...]] = (
    r"patient\w*",
    r"customer\w*",
    r"client\w*",
    r"insured\w*",
    r"policy",
    r"member\w*",
    r"passenger\w*",
    r"owner",
    r"holder",
    r"job",
    r"order\w*",
    r"tax",
    r"account",
    r"ticket\w*",
    r"flight\w*",
    r"serial\w*",
    r"article\w*",
    r"passport",
    r"licen[cs]e",
    r"plate",
    r"contract\w*",
    r"delivery",
    r"receipt",
)


# Short technical names that are labels wherever they stand.
_ID_TECHNICAL: Final[tuple[str, ...]] = (
    r"vat",
    r"uid",
    r"iban",
    r"bic",
    r"swift",
    r"gln",
    r"zsr",
    r"ahv\w*",
    r"ssn",
    r"tel\w*",
    r"phone",
    r"fax",
    r"mobile",
    r"zip",
    r"postal",
    r"sku",
    r"vin",
    r"barcode",
    r"tracking",
    r"bag tag",
)
# Words of a document that no party owns: a veto as a label, but not a party
# between a keyword and its value (`_PARTY`).
_GENERIC_WORDS: Final[tuple[str, ...]] = (
    r"date",
    r"total",
    r"amount",
    r"sum",
    r"price",
    r"file",
    r"case",
    r"agent",
    r"officer",
    r"customer ref\w*",
)


def _labelled(word: str) -> str:
    """The word as a label: followed by a colon or by "no.", "nr.", "number", "#"."""
    number = r"(?:no|nr|n|num\w*|number|n\u00b0|id|#)(?![a-z0-9])"
    return rf"(?:{word})(?:\s*{number}|\s*:)"


def _id_rules(own: tuple[str, ...]) -> _Rules:
    """The words of the groups of this field match; those of the other groups of
    numbers and the other things (`_ID_OTHER`) veto. The luggage words ("reference",
    "ref") are a match for a report reference only: alone they say too little."""
    mine = tuple(w for group in own for w in _ID_WORDS[group])
    others = tuple(
        w
        for group, words in _ID_WORDS.items()
        if group not in own and group != "luggage"
        for w in words
    )
    # A word of the language ("order", "customer", "date") vetoes as a label ("Order
    # no.", "Customer:") or when it ends a label of 1-2 words ("Your order", not
    # "Customer service"), not inside a sentence ("Thank you for your order").
    natural = others + _ID_OTHER + _GENERIC_WORDS
    ends = tuple(f"(?:{w})$" for w in natural)
    return _rules(mine, _ID_TECHNICAL + tuple(_labelled(w) for w in natural), ends)


# Dates. Words that are never a document date or an event date.
_DATE_VETO: Final[tuple[str, ...]] = (
    r"validity",
    r"until",
    r"till",
    r"through",
    r"thru",
    r"(?:due|print|printed|expiry|expiration|return|birth|maturity|closing"
    r"|end)\s+date",
    r"date of (?:birth|expiry|expiration|return)",
    r"expir\w*",
    r"due",
    r"payable",
    r"birth\w*",
    r"born",
    r"dob",
    r"print\w*",
    r"return\w*",
    r"follow-?up",
    r"end",
    r"ends",
)
# The generic words for "date": the date of the document itself.
# A bare word is not the end of a longer name: "Departure date" is not "Date".
_BARE = r"(?<![a-z0-9] )(?<![a-z0-9]-)"
_DATE_GENERIC: Final[tuple[str, ...]] = (
    _BARE + r"date(?! of\b)",
    _BARE + r"dated",
    r"date of issue",
    r"issue date",
    r"issued(?: on)?",
)
_DATE_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "invoice_date": (
        r"invoice date",
        r"date of (?:the )?invoice",
        r"invoice dated",
        r"invoiced on",
    ),
    "quote_date": (
        r"quote date",
        r"date of (?:the )?quote",
        r"quotation date",
        r"quote dated",
        r"offer date",
    ),
    "purchase_date": (
        r"purchase date",
        r"date of purchase",
        r"purchased(?: on)?",
        r"bought on",
        r"date of sale",
        r"sale date",
        r"transaction date",
        r"receipt date",
    ),
    "cancellation_date": (
        r"cancellation date",
        r"date of cancellation",
        r"cancell?ed(?: on)?",
        r"termination date",
        r"date of termination",
    ),
    "departure_date": (
        r"depart\w*",
        r"outbound",
        r"travel date",
        r"date of travel",
        r"travel period",
        r"start of (?:the )?"
        r"(?:trip|travel|journey)",
        r"trip start",
        r"trip begins",
    ),
    "incident_date": (
        r"incident\w*",
        r"event",
        r"events",
        r"accident\w*",
        r"occurr\w*",
        r"happened",
        r"took place",
        r"loss",
        r"damage date",
        r"date of (?:the )?"
        r"(?:loss|damage|theft|offen[cs]e)",
        r"offen[cs]e date",
        r"time of (?:the )?"
        r"offen[cs]e",
        r"discovered",
        r"detected",
        r"noticed",
        r"found on",
    ),
    "treatment_date": (
        r"treat\w*",
        r"examin\w*",
        r"consult\w*",
        r"visit\w*",
        r"seen on",
        r"date of (?:the )?(?:service|treatment|examination|consultation|visit)",
        r"service date",
    ),
    "unfit_from": (
        r"unfit",
        r"incapacit\w*",
        r"incapable",
        r"sick leave",
        r"off work",
        r"not fit",
        r"not able to work",
        r"certified (?:from|as)",
        r"sick from",
    ),
}
_OWN_DATE = frozenset(
    {"invoice_date", "quote_date", "purchase_date", "cancellation_date"}
)

# Amounts. A part of an amount is never the amount: a veto for every amount field.
_AMOUNT_PARTS: Final[tuple[str, ...]] = (
    r"sub[- ]?totals?",
    r"vat",
    r"tax\w*",
    r"fees?",
    r"charges?",
    r"deposit\w*",
    r"discount\w*",
)
_AMOUNT_VETO: Final[tuple[str, ...]] = _AMOUNT_PARTS + (
    r"net",
    r"excl\w*",
    r"of which",
    r"rebate",
    r"previous balance",
    r"balance (?:brought )?forward",
    r"carried forward",
    r"opening balance",
    r"shipping",
    r"freight",
    r"tips?",
    r"change",
    r"rounding",
    r"unit\w*",
    r"qty",
    r"quantity",
    r"refund\w*",
    r"cancel\w*",
    r"per (?:person|night|day|adult|child)",
    r"cash",
    r"tendered",
)
_TOTAL_WORDS: Final[tuple[str, ...]] = (
    # A bare "total" is the total only when no other word follows it: "Total labour"
    # is the labour. "Total in CHF" and "Total for 2 nights" stay the total.
    _BARE + r"total(?: in| for(?= \d))?(?! [a-z])",
    r"total (?:amount|price|sum|due|cost|payable|to pay)",
    r"grand total",
    r"amount (?:due|payable|to pay)",
    r"balance due",
    r"invoice (?:total|amount)",
    r"to pay",
    r"offer total",
    r"quote total",
)
_AMOUNT_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "total": _TOTAL_WORDS,
    "trip_price": (
        r"trip price",
        r"price of the trip",
        r"travel price",
        r"price of travel",
        r"total price",
        r"trip total",
        r"trip cost",
        r"cost of the trip",
        r"package price",
    )
    + _TOTAL_WORDS,
    "amount_paid": (
        r"total paid",
        r"amount paid",
        r"paid amount",
        r"paid",
    )
    + _TOTAL_WORDS,
    "refund_received": (
        r"refund\w*",
        r"reimburs\w*",
        r"credit note",
        r"paid back",
        r"payed back",
        r"amount returned",
        r"restitution",
    ),
}
_REFUND_VETO: Final[tuple[str, ...]] = _AMOUNT_PARTS + (
    r"price",
    r"paid(?! back)",
    r"cost\w*",
    r"deduct\w*",
    r"retain\w*",
    r"penalt\w*",
)
_AMOUNT_NOT_TOTAL = {  # a total is never the amount of these (the veto of a field)
    "total": _AMOUNT_VETO,
    "trip_price": _AMOUNT_VETO,
    "amount_paid": tuple(
        w for w in _AMOUNT_VETO if w not in (r"refund\w*", r"cancel\w*")
    ),
    "refund_received": _REFUND_VETO,
}


# Words that veto wherever they stand in the label, also before the keyword
# ("Requested refund", "Outstanding amount", "Next visit").
_ANYWHERE: Final[dict[str, tuple[str, ...]]] = {
    "refund_received": (
        r"request\w*",
        r"claim\w*",
    ),
    "amount_paid": (
        r"amount (?:due|outstanding)",
        r"balance due",
        r"total due",
        r"outstanding",
        # a part paid in advance is not the total paid (the definition of the field)
        r"deposit",
        r"down payment",
        r"advance\w*",
        # money paid back to the customer is not money that the customer paid
        r"refund\w*",
    ),
    "treatment_date": (r"next",),
}


def _build() -> dict[str, _Rules]:
    rules: dict[str, _Rules] = {
        "invoice_number": _id_rules(("invoice",)),
        "quote_number": _id_rules(("quote",)),
        "booking_reference": _id_rules(("booking",)),
        "report_number": _id_rules(("report",)),
        "report_reference": _id_rules(("report", "luggage")),
    }
    for name, words in _DATE_WORDS.items():
        generic = _DATE_GENERIC if name in _OWN_DATE else ()
        rules[name] = _rules(
            words + generic, _DATE_VETO, anywhere=_ANYWHERE.get(name, ())
        )
    for name, words in _AMOUNT_WORDS.items():
        rules[name] = _rules(
            words, _AMOUNT_NOT_TOTAL[name], anywhere=_ANYWHERE.get(name, ())
        )
    return rules


_RULES: Final[dict[str, _Rules]] = _build()


# --------------------------------------------------------------------- the label

CURRENCY = re.compile(
    r"(?<![^\W_])(?:chf|eur|usd|gbp|sfr?\.?|fr\.)(?![^\W_])|[\u20ac$\u00a3]", re.I
)
_SEPARATOR = re.compile(r"\||;|\t| {2,}")
_PARENTHESIS = re.compile(r"\([^()]*\)")
# "incl. VAT", "including 8.1% VAT", "VAT included": a remark, not another amount.
_INCLUDED = r"(?:including|included|incl\.?)"
_TAX_INCLUDED = re.compile(
    rf"\b{_INCLUDED}\s+(?:\d+(?:[.,]\d+)?\s*%\s*)?vat\b|\bvat\s+{_INCLUDED}"
)
MAX_AFTER = 4  # words after the keyword: a long sentence is not a label
MAX_SHORT = 2  # words of a label that is only a name ("Your order")
NUMBER = re.compile(r"(?:(?<!\w)[-\u2212])?\d(?:[',]?\d)*(?:\.\d+)?")  # as omni.NUMBER
_LETTER = re.compile(r"[^\W\d_]")


_ABBREVIATION = re.compile(r"(?:no|nr|n|num|number|n\u00b0)[:.]*")


def _last_segment(line: str, above: bool = False) -> str | None:
    """The text before the value on this line: its last part with letters. "" when
    a number stands in the way (a row of a table), or, for a line `above` the value,
    when the line is a label with its own value ("Doctor: Hans Muster"); None when
    the line has no letters at all (blank, or only a currency): the label is above."""
    parts = _SEPARATOR.split(line)
    for i in range(len(parts) - 1, -1, -1):
        whole = CURRENCY.sub(" ", parts[i]).strip()
        if above and ":" in whole and whole.rsplit(":", 1)[1].strip():
            return ""
        plain = whole.strip(" \t:.,-\u2013\u2014")
        if _LETTER.search(plain):
            colon = ":" if whole.rstrip(" .").endswith(":") else ""
            if i and _ABBREVIATION.fullmatch(_fold(plain)):
                # "INVOICE   NO.:" : the abbreviation alone is not the label
                before = _last_segment(" | ".join(parts[:i]))
                return f"{before} {plain}{colon}" if before else plain + colon
            return plain + colon
        if plain:  # digits or signs: no label from this row
            return ""
    return None


def _label_before(context: str) -> str:
    """The label of the value that follows `context`: the nearest text before it on
    the same line, else the nearest non-empty line above."""
    lines = context.split("\n")
    found = _last_segment(lines[-1])
    for line in reversed(lines[:-1]):
        if found is not None:
            break
        found = _last_segment(line, above=True)
    return found or ""


def _clean(label: str) -> str:
    folded = _fold(label)
    folded = _TAX_INCLUDED.sub(" ", _PARENTHESIS.sub(" ", folded))
    # "no.:" is "no:"; a dot inside a number ("8.1%") stays
    return " ".join(re.sub(r"(?<=[a-z])\.(?=\s|:|$)", "", folded).split())


MATCH, VETO, NONE = "match", "veto", "none"


# A word that says "reported" is not a word of an incident in general ("Date reported"
# is the day of the report). On a baggage irregularity report the loss is reported on
# arrival: there the bare label alone ("Reported:") is the date of the incident.
_REPORTED_BARE = "reported"

# Words of another party or thing: between an identifier keyword and the value they say
# that the value is theirs ("Invoice for customer 4471").
_PARTY: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(_ID_TECHNICAL + _ID_OTHER) + r")(?![a-z0-9])"
)
# A number or a count after the keyword of an amount ("Refund number", "Total
# pages", "Paid days") is not the amount.
_COUNT: Final[re.Pattern[str]] = re.compile(
    r"(?<![a-z0-9])(?:no|nr|num\w*|number|count|pages?|persons?|people|items?"
    r"|articles?|pieces?|pcs|positions?|quantity|qty|nights?|days?|passengers?"
    r"|travell?ers?|adults?|children)(?![a-z0-9])"
)


def _other_value(tail: str) -> bool:
    """The text holds a value of its own: a token of 3 characters or more with a
    digit, and not a percent ("8.1%")."""
    return any(
        any(c.isdigit() for c in token)
        and "%" not in token
        and len(token.strip(",.;:()")) >= 3
        for token in tail.split()
    )


def _verdict(
    rules: _Rules, label: str, kind: str = "date", reported: bool = False
) -> str:
    """The keyword nearest to the value decides (the longest one when two end at the
    same place; a veto when a match and a veto are the same text). A match is void
    (none) when the text between the keyword and the value holds another value, a
    word of another party (identifiers) or a count (amounts)."""
    text = _clean(label)
    if any(p.search(text) for p in rules.anywhere):
        return VETO
    if reported and text.strip(" :") == _REPORTED_BARE:
        return MATCH
    best: tuple[int, int, bool] | None = None
    short = rules.short if len(text.split()) <= MAX_SHORT else ()
    for patterns, is_veto in (
        (rules.match, False),
        (rules.veto, True),
        (short, True),
    ):
        for pattern in patterns:
            for m in pattern.finditer(text):
                key = (m.end(), m.end() - m.start(), is_veto)
                if best is None or key > best:
                    best = key
    if best is None or len(text[best[0] :].split()) > MAX_AFTER:
        return NONE
    if best[2]:
        return VETO
    tail = text[best[0] :]
    if (
        _other_value(tail)
        or (kind == "id" and _PARTY.search(tail))
        or (kind == "amount" and _COUNT.match(tail.strip()))
    ):
        return NONE
    return MATCH


# ------------------------------------------------------------ where the value is


def _spaced(text: str) -> str:
    """A pattern that finds the text with any white space between its words."""
    return r"\s+".join(re.escape(word) for word in text.split())


def _occurrences(snippet: str, text: str) -> list[tuple[int, int]]:
    """Where the snippet is on the page (start, end of each), numbers whole (as
    `_shown`)."""
    if not snippet.strip():
        return []
    spans = [m.span() for m in NUMBER.finditer(text)]
    found = []
    for m in re.finditer(_spaced(snippet), text, re.I):
        start, end = m.span()
        if all(start <= a and b <= end for a, b in spans if a < end and b > start):
            found.append((start, end))
    return found


def _value_span(snippet: str, value: object) -> tuple[int, int]:
    """Where the value stands inside the snippet; (0, 0) when it cannot be found."""
    if isinstance(value, date):
        forms = (value.isoformat(), value.strftime("%d.%m.%Y"))
        spans = [(i, i + len(f)) for f in forms if (i := snippet.find(f)) >= 0]
        return min(spans, default=(0, 0))
    if isinstance(value, int | float) and not isinstance(value, bool):
        for m in NUMBER.finditer(snippet):
            number = float(
                m.group().replace(",", "").replace("'", "").replace("\u2212", "-")
            )
            if abs(number - value) < 0.005:
                return m.span()
        return 0, 0
    found = re.search(_spaced(str(value)), snippet, re.I)
    return found.span() if found else (0, 0)


def _is_cut(after: str) -> bool:
    """Text that sticks to the end of an identifier ("4471-A", "4471/2", "4471A"):
    the value is only the beginning of a longer identifier."""
    return bool(after) and (
        after[0].isalnum() or (after[0] in "-/_" and after[1:2].isalnum())
    )


def _labels(
    snippet: str, value: object, text: str, cut: bool = False
) -> list[tuple[str, str]]:
    """(label, the rest of the line) of the value at each place where the snippet is
    on the page. The label is "" when text sticks to the value ("mail@x.example",
    "A-4471"): that text is part of a word, not a label. With `cut` (identifiers) the
    same holds for text that sticks behind it ("4471-A", "4471/2")."""
    start_in, end_in = _value_span(snippet, value)
    pre, rest = snippet[:start_in], snippet[end_in:]
    places = []
    for start, end in _occurrences(snippet, text):
        context = text[:start] + pre
        glued = bool(context) and (context[-1].isalnum() or context[-1] in "@/\\_-")
        glued = glued or (cut and _is_cut(rest[:2] if rest else text[end : end + 2]))
        label = "" if glued else _label_before(context)
        places.append((label, text[end : end + 30].split("\n")[0]))
    return places


# A number followed by a unit of time, a quantity or a percent is not an amount.
_QUANTITY = re.compile(
    r"[ \t]*(?:%|(?:(?:working|business|calendar) )?(?:days?|weeks?|months?|years?|"
    r"hours?|minutes?|nights?|persons?|people|pieces|pcs|pax|km|kg|cm|mm))(?![a-z])",
    re.I,
)


def _places(
    document_type: str, name: str, value: object, snippet: str, text: str
) -> list[tuple[str, str]]:
    """(label, verdict) at each place where the snippet is on the page."""
    kind = SAFEGUARDED[name]
    reported = (
        name == "incident_date"
        and document_type == "luggage_report"
        and not _names_the_incident(text)
    )
    places = []
    for label, after in _labels(snippet, value, text, cut=kind == "id"):
        if kind == "amount" and _QUANTITY.match(_fold(after)):
            places.append((label, VETO))
        else:
            places.append((label, _verdict(_RULES[name], label, kind, reported)))
    return places


def _names_the_incident(text: str) -> bool:
    """The page has a label of the date of the incident itself ("Date of loss:"):
    then "Reported:" is only the day of the report (review of phase 3, F1)."""
    rules = _RULES["incident_date"]
    return any(
        _verdict(rules, line.split(":")[0] + ":") == MATCH
        for line in text.split("\n")
        if ":" in line
    )


# A label that says nothing about which field the value is: none, a place and a comma
# ("Thalwil,"), or only the generic word for a date ("Date:"), wherever it stands on the
# line. Any word of the vocabulary of a field, or another key with a colon, is a label
# of its own: never neutral.
_VOCABULARY: Final[tuple[re.Pattern[str], ...]] = tuple(
    pattern
    for rules in _RULES.values()
    for group in (rules.match, rules.veto, rules.short, rules.anywhere)
    for pattern in group
)


def _neutral(label: str) -> bool:
    if not label or _fold(label).strip(" :.") == "date":
        return True
    return ":" not in label and not any(p.search(_clean(label)) for p in _VOCABULARY)


def _forms(value: object, text: str) -> list[str]:
    """The ways the value is printed on the page: each one is a bare snippet."""
    if isinstance(value, date):
        return [value.isoformat(), value.strftime("%d.%m.%Y")]
    if isinstance(value, int | float) and not isinstance(value, bool):
        found: list[str] = []
        for m in NUMBER.finditer(text):
            number = float(
                m.group().replace(",", "").replace("'", "").replace("\u2212", "-")
            )
            if abs(number - value) < 0.005 and m.group() not in found:
                found.append(m.group())
        return found
    return [str(value)]


def _by_label(
    document_type: str, name: str, value: object, snippet: str, text: str
) -> bool:
    """True when the label at the snippet fits and nothing vetoes there.

    Fallback (Nikola, 2026-11-24): when every place of the snippet has a neutral
    label ("Thalwil, 2026-10-25", "Date: 2026-10-09"), the other places of the same
    value on the same page decide, and only when the evidence is clear: one place
    has the label of the field, and every other place is the label of the field or
    neutral (no veto, no label of something else). The value is then the value that
    the page gives to the field. The stored source stays the snippet of the model."""
    places = _places(document_type, name, value, snippet, text)
    verdicts = {verdict for _, verdict in places}
    if VETO in verdicts:
        return False
    if MATCH in verdicts:
        return True
    if not places or not all(_neutral(label) for label, _ in places):
        return False
    others = [
        p
        for form in _forms(value, text)
        for p in _places(document_type, name, value, form, text)
    ]
    return any(v == MATCH for _, v in others) and all(
        v == MATCH or (v == NONE and _neutral(label)) for label, v in others
    )


# ----------------------------------------------------------------- id: not prose

_STATUS = frozenset(
    {
        "pending",
        "tbd",
        "tba",
        "to follow",
        "follows",
        "separately",
        "later",
        "to be determined",
        "to be confirmed",
        "n/a yet",
        "see attachment",
        "see above",
        "see below",
    }
)


# A word in lower case, 3 letters or more, with a vowel: "pending", not "xkqzpl".
_WORDLIKE = re.compile(
    r"(?=[a-z\u00e0-\u00ff]{3})[a-z\u00e0-\u00ff]*[aeiouy\u00e0-\u00ff]"
)


def _is_prose(value: str) -> bool:
    """An explanation, not an identifier: "sent separately by e-mail", "pending",
    or two words or more with no digit ("Sent separately"). One word with no digit
    ("XKQZPL") or an identifier in a strange format is not prose."""
    text = value.strip()
    words = text.split()
    lower = [w for w in words if w.isalpha() and w.islower()]
    return (
        _fold(text) in _STATUS
        or bool(re.search(r"[.!?;:]$|\?|,\s", text))
        or (len(words) >= 3 and len(lower) >= 2)
        or (len(words) >= 2 and not any(c.isdigit() for c in text))
        or (len(words) == 1 and len(lower) == 1 and _WORDLIKE.search(text) is not None)
    )


# -------------------------------------------------- organisation and person names

_ORG_PARTS = (
    # names of Swiss organisations in the mock documents ("Praxis am Lindenhof",
    # "Medizinzentrum Seeblick AG"), then English words and legal forms
    r"\w*praxis",
    r"\w*zentrum",
    r"clinic\w*",
    r"hospital\w*",
    r"cent(?:er|re)",
    r"studio",
    r"medical",
    r"pharmac\w*",
    r"labor\w*",
    r"institut\w*",
    r"foundation",
    r"dental",
    r"physio\w*",
    r"radiolog\w*",
    r"emergency",
    r"health\w*",
    r"group",
    r"team",
    r"partner\w*",
    r"associ\w*",
    r"ag",
    r"gmbh",
    r"sa",
    r"sarl",
    r"sagl",
    r"ltd",
    r"llc",
    r"inc",
    r"srl",
    r"spa",
    r"kg",
    r"co",
    r"corp",
    r"plc",
    r"bv",
    r"nv",
    r"oy",
    r"ohg",
    r"eg",
    r"ev",
)
_ORG = re.compile(rf"(?<![a-z])(?:{'|'.join(_ORG_PARTS)})(?![a-z])")
# A personal title: part of a name of one person, never of an organisation alone.
_TITLES = frozenset("dr drs prof pd med dent pract doctor".split())
_NAME = re.compile(r"[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*")
_PERSON_LABEL = re.compile(
    r"(?<![a-z])(?:doctors?|physicians?|dentist\w*|signed|signature|treating|attending)"
    r"(?![a-z])"
)


def _names_organisation(value: str) -> bool:
    text = _fold(value)
    return bool(_ORG.search(text)) or "&" in text


def _one_person(value: str) -> tuple[bool, bool]:
    """(reads as one person, has a personal title): 1 to 4 name words (initials
    allowed) after the titles, and no word of an organisation."""
    if any(c.isdigit() for c in value) or _names_organisation(value):
        return False, False
    words = _NAME.findall(_fold(value))
    names = list(words)
    while names and names[0] in _TITLES:
        names.pop(0)
    titled = len(names) < len(words)
    return 1 <= len(names) <= 4, titled


def _organisation(value: str, snippet: str, text: str) -> bool:
    person, titled = _one_person(value)
    if not person:
        return True
    if titled:
        return False
    return not any(
        _PERSON_LABEL.search(_fold(label)) for label, _ in _labels(snippet, value, text)
    )


def _person(value: str) -> bool:
    return not _names_organisation(value)


# ------------------------------------------------------------------------ verified


def verified(
    document_type: str, name: str, value: object, snippet: str, text: str
) -> bool:
    """True: the words on the page support that `value` belongs to field `name` of a
    document of `document_type`, so the reader may keep VERIFIED. False: missing,
    doubtful or contradicting evidence: the reader stores the field at UNVERIFIED,
    with its value and its source unchanged. For a field that is not in SAFEGUARDED
    it returns True (out of scope). Only for a page with a text layer, after the
    checks of the snippet passed."""
    kind = SAFEGUARDED.get(name)
    if kind is None:
        return True
    if kind == "person":
        return _person(str(value))
    if kind == "organisation":
        return _organisation(str(value), snippet, text)
    if kind == "id" and (
        not isinstance(value, str)
        or sum(c.isalnum() for c in value) < 2  # "A": no identifier
        or _is_prose(value)
    ):
        return False
    return _by_label(document_type, name, value, snippet, text)
