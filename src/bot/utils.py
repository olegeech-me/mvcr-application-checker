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
