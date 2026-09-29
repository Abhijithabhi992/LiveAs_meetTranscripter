
import re
import unicodedata
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import main as meeting_main


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Client folders will be created inside this directory.
CLIENTS_DIR = BASE_DIR / "clients"
CLIENTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Meet Transcriber Desktop API",
    version="1.0.0",
)


# ============================================================
# CORS CONFIGURATION
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ClientRequest(BaseModel):
    client_name: str = Field(
        min_length=1,
        max_length=150,
    )


class MeetingStartRequest(BaseModel):
    client_name: str = Field(
        min_length=1,
        max_length=150,
    )


# ============================================================
# CLIENT FOLDER HELPERS
# ============================================================

def clean_folder_name(name: str) -> str:
    """
    Convert a company/client name into a safe folder name.
    """

    name = unicodedata.normalize("NFKC", name)
    name = name.strip()

    # Remove characters that are unsafe in Windows paths.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)

    # Replace multiple spaces with underscores.
    name = re.sub(r"\s+", "_", name)

    # Remove trailing dots and spaces.
    name = name.strip(" .")

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid company or client name.",
        )

    # Windows reserved folder names.
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    if name.upper() in reserved_names:
        name = f"Client_{name}"

    return name


def find_existing_folder(folder_name: str):
    """
    Find an existing client folder without creating
    duplicates due to capitalization.
    """

    for folder in CLIENTS_DIR.iterdir():
        if (
            folder.is_dir()
            and folder.name.casefold() == folder_name.casefold()
        ):
            return folder

    return None


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "application": "Meet Transcriber",
    }


# ============================================================
# CLIENT FOLDER API
# ============================================================

@app.post("/api/clients/resolve")
def resolve_client(request: ClientRequest):
    client_name = request.client_name.strip()

    if not client_name:
        raise HTTPException(
            status_code=400,
            detail="Client name cannot be empty.",
        )

    folder_name = clean_folder_name(client_name)
    existing_folder = find_existing_folder(folder_name)

    if existing_folder:
        client_folder = existing_folder
        is_existing = True
    else:
        client_folder = CLIENTS_DIR / folder_name

        try:
            client_folder.mkdir(
                parents=False,
                exist_ok=False,
            )
            is_existing = False

        except FileExistsError:
            # Another request may have created the folder.
            client_folder = find_existing_folder(folder_name)

            if client_folder is None:
                raise HTTPException(
                    status_code=500,
                    detail="Could not resolve the client folder.",
                )

            is_existing = True

    return {
        "success": True,
        "client_name": client_name,
        "folder_name": client_folder.name,
        "folder_path": str(client_folder.resolve()),
        "existing_client": is_existing,
        "message": (
            "Existing client found."
            if is_existing
            else "New client folder created."
        ),
    }


# ============================================================
# MEETING CONTROL API
# ============================================================

@app.post("/api/meeting/start")
def start_meeting(request: MeetingStartRequest):
    """
    Start a meeting transcription session.
    """

    client_name = request.client_name.strip()

    if not client_name:
        raise HTTPException(
            status_code=400,
            detail="Client name cannot be empty.",
        )

    try:
        # Resolve or create the client folder.
        client_info = resolve_client(
            ClientRequest(client_name=client_name)
        )

        # Start meeting using the resolved client folder.
        result = meeting_main.start_api_meeting(
            client_name,
            client_info["folder_path"],
        )

        return {
            "success": True,
            "message": "Meeting start requested.",
            **result,
        }

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not start meeting: {exc}",
        )


@app.post("/api/meeting/stop")
def stop_meeting():
    """
    Request the current meeting to stop.
    """

    try:
        result = meeting_main.stop_api_meeting()

        return {
            "success": True,
            "message": "Meeting stop requested.",
            **result,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not stop meeting: {exc}",
        )


@app.get("/api/meeting/status")
def meeting_status():
    """
    Get current meeting status.
    """

    try:
        return meeting_main.get_api_meeting_status()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not get meeting status: {exc}",
        )


# ============================================================
# TRANSCRIPT API
# ============================================================

@app.get("/api/meeting/transcript")
def meeting_transcript():
    """
    Get the current transcript.
    """

    try:
        transcript = meeting_main.get_api_transcript()

        return {
            "success": True,
            "transcript": transcript,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not get transcript: {exc}",
        )


# ============================================================
# SUGGESTIONS API
# ============================================================

@app.get("/api/meeting/suggestions")
def meeting_suggestions():
    """
    Return suggestions detected during the meeting.
    """

    try:
        return {
            "success": True,
            "suggestions": meeting_main.get_api_suggestions(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not get suggestions: {exc}",
        )


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8765,
        reload=False,
    )