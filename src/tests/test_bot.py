import pytest

from bot.handlers import _parse_application_number_full, _parse_application_number

@pytest.mark.parametrize(
    "num_str, app_num, app_suffix, app_type, app_year",
    [("OAM-4242/TP-2042", "4242", "0", "TP", "2042"),
     ("4242-5/DO-2020", "4242", "5", "DO", "2020"),
     ("oAM-12345-9/MK-2023", "12345", "9", "MK", "2023"),
     ("BAD-NUMBER/MK-2023", None, None, None, None)])
def test__parse_application_number_full(num_str, app_num, app_suffix, app_type, app_year):
    res = _parse_application_number_full(num_str)
    if res:
        assert res == (app_num, app_suffix, app_type, app_year)
    else:
        assert res is None


@pytest.mark.parametrize(
    "num_str, app_num, app_suffix",
    [("OAM-4242/TP-2042", "4242", "0"),
     ("4242-5/DO-2020", "4242", "5"),
     ("oAM-12345-9/MK-2023", "12345", "9"),
     ("BAD-NUMBER/MK-2023", None, None)])
def test__parse_application_number(num_str, app_num, app_suffix):
    res = _parse_application_number(num_str)
    if res:
        assert res == (app_num, app_suffix, app_type, app_year)
    else:
        assert res is None
