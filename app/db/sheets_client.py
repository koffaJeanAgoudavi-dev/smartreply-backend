import gspread
import json
import os
from google.oauth2.service_account import Credentials
from app.core.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_sheets_client():
    """Initialise le client gspread avec les identifiants du compte de service."""
    service_account_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_info:
        # En production (Render) : JSON stocké dans une variable d'environnement
        info = json.loads(service_account_info)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        # En local : fichier credentials.json à la racine du projet
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

    return gspread.authorize(creds)


def get_central_workbook():
    """Retourne le classeur central SmartReply (onglets Utilisateurs,
    Entreprises, Integrations, telegram_connection_codes, Plans)."""
    client = get_sheets_client()
    return client.open_by_key(settings.GOOGLE_SHEET_ID)
