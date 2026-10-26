from app.opportunities import CATEGORIES, classify, dates_mentioned, institutions

HOSPITAL_NOTICE = """PROCUREMENT NOTICE
NATIONAL NEPHROLOGY SPECIALIZED HOSPITAL - POLONNARUWA
Registration of Suppliers for the Year 2027
Applications are invited from reputed manufacturers or their authorized agents.
Medical / Surgical / laboratory equipment.
Hospital Equipment (Hospital beds, Trolleys, Saline Stands, Oxygen Stands)
Applications should reach the hospital on or before 15.12.2026."""


def test_hospital_supplier_registration_is_a_strong_medical_tender():
    result = classify("PROCUREMENT NOTICE", HOSPITAL_NOTICE, medical_candidate=True)
    assert result.primary == "medical_tender"
    assert result.strength == 2
    assert not result.borderline
    assert "equipment" in result.categories
    assert result.is_tender


def test_medical_tender_found_without_the_medical_safety_rule():
    # Tender wording plus a specific term must be enough on its own.
    result = classify(
        "INVITATION FOR BIDS",
        "Supply of infusion pumps and syringes to the Base Hospital. Sealed bids are invited.",
        medical_candidate=False,
    )
    assert result.primary == "medical_tender"
    assert result.strength == 2


def test_ocr_damaged_hospital_name_still_reaches_review():
    # "Hosp1tal" defeats the hospital term, but surgical keeps it medical.
    result = classify(
        "CALLING FOR TENDERS",
        "Teaching Hosp1tal Kandy. Tenders are invited for surgical consumables.",
        medical_candidate=False,
    )
    assert result is not None
    assert result.primary == "medical_tender"


def test_general_health_word_tender_is_kept_but_marked_possible():
    result = classify(
        "Contract No CPC/PRDA",
        "ROAD MAINTENANCE PROGRAMME. Tenders are invited. Occupational health and safety apply.",
        medical_candidate=True,
    )
    assert result.primary == "medical_tender"
    assert result.borderline
    assert result.strength == 1
    assert "verify" in result.note


def test_laboratory_only_tender_is_borderline_not_dropped():
    result = classify(
        "Invitation for Bids (IFB)",
        "Supply, delivery, installation and commissioning of analytical equipment for a "
        "soil testing laboratory.",
        medical_candidate=False,
    )
    assert result.primary == "medical_tender"
    assert result.borderline


def test_non_medical_tender_is_kept_in_hidden_other_tenders():
    result = classify(
        "INVITATION FOR BIDS",
        "Rehabilitation of the Kandy road. Bid security of Rs. 50,000 is required.",
        medical_candidate=False,
    )
    assert result.categories == ["other_tender"]
    assert CATEGORIES["other_tender"].priority < CATEGORIES["health"].priority


def test_medical_news_without_tender_gets_business_categories():
    result = classify(
        "New dialysis unit opened",
        "Five dialysis machines were donated to the Teaching Hospital.",
        medical_candidate=True,
    )
    assert result.primary == "equipment"
    assert {"equipment", "facility", "policy"} <= set(result.categories)
    assert not result.is_tender


def test_every_medical_candidate_keeps_at_least_one_category():
    result = classify("Dengue cases rise", "Health officials warn of an outbreak.", True)
    assert result.categories == ["health"]


def test_unrelated_item_is_not_categorised():
    assert classify("Cricket final today", "The match starts at 2 pm.", False) is None


def test_institutions_are_copied_exactly_and_heading_words_removed():
    assert institutions(HOSPITAL_NOTICE) == [
        "NATIONAL NEPHROLOGY SPECIALIZED HOSPITAL - POLONNARUWA"
    ]
    assert institutions("MINISTRY OF HEALTH\nTEACHING HOSPITAL - KURUNEGALA") == [
        "TEACHING HOSPITAL - KURUNEGALA"
    ]
    assert institutions("INVITATION FOR BIDS NATIONAL HOSPITAL OF SRI LANKA INVITATION") == [
        "NATIONAL HOSPITAL OF SRI LANKA"
    ]
    names = institutions("MINISTRY OF HEALTH & MASS MEDIA BASE HOSPITAL, AKKARAIPATTU")
    assert "MINISTRY OF HEALTH" in names
    assert any(name.endswith("BASE HOSPITAL, AKKARAIPATTU") for name in names)
    assert institutions("at Karapitiya Teaching Hospital. Visiting the hospital ward") == [
        "Karapitiya Teaching Hospital"
    ]


def test_dates_are_returned_exactly_as_printed():
    text = "Bids close on 09.11.2026 at 2.00 p.m.; pre-bid meeting 28th October 2026."
    assert dates_mentioned(text) == ["09.11.2026", "28th October 2026"]
