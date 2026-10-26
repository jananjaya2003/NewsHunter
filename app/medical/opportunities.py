"""Business-opportunity categories for a medical supply company.

The categories decide how a candidate is grouped and ranked on the results
page. They never remove a medical candidate: anything selected by the medical
safety rules keeps at least the "Other health news" category.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    priority: int
    description: str


CATEGORIES = {
    item.key: item
    for item in (
        Category(
            "medical_tender",
            "Medical tenders & procurement",
            100,
            "Bids, quotations, supplier registration and procurement notices with a "
            "hospital, medical, laboratory or health-sector context.",
        ),
        Category(
            "equipment",
            "Medical equipment & devices",
            80,
            "Machines, devices, consumables, laboratory and hospital equipment.",
        ),
        Category(
            "facility",
            "Hospitals & medical centres",
            70,
            "New, expanding or upgraded hospitals, clinics, units and medical centres.",
        ),
        Category(
            "pharma",
            "Medicines & pharmacy",
            60,
            "Medicine supply, shortages, pricing, regulation, vaccines and pharmacies.",
        ),
        Category(
            "policy",
            "Health policy & funding",
            50,
            "Ministry decisions, health budgets, donations, grants and loans.",
        ),
        Category(
            "health",
            "Other health news",
            30,
            "Remaining medical and public-health candidates kept for review.",
        ),
        Category(
            "other_tender",
            "Other tenders (non-medical)",
            10,
            "Tenders without a detected medical context. Kept so OCR damage cannot "
            "hide a medical tender; hidden by default.",
        ),
    )
}
DEFAULT_HIDDEN_CATEGORIES = {"other_tender"}

TENDER_PHRASES = {
    "bid invitation",
    "bid notice",
    "bid security",
    "bidding document",
    "bidding documents",
    "bids are invited",
    "bids will be",
    "call for quotations",
    "calling for bids",
    "calling for tenders",
    "notice inviting tender",
    "expression of interest",
    "expressions of interest",
    "international competitive bidding",
    "invitation for bid",
    "invitation for bids",
    "invitation for tender",
    "invitation for tenders",
    "invitation to bid",
    "national competitive bidding",
    "pre bid",
    "procurement committee",
    "procurement notice",
    "quotations are invited",
    "registration of suppliers",
    "request for proposal",
    "request for proposals",
    "request for quotation",
    "request for quotations",
    "rfq",
    "sealed bids",
    "supplier registration",
    "supply and delivery",
    "supply delivery",
    "supply installation",
    "tender",
    "tender notice",
    "tenders",
    "tenders are invited",
}

# Strong signals make a tender medical on their own.
MEDICAL_CONTEXT_STRONG = {
    "ambulance",
    "bandage",
    "bandages",
    "blood bank",
    "cannula",
    "cardiology",
    "catheter",
    "catheters",
    "clinic",
    "clinical",
    "ct scanner",
    "dental",
    "dialysis",
    "health",
    "healthcare",
    "hospital",
    "hospitals",
    "icu",
    "infusion",
    "medical",
    "medical supplies division",
    "medicine",
    "medicines",
    "mri",
    "msd",
    "nephrology",
    "nmra",
    "nursing",
    "oncology",
    "patient",
    "patients",
    "pharmaceutical",
    "pharmaceuticals",
    "pharmacy",
    "radiology",
    "state pharmaceuticals",
    "surgical",
    "syringe",
    "syringes",
    "ultrasound",
    "vaccine",
    "vaccines",
    "ventilator",
    "ventilators",
    "x ray",
}
# Weak signals are plausible (laboratory, oxygen) but often belong to
# non-health institutions, so the item is flagged as borderline.
# Too general to mark a tender as medical on their own.
GENERAL_MEDICAL_TERMS = {"health", "healthcare"}
MEDICAL_CONTEXT_WEAK = {
    "analytical equipment",
    "autoclave",
    "biomedical",
    "gloves",
    "laboratory",
    "laboratory equipment",
    "oxygen",
    "reagents",
    "scientific equipment",
    "sterilization",
    "sterilisation",
}

EQUIPMENT_TERMS = {
    "autoclave",
    "biomedical",
    "cannula",
    "cath lab",
    "catheter",
    "catheters",
    "ct scan",
    "ct scanner",
    "defibrillator",
    "defibrillators",
    "dialysis machine",
    "dialysis machines",
    "ecg",
    "endoscopy",
    "haemodialysis",
    "hemodialysis",
    "hospital beds",
    "hospital equipment",
    "implants",
    "incubator",
    "incubators",
    "infusion pump",
    "infusion pumps",
    "laboratory equipment",
    "linear accelerator",
    "mammography",
    "medical device",
    "medical devices",
    "medical equipment",
    "mri",
    "oxygen concentrator",
    "oxygen concentrators",
    "patient monitor",
    "patient monitors",
    "scanner",
    "scanners",
    "stent",
    "stents",
    "surgical consumables",
    "surgical equipment",
    "syringe",
    "syringes",
    "ultrasound",
    "ventilator",
    "ventilators",
    "wheelchair",
    "wheelchairs",
    "x ray",
}
# Generic equipment words count only beside a medical signal.
GENERIC_EQUIPMENT_TERMS = {"consumables", "devices", "equipment", "machine", "machines"}

FACILITY_TERMS = {
    "base hospital",
    "blood bank",
    "cancer hospital",
    "clinic",
    "clinics",
    "dialysis centre",
    "dialysis unit",
    "divisional hospital",
    "emergency unit",
    "general hospital",
    "health centre",
    "hospital",
    "hospitals",
    "icu",
    "intensive care unit",
    "maternity",
    "medical centre",
    "medical center",
    "medical faculty",
    "nursing home",
    "opd",
    "operating theatre",
    "outpatient",
    "primary medical care unit",
    "teaching hospital",
    "ward complex",
}

PHARMA_TERMS = {
    "antibiotic",
    "antibiotics",
    "drug shortage",
    "insulin",
    "medical supplies division",
    "medicine",
    "medicine shortage",
    "medicines",
    "msd",
    "nmra",
    "pharmaceutical",
    "pharmaceuticals",
    "pharmacies",
    "pharmacist",
    "pharmacists",
    "pharmacy",
    "prescription",
    "prescriptions",
    "state pharmaceuticals",
    "vaccine",
    "vaccines",
    "vaccination",
}

POLICY_TERMS = {
    "health budget",
    "health minister",
    "health ministry",
    "health policy",
    "health sector",
    "ministry of health",
    "world health organization",
}
FUNDING_TERMS = {"budget", "donate", "donated", "donates", "donation", "funding", "grant", "loan"}

_INSTITUTION_TYPES = (
    "hospital",
    "medical centre",
    "medical center",
    "clinic",
    "medical research institute",
    "medical supplies division",
    "state pharmaceuticals corporation",
    "ministry of health",
    "health services",
    "medical faculty",
    "faculty of medicine",
    "blood bank",
    "cancer institute",
    "nursing home",
)


def _capitalised_alternatives(phrase: str) -> str:
    # Accept "Teaching Hospital" and "TEACHING HOSPITAL", but not a lowercase
    # "the hospital" that ends an ordinary sentence.
    def word(token: str) -> str:
        if token in {"of", "and", "for", "the"}:
            return f"(?:{token}|{token.upper()}|{token.title()})"
        return f"(?:{token.title()}|{token.upper()})"

    return r"\s+".join(word(token) for token in phrase.split())


INSTITUTION_PATTERN = re.compile(
    r"\b((?:[A-Z][\w'&-]*\s+(?:(?:of|and|for|the|OF|AND|FOR|THE)\s+)?){0,6}(?:"
    + "|".join(_capitalised_alternatives(item) for item in _INSTITUTION_TYPES)
    # Keep a place suffix: "Hospital of Sri Lanka", "Hospital - Kurunegala".
    + r")(?:\s+(?:of|OF)(?:\s+(?!(?:The|THE|And|AND|Invitation|INVITATION)\b)[A-Z][\w'&-]*){1,2}"
    + r"|\s*[-,]\s*(?!(?:The|THE)\b)[A-Z][A-Za-z]{2,})?)\b"
)
MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|"
    "december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
DATE_PATTERN = re.compile(
    rf"\b(\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{4}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{MONTHS})\.?,?\s+\d{{4}}"
    rf"|(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}})\b",
    re.IGNORECASE,
)


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _hits(text: str, terms: set[str]) -> set[str]:
    padded = f" {text} "
    return {term for term in terms if f" {term} " in padded}


@dataclass(frozen=True)
class Classification:
    categories: list[str]
    primary: str
    priority: int
    signals: list[str]
    is_tender: bool
    borderline: bool
    note: str
    # 2 = a specific medical term is present; 1 = only general signals.
    strength: int = 1


def classify(title: str, text: str, medical_candidate: bool) -> Classification | None:
    """Return categories, or None when the item is neither medical nor a tender."""
    combined = normalise(f"{title} {text}")
    tender_hits = _hits(combined, TENDER_PHRASES)
    strong = _hits(combined, MEDICAL_CONTEXT_STRONG)
    weak = _hits(combined, MEDICAL_CONTEXT_WEAK)
    specific = strong - GENERAL_MEDICAL_TERMS
    medical_context = bool(strong) or medical_candidate
    if not medical_candidate and not tender_hits:
        return None

    categories: list[str] = []
    notes: list[str] = []
    borderline = False
    if tender_hits:
        if specific:
            categories.append("medical_tender")
        elif medical_context or weak:
            # Kept for review: "health", "rehabilitation" or "laboratory" alone
            # also appear in road, office and agricultural notices.
            categories.append("medical_tender")
            borderline = True
            general = sorted((strong | weak) or {"general medical wording"})
            notes.append(
                "Possible medical tender, verify: only general signals were found "
                f"({', '.join(general)}); the notice may be for non-medical goods or works."
            )
        else:
            categories.append("other_tender")

    equipment = _hits(combined, EQUIPMENT_TERMS)
    if medical_context or weak:
        equipment |= _hits(combined, GENERIC_EQUIPMENT_TERMS)
    if equipment and (medical_context or weak):
        categories.append("equipment")
    facility = _hits(combined, FACILITY_TERMS)
    if facility:
        categories.append("facility")
    pharma = _hits(combined, PHARMA_TERMS)
    if pharma and medical_context:
        categories.append("pharma")
    policy = _hits(combined, POLICY_TERMS)
    funding = _hits(combined, FUNDING_TERMS) if medical_context else set()
    if policy or funding:
        categories.append("policy")
    if medical_candidate and not set(categories) - {"other_tender"}:
        categories.append("health")

    categories = sorted(set(categories), key=lambda key: -CATEGORIES[key].priority)
    primary = categories[0]
    signals = sorted(tender_hits | strong | weak | equipment | facility | pharma | policy | funding)
    return Classification(
        categories=categories,
        primary=primary,
        priority=CATEGORIES[primary].priority,
        signals=signals,
        is_tender=bool(tender_hits),
        borderline=borderline,
        note=" ".join(notes),
        strength=2 if specific else 1,
    )


# Notice headings that the capitalised-word pattern would otherwise glue onto
# the front of a name ("PROCUREMENT NOTICE NATIONAL HOSPITAL").
_LEADING_NOISE = {
    "and", "bid", "bids", "calling", "for", "goods", "icb", "invitation", "lanka", "ncb",
    "notice", "of", "procurement", "registration", "services", "sri", "suppliers", "tender",
    "tenders", "the",
}
_INNER_TYPE = re.compile(
    r"\b(?:" + "|".join(_capitalised_alternatives(item) for item in _INSTITUTION_TYPES)
    # Only where another capitalised name follows, not "Hospital of Sri Lanka".
    + r")\s+(?=(?!(?:of|OF)\b)[A-Z])"
)


def _clean_institution(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw).strip(" .,-")
    # "MINISTRY OF HEALTH TEACHING HOSPITAL" holds two names; keep the last.
    inner = list(_INNER_TYPE.finditer(name))
    if inner:
        name = name[inner[-1].end() :]
    words = name.split()
    while len(words) > 1 and words[0].lower() in _LEADING_NOISE:
        words.pop(0)
    return " ".join(words)


def institutions(text: str, limit: int = 4) -> list[str]:
    """Institution names copied exactly from the text (never inferred)."""
    names: list[str] = []
    for match in INSTITUTION_PATTERN.finditer(text):
        name = _clean_institution(match.group(1))
        if len(name.split()) >= 2 and name.lower() not in {item.lower() for item in names}:
            names.append(name)
    # Drop a name that is the tail of a longer, more specific one.
    found = [
        name for name in names
        if not any(
            other != name and other.lower().endswith(" " + name.lower()) for other in names
        )
    ]
    return found[:limit]


def dates_mentioned(text: str, limit: int = 6) -> list[str]:
    """Date strings exactly as printed, for the reader to verify."""
    found: list[str] = []
    for match in DATE_PATTERN.finditer(text):
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        if value not in found:
            found.append(value)
        if len(found) >= limit:
            break
    return found
