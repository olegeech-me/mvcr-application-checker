import sqlite3
import json

cookies_path = "/home/olegeech/git/mvcr-application-checker/chrome-cookies.sql"


def extract_cookies_from_chrome_db(filepath):
    conn = sqlite3.connect(filepath)
    cursor = conn.cursor()

    # Extract cookies from the database
    cursor.execute("SELECT host_key, name, value, path, expires_utc, is_secure, is_httponly FROM cookies")
    cookies = cursor.fetchall()

    # Convert cookies into a list of dictionaries
    cookies_list = [
        {
            "domain": host_key,
            "name": name,
            "value": value,
            "path": path,
            "expiry": expires_utc,
            "secure": bool(is_secure),
            "httpOnly": bool(is_httponly),
        }
        for host_key, name, value, path, expires_utc, is_secure, is_httponly in cookies
    ]

    return cookies_list


all_cookies = extract_cookies_from_chrome_db(cookies_path)

with open("all_cookies.json", "w") as f:
    json.dump(all_cookies, f)
