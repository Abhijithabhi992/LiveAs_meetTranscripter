# Meet → Bilingual Proposal (LiveAs Technologies)

Live-captures your microphone and your speaker output (i.e. what the client
says on Google Meet) on Windows, transcribes + translates both to English
in real time using Sarvam AI's Saaras v3 model, and generates a **branded,
bilingual (English + Malayalam) proposal** — as both `.docx` and `.pdf` —
from the conversation when the call ends.

## How it works

- **Your mic** → one Sarvam streaming session, tagged "You"
- **Your speakers (WASAPI loopback)** → a second Sarvam streaming session,
  tagged "Client" — this captures Meet's audio output without needing
  screen access or any Google Meet integration at all
- Both streams use `mode="translate"`, so Malayalam / Kannada / English
  (or code-switched mixes) all come back as English text
- On stop (Ctrl+C):
  1. The merged transcript is sent to Sarvam's chat model, which drafts a
     structured proposal in English
  2. That proposal is translated to Malayalam section-by-section using
     Sarvam's Mayura translation model
  3. Both language versions are laid out in one document under a
     **LiveAs Technologies** letterhead (English section, then a
     Malayalam section) and saved as `output/Proposal_<client>_<time>.docx`
  4. The same document is exported to `output/Proposal_<client>_<time>.pdf`
     (requires Microsoft Word installed — see notes below)

## Setup (Windows)

1. Install Python 3.10+ if you don't have it.
2. In this folder, create a virtual environment and install dependencies:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Get a Sarvam API key from https://dashboard.sarvam.ai and set it:
   ```
   set SARVAM_API_KEY=your_key_here
   ```
   or create a file named `.env` in this folder containing:
   ```
   SARVAM_API_KEY=your_key_here
   ```

## Running

```
python main.py
```

Then join/continue your Google Meet call in the browser as normal — no
integration with Meet itself is needed since audio capture happens at the
OS level. When the call is done, press **Ctrl+C** in the terminal. It will
print the live transcript as it goes and, on stop, write:

- `output/transcript_<timestamp>.txt` — the raw merged transcript
- `output/proposal_<timestamp>.docx` — the generated proposal

To tag the proposal with a client name:
```
python main.py --client "Acme Corp"
```

If your microphone or speaker device isn't the Windows default (e.g. you
use a headset for the mic but Meet's audio plays out of your monitor
speakers), list all devices and pick indices manually:
```
python main.py --list-devices
```
Then edit the `mic_device_index` / `loopback_device_index` arguments passed
into `create_default_sources(...)` in `main.py`.

## Customizing the letterhead

Set these via environment variables (or in `.env`) — all optional except
the company name, which already defaults to LiveAs Technologies:
```
COMPANY_NAME=LiveAs Technologies
COMPANY_TAGLINE=Software Solutions & Consulting
COMPANY_EMAIL=hello@liveastech.com
COMPANY_PHONE=+91 90000 00000
COMPANY_WEBSITE=www.liveastech.com
COMPANY_LOGO_PATH=assets/logo.png
```
Drop a `logo.png` into an `assets/` folder next to `main.py` and it will
be placed at the top of the proposal automatically; if it's missing, the
letterhead just shows the company name/tagline/contact line instead.

## PDF export

`docx2pdf` converts the `.docx` to `.pdf` by driving a real, installed
copy of Microsoft Word in the background — so **Microsoft Word must be
installed** on the Windows machine running this script. If it isn't (or
the conversion fails for any reason), the script prints a warning and
still leaves you the `.docx`, which you can save as PDF manually from
Word (`File > Save As > PDF`). If you'd rather not depend on Word at all,
LibreOffice's headless converter is a free alternative — ask me to swap
`_export_pdf()` over to that if you'd prefer it.

## Things worth knowing before you rely on this

- **This is Windows-only** as written — WASAPI loopback capture doesn't
  exist on macOS/Linux the same way; those need a virtual audio device
  (BlackHole on Mac, a PulseAudio monitor source on Linux) and a different
  `audio_capture.py`.
- **Language auto-detection**: `LANGUAGE_CODE = "unknown"` in `config.py`
  is a best-effort auto-detect setting. If Sarvam's streaming endpoint
  rejects it for your account, set it to your client's dominant language
  (`"ml-IN"` or `"kn-IN"`) in `config.py` or via the `SARVAM_LANGUAGE_CODE`
  environment variable.
- **Consent**: recording a call — even just to transcribe it — has legal
  consent requirements in many places (some require informing or getting
  agreement from the other party before recording). Worth checking what
  applies where your clients are, and/or saying at the start of the call
  that you're taking AI-assisted notes.
- **Accuracy of the generated proposal**: the LLM step is instructed not
  to invent numbers, dates, or commitments that weren't actually said —
  but always read the draft before sending it to a client. Treat it as a
  first draft, not a final document.
- **Costs**: both the streaming STT and the chat-completion call are
  billed by Sarvam per their pricing page — a long call will use
  meaningfully more streaming minutes than a short one.

## File overview

| File | Purpose |
|---|---|
| `config.py` | API key, language, audio, and model settings |
| `audio_capture.py` | Mic + WASAPI loopback capture, resampled to 16kHz mono |
| `sarvam_stream.py` | Streams audio to Sarvam's WebSocket STT, writes results to the transcript |
| `transcript_store.py` | Thread-safe ordered transcript across both speakers |
| `proposal_writer.py` | Sends transcript to Sarvam's chat model, writes the `.docx` |
| `main.py` | Orchestrates everything; the script you actually run |
