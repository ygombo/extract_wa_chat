#!/usr/bin/env python3
"""Summarize filtered WhatsApp chats with OpenAI and append slides to Google Slides."""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from filter_chat import (
    Message,
    daily_journal,
    date_range_title,
    filter_messages,
    parse_filter_date,
    plain_text_report,
    read_messages,
)


PRESENTATION_ID_RE = re.compile(r"/presentation/d/([^/]+)")
SLIDES_SCOPE = ["https://www.googleapis.com/auth/presentations"]


def normalize_presentation_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        match = PRESENTATION_ID_RE.search(parsed.path)
        if match:
            return match.group(1)
    return value


def google_error_message(exc: Exception, presentation_id: str) -> str:
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
            f"Google Slides presentation '{presentation_id}' was not found.\n"
            "Use the presentation ID from the URL, or paste the full Google Slides URL."
        )
    if status == 403:
        message = (
            f"Google refused access to presentation '{presentation_id}'.\n"
            "Make sure the signed-in Google account can edit the presentation.\n"
            "If the wrong account is cached, rerun with --reset-google-token."
        )
        if details:
            message += f"\nGoogle detail: {details}"
        return message
    return f"Google Slides API error for presentation '{presentation_id}': {exc}"


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
            "Google Slides output requires these packages:\n"
            "  pip install -r requirements.txt"
        ) from exc

    creds = None

    if reset_token and token_path.exists():
        token_path.unlink()

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_path, SLIDES_SCOPE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Google credentials file not found: {credentials_path}\n"
                    "Create a Desktop app OAuth client in Google Cloud and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path,
                SLIDES_SCOPE,
            )
            auth_kwargs = {"prompt": "select_account consent"}
            if login_hint:
                auth_kwargs["login_hint"] = login_hint
            try:
                creds = flow.run_local_server(
                    port=auth_port,
                    redirect_uri_trailing_slash=False,
                    **auth_kwargs,
                )
            except Exception as exc:
                raise RuntimeError(f"Google OAuth sign-in failed: {exc}") from exc

        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def build_slides_service(
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
            "Google Slides output requires these packages:\n"
            "  pip install -r requirements.txt"
        ) from exc

    creds = get_google_credentials(
        credentials_path,
        token_path,
        auth_port,
        reset_token,
        login_hint,
    )
    return build("slides", "v1", credentials=creds)


def filtered_chat_text(
    messages: list[Message],
    start_date: date | None,
    end_date: date | None,
    max_chars: int,
) -> str:
    report = plain_text_report(messages, start_date, end_date)
    if len(report) <= max_chars:
        return report
    return report[:max_chars] + "\n\n[Chat truncated because --max-chat-chars was reached.]"


def read_openai_api_key(api_key: str | None, api_key_file: Path | None) -> str | None:
    if api_key:
        return api_key

    if api_key_file:
        if not api_key_file.exists():
            raise FileNotFoundError(f"OpenAI API key file not found: {api_key_file}")
        data = json.loads(api_key_file.read_text(encoding="utf-8"))
        key = data.get("api_key") or data.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                f"OpenAI API key file {api_key_file} must contain 'api_key'."
            )
        return str(key)

    return os.environ.get("OPENAI_API_KEY")


def summarize_with_openai(
    messages: list[Message],
    start_date: date | None,
    end_date: date | None,
    model: str,
    api_key: str | None,
    api_key_file: Path | None,
    max_chat_chars: int,
) -> dict:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI summarization requires the openai package:\n"
            "  pip install -r requirements.txt"
        ) from exc

    key = read_openai_api_key(api_key, api_key_file)
    if not key:
        raise RuntimeError(
            "OpenAI API key not found. Set OPENAI_API_KEY, pass --openai-api-key, "
            "or use --openai-api-key-file."
        )

    client = OpenAI(api_key=key)
    period = week_label(start_date, end_date)
    chat_text = filtered_chat_text(messages, start_date, end_date, max_chat_chars)

    prompt = f"""
Summarize this WhatsApp daily journal into the same format as a Development Weekly Report Google Slides deck.

Return JSON only. Do not wrap it in Markdown.

Required JSON shape:
{{
  "week": "{period}",
  "division": "Development",
  "products_covered": ["Consdoc", "Conspact", "Conspact+", "AI Services"],
  "team_members": ["Name", "..."],
  "daily_journal_label": "Daily Journal",
  "key_highlights": [
    {{
      "product": "Consdoc",
      "bullets": ["completed or advanced work", "..."]
    }}
  ],
  "plan_for_next_week": ["short bullet", "..."],
  "bottlenecks": ["short bullet", "..."],
  "plan_to_resolve_blockers": ["short bullet", "..."],
  "overall_progress": ["short bullet", "..."],
  "impact": ["short bullet", "..."]
}}

Rules:
- Match this slide sequence exactly:
  1. Cover slide with week, division, products covered, team members, daily journal label.
  2. Key Highlights grouped by product/project.
  3. Plan for Next Week.
  4. Bottlenecks / Blockers.
  5. Plan to Resolve Blockers.
  6. Overall Progress and Impact.
- Keep bullets concise enough for a 16:9 presentation slide.
- Key Highlights must fit on one slide: use at most 3 product/project groups,
  at most 4 bullets per group, and keep each bullet under 90 characters.
- Prefer 3 to 5 bullets per single-section slide.
- For Key Highlights, group bullets by product/project. Use products that appear in the chat.
- Preserve names exactly as written.
- Infer completion from check-out updates when possible.
- If no blockers are mentioned, infer realistic risks from unfinished or repeated in-progress work.
- Avoid inventing work not present in the chat.

Chat:
{chat_text}
""".strip()

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": "You create concise, factual project status summaries for slides.",
            },
            {"role": "user", "content": prompt},
        ],
    )

    text = response.output_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid JSON:\n{text}") from exc


def week_label(start_date: date | None, end_date: date | None) -> str:
    if start_date and end_date:
        if start_date.year == end_date.year and start_date.month == end_date.month:
            return f"{start_date.strftime('%B')} {start_date.day} - {end_date.day}, {end_date.year}"
        if start_date.year == end_date.year:
            return f"{start_date.strftime('%B')} {start_date.day} - {end_date.strftime('%B')} {end_date.day}, {end_date.year}"
        return f"{start_date.strftime('%B')} {start_date.day}, {start_date.year} - {end_date.strftime('%B')} {end_date.day}, {end_date.year}"
    return date_range_title(start_date, end_date)


def short_lines(values: list[str], limit: int = 8) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()][:limit]


def bullet_section(title: str, values: list[str], limit: int = 8, bullet: str = "•") -> str:
    lines = short_lines(values, limit)
    if not lines:
        return ""
    return "\n".join([title, *[f"{bullet}   {line}" for line in lines]])


def products_text(summary: dict) -> str:
    products = short_lines(summary.get("products_covered", []), 8)
    if not products:
        products = ["Consdoc", "Conspact", "Conspact+", "AI Services"]
    return "\n".join(f"•   {product}" for product in products)


def team_members_text(summary: dict) -> str:
    members = short_lines(summary.get("team_members", []), 12)
    return f"•   {', '.join(members)}" if members else ""


def key_highlights_text(summary: dict) -> str:
    sections = []
    for item in summary.get("key_highlights", [])[:3]:
        product = str(item.get("product") or "").strip()
        bullets = item.get("bullets", [])
        section = bullet_section(product, bullets, 4)
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def overall_progress_text(summary: dict) -> str:
    sections = [
        bullet_section("Overall Progress", summary.get("overall_progress", []), 5),
        bullet_section("Impact", summary.get("impact", []), 5),
    ]
    return "\n\n".join(section for section in sections if section)


def wrap_slide_body(value: str, width: int = 88, max_lines: int = 34) -> str:
    wrapped: list[str] = []
    for line in value.splitlines():
        if not line:
            wrapped.append("")
            continue
        prefix = ""
        content = line
        if line.startswith("•   "):
            prefix = "•   "
            content = line[4:]
        wrapped.extend(
            textwrap.wrap(
                content,
                width=width,
                initial_indent=prefix,
                subsequent_indent="  " if prefix else "",
            )
            or [prefix]
        )
    if len(wrapped) > max_lines:
        return "\n".join(wrapped[: max_lines - 1] + ["..."])
    return "\n".join(wrapped)


def new_object_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def shape_request(
    slide_id: str,
    object_id: str,
    left: float,
    top: float,
    width: float,
    height: float,
    shape_type: str = "TEXT_BOX",
) -> dict:
    return {
        "createShape": {
            "objectId": object_id,
            "shapeType": shape_type,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "width": {"magnitude": width, "unit": "PT"},
                    "height": {"magnitude": height, "unit": "PT"},
                },
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": left,
                    "translateY": top,
                    "unit": "PT",
                },
            },
        }
    }


def fill_request(object_id: str, red: float, green: float, blue: float) -> dict:
    return {
        "updateShapeProperties": {
            "objectId": object_id,
            "shapeProperties": {
                "shapeBackgroundFill": {
                    "solidFill": {
                        "color": {
                            "rgbColor": {
                                "red": red,
                                "green": green,
                                "blue": blue,
                            }
                        }
                    }
                }
            },
            "fields": "shapeBackgroundFill.solidFill.color",
        }
    }


def text_style_request(
    object_id: str,
    font_size: int,
    bold: bool = False,
) -> dict:
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "style": {
                "fontSize": {"magnitude": font_size, "unit": "PT"},
                "bold": bold,
            },
            "fields": "fontSize,bold",
        }
    }


def text_box_requests(
    slide_id: str,
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    font_size: int,
    bold: bool = False,
) -> list[dict]:
    object_id = new_object_id("text")
    return [
        shape_request(slide_id, object_id, left, top, width, height),
        {"insertText": {"objectId": object_id, "text": text}},
        text_style_request(object_id, font_size, bold=bold),
    ]


def cover_slide_requests(summary: dict) -> list[dict]:
    slide_id = new_object_id("slide")
    bg_id = new_object_id("background")
    week = str(summary.get("week") or "Weekly Report")
    division = str(summary.get("division") or "Development")
    daily_journal = str(summary.get("daily_journal_label") or "Daily Journal")

    requests = [
        {"createSlide": {"objectId": slide_id, "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
        shape_request(slide_id, bg_id, 0, 0, 720, 405, shape_type="RECTANGLE"),
        fill_request(bg_id, 0.95, 0.86, 0.75),
    ]
    requests.extend(text_box_requests(slide_id, f"Week: {week}", 220, 48, 340, 44, 24, bold=True))
    requests.extend(text_box_requests(slide_id, f"Division: {division}", 32, 104, 430, 28, 15, bold=True))
    requests.extend(text_box_requests(slide_id, "Products Covered:", 32, 128, 300, 28, 15, bold=True))
    requests.extend(text_box_requests(slide_id, products_text(summary), 42, 162, 360, 96, 14))
    requests.extend(text_box_requests(slide_id, "Team Members:", 32, 246, 300, 28, 15, bold=True))
    requests.extend(text_box_requests(slide_id, team_members_text(summary), 42, 282, 540, 34, 14))
    requests.extend(text_box_requests(slide_id, f"Daily Journal: {daily_journal}", 32, 330, 420, 28, 14, bold=True))
    return requests


def report_slide_requests(title: str, body: str, max_lines: int = 30) -> list[dict]:
    slide_id = new_object_id("slide")
    body = wrap_slide_body(body, width=92, max_lines=max_lines)

    requests = [
        {"createSlide": {"objectId": slide_id, "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
    ]
    requests.extend(text_box_requests(slide_id, title, 32, 42, 620, 48, 28, bold=True))
    requests.extend(text_box_requests(slide_id, body, 42, 100, 638, 265, 11))
    return requests


def report_slide_plan(summary: dict) -> list[tuple[str, str, int]]:
    return [
        ("Key Highlights", key_highlights_text(summary), 24),
        ("Plan for Next Week", bullet_section("", summary.get("plan_for_next_week", []), 8).strip(), 14),
        ("Bottlenecks / Blockers", bullet_section("", summary.get("bottlenecks", []), 8).strip(), 14),
        (
            "Plan to Resolve Blockers",
            bullet_section("", summary.get("plan_to_resolve_blockers", []), 8).strip(),
            14,
        ),
        ("Overall Progress", overall_progress_text(summary), 16),
    ]


def write_summary_to_slides(
    summary: dict,
    presentation_id: str,
    credentials_path: Path,
    token_path: Path,
    auth_port: int,
    reset_token: bool,
    login_hint: str | None,
) -> int:
    presentation_id = normalize_presentation_id(presentation_id)
    service = build_slides_service(
        credentials_path,
        token_path,
        auth_port,
        reset_token,
        login_hint,
    )

    requests: list[dict] = []
    requests.extend(cover_slide_requests(summary))

    for title, body, max_lines in report_slide_plan(summary):
        requests.extend(report_slide_requests(title, body, max_lines=max_lines))

    try:
        service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests},
        ).execute()
    except Exception as exc:
        raise RuntimeError(google_error_message(exc, presentation_id)) from exc

    return 1 + len(report_slide_plan(summary))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize WhatsApp daily journals with OpenAI and append them to Google Slides."
    )
    parser.add_argument("--input", default="_chat.txt", type=Path)
    parser.add_argument("--start", type=parse_filter_date, required=True)
    parser.add_argument("--end", type=parse_filter_date, required=True)
    parser.add_argument("--presentation-id", required=True, help="Google Slides ID or full URL.")
    parser.add_argument("--model", default="gpt-4.1-mini", help="OpenAI model. Default: gpt-4.1-mini")
    parser.add_argument("--openai-api-key", help="Defaults to OPENAI_API_KEY environment variable.")
    parser.add_argument(
        "--openai-api-key-file",
        type=Path,
        help="JSON file containing {'api_key': '...'}; ignored by Git if named openai_key.json.",
    )
    parser.add_argument("--max-chat-chars", type=int, default=50000)
    parser.add_argument("--google-credentials", default=Path("credentials.json"), type=Path)
    parser.add_argument("--google-token", default=Path("slides_token.json"), type=Path)
    parser.add_argument("--google-auth-port", default=8080, type=int)
    parser.add_argument("--reset-google-token", action="store_true")
    parser.add_argument("--google-login-hint")
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Optional path to save the OpenAI summary JSON for review/debugging.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.start > args.end:
        parser.error("--start cannot be after --end")

    messages = filter_messages(read_messages(args.input), args.start, args.end)
    journal = daily_journal(messages)
    if not journal:
        parser.exit(1, "error: no check-in or check-out messages found for the selected date range.\n")

    try:
        summary = summarize_with_openai(
            messages,
            args.start,
            args.end,
            args.model,
            args.openai_api_key,
            args.openai_api_key_file,
            args.max_chat_chars,
        )
        if args.summary_json:
            args.summary_json.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        slide_count = write_summary_to_slides(
            summary,
            args.presentation_id,
            args.google_credentials,
            args.google_token,
            args.google_auth_port,
            args.reset_google_token,
            args.google_login_hint,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")

    print(f"Read {len(messages)} filtered messages.")
    print(f"Summarized {len(journal)} day(s).")
    print(f"Added {slide_count} slide(s) to the presentation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
