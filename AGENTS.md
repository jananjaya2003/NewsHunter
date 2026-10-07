# Medical completeness is safety-critical

For every task that changes medical, medicine, pharmacy, public-health, or
healthcare detection or reporting, optimize for recall before precision. A
possibly relevant item must reach human review instead of being silently
discarded.

- Treat medicine names, medicine availability/pricing/quality/safety,
  pharmacies, vaccines, devices, diagnosis, treatment, disease, injury,
  mental health, nutrition, public health, healthcare staff, facilities,
  policy, financing, regulation, education, and research as in scope.
- Include uncertain or borderline medical candidates and clearly flag the
  uncertainty. Exclude only when the available text establishes that the
  medical reference is incidental or is solely about illicit-drug crime.
- Never invent medical facts. Preserve the source, page/location, attribution,
  dosage, strength, route, population, dates, quantities, and warnings exactly
  when they are present. Say when any of these details are missing or unclear.
- Do not present extracted news as diagnosis, treatment advice, or a substitute
  for a qualified clinician or pharmacist. Surface urgent safety concerns for
  human review.
- Do not silently drop a candidate because of OCR damage, weak evidence,
  validation failure, or a processing limit. Retain it when safe, or emit a
  visible review warning explaining why it could not be included.
- Add or update tests for inclusion, ambiguity, evidence validation, and the
  risk of false negatives whenever these rules or related code change.

