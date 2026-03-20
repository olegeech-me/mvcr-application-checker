import pytest

from bot.utils import generate_oam_full_string, categorize_application_status, MVCR_STATUSES

from conftest import make_rabbit


# ---------------------------------------------------------------------------
# generate_oam_full_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_details, expected",
    [
        # OAM with short keys (RabbitMQ message format)
        ({"number": "4242", "suffix": "0", "type": "TP", "year": "2042"}, "OAM-4242/TP-2042"),
        # OAM with suffix
        ({"number": "4242", "suffix": "5", "type": "DO", "year": "2020"}, "OAM-4242-5/DO-2020"),
        # OAM with DB column keys
        (
            {"application_number": "12345", "application_suffix": "0", "application_type": "MK", "application_year": "2023"},
            "OAM-12345/MK-2023",
        ),
        # ZOV with short keys
        ({"number": "ISTA202504220001", "type": "ZOV", "year": 0}, "ISTA202504220001"),
        # ZOV with DB column keys
        (
            {
                "application_number": "ISTA202601150003",
                "application_type": "ZOV",
                "application_year": 0,
                "application_suffix": "0",
            },
            "ISTA202601150003",
        ),
        # No type=ZOV defaults to OAM
        ({"number": "999", "suffix": "0", "type": "TP", "year": "2025"}, "OAM-999/TP-2025"),
    ],
)
def test_generate_oam_full_string(app_details, expected):
    assert generate_oam_full_string(app_details) == expected


# ---------------------------------------------------------------------------
# categorize_application_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_text, expected_category, expected_emoji",
    [
        ("Your application has been preliminarily assessed positively", "pre_approved", "⭐"),
        ("Vaše žádost bylo předběžně vyhodnoceno kladně", "pre_approved", "⭐"),
        ("Your application bylo <b>povoleno</b>", "approved", "🟢"),
        ("rizeni-povoleno", "approved", "🟢"),
        ("is still being processed", "in_progress", "🟡"),
        ("was <b>rejected</b>", "denied", "🔴"),
        ("reference number not found", "not_found", "⚪️"),
        ("has been suspended", "suspended", "🟠"),
        ("totally unknown status xyz", None, None),
    ],
)
def test_categorize_application_status(status_text, expected_category, expected_emoji):
    category, emoji = categorize_application_status(status_text)
    assert category == expected_category
    assert emoji == expected_emoji


def test_categorize_zov_pre_approved_with_povoleno_link():
    """Real ZOV pre_approved response contains 'rizeni-povoleno' in a link URL;
    must still be classified as pre_approved, not approved"""
    real_status = (
        'Číslo žádosti o vízum<strong> ISTA202504220001 </strong>bylo '
        '<b>předběžně vyhodnoceno kladně</b>. \n\nPro objednání a případné další '
        'informace kontaktujte <a href="https://ipc.gov.cz/kontakty/#3">klientské '
        'centrum</a> na čísle +420 974 801 801 (Po-Čt 8:00-16:00, Pá 8:00-14:00). '
        'Informace o tom, jak dále postupovat, naleznete dále na '
        '<a href="https://ipc.gov.cz/spravni-rizeni/rizeni-povoleno/">této stránce</a>.'
        '\n\n<b>Stav řízení je pouze orientační.</b>'
    )
    category, emoji = categorize_application_status(real_status)
    assert category == "pre_approved", (
        f"Expected pre_approved but got {category}; "
        f"'rizeni-povoleno' in link URL must not trigger approved"
    )
    assert emoji == "⭐"


# ---------------------------------------------------------------------------
# pre_approved resolution check (uses RabbitMQ.is_resolved)
# ---------------------------------------------------------------------------


def test_pre_approved_in_resolved_statuses():
    """pre_approved IS a final/resolved status (ZOV has no separate approved)"""
    rabbit = make_rabbit()
    for kw in MVCR_STATUSES.get("pre_approved")[0]:
        assert rabbit.is_resolved(f"Application {kw}"), f"'{kw}' must be treated as resolved"


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["EN", "RU", "CZ", "UA"])
def test_i18n_has_zov_keys(lang):
    """All languages must have ZOV-related i18n keys with proper content"""
    from bot.texts import message_texts as mt

    assert "pre_approved" in mt[lang], f"Missing 'pre_approved' in {lang}"
    assert "{status_sign}" in mt[lang]["pre_approved"], f"'pre_approved' in {lang} missing {{status_sign}} placeholder"

    assert "dialog_confirmation_zov" in mt[lang], f"Missing 'dialog_confirmation_zov' in {lang}"
    assert "{number}" in mt[lang]["dialog_confirmation_zov"], f"'dialog_confirmation_zov' in {lang} missing {{number}} placeholder"
    assert "OAM" not in mt[lang]["dialog_confirmation_zov"], f"'dialog_confirmation_zov' in {lang} should not contain 'OAM'"

    assert "dialog_app_number" in mt[lang], f"Missing 'dialog_app_number' in {lang}"
    assert "ISTA" in mt[lang]["dialog_app_number"], f"'dialog_app_number' in {lang} should mention ZOV example number"
