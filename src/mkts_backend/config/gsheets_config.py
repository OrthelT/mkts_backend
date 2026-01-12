
import os
import json
import re
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from mkts_backend.config.logging_config import configure_logging

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)

"""
Configures Google Sheets API and updates a spreadsheet with market data.
Works locally with a file, and in CI via GOOGLE_APPLICATION_CREDENTIALS.

Credentials precedence:
1) GOOGLE_APPLICATION_CREDENTIALS = full path to SA JSON (ideal for CI)
2) GOOGLE_SERVICE_ACCOUNT_FILE = filename in project root (ideal for local dev)
3) GOOGLE_SHEET_KEY = literal SA JSON string
4) Default local file: wcupdates-1eec6cbb5e0c.json in project root
"""

# Project root (where pyproject.toml lives)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class GoogleSheetConfig:
    # Defaults (can be overridden via env or __init__)
    _default_local_credentials_file = "wcupdates-1eec6cbb5e0c.json"
    _google_sheet_url = "https://docs.google.com/spreadsheets/d/1I5XwtI9dfAVE4E73v3Lwr8z3od-ibr3h_evBeaAuhaw/edit?gid=800271361#gid=800271361"
    _default_sheet_name = "4H Market Data"
    _default_clear_range = "A2:Z10000"
    _default_worksheet_rows = 1000
    _default_worksheet_cols = 20

    _scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(
        self,
        private_key_file: Optional[str] = None,
        sheet_url: Optional[str] = None,
        sheet_name: Optional[str] = None,
        market_context: Optional["MarketContext"] = None,
    ):
        """
        Initialize Google Sheets configuration.

        Args:
            private_key_file: Path to service account JSON file.
            sheet_url: Google Sheets URL.
            sheet_name: Default worksheet name.
            market_context: Optional MarketContext that provides sheet URL and worksheets.
                           When provided, takes precedence over sheet_url parameter.
        """
        # Allow env overrides without changing code
        # For local dev: use GOOGLE_SERVICE_ACCOUNT_FILE (filename only, resolved to project root)
        # For CI: use GOOGLE_APPLICATION_CREDENTIALS (full path)
        self.google_private_key_file = private_key_file or self._resolve_credentials_file()

        if market_context is not None:
            # Use MarketContext for configuration (preferred method)
            self.google_sheet_url = market_context.gsheets_url
            self.worksheets = market_context.gsheets_worksheets
            self.sheet_name = sheet_name or self.worksheets.get("market_data", self._default_sheet_name)
            logger.info(f"GoogleSheetConfig initialized from MarketContext: {market_context.name}")
        else:
            # Legacy initialization (backward compatibility)
            self.google_sheet_url = sheet_url or os.getenv("GOOGLE_SHEET_URL") or self._google_sheet_url
            self.sheet_name = sheet_name or os.getenv("GOOGLE_SHEET_NAME") or self._default_sheet_name
            self.worksheets = {}

        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

    def _resolve_credentials_file(self) -> Optional[str]:
        """
        Resolve the credentials file path from various sources.
        Returns the full path to the credentials file, or None if not found.
        """
        # Check GOOGLE_SERVICE_ACCOUNT_FILE (filename in project root)
        local_filename = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
        if local_filename:
            local_path = PROJECT_ROOT / local_filename
            if local_path.is_file():
                return str(local_path)
        
        # Check default local file in project root
        default_path = PROJECT_ROOT / self._default_local_credentials_file
        if default_path.is_file():
            return str(default_path)
        
        return None

    # ---------- Credentials handling ----------
    def _build_credentials(self) -> Credentials:
        """
        Precedence:
        1) GOOGLE_APPLICATION_CREDENTIALS = full path to SA JSON (ideal for CI)
        2) GOOGLE_SERVICE_ACCOUNT_FILE = filename in project root (local dev)
        3) GOOGLE_SHEET_KEY = literal SA JSON (string)
        4) self.google_private_key_file (resolved local default)
        """
        # 1) Full file path set by CI or user (GOOGLE_APPLICATION_CREDENTIALS)
        gac_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path and os.path.isfile(gac_path):
            logger.info("Using Google credentials from GOOGLE_APPLICATION_CREDENTIALS file path")
            return Credentials.from_service_account_file(gac_path, scopes=self._scopes)

        # 2) Local file from GOOGLE_SERVICE_ACCOUNT_FILE or default
        if self.google_private_key_file and os.path.isfile(self.google_private_key_file):
            logger.info(f"Using Google credentials from local file: {self.google_private_key_file}")
            return Credentials.from_service_account_file(self.google_private_key_file, scopes=self._scopes)

        # 3) Literal JSON in env (GOOGLE_SHEET_KEY)
        google_credentials_json = os.getenv("GOOGLE_SHEET_KEY")
        if google_credentials_json:
            try:
                credentials_info = json.loads(google_credentials_json)
                logger.info("Using Google credentials from GOOGLE_SHEET_KEY env JSON")
                return Credentials.from_service_account_info(credentials_info, scopes=self._scopes)
            except json.JSONDecodeError as e:
                logger.warning(f"GOOGLE_SHEET_KEY is set but not valid JSON: {e}")

        raise FileNotFoundError(
            "No valid Google credentials found. Options:\n"
            "  1) Set GOOGLE_APPLICATION_CREDENTIALS to full path (for CI)\n"
            "  2) Set GOOGLE_SERVICE_ACCOUNT_FILE to filename in project root (for local dev)\n"
            "  3) Set GOOGLE_SHEET_KEY to JSON string\n"
            f"  4) Place credentials at: {PROJECT_ROOT / self._default_local_credentials_file}"
        )

    def get_client(self) -> gspread.Client:
        if self._client is None:
            try:
                credentials = self._build_credentials()
                self._client = gspread.authorize(credentials)
                logger.info("Google Sheets client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Google Sheets client: {e}")
                raise
        return self._client

    # ---------- Spreadsheet helpers ----------
    @staticmethod
    def extract_sheet_id_from_url(url: str) -> str:
        pattern = r"/spreadsheets/d/([a-zA-Z0-9-_]+)"
        match = re.search(pattern, url)
        if match:
            return match.group(1)
        raise ValueError(f"Could not extract spreadsheet ID from URL: {url}")

    def get_spreadsheet(self, sheet_url: Optional[str] = None) -> gspread.Spreadsheet:
        if self._spreadsheet is None or sheet_url:
            client = self.get_client()
            target_url = sheet_url or self.google_sheet_url
            sheet_id = self.extract_sheet_id_from_url(target_url)
            self._spreadsheet = client.open_by_key(sheet_id)
        return self._spreadsheet

    def get_worksheet(
        self,
        sheet_name: Optional[str] = None,
        create_if_missing: bool = True,
    ) -> gspread.Worksheet:
        target_sheet_name = sheet_name or self.sheet_name
        spreadsheet = self.get_spreadsheet()

        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)
            logger.info(f"Found existing worksheet: {target_sheet_name}")
            return worksheet
        except gspread.WorksheetNotFound:
            if not create_if_missing:
                raise
            worksheet = spreadsheet.add_worksheet(
                title=target_sheet_name,
                rows=self._default_worksheet_rows,
                cols=self._default_worksheet_cols,
            )
            logger.info(f"Created new worksheet: {target_sheet_name}")
            return worksheet

    def get_all_worksheets(self, sheet_url: Optional[str] = None) -> List[gspread.Worksheet]:
        spreadsheet = self.get_spreadsheet(sheet_url)
        return spreadsheet.worksheets()

    # ---------- Update ops ----------
    def update_sheet(
        self,
        data: pd.DataFrame,
        sheet_name: Optional[str] = None,
        append_data: bool = False,
        clear_range: Optional[str] = None,
    ) -> bool:
        try:
            worksheet = self.get_worksheet(sheet_name)

            data = data.infer_objects()
            data = data.fillna(0)
            data = data.reset_index(drop=True)

            # Convert datetime/timestamp columns to strings for JSON serialization
            for col in data.columns:
                if pd.api.types.is_datetime64_any_dtype(data[col]):
                    data[col] = data[col].astype(str)

            logger.info(f"Data shape: {data.shape}")
            logger.info(f"Data columns: {list(data.columns)}")

            values = data.values.tolist()

            if append_data:
                try:
                    existing_values = worksheet.get_all_values()
                    next_row = len(existing_values) + 1 if len(existing_values) > 1 else 2
                except Exception:
                    next_row = 2

                if values:
                    range_name = f"A{next_row}"
                    worksheet.update(range_name, values, value_input_option="USER_ENTERED")
                    logger.info(f"Appended {len(values)} rows starting at row {next_row}")
            else:
                clear_target = clear_range or self._default_clear_range
                worksheet.batch_clear([clear_target])

                if values:
                    worksheet.update("A2", values, value_input_option="USER_ENTERED")
                    logger.info(f"Cleared existing data and inserted {len(values)} rows starting at A2")
                else:
                    logger.info("Cleared existing data, no new data to insert")

            logger.info(f"Successfully updated Google Sheet with {len(data)} rows of data")
            return True
        except Exception as e:
            logger.error(f"Failed to update Google Sheet: {e}")
            return False




if __name__ == "__main__":
    pass
