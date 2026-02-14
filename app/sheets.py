import logging
import os
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from app.database import db

# Scopes for Google Sheets and Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_gspread_client():
    creds_path = "creds.json"
    if not os.path.exists(creds_path):
        logging.error(f"Credentials file not found at {creds_path}")
        return None
    
    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        logging.error(f"Failed to authorize Google Sheets client: {e}")
        return None

async def sync_to_sheets():
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    sheet_name = os.getenv("SHEET_NAME", "Sheet1")
    
    if not spreadsheet_id:
        logging.error("SPREADSHEET_ID not found in environment variables")
        return

    client = await asyncio.to_thread(get_gspread_client)
    if not client:
        return

    try:
        # Get unsynced data
        unsynced = await db.get_unsynced_warranties()
        if not unsynced:
            logging.info("No new warranties to sync to Google Sheets")
            return

        spreadsheet = await asyncio.to_thread(client.open_by_key, spreadsheet_id)
        sheet = await asyncio.to_thread(spreadsheet.worksheet, sheet_name)
        
        # Prepare rows
        # Columns: Email, Username, CZ Code, Date, SKU
        rows = []
        warranty_ids = []
        for w in unsynced:
            rows.append([
                w.get("email") or "-",
                f"@{w.get('username')}" if w.get('username') else "-",
                w.get("cz_code") or "-",
                w.get("created_at") or "-",
                w.get("sku") or "-"
            ])
            warranty_ids.append(w["id"])

        # Append rows to sheet
        await asyncio.to_thread(sheet.append_rows, rows)
        
        # Mark as synced in DB
        await db.mark_as_synced(warranty_ids)
        logging.info(f"Successfully synced {len(rows)} warranties to Google Sheets")
        
    except Exception as e:
        logging.error(f"Error during Google Sheets sync: {e}")

async def sheets_sync_scheduler():
    logging.info("Starting Google Sheets sync scheduler (every 10 minutes)")
    while True:
        try:
            await sync_to_sheets()
        except Exception as e:
            logging.error(f"Unexpected error in sheets_sync_scheduler: {e}")
        
        # Wait for 10 minutes (600 seconds)
        await asyncio.sleep(600)

