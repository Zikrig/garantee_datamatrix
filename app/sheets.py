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
    
    if not spreadsheet_id:
        logging.error("SPREADSHEET_ID not found in environment variables")
        return

    logging.info("Sheets sync started")
    logging.info(f"Target spreadsheet_id: {spreadsheet_id}")

    client = await asyncio.to_thread(get_gspread_client)
    if not client:
        logging.error("Sheets sync aborted: gspread client is not available")
        return

    try:
        # Get unsynced data
        unsynced = await db.get_unsynced_warranties()
        logging.info(f"Unsynced warranties found: {len(unsynced)}")
        if not unsynced:
            logging.info("No new warranties to sync to Google Sheets")
            return

        spreadsheet = await asyncio.to_thread(client.open_by_key, spreadsheet_id)
        logging.info("Spreadsheet opened successfully")
        worksheets = await asyncio.to_thread(spreadsheet.worksheets)
        logging.info(f"Worksheets found: {len(worksheets)}")
        if not worksheets:
            logging.error("No worksheets found in spreadsheet")
            return
        sheet = worksheets[0]
        logging.info(f"Using worksheet[0]: title='{sheet.title}'")
        
        # Проверяем, есть ли заголовки в первой строке
        existing_data = await asyncio.to_thread(sheet.get_all_values)
        headers = [
            "Name",
            "Phone",
            "Email",
            "Username",
            "Date",
            "SKU",
            "Receipt number",
            "Start Date",
        ]
        logging.info(f"Current sheet rows count (including header): {len(existing_data)}")
        
        # Проверяем первую строку
        if not existing_data or len(existing_data) == 0:
            # Таблица полностью пустая - добавляем заголовки
            logging.info("Sheet is empty, adding headers")
            await asyncio.to_thread(sheet.insert_row, headers, 1)
        elif not existing_data[0] or existing_data[0] != headers:
            # Первая строка не содержит правильные заголовки - обновляем её
            logging.info("Updating headers in Google Sheets")
            await asyncio.to_thread(sheet.update, "A1:H1", [headers])
        else:
            logging.info("Headers are already up to date")
        
        # Prepare rows
        # Columns: ... SKU, Receipt number (receipt_text / номер чека WB), Start Date
        rows = []
        warranty_ids = []
        for w in unsynced:
            rows.append([
                w.get("name") or "-",
                w.get("phone") or "-",
                w.get("email") or "-",
                f"@{w.get('username')}" if w.get('username') else "-",
                w.get("created_at") or "-",
                w.get("sku") or "-",
                w.get("receipt_text") or "-",
                w.get("start_date") or "-",
            ])
            warranty_ids.append(w["id"])

        logging.info(f"Prepared rows for append: {len(rows)}")
        if warranty_ids:
            logging.info(f"Warranty IDs to mark as synced: {warranty_ids}")

        # Append rows to sheet
        await asyncio.to_thread(sheet.append_rows, rows)
        logging.info("Rows appended to Google Sheets successfully")
        
        # Mark as synced in DB
        await db.mark_as_synced(warranty_ids)
        logging.info(f"Marked as synced in DB: {len(warranty_ids)}")
        logging.info(f"Successfully synced {len(rows)} warranties to Google Sheets")
        
    except Exception as e:
        logging.exception(f"Error during Google Sheets sync: {e}")

async def sheets_sync_scheduler():
    logging.info("Starting Google Sheets sync scheduler (every 10 minutes)")
    while True:
        try:
            await sync_to_sheets()
        except Exception as e:
            logging.error(f"Unexpected error in sheets_sync_scheduler: {e}")
        
        # Wait for 10 minutes (600 seconds)
        await asyncio.sleep(600)

