"""Conversation export module for the bonded exhibition chatbot."""

import csv
import io
import json
from datetime import datetime


class ConversationExporter:
    """Exports conversation history in various formats."""

    def __init__(self):
        pass

    def export_text(self, history: list[dict], session_id: str = "") -> str:
        """Export conversation as plain text format."""
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "보세전시장 챗봇 대화 기록",
            f"세션: {session_id}",
            f"날짜: {date_str}",
            "---",
        ]

        for entry in history:
            role = entry.get("role", "")
            message = entry.get("message", "")
            if role == "user":
                lines.append(f"[사용자] {message}")
            elif role == "bot":
                lines.append(f"[챗봇] {message}")

        lines.append("---")
        lines.append(f"총 {len(history)}개 대화")

        return "\n".join(lines)

    def export_json(self, history: list[dict], session_id: str = "") -> str:
        """Export conversation as formatted JSON string with metadata."""
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        export_data = {
            "session_id": session_id,
            "export_date": date_str,
            "messages_count": len(history),
            "messages": history,
        }

        return json.dumps(export_data, ensure_ascii=False, indent=2)

    def export_csv(self, history: list[dict], session_id: str = "") -> str:
        """Export conversation as CSV string (role, message, timestamp)."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["role", "message", "timestamp"])

        for entry in history:
            role = entry.get("role", "")
            message = entry.get("message", "")
            timestamp = entry.get("timestamp", "")
            writer.writerow([role, message, timestamp])

        return output.getvalue()

    def export_html(self, history: list[dict], session_id: str = "") -> str:
        """Export conversation as standalone HTML with dark theme and chat bubbles."""
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        messages_html = []
        for entry in history:
            role = entry.get("role", "")
            message = entry.get("message", "")
            escaped_message = (
                message.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            if role == "user":
                messages_html.append(
                    f'<div class="bubble user"><span class="label">사용자</span>'
                    f"{escaped_message}</div>"
                )
            elif role == "bot":
                messages_html.append(
                    f'<div class="bubble bot"><span class="label">챗봇</span>'
                    f"{escaped_message}</div>"
                )

        messages_block = "\n".join(messages_html)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>보세전시장 챗봇 대화 기록</title>
<style>
  body {{
    background-color: #1e1e1e;
    color: #e0e0e0;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    margin: 0;
    padding: 20px;
  }}
  .header {{
    text-align: center;
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 1px solid #444;
  }}
  .header h1 {{
    margin: 0 0 8px 0;
    font-size: 1.4em;
  }}
  .header p {{
    margin: 2px 0;
    color: #aaa;
    font-size: 0.9em;
  }}
  .chat-container {{
    max-width: 700px;
    margin: 0 auto;
  }}
  .bubble {{
    padding: 12px 16px;
    margin: 8px 0;
    border-radius: 12px;
    max-width: 80%;
    line-height: 1.5;
  }}
  .bubble .label {{
    display: block;
    font-size: 0.75em;
    margin-bottom: 4px;
    opacity: 0.7;
  }}
  .bubble.user {{
    background-color: #2b5278;
    margin-left: auto;
    border-bottom-right-radius: 4px;
  }}
  .bubble.bot {{
    background-color: #333;
    margin-right: auto;
    border-bottom-left-radius: 4px;
  }}
  .footer {{
    text-align: center;
    margin-top: 20px;
    padding-top: 10px;
    border-top: 1px solid #444;
    color: #aaa;
    font-size: 0.85em;
  }}
</style>
</head>
<body>
<div class="chat-container">
  <div class="header">
    <h1>보세전시장 챗봇 대화 기록</h1>
    <p>세션: {session_id}</p>
    <p>날짜: {date_str}</p>
  </div>
  {messages_block}
  <div class="footer">총 {len(history)}개 대화</div>
</div>
</body>
</html>"""

        return html
