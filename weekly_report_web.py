#!/usr/bin/env python3
"""Simple web UI for uploading a WhatsApp export and scheduling a report email."""

from __future__ import annotations

import json
import smtplib
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, flash, redirect, render_template_string, request, url_for

from filter_chat import daily_journal, filter_messages, parse_filter_date, read_messages
from summarize_to_slides import (
    normalize_presentation_id,
    summarize_with_openai,
    week_label,
    write_summary_to_slides,
)


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
SUMMARY_DIR = BASE_DIR / "summaries"
SMTP_CONFIG_PATH = BASE_DIR / "smtp_config.json"
APP_CONFIG_PATH = BASE_DIR / "weekly_report_config.json"


@dataclass
class ScheduledEmail:
    job_id: str
    recipients: list[str]
    send_at: datetime
    presentation_url: str
    subject: str


app = Flask(__name__)
app.secret_key = "local-weekly-report-dev"
scheduled_emails: list[ScheduledEmail] = []


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Weekly Report Generator</title>
    <style>
      :root {
        color-scheme: light;
        font-family: Arial, sans-serif;
        color: #1f2933;
        background: #f5f7fa;
      }
      body {
        margin: 0;
        padding: 32px;
      }
      main {
        max-width: 920px;
        margin: 0 auto;
      }
      h1 {
        font-size: 28px;
        margin: 0 0 8px;
      }
      p {
        line-height: 1.5;
      }
      form, .panel {
        background: white;
        border: 1px solid #d9e2ec;
        border-radius: 8px;
        padding: 24px;
        margin-top: 20px;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      }
      label {
        display: block;
        font-weight: 700;
        margin-bottom: 6px;
      }
      input, select {
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #bcccdc;
        border-radius: 6px;
        padding: 10px 12px;
        font-size: 14px;
      }
      .full {
        grid-column: 1 / -1;
      }
      button {
        margin-top: 20px;
        border: 0;
        border-radius: 6px;
        background: #116466;
        color: white;
        font-size: 15px;
        font-weight: 700;
        padding: 11px 16px;
        cursor: pointer;
      }
      .hint {
        color: #52606d;
        font-size: 13px;
        margin-top: 6px;
      }
      .messages {
        margin-top: 16px;
      }
      .message {
        border-radius: 6px;
        padding: 12px;
        margin-bottom: 8px;
        background: #e6f6ff;
        border: 1px solid #91d5ff;
      }
      .config {
        background: #f8fafc;
        border: 1px solid #d9e2ec;
        border-radius: 6px;
        padding: 12px;
        margin: 16px 0;
        font-size: 13px;
      }
      .jobs {
        width: 100%;
        border-collapse: collapse;
        margin-top: 12px;
      }
      .jobs th, .jobs td {
        border-bottom: 1px solid #d9e2ec;
        padding: 10px;
        text-align: left;
        vertical-align: top;
      }
      @media (max-width: 720px) {
        body {
          padding: 16px;
        }
        .grid {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Weekly Report Generator</h1>
      <p>Upload a WhatsApp <code>_chat.txt</code>, generate the weekly Google Slides report now, then schedule an email with the Slides link one hour later.</p>
      <div class="config">
        <strong>Google Slides:</strong>
        {% if config.presentation_id %}
          configured in <code>weekly_report_config.json</code>
        {% else %}
          missing. Add <code>presentation_id</code> to <code>weekly_report_config.json</code>.
        {% endif %}
      </div>

      <div class="messages">
        {% for message in get_flashed_messages() %}
          <div class="message">{{ message }}</div>
        {% endfor %}
      </div>

      <form method="post" enctype="multipart/form-data">
        <div class="grid">
          <div class="full">
            <label for="chat_file">WhatsApp _chat.txt</label>
            <input id="chat_file" name="chat_file" type="file" accept=".txt" required>
          </div>

          <div>
            <label for="start">Start date</label>
            <input id="start" name="start" type="date" required>
          </div>

          <div>
            <label for="end">End date</label>
            <input id="end" name="end" type="date" required>
          </div>

          <div>
            <label for="openai_key_file">OpenAI key file</label>
            <input id="openai_key_file" name="openai_key_file" type="text" value="{{ config.openai_key_file }}">
          </div>

          <div>
            <label for="model">OpenAI model</label>
            <input id="model" name="model" type="text" value="{{ config.model }}">
          </div>

          <div>
            <label for="google_credentials">Google credentials</label>
            <input id="google_credentials" name="google_credentials" type="text" value="{{ config.google_credentials }}">
          </div>

          <div>
            <label for="google_token">Google token</label>
            <input id="google_token" name="google_token" type="text" value="{{ config.google_token }}">
          </div>

          <div class="full">
            <label for="google_login_hint">Google login hint</label>
            <input id="google_login_hint" name="google_login_hint" type="email" value="{{ config.google_login_hint }}">
          </div>

          <div class="full">
            <label for="email_to">Email recipients</label>
            <input id="email_to" name="email_to" type="text" value="{{ config.default_recipients }}" placeholder="one@example.com, two@example.com" required>
            <div class="hint">Separate multiple recipients with commas. Email is sent 1 hour after the Slides report is generated.</div>
          </div>
        </div>

        <button type="submit">Generate Slides and Schedule Email</button>
      </form>

      <section class="panel">
        <h2>Scheduled emails</h2>
        {% if scheduled_emails %}
          <table class="jobs">
            <thead>
              <tr>
                <th>Send at</th>
                <th>Recipients</th>
                <th>Slides</th>
              </tr>
            </thead>
            <tbody>
              {% for job in scheduled_emails %}
                <tr>
                  <td>{{ job.send_at.strftime("%Y-%m-%d %H:%M:%S") }}</td>
                  <td>{{ ", ".join(job.recipients) }}</td>
                  <td><a href="{{ job.presentation_url }}" target="_blank">Open report</a></td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <p>No emails scheduled in this server session.</p>
        {% endif %}
      </section>
    </main>
  </body>
</html>
"""


def resolve_local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def parse_recipients(value: str) -> list[str]:
    recipients = [item.strip() for item in value.replace(";", ",").split(",")]
    return [item for item in recipients if item]


def load_app_config() -> dict:
    defaults = {
        "presentation_id": "",
        "default_recipients": [],
        "email_subject": "Development Weekly Report - {week}",
        "email_body": (
            "Weekly development report for {week} is ready.\n\n"
            "Google Slides:\n{presentation_url}\n"
        ),
        "openai_key_file": "openai_key.json",
        "model": "gpt-4.1-mini",
        "google_credentials": "credentials.json",
        "google_token": "slides_token.json",
        "google_login_hint": "",
    }
    if not APP_CONFIG_PATH.exists():
        return defaults
    config = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
    return {**defaults, **config}


def config_for_template() -> dict:
    config = load_app_config()
    config["default_recipients"] = ", ".join(config.get("default_recipients", []))
    return config


def load_smtp_config() -> dict:
    if not SMTP_CONFIG_PATH.exists():
        raise FileNotFoundError(
            "smtp_config.json not found. Create it before scheduling email."
        )
    config = json.loads(SMTP_CONFIG_PATH.read_text(encoding="utf-8"))
    required = ["host", "port", "username", "password", "from_email"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"smtp_config.json is missing: {', '.join(missing)}")
    return config


def send_report_email(
    recipients: list[str],
    subject: str,
    presentation_url: str,
    week: str,
    body_template: str,
) -> None:
    config = load_smtp_config()
    message = EmailMessage()
    message["From"] = config["from_email"]
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(
        body_template.format(
            week=week,
            presentation_url=presentation_url,
            recipients=", ".join(recipients),
        )
    )

    with smtplib.SMTP(config["host"], int(config["port"])) as smtp:
        if config.get("use_tls", True):
            smtp.starttls()
        smtp.login(config["username"], config["password"])
        smtp.send_message(message)


def schedule_email(
    recipients: list[str],
    subject: str,
    presentation_url: str,
    week: str,
    body_template: str,
    delay_seconds: int = 3600,
) -> ScheduledEmail:
    job = ScheduledEmail(
        job_id=uuid.uuid4().hex,
        recipients=recipients,
        send_at=datetime.now() + timedelta(seconds=delay_seconds),
        presentation_url=presentation_url,
        subject=subject,
    )

    timer = threading.Timer(
        delay_seconds,
        send_report_email,
        args=(recipients, subject, presentation_url, week, body_template),
    )
    timer.daemon = True
    timer.start()
    scheduled_emails.append(job)
    return job


def generate_report(form, chat_path: Path) -> tuple[int, str, str]:
    config = load_app_config()
    if not config.get("presentation_id"):
        raise RuntimeError(
            "presentation_id is missing in weekly_report_config.json."
        )

    start_date = parse_filter_date(form["start"])
    end_date = parse_filter_date(form["end"])
    if start_date > end_date:
        raise RuntimeError("Start date cannot be after end date.")

    messages = filter_messages(read_messages(chat_path), start_date, end_date)
    journal = daily_journal(messages)
    if not journal:
        raise RuntimeError("No check-in or check-out messages found for that date range.")

    summary = summarize_with_openai(
        messages=messages,
        start_date=start_date,
        end_date=end_date,
        model=form.get("model") or config["model"],
        api_key=None,
        api_key_file=resolve_local_path(form.get("openai_key_file") or config["openai_key_file"]),
        max_chat_chars=50000,
    )

    week = str(summary.get("week") or week_label(start_date, end_date))
    presentation_id = normalize_presentation_id(config["presentation_id"])
    slide_count = write_summary_to_slides(
        summary=summary,
        presentation_id=presentation_id,
        credentials_path=resolve_local_path(form.get("google_credentials") or config["google_credentials"]),
        token_path=resolve_local_path(form.get("google_token") or config["google_token"]),
        auth_port=8080,
        reset_token=False,
        login_hint=form.get("google_login_hint") or config.get("google_login_hint") or None,
    )

    SUMMARY_DIR.mkdir(exist_ok=True)
    summary_path = SUMMARY_DIR / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    return slide_count, presentation_url, week


@app.route("/", methods=["GET", "POST"])
def index():
    config = load_app_config()
    if request.method == "POST":
        try:
            upload = request.files.get("chat_file")
            if not upload or not upload.filename:
                raise RuntimeError("Upload a WhatsApp _chat.txt file.")

            recipients = parse_recipients(request.form.get("email_to", ""))
            if not recipients:
                raise RuntimeError("Enter at least one email recipient.")

            UPLOAD_DIR.mkdir(exist_ok=True)
            chat_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.txt"
            upload.save(chat_path)

            slide_count, presentation_url, week = generate_report(request.form, chat_path)
            subject = config["email_subject"].format(
                week=week,
                presentation_url=presentation_url,
                recipients=", ".join(recipients),
            )
            job = schedule_email(
                recipients=recipients,
                subject=subject,
                presentation_url=presentation_url,
                week=week,
                body_template=config["email_body"],
            )
            flash(
                f"Generated {slide_count} slides. Email scheduled for "
                f"{job.send_at.strftime('%Y-%m-%d %H:%M:%S')}."
            )
            flash(f"Slides: {presentation_url}")
        except Exception as exc:
            flash(f"Error: {exc}")
        return redirect(url_for("index"))

    return render_template_string(
        PAGE_TEMPLATE,
        scheduled_emails=scheduled_emails,
        config=config_for_template(),
    )


def main() -> int:
    UPLOAD_DIR.mkdir(exist_ok=True)
    SUMMARY_DIR.mkdir(exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
