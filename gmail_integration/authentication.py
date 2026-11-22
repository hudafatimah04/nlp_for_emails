"""Create a Gmail service with read-only OAuth credentials."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from config import settings

SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)


def _load_token(path: Path) -> Credentials | None:
    if not path.exists():
        return None
    try:
        credentials = Credentials.from_authorized_user_file(str(path))
    except OSError:
        raise RuntimeError("Could not read the Gmail token. Check GMAIL_TOKEN_PATH permissions.") from None
    except (ValueError, TypeError):
        return None
    # Inspect the stored scopes without overriding them at load time.
    if set(credentials.scopes or ()) != set(SCOPES):
        return None
    return credentials


def _save_token(path: Path, credentials: Credentials) -> None:
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as token_file:
            temporary_path = Path(token_file.name)
            token_file.write(credentials.to_json())
        temporary_path.replace(path)
    except OSError:
        raise RuntimeError(
            "Could not save the Gmail token. Check that GMAIL_TOKEN_PATH has a writable parent directory."
        ) from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def get_gmail_service() -> Resource:
    """Reuse, refresh, or obtain a read-only token, then build the Gmail client."""
    if not settings.GMAIL_CREDENTIALS_PATH:
        raise RuntimeError("Set GMAIL_CREDENTIALS_PATH to your Google OAuth desktop credentials JSON.")
    client_path = Path(settings.GMAIL_CREDENTIALS_PATH).expanduser()
    if not client_path.is_file():
        raise RuntimeError("Gmail credentials file is missing. Check GMAIL_CREDENTIALS_PATH.")
    if not settings.GMAIL_TOKEN_PATH:
        raise RuntimeError("Set GMAIL_TOKEN_PATH to a local file for the Gmail OAuth token.")
    token_path = Path(settings.GMAIL_TOKEN_PATH).expanduser()
    if token_path.resolve() == client_path.resolve():
        raise RuntimeError("Use separate files for Gmail credentials and the OAuth token.")

    credentials = _load_token(token_path)
    if credentials is not None and credentials.valid:
        return build("gmail", "v1", credentials=credentials)

    if credentials is not None and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except GoogleAuthError:
            credentials = None

    if credentials is None or not credentials.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=SCOPES)
            credentials = flow.run_local_server(port=0, authorization_prompt_message="")
        except Exception:
            raise RuntimeError(
                "Gmail authorization failed. Check the OAuth desktop credentials and complete consent in your browser."
            ) from None

    granted = credentials.granted_scopes
    if granted is not None and set(granted) != set(SCOPES):
        raise RuntimeError("Gmail authorization must grant only the read-only Gmail scope.")
    _save_token(token_path, credentials)
    return build("gmail", "v1", credentials=credentials)
