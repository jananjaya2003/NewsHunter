from app.rules import medical_evidence_supported


def test_accepts_exact_medical_evidence_from_source():
    source = "The Ministry announced that hospital patient services will expand next month."
    assert medical_evidence_supported("hospital patient services", source)


def test_rejects_phrase_not_present_in_source():
    source = "The council opened a new public park."
    assert not medical_evidence_supported("hospital patient services", source)


def test_rejects_incidental_nonmedical_evidence():
    source = "The minister attended the annual ceremony in Colombo."
    assert not medical_evidence_supported("annual ceremony in Colombo", source)


def test_rejects_overlong_evidence():
    evidence = "health " + "word " * 18
    assert not medical_evidence_supported(evidence, evidence)


def test_accepts_medicine_safety_evidence():
    source = "Patients were warned about an adverse effect of the medication."
    assert medical_evidence_supported("adverse effect of the medication", source)


def test_accepts_diagnosis_and_injury_evidence():
    source = "The clinic provides diagnosis and rehabilitation after injury."
    assert medical_evidence_supported("diagnosis and rehabilitation after injury", source)


def test_rejects_care_as_substring_of_scared():
    source = "Officials were scared by delays during the meeting."
    assert not medical_evidence_supported("officials were scared by delays", source)


def test_rejects_health_as_substring_of_healthy():
    source = "Trade was healthy this year according to exporters."
    assert not medical_evidence_supported("trade was healthy this year", source)
