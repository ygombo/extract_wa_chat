#!/usr/bin/env python3
"""Filter a WhatsApp _chat.txt export by date and write DOCX/Google Docs reports."""

from __future__ import annotations

import argparse
import html
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse


CHAT_LINE_RE = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}), "
    r"(?P<time>\d{1,2}\.\d{2}(?:\.\d{2})?)\s*(?P<ampm>[AP]M)?\] "
    r"(?P<sender>.*?): (?P<message>.*)$",
    re.IGNORECASE,
)

INVISIBLE_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
DOC_ID_RE = re.compile(r"/document/d/([^/]+)")
CHECKIN_RE = re.compile(r"^\s*check[\s-]*in\b", re.IGNORECASE)
CHECKOUT_RE = re.compile(r"^\s*check[\s-]*out\b", re.IGNORECASE)


@dataclass
class Message:
    timestamp: datetime
    sender: str
    text: str


@dataclass
class JournalPerson:
    sender: str
    checkins: list[Message]
    checkouts: list[Message]


def clean_text(value: str) -> str:
    return value.translate({ord(ch): None for ch in INVISIBLE_CHARS}).replace("*", "").strip()


def parse_chat_timestamp(date_text: str, time_text: str, ampm: str | None) -> datetime:
    day, month, year = [int(part) for part in date_text.split("/")]
    if year < 100:
        year += 2000

    time_parts = [int(part) for part in time_text.split(".")]
    hour = time_parts[0]
    minute = time_parts[1]
    second = time_parts[2] if len(time_parts) == 3 else 0

    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

    return datetime(year, month, day, hour, minute, second)


def parse_filter_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        f"invalid date '{value}'. Use YYYY-MM-DD, DD/MM/YYYY, or DD/MM/YY."
    )


def read_messages(chat_path: Path) -> list[Message]:
    messages: list[Message] = []

    with chat_path.open("r", encoding="utf-8-sig") as chat_file:
        for raw_line in chat_file:
            line = raw_line.rstrip("\n\r")
            match = CHAT_LINE_RE.match(line)

            if match:
                messages.append(
                    Message(
                        timestamp=parse_chat_timestamp(
                            match.group("date"),
                            match.group("time"),
                            match.group("ampm"),
                        ),
                        sender=clean_text(match.group("sender")),
                        text=clean_text(match.group("message")),
                    )
                )
            elif messages:
                continuation = clean_text(line)
                messages[-1].text = f"{messages[-1].text}\n{continuation}".strip()

    return messages


def filter_messages(
    messages: list[Message], start_date: date | None, end_date: date | None
) -> list[Message]:
    return [
        message
        for message in messages
        if (start_date is None or message.timestamp.date() >= start_date)
        and (end_date is None or message.timestamp.date() <= end_date)
    ]


def date_range_title(start_date: date | None, end_date: date | None) -> str:
    if start_date and end_date:
        return f"{start_date.isoformat()} to {end_date.isoformat()}"
    if start_date:
        return f"From {start_date.isoformat()}"
    if end_date:
        return f"Until {end_date.isoformat()}"
    return "All dates"


def daily_journal(messages: list[Message]) -> dict[date, dict[str, JournalPerson]]:
    journal: dict[date, dict[str, JournalPerson]] = {}

    for message in messages:
        is_checkin = CHECKIN_RE.search(message.text)
        is_checkout = CHECKOUT_RE.search(message.text)
        if not is_checkin and not is_checkout:
            continue

        message_date = message.timestamp.date()
        people = journal.setdefault(message_date, {})
        person = people.setdefault(
            message.sender,
            JournalPerson(sender=message.sender, checkins=[], checkouts=[]),
        )

        if is_checkin:
            person.checkins.append(message)
        if is_checkout:
            person.checkouts.append(message)

    return journal


def message_text_for_journal(message: Message) -> str:
    return message.text


def plain_text_report(
    messages: list[Message], start_date: date | None, end_date: date | None
) -> str:
    lines = ["Dev Daily Journal", ""]
    journal = daily_journal(messages)

    for journal_date, people in journal.items():
        lines.append(journal_date.strftime("%d %B %Y"))
        lines.append("")

        for person in people.values():
            lines.append(person.sender)
            for checkin in person.checkins:
                lines.extend(message_text_for_journal(checkin).splitlines() or [""])
                lines.append("")
            for checkout in person.checkouts:
                lines.extend(message_text_for_journal(checkout).splitlines() or [""])
                lines.append("")

        lines.append("")

    if not journal:
        lines.append("No check-in or check-out messages found for the selected date range.")

    return "\n".join(lines).rstrip() + "\n"


def google_doc_text_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def bold_ranges_for_google_doc(
    text: str, messages: list[Message], base_index: int
) -> list[tuple[int, int]]:
    journal = daily_journal(messages)
    names = {
        person.sender
        for people in journal.values()
        for person in people.values()
    }
    ranges: list[tuple[int, int]] = []
    offset = base_index

    for line in text.splitlines(keepends=True):
        line_text = line.rstrip("\r\n")
        if line_text in names:
            ranges.append((offset, offset + google_doc_text_length(line_text)))
        offset += google_doc_text_length(line)

    return ranges


def xml_text(value: str) -> str:
    return html.escape(value, quote=False)


def text_runs(value: str) -> str:
    lines = value.splitlines() or [""]
    parts: list[str] = []
    for index, line in enumerate(lines):
        if index:
            parts.append("<w:br/>")
        preserve = ' xml:space="preserve"' if line.startswith(" ") or line.endswith(" ") else ""
        parts.append(f"<w:t{preserve}>{xml_text(line)}</w:t>")
    return "".join(parts)


def paragraph(text: str, style: str | None = None, bold: bool = False) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    bold_xml = "<w:b/>" if bold else ""
    return f"<w:p>{style_xml}<w:r><w:rPr>{bold_xml}</w:rPr>{text_runs(text)}</w:r></w:p>"


def message_paragraph(message: Message) -> str:
    sender = xml_text(message.sender)
    time_text = message.timestamp.strftime("%H:%M")
    body = text_runs(message.text)
    return (
        "<w:p>"
        "<w:r><w:rPr><w:b/></w:rPr>"
        f'<w:t>{sender} ({time_text})</w:t>'
        "</w:r>"
        '<w:r><w:t xml:space="preserve">: </w:t></w:r>'
        f"<w:r>{body}</w:r>"
        "</w:p>"
    )


def document_xml(messages: list[Message]) -> str:
    paragraphs: list[str] = [paragraph("Dev Daily Journal", "Heading1")]
    journal = daily_journal(messages)

    for journal_date, people in journal.items():
        paragraphs.append(paragraph(journal_date.strftime("%d %B %Y"), "Heading2"))

        for person in people.values():
            paragraphs.append(paragraph(person.sender, bold=True))
            for checkin in person.checkins:
                paragraphs.append(paragraph(message_text_for_journal(checkin)))
            for checkout in person.checkouts:
                paragraphs.append(paragraph(message_text_for_journal(checkout)))
            paragraphs.append(paragraph(""))

    if not journal:
        paragraphs.append(
            paragraph("No check-in or check-out messages found for the selected date range.")
        )

    body = "".join(paragraphs)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


def styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr><w:sz w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:pPr><w:spacing w:before="220" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/></w:rPr>
  </w:style>
</w:styles>
"""


def write_docx(messages: list[Message], output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        docx.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        )
        docx.writestr("word/document.xml", document_xml(messages))
        docx.writestr("word/styles.xml", styles_xml())


def get_google_credentials(
    credentials_path: Path,
    token_path: Path,
    auth_port: int,
    reset_token: bool,
    login_hint: str | None,
):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Google Docs output requires these packages:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    scopes = ["https://www.googleapis.com/auth/documents"]
    creds = None

    if reset_token and token_path.exists():
        token_path.unlink()

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Google credentials file not found: {credentials_path}\n"
                    "Download OAuth client credentials from Google Cloud and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            try:
                auth_kwargs = {"prompt": "select_account consent"}
                if login_hint:
                    auth_kwargs["login_hint"] = login_hint
                creds = flow.run_local_server(
                    port=auth_port,
                    redirect_uri_trailing_slash=False,
                    **auth_kwargs,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Google OAuth sign-in failed.\n"
                    "Your credentials.json is a Desktop/installed OAuth client, so "
                    "Google should accept the local redirect automatically.\n"
                    f"Underlying error: {exc}"
                ) from exc

        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_docs_service(
    credentials_path: Path,
    token_path: Path,
    auth_port: int,
    reset_token: bool,
    login_hint: str | None,
):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Docs output requires these packages:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    return build(
        "docs",
        "v1",
        credentials=get_google_credentials(
            credentials_path,
            token_path,
            auth_port,
            reset_token,
            login_hint,
        ),
    )


def normalize_google_doc_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        match = DOC_ID_RE.search(parsed.path)
        if match:
            return match.group(1)
    return value


def google_api_error_message(exc: Exception, document_id: str) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    details = ""
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        try:
            payload = json.loads(content.decode("utf-8"))
            details = payload.get("error", {}).get("message", "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            details = content.decode("utf-8", errors="replace")

    if status == 404:
        return (
            f"Google Doc '{document_id}' was not found.\n"
            "Use the document ID from the Google Docs URL, not the tab ID.\n"
            "Example URL:\n"
            "  https://docs.google.com/document/d/DOCUMENT_ID/edit?tab=t.xxxxx\n"
            "Pass only DOCUMENT_ID, or paste the full URL into --google-doc-id."
        )
    if status == 403:
        message = (
            f"Google refused access to document '{document_id}'.\n"
            "Make sure the signed-in Google account can edit the document.\n"
            "If the wrong account is cached, rerun with --reset-google-token."
        )
        if details:
            message += f"\nGoogle detail: {details}"
        return message
    return f"Google API error while writing document '{document_id}': {exc}"


def iter_google_tabs(tabs: list[dict]):
    for tab in tabs:
        yield tab
        yield from iter_google_tabs(tab.get("childTabs", []))


def find_google_tab(document: dict, tab_title: str) -> dict | None:
    for tab in iter_google_tabs(document.get("tabs", [])):
        if tab.get("tabProperties", {}).get("title") == tab_title:
            return tab
    return None


def google_tab_end_index(tab: dict) -> int:
    body = tab.get("documentTab", {}).get("body", {})
    content = body.get("content", [])
    if not content:
        return 1
    return content[-1].get("endIndex", 1)


def clear_google_tab(service, document_id: str, tab_id: str, end_index: int) -> None:
    if end_index <= 2:
        return

    service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "deleteContentRange": {
                        "range": {
                            "segmentId": "",
                            "tabId": tab_id,
                            "startIndex": 1,
                            "endIndex": end_index - 1,
                        }
                    }
                }
            ]
        },
    ).execute()


def write_google_doc_tab(
    messages: list[Message],
    document_id: str,
    tab_title: str,
    start_date: date | None,
    end_date: date | None,
    credentials_path: Path,
    token_path: Path,
    auth_port: int,
    reset_token: bool,
    login_hint: str | None,
    replace_tab: bool,
) -> None:
    document_id = normalize_google_doc_id(document_id)
    service = build_docs_service(
        credentials_path,
        token_path,
        auth_port,
        reset_token,
        login_hint,
    )
    try:
        document = (
            service.documents()
            .get(documentId=document_id, includeTabsContent=True)
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(google_api_error_message(exc, document_id)) from exc

    tab = find_google_tab(document, tab_title)
    if not tab:
        available_tabs = [
            item.get("tabProperties", {}).get("title", "(untitled)")
            for item in iter_google_tabs(document.get("tabs", []))
        ]
        raise ValueError(
            f"Tab '{tab_title}' was not found in Google Doc {document_id}.\n"
            f"Create the tab first, then run this script again.\n"
            f"Available tabs: {', '.join(available_tabs)}"
        )

    tab_id = tab["tabProperties"]["tabId"]

    try:
        insertion_index = google_tab_end_index(tab) - 1
        if replace_tab:
            clear_google_tab(service, document_id, tab_id, google_tab_end_index(tab))
            insertion_index = 1

        report_text = plain_text_report(messages, start_date, end_date)
        requests = [
            {
                "insertText": {
                    "location": {
                        "tabId": tab_id,
                        "index": insertion_index,
                    },
                    "text": report_text,
                }
            }
        ]

        for start_index, end_index in reversed(
            bold_ranges_for_google_doc(report_text, messages, insertion_index)
        ):
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {
                            "segmentId": "",
                            "tabId": tab_id,
                            "startIndex": start_index,
                            "endIndex": end_index,
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                }
            )

        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        raise RuntimeError(google_api_error_message(exc, document_id)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter a WhatsApp _chat.txt export by date and write a DOCX or Google Doc tab."
    )
    parser.add_argument(
        "--input",
        default="_chat.txt",
        type=Path,
        help="Path to the WhatsApp chat export. Default: _chat.txt",
    )
    parser.add_argument(
        "--output",
        default="filtered_chat.docx",
        type=Path,
        help="Output DOCX path. Default: filtered_chat.docx",
    )
    parser.add_argument(
        "--start",
        type=parse_filter_date,
        help="Inclusive start date. Formats: YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY.",
    )
    parser.add_argument(
        "--end",
        type=parse_filter_date,
        help="Inclusive end date. Formats: YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY.",
    )
    parser.add_argument(
        "--google-doc-id",
        help="Existing Google Doc ID to write into. The target tab must already exist.",
    )
    parser.add_argument(
        "--google-tab-title",
        help="Existing Google Docs tab title to write into.",
    )
    parser.add_argument(
        "--google-credentials",
        default=Path("credentials.json"),
        type=Path,
        help="OAuth client credentials JSON path. Default: credentials.json",
    )
    parser.add_argument(
        "--google-token",
        default=Path("token.json"),
        type=Path,
        help="OAuth token cache path. Default: token.json",
    )
    parser.add_argument(
        "--google-auth-port",
        default=8080,
        type=int,
        help="Local OAuth callback port. Default: 8080",
    )
    parser.add_argument(
        "--reset-google-token",
        action="store_true",
        help="Delete cached token.json and force Google login again.",
    )
    parser.add_argument(
        "--google-login-hint",
        help="Email address to suggest on the Google login screen.",
    )
    parser.add_argument(
        "--replace-google-tab",
        action="store_true",
        help="Delete existing tab content before writing the filtered chat.",
    )
    parser.add_argument(
        "--no-docx",
        action="store_true",
        help="Skip DOCX output. Useful when only writing to Google Docs.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.start and args.end and args.start > args.end:
        parser.error("--start cannot be after --end")

    if args.no_docx and not args.google_doc_id:
        parser.error("--no-docx requires --google-doc-id")

    if args.google_doc_id and not args.google_tab_title:
        parser.error("--google-doc-id requires --google-tab-title")

    if args.google_tab_title and not args.google_doc_id:
        parser.error("--google-tab-title requires --google-doc-id")

    if not args.no_docx and args.output.suffix.lower() != ".docx":
        parser.error("--output must end with .docx")

    messages = read_messages(args.input)
    filtered = filter_messages(messages, args.start, args.end)

    if not args.no_docx:
        write_docx(filtered, args.output)

    if args.google_doc_id:
        try:
            write_google_doc_tab(
                filtered,
                args.google_doc_id,
                args.google_tab_title,
                args.start,
                args.end,
                args.google_credentials,
                args.google_token,
                args.google_auth_port,
                args.reset_google_token,
                args.google_login_hint,
                args.replace_google_tab,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            parser.exit(1, f"error: {exc}\n")

    print(f"Read {len(messages)} messages.")
    if not args.no_docx:
        print(f"Wrote {len(filtered)} messages to {args.output}.")
    if args.google_doc_id:
        print(
            f"Wrote {len(filtered)} messages to Google Doc tab "
            f"'{args.google_tab_title}' in document {args.google_doc_id}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
