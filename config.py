"""
Central configuration. Values are read from environment variables so the
API key never has to be hard-coded into the script.

On Windows (cmd.exe), before running the app:
    set SARVAM_API_KEY=your_key_here

Or (PowerShell):
    $env:SARVAM_API_KEY = "your_key_here"

Or just create a ".env" file next to main.py (see README) and it will be
picked up automatically.
"""

import os

# --- Load a local .env file if present (optional convenience) ---
def _load_dotenv(path=".env"):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

_load_dotenv()

# --- Sarvam API ---
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")

# Speech-to-text model + mode.
# mode="translate" -> transcribes speech in the source language AND
# translates it to English in one step, which is what you asked for.
STT_MODEL = "saaras:v3"
STT_MODE = "translate"  # or "translate" if you want English translation too

# Language code fed to the model. Sarvam's streaming endpoint expects a
# BCP-47-style code (e.g. "ml-IN", "kn-IN", "en-IN"). Your meetings mix
# Malayalam, English and Kannada, so:
#   - Try "unknown" first (many Sarvam endpoints auto-detect the spoken
#     language from this value). If your account/model rejects "unknown"
#     on the streaming endpoint, pick the language your client speaks most
#     and set it explicitly (e.g. "ml-IN"), or run one stream per language
#     and see which gives cleaner output.
LANGUAGE_CODE = os.environ.get("SARVAM_LANGUAGE_CODE", "unknown")

# --- Audio ---
SAMPLE_RATE = 16000       # Hz, required by Sarvam's streaming endpoint
CHANNELS = 1              # mono
CHUNK_MS = 500            # how much audio we buffer before sending a chunk

# --- LLM used to turn the transcript into a proposal ---
# Uses Sarvam's own OpenAI-compatible chat endpoint, so you only need the
# one API key for everything.
SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
SARVAM_CHAT_MODEL = "sarvam-105b"  # or "sarvam-7b" if you want faster but less capable

# # --- Translation (English proposal -> Malayalam) ---
# SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
# SARVAM_TRANSLATE_MODEL = "mayura:v1"
# SOURCE_LANGUAGE_CODE = "en-IN"
# TARGET_LANGUAGE_CODE = "ml-IN"
# # Sarvam's translate endpoint has a per-request character limit; long
# # sections are automatically chunked below this size.
# TRANSLATE_CHUNK_CHARS = 900

# --- Company branding ---
COMPANY_NAME = os.environ.get("COMPANY_NAME", "LiveAs Technologies")
COMPANY_TAGLINE = os.environ.get("COMPANY_TAGLINE", "")  # e.g. "Software Solutions & Consulting"
COMPANY_EMAIL = os.environ.get("COMPANY_EMAIL", "")
COMPANY_PHONE = os.environ.get("COMPANY_PHONE", "")
COMPANY_WEBSITE = os.environ.get("COMPANY_WEBSITE", "")
# Optional: path to a logo image (png/jpg) to place at the top of the
# proposal. Leave the file missing and it's simply skipped.
COMPANY_LOGO_PATH = os.environ.get("COMPANY_LOGO_PATH", "assets/logo.png")

# --- Output ---
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
# Also produce a .pdf alongside the .docx (requires Microsoft Word to be
# installed on this Windows machine, via the docx2pdf package).
EXPORT_PDF = True
