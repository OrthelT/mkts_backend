import os
import pathlib
from typing import Dict, List, Tuple
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)


def _find_project_root(start_dir: str = None) -> str:
    """Find the project root directory by looking for pyproject.toml."""
    if start_dir is None:
        start_dir = os.path.dirname(__file__)
    cur = os.path.abspath(start_dir)
    for _ in range(6):
        if os.path.exists(os.path.join(cur, "pyproject.toml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(start_dir)


def _get_env_file_path() -> pathlib.Path:
    """Get the path to the .env file in the project root."""
    project_root = _find_project_root()
    return pathlib.Path(project_root) / ".env"


def validate_env_file_exists() -> Tuple[bool, str]:
    """
    Check if the .env file exists in the project root.

    Returns:
        Tuple[bool, str]: (exists, message)
    """
    env_path = _get_env_file_path()
    if env_path.exists():
        logger.info(f".env file found at: {env_path}")
        return True, f".env file found at: {env_path}"
    else:
        logger.error(f".env file not found at: {env_path}")
        return False, f".env file not found at: {env_path}"


def validate_required_credentials() -> Tuple[bool, List[str], List[str]]:
    """
    Validate that all required credentials are present in the environment.

    Required credentials:
        - CLIENT_ID: Eve Online ESI application client ID
        - SECRET_KEY: Eve Online ESI application secret key
        - REFRESH_TOKEN: Eve Online SSO refresh token

    Returns:
        Tuple[bool, List[str], List[str]]: (is_valid, missing_credentials, present_credentials)
    """
    required_credentials = [
        "CLIENT_ID", 
        "SECRET_KEY", 
        "REFRESH_TOKEN", 
        "TURSO_WCMKTPROD_URL", 
        "TURSO_WCMKTPROD_TOKEN", 
        "TURSO_SDE_URL", 
        "TURSO_SDE_TOKEN", 
        "TURSO_FITTING_URL", 
        "TURSO_FITTING_TOKEN"]
    missing = []
    present = []

    for cred in required_credentials:
        value = os.getenv(cred)
        if not value or value.strip() == "":
            missing.append(cred)
            logger.warning(f"Missing required credential: {cred}")
        else:
            present.append(cred)
            logger.debug(f"Found credential: {cred}")

    is_valid = len(missing) == 0

    if is_valid:
        logger.info("All required credentials are present")
    else:
        logger.error(f"Missing {len(missing)} required credential(s): {', '.join(missing)}")

    return is_valid, missing, present


def validate_optional_credentials() -> Tuple[List[str], List[str]]:
    """
    Check for optional credentials in the environment.

    Optional credentials:
        - Turso database URLs and tokens
        - Google Sheets credentials

    Returns:
        Tuple[List[str], List[str]]: (present_optional, missing_optional)
    """
    optional_credentials = [
        "TURSO_WCMKTTEST_URL", 
        "TURSO_WCMKTTEST_TOKEN", 
        "GOOGLE_SHEET_KEY",
        "GOOGLE_SHEETS_PRIVATE_KEY",
    ]

    present = []
    missing = []

    for cred in optional_credentials:
        value = os.getenv(cred)
        if value and value.strip() != "":
            present.append(cred)
            logger.debug(f"Found optional credential: {cred}")
        else:
            missing.append(cred)

    if present:
        logger.info(f"Found {len(present)} optional credential(s)")
    if missing:
        logger.debug(f"Missing {len(missing)} optional credential(s) (this is OK)")

    return present, missing


def validate_all() -> Dict:
    """
    Perform complete validation of .env file and credentials.

    Returns:
        Dict: Validation results with the following keys:
            - env_file_exists: bool
            - env_file_path: str
            - required_valid: bool
            - missing_required: List[str]
            - present_required: List[str]
            - present_optional: List[str]
            - missing_optional: List[str]
            - is_valid: bool (True if env file exists and all required credentials are present)
            - message: str (summary message)
    """
    # Check if .env file exists
    env_exists, env_message = validate_env_file_exists()
    env_path = str(_get_env_file_path())

    # Load .env if it exists (for local development)
    if env_exists:
        from dotenv import load_dotenv
        load_dotenv()

    # Always validate credentials - they may be set via .env OR environment variables (GitHub Actions)
    required_valid, missing_required, present_required = validate_required_credentials()
    present_optional, missing_optional = validate_optional_credentials()

    # Overall validation result - credentials are what matter, not .env file existence
    is_valid = required_valid

    # Generate summary message
    if is_valid:
        message = "✓ Validation passed: All required credentials are present"
    else:
        message = f"✗ Validation failed: Missing required credentials: {', '.join(missing_required)}"

    result = {
        "env_file_exists": env_exists,
        "env_file_path": env_path,
        "required_valid": required_valid,
        "missing_required": missing_required,
        "present_required": present_required,
        "present_optional": present_optional,
        "missing_optional": missing_optional,
        "is_valid": is_valid,
        "message": message,
    }

    logger.info(message)
    return result


def validate_db_credentials():
    """Legacy function - kept for backward compatibility."""
    from mkts_backend.config.db_config import DatabaseConfig
    db = DatabaseConfig("wcmkt")
    credentials = db.get_db_credentials_dicts()
    return credentials

if __name__ == "__main__":
    pass