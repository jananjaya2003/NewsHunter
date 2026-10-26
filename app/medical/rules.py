from __future__ import annotations

import re

MEDICAL_SCOPE_RULES = """
MEDICAL COMPLETENESS IS SAFETY-CRITICAL. Find every article with meaningful
information about human health, medicine, pharmacy, public health, or delivery
of healthcare. Prefer an extra candidate for human review over a missed medical
item. Qualifying subjects include, but are not limited to:

1. Public health: outbreaks, disease surveillance, prevention, vaccination,
   sanitation when tied to health outcomes, nutrition programmes, maternal and
   child health, mental health, and official health warnings.
2. Healthcare services: hospitals, clinics, laboratories, blood banks,
   ambulances, emergency care, appointments, waiting times, rural access,
   telemedicine, service openings/closures, capacity, quality, or safety.
3. Healthcare workforce: doctors, nurses, pharmacists, allied professionals,
   training, staffing, shortages, strikes, migration, or working conditions
   when patient services are affected.
4. Medicines and medical products: medicines, vaccines, medical devices,
   availability, shortages, prices, quality, approvals, regulation, recalls,
   procurement, or clinically relevant safety warnings.
5. Health policy and financing: Ministry of Health decisions, health laws,
   regulation, public/private healthcare policy, insurance, budgets, and
   funding when the effect on health or patient services is clear.
6. Medical research and education: clinical or public-health research,
   evidence-based findings, trials, professional education, and training with
   a clear human-health or service-delivery consequence.
7. Diagnosis and treatment: symptoms, screening, tests, diseases, injuries,
   procedures, surgery, rehabilitation, disability, palliative care, and
   patient experiences containing substantive medical information.
8. Health determinants and safety: mental health, nutrition, occupational or
   environmental health, sanitation, food/drug safety, poisoning, accidents,
   disasters, or zoonotic disease when medical care, injury, illness, or a
   specific human-health consequence is a substantive part of the article.
9. Medicine-specific details: generic or brand names, dosage, strength, route,
   indication, contraindication, interactions, adverse effects, prescribing,
   dispensing, supply, access, price, quality, falsification, approval, recall,
   or pharmacovigilance.

EXCLUDE the article when any of these applies:

1. A medical word is demonstrably incidental and the article contains no
   substantive health, clinical, medicine, pharmacy, or healthcare information.
2. It concerns illicit-drug possession, trafficking, or crime and contains no
   treatment, poisoning, addiction, prevention, patient, or public-health angle.
3. It is veterinary-only with no stated zoonotic, food-safety, One Health, or
   human public-health consequence.
4. It merely names a person's medical occupation and gives no health or
   healthcare information.
5. A Health Minister, Health Ministry, hospital, doctor, or other medical actor
   is mentioned only as the speaker, employer, location, or biographical detail
   in an article whose actual subject is not health or healthcare.
6. It discusses general poverty, livelihoods, welfare, food security, economic
   development, accidents, deaths, or disasters without substantive information
   about illness, injury, medical response, treatment, nutrition-related health,
   patient services, or an explicit public-health intervention.
7. Health relevance is merely inferred from a broad social issue; the supplied
   text must state the concrete health or healthcare connection.

Do not exclude a candidate merely because it is an advertisement, product
announcement, court/political/business story, individual illness or injury,
wellness item, or damaged text. Include it when it contains substantive medical
information, use a low confidence score when appropriate, and add an extraction
warning so an editor can decide. Borderline means the text states a plausible
but unclear medical angle; it does not mean that health could hypothetically be
inferred from an otherwise non-medical topic. Every candidate must have a short,
exact phrase in the supplied text that demonstrates its medical relevance.
"""


MEDICAL_EVIDENCE_TERMS = {
    "addiction",
    "adverse effect",
    "antibiotic",
    "ambulance",
    "blood",
    "cancer",
    "care",
    "clinic",
    "clinical",
    "diagnosis",
    "diagnostic",
    "dengue",
    "diabetes",
    "disease",
    "disability",
    "doctor",
    "dose",
    "dosage",
    "drug",
    "emergency",
    "emergency care",
    "epidemic",
    "health ministry",
    "health",
    "healthcare",
    "hospital",
    "illness",
    "infection",
    "injury",
    "laboratory",
    "maternal",
    "medical",
    "medication",
    "medicine",
    "mental health",
    "nurse",
    "nutrition",
    "outbreak",
    "patient",
    "pharmaceutical",
    "pharmacist",
    "pharmacy",
    "poisoning",
    "prescription",
    "prevention",
    "public health",
    "rehabilitation",
    "screening",
    "surgery",
    "symptom",
    "therapy",
    "treatment",
    "vaccine",
    "vaccination",
    "virus",
}


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def medical_evidence_supported(evidence: str, source_text: str) -> bool:
    """Accept only a short source-grounded phrase containing a medical term."""
    normalized_evidence = _normalise(evidence)
    if not normalized_evidence:
        return False
    word_count = len(normalized_evidence.split())
    if word_count < 2 or word_count > 18:
        return False
    if normalized_evidence not in _normalise(source_text):
        return False
    padded_evidence = f" {normalized_evidence} "
    return any(f" {_normalise(term)} " in padded_evidence for term in MEDICAL_EVIDENCE_TERMS)
