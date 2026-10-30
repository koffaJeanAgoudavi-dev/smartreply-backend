"""Client gspread partagé par les Sheets propres à chaque entreprise."""

import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheets_client():
    """Initialise le client gspread avec les identifiants du compte de service."""
    service_account_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_info:
        info = json.loads(service_account_info)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(
            "credentials.json",
            scopes=SCOPES,
        )

    return gspread.authorize(creds)
