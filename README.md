# WhatsApp Chat Daily Journal Exporter

Filter a WhatsApp `_chat.txt` export by date and turn it into reports.

This repo has two scripts:

- `filter_chat.py` creates a daily journal as a local `.docx` or writes it into an existing Google Doc tab.
- `summarize_to_slides.py` uses OpenAI to summarize the journal and append a weekly report to an existing Google Slides presentation.

The journal format is:

```text
Dev Daily Journal

Date

Username
Check-in message
Check-out message
```

Only messages that start with check-in/check-out variants are included, such as:

- `Checkin`
- `Check in`
- `Check-in`
- `Checkout`
- `Check out`
- `Check-out`

## Setup

Use Python 3.10 or newer.

For local DOCX output from `filter_chat.py`, no extra packages are required.

For Google Docs, Google Slides, and OpenAI summarization, install the dependencies:

```bash
pip install -r requirements.txt
```

## Export to DOCX

```bash
./filter_chat.py \
  --start 2026-05-25 \
  --end 2026-05-29 \
  --output week.docx
```

The script reads `_chat.txt` by default.

Supported date formats:

```text
2026-05-25
25/05/2026
25/05/26
```

## Export to Google Docs Tab

The Google Doc and tab must already exist.

```bash
./filter_chat.py \
  --start 2026-05-25 \
  --end 2026-05-29 \
  --google-doc-id YOUR_GOOGLE_DOC_ID \
  --google-tab-title "May 25 - May 29" \
  --replace-google-tab \
  --no-docx
```

You can also pass the full Google Docs URL as `--google-doc-id`; the script will extract the real document ID.

Use `--replace-google-tab` to clear existing tab content before writing.

## Google OAuth Setup

1. Open Google Cloud credentials:
   <https://console.cloud.google.com/apis/credentials>
2. Create an OAuth client ID.
3. Select application type **Desktop app**.
4. Download the JSON file.
5. Save it in this folder as:

```text
credentials.json
```

6. Enable the required APIs for the same Google Cloud project:

- Google Docs API, if using `filter_chat.py` with Google Docs
- Google Slides API, if using `summarize_to_slides.py`

On the first Google Docs run, the script opens a browser for login and creates:

```text
token.json
```

If the wrong Google account is cached, force login again:

```bash
./filter_chat.py \
  --start 2026-05-25 \
  --end 2026-05-29 \
  --google-doc-id YOUR_GOOGLE_DOC_ID \
  --google-tab-title "May 25 - May 29" \
  --replace-google-tab \
  --no-docx \
  --reset-google-token \
  --google-login-hint your.email@example.com
```

## Common Issues

### Google refused access

The signed-in Google account does not have edit access to the document, or the wrong account is cached.

Try:

```bash
--reset-google-token --google-login-hint your.email@example.com
```

Also confirm the account can edit the Google Doc in the browser.

### Requested entity was not found

The value passed to `--google-doc-id` is probably wrong.

Use the ID from this part of the Google Docs URL:

```text
https://docs.google.com/document/d/DOCUMENT_ID/edit?tab=t.xxxxx
```

Pass `DOCUMENT_ID`, not the tab ID.

### Tab was not found

Create the tab manually in Google Docs first, then rerun the script with the exact tab title:

```bash
--google-tab-title "May 25 - May 29"
```

### OAuth redirect or sign-in error

Create a new OAuth client with application type **Desktop app**, download the JSON, and save it as `credentials.json`.

## Summarize to Google Slides

Use `summarize_to_slides.py` to summarize the filtered daily journal with OpenAI and append a weekly report to an existing Google Slides presentation.

The target Google Slides presentation must already exist.

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="your_api_key_here"
```

Or create a local JSON file named `openai_key.json`:

```json
{
  "api_key": "your_api_key_here"
}
```

`openai_key.json` is ignored by Git.

Then run:

```bash
./summarize_to_slides.py \
  --start 2026-05-25 \
  --end 2026-05-29 \
  --presentation-id YOUR_GOOGLE_SLIDES_ID \
  --openai-api-key-file openai_key.json \
  --google-login-hint your.email@example.com
```

You can also pass a full Google Slides URL as `--presentation-id`.

The script creates six slides in the weekly report format:

- cover slide with week, division, products covered, team members, and daily journal label
- Key Highlights
- Plan for Next Week
- Bottlenecks / Blockers
- Plan to Resolve Blockers
- Overall Progress and Impact

It uses `slides_token.json` for Google OAuth because Slides needs a different permission scope from Docs. That token is ignored by Git.

On first run, the script opens a browser for Google login and creates:

```text
slides_token.json
```

If the wrong Google account is cached, rerun with:

```bash
--reset-google-token --google-login-hint your.email@example.com
```

Useful options:

```bash
--model gpt-4.1-mini
--openai-api-key YOUR_KEY
--openai-api-key-file openai_key.json
--summary-json summary.json
--reset-google-token
--max-chat-chars 50000
```

## Useful Commands

Show all options:

```bash
./filter_chat.py --help
./summarize_to_slides.py --help
```

Write Google Docs only:

```bash
./filter_chat.py --start 2026-05-25 --end 2026-05-29 \
  --google-doc-id YOUR_GOOGLE_DOC_ID \
  --google-tab-title "May 25 - May 29" \
  --replace-google-tab \
  --no-docx
```

Write both DOCX and Google Docs:

```bash
./filter_chat.py --start 2026-05-25 --end 2026-05-29 \
  --output week.docx \
  --google-doc-id YOUR_GOOGLE_DOC_ID \
  --google-tab-title "May 25 - May 29" \
  --replace-google-tab
```

Summarize to Google Slides:

```bash
./summarize_to_slides.py --start 2026-05-25 --end 2026-05-29 \
  --presentation-id YOUR_GOOGLE_SLIDES_ID \
  --openai-api-key-file openai_key.json \
  --google-login-hint your.email@example.com
```
