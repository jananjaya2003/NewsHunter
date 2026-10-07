# Medical-only selection rules

Medical completeness is safety-critical. The agent retains every article with
meaningful information about **human health, medicine, pharmacy, public health,
or healthcare delivery**. If relevance is uncertain, the article is included
for human review with a warning or lower confidence rather than silently
discarded.

## Included

- Public health, outbreaks, prevention, vaccination, maternal and child health,
  mental health, nutrition programmes, and official health warnings
- Hospitals, clinics, laboratories, blood banks, ambulances, emergency care,
  waiting times, access, capacity, service quality, and patient safety
- Doctors, nurses, pharmacists, allied professionals, training, staffing,
  migration, shortages, and strikes affecting patient services
- Medicines, vaccines, devices, availability, shortages, pricing, quality,
  approvals, regulation, recalls, procurement, and safety warnings
- Health policy, Ministry of Health decisions, regulation, insurance, budgets,
  and financing with a clear patient or public-health effect
- Clinical/public-health research and medical education with a clear health or
  service-delivery consequence
- Symptoms, screening, diagnosis, disease, infection, treatment, procedures,
  surgery, rehabilitation, disability, palliative care, injuries, poisoning,
  addiction, and substantive patient experiences
- Medicine names and details such as indication, dose, strength, route,
  contraindications, interactions, adverse effects, prescribing, dispensing,
  supply, pricing, quality, falsification, approval, recall, and safety
- Occupational and environmental health, sanitation, food/drug safety,
  zoonoses, and disasters or accidents with a stated human-health consequence

## Excluded

- Items where a medical word is demonstrably incidental and no substantive
  health, clinical, medicine, pharmacy, or healthcare information is present
- Illicit-drug crime with no addiction, treatment, poisoning, prevention,
  patient, or public-health angle
- General wellness, beauty, cosmetics, fitness, food, or lifestyle content
  without a concrete clinical or public-health basis
- Veterinary-only news unless it clearly concerns zoonotic disease or human
  public health
- Obituaries or profiles that merely mention a medical occupation

Advertising, product announcements, court/political/business stories,
individual illnesses or injuries, wellness items, and damaged text are not
automatically excluded. They are retained when they contain substantive
medical information and are flagged for editorial review.

## Enforcement

For every returned story, the model must provide an exact 2–18 word phrase from
the PDF demonstrating medical relevance. Application code verifies that the
phrase exists in the supplied page text and contains a recognized medical term.
Stories that fail this check cannot be published as validated stories, but the
pipeline records a visible manual-review warning instead of dropping them
silently. The extraction limit is a high safety ceiling; reaching it also
requires a visible warning.

The editor remains responsible for checking each accepted story against its
cited page. Scope rules prevent irrelevant output; they do not guarantee that a
medical summary is factually correct.
