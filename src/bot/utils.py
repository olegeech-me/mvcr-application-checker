import logging

logger = logging.getLogger(__name__)

MVCR_STATUSES = {
    "not_found": (["nebylo nalezeno", "bez úvodních nul"], "⚪️"),
    "in_progress": (["zpracovává se", "v-prubehu-rizeni"], "🟡"),
    "approved": (["bylo <b>povoleno</b>", "rizeni-povoleno"], "🟢"),
    "denied": (["bylo <b>nepovoleno</b>", "zamítlo", "zastavilo"], "🔴"),
    "error": (["ERROR"], "🔴"),
}


def generate_oam_full_string(app_details):
    """Generate full OAM application identifier"""

    # Extract data, checking for both possible key formats
    number = app_details.get("number") or app_details.get("application_number")
    suffix = app_details.get("suffix") or app_details.get("application_suffix", "0")
    type_ = app_details.get("type") or app_details.get("application_type")
    year = app_details.get("year") or app_details.get("application_year")

    if suffix != "0":
        oam_string = "OAM-{}-{}/{}-{}".format(number, suffix, type_, year)
    else:
        oam_string = "OAM-{}/{}-{}".format(number, type_, year)

    return oam_string


def categorize_application_status(status):
    """Return category and emoji based on status string"""
    for category, (keywords, emoji_sign) in MVCR_STATUSES.items():
        for keyword in keywords:
            if keyword in status:
                return category, emoji_sign
    logger.error(f"Failed to categorize status: {status}")
    return None, None
