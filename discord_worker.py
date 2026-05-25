"""
Discord Chat History Export Worker — run as a subprocess from server.py.

Usage:
    python discord_worker.py <token> <channel_url_or_id> <output_path> [limit]

Progress is written to stdout as:
    STATUS:<message>
    DONE:<json_data>
    ERROR:<message>
"""
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
)

DISCORD_API = "https://discord.com/api/v10"
MESSAGES_PER_REQUEST = 100


def status(msg: str):
    print(f"STATUS:{msg}", flush=True)


def done(data: dict):
    print(f"DONE:{json.dumps(data, ensure_ascii=False)}", flush=True)


def error(msg: str):
    print(f"ERROR:{msg}", flush=True)
    sys.exit(1)


def parse_channel_id(url_or_id: str) -> str:
    """Extract channel ID from a Discord URL or plain ID."""
    url_or_id = url_or_id.strip()
    # Match: https://discord.com/channels/guild_id/channel_id
    m = re.match(r"https?://(?:www\.)?discord\.com/channels/\d+/(\d+)", url_or_id)
    if m:
        return m.group(1)
    # Plain numeric ID
    if url_or_id.isdigit():
        return url_or_id
    error(f"无法解析频道 ID: {url_or_id}\n请粘贴 Discord 频道的完整 URL，例如 https://discord.com/channels/xxx/xxx")
    return ""


def api_get(endpoint: str, token: str) -> dict | list:
    """Make an authenticated GET request to Discord API with rate limit handling."""
    url = f"{DISCORD_API}{endpoint}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=30)

            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 5) if resp.content else 5
                status(f"Rate limited, waiting {retry_after}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_after + 0.5)
                continue

            if resp.status_code == 401:
                error("Token 无效或已过期。请重新从浏览器 DevTools 获取 Token。")
            elif resp.status_code == 403:
                error("没有权限访问该频道。请确认你有权查看此频道的消息。")
            elif resp.status_code == 404:
                error("频道不存在。请检查 URL 是否正确。")
            elif not resp.ok:
                error(f"Discord API 错误: HTTP {resp.status_code}")

            data = resp.json()
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining and int(remaining) == 0:
                reset_after = float(resp.headers.get("X-RateLimit-Reset-After", "1"))
                status(f"Rate limit reached, waiting {reset_after:.1f}s...")
                time.sleep(reset_after + 0.5)
            return data

        except requests.exceptions.ConnectionError as e:
            if attempt < max_retries - 1:
                status(f"网络错误，重试中... ({e})")
                time.sleep(2)
                continue
            error(f"网络连接失败: {e}")
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                status(f"请求超时，重试中...")
                time.sleep(2)
                continue
            error("请求超时，请检查网络连接。")
        except requests.exceptions.RequestException as e:
            error(f"请求失败: {e}")

    error("请求重试次数超限")
    return {}



def fetch_channel_info(channel_id: str, token: str) -> dict:
    """Fetch channel metadata."""
    return api_get(f"/channels/{channel_id}", token)


def fetch_messages(channel_id: str, token: str, limit: int | None = None) -> list[dict]:
    """Fetch all messages from a channel with pagination."""
    all_messages = []
    before = None
    batch_num = 0

    while True:
        endpoint = f"/channels/{channel_id}/messages?limit={MESSAGES_PER_REQUEST}"
        if before:
            endpoint += f"&before={before}"

        batch = api_get(endpoint, token)
        if not batch:
            break

        all_messages.extend(batch)
        batch_num += 1
        status(f"已获取 {len(all_messages)} 条消息...")

        if limit and len(all_messages) >= limit:
            all_messages = all_messages[:limit]
            break

        if len(batch) < MESSAGES_PER_REQUEST:
            break

        before = batch[-1]["id"]
        # Small delay between requests to be respectful
        time.sleep(0.5)

    # Messages come in reverse chronological order, reverse for export
    all_messages.reverse()
    return all_messages


def format_timestamp(iso_str: str) -> str:
    """Format ISO timestamp to readable string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("+00:00", "+00:00").rstrip("Z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str


def get_display_name(author: dict) -> str:
    """Get display name for a message author."""
    return author.get("global_name") or author.get("username", "Unknown")


def get_avatar_url(author: dict) -> str:
    """Get avatar URL for a user."""
    avatar = author.get("avatar")
    user_id = author.get("id", "0")
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=64"
    # Default avatar
    discriminator = int(author.get("discriminator", "0") or "0")
    index = (int(user_id) >> 22) % 6 if discriminator == 0 else discriminator % 5
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def render_html(messages: list[dict], channel_info: dict, guild_name: str = "") -> str:
    """Render messages to styled HTML resembling Discord's dark theme."""
    channel_name = channel_info.get("name", "unknown-channel")
    topic = channel_info.get("topic", "")

    # Build user/channel lookup from message data
    user_map = {}  # user_id -> display_name
    for msg in messages:
        author = msg.get("author", {})
        uid = author.get("id")
        if uid:
            user_map[uid] = get_display_name(author)
        # Discord includes mentioned users in the 'mentions' field
        for mentioned in msg.get("mentions", []):
            mid = mentioned.get("id")
            if mid:
                user_map[mid] = get_display_name(mentioned)

    header_text = f"#{channel_name}"
    if guild_name:
        header_text = f"{guild_name} — #{channel_name}"

    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>Discord Export — #{channel_name}</title>",
        "<style>",
        _get_css(),
        "</style>",
        "</head>",
        "<body>",
        '<div class="container">',
        '<div class="header">',
        f'<h1>{_escape_html(header_text)}</h1>',
    ]
    if topic:
        html_parts.append(f'<p class="topic">{_escape_html(topic)}</p>')
    html_parts.append(f'<p class="meta">Exported {len(messages)} messages on {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>')
    html_parts.append("</div>")
    html_parts.append('<div class="messages">')

    prev_author_id = None
    prev_time = None

    for msg in messages:
        author = msg.get("author", {})
        author_id = author.get("id", "")
        display_name = get_display_name(author)
        avatar_url = get_avatar_url(author)
        timestamp = format_timestamp(msg.get("timestamp", ""))
        content = msg.get("content", "")

        # Determine if this is a continuation of the same author (within 7 minutes)
        is_continuation = (
            author_id == prev_author_id
            and prev_time
            and _time_diff_minutes(prev_time, msg.get("timestamp", "")) < 7
        )

        msg_class = "message compact" if is_continuation else "message"

        html_parts.append(f'<div class="{msg_class}">')

        if not is_continuation:
            html_parts.append(f'<img class="avatar" src="{_escape_html(avatar_url)}" alt="">')
            html_parts.append('<div class="msg-content">')
            color = _name_color(author_id)
            html_parts.append(f'<span class="author" style="color:{color}">{_escape_html(display_name)}</span>')
            html_parts.append(f'<span class="timestamp">{_escape_html(timestamp)}</span>')
        else:
            html_parts.append('<div class="msg-content continuation">')

        # Message content
        if content:
            formatted = _format_discord_markdown(content, user_map)
            html_parts.append(f'<div class="text">{formatted}</div>')

        # Attachments
        attachments = msg.get("attachments", [])
        for att in attachments:
            att_url = att.get("url", "")
            att_name = att.get("filename", "attachment")
            content_type = att.get("content_type", "")
            if content_type.startswith("image/"):
                html_parts.append(f'<div class="attachment"><a href="{_escape_html(att_url)}" target="_blank"><img class="att-image" src="{_escape_html(att_url)}" alt="{_escape_html(att_name)}"></a></div>')
            else:
                size = att.get("size", 0)
                size_str = _format_size(size)
                html_parts.append(f'<div class="attachment file"><a href="{_escape_html(att_url)}" target="_blank">📎 {_escape_html(att_name)} ({size_str})</a></div>')

        # Embeds
        embeds = msg.get("embeds", [])
        for embed in embeds:
            html_parts.append(_render_embed(embed))

        # Reactions
        reactions = msg.get("reactions", [])
        if reactions:
            html_parts.append('<div class="reactions">')
            for r in reactions:
                emoji = r.get("emoji", {})
                emoji_str = emoji.get("name", "?")
                count = r.get("count", 1)
                html_parts.append(f'<span class="reaction">{emoji_str} {count}</span>')
            html_parts.append("</div>")

        html_parts.append("</div>")  # .msg-content
        html_parts.append("</div>")  # .message

        prev_author_id = author_id
        prev_time = msg.get("timestamp", "")

    html_parts.extend([
        "</div>",  # .messages
        "</div>",  # .container
        "</body>",
        "</html>",
    ])
    return "\n".join(html_parts)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _format_discord_markdown(text: str, user_map: dict = None) -> str:
    """Convert Discord markdown to HTML."""
    if user_map is None:
        user_map = {}
    text = _escape_html(text)
    # Code blocks
    text = re.sub(r"```(\w*)\n?(.*?)```", r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
    # Underline
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    # Spoiler
    text = re.sub(r"\|\|(.+?)\|\|", r'<span class="spoiler">\1</span>', text)
    # URLs
    text = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank">\1</a>', text)
    # Mentions — resolve user IDs to display names
    def _replace_user_mention(m):
        uid = m.group(1)
        name = user_map.get(uid, f"user_{uid[-4:]}")
        return f'<span class="mention">@{_escape_html(name)}</span>'
    text = re.sub(r"&lt;@!?(\d+)&gt;", _replace_user_mention, text)
    text = re.sub(r"&lt;#(\d+)&gt;", r'<span class="mention">#channel</span>', text)
    text = re.sub(r"&lt;@&amp;(\d+)&gt;", r'<span class="mention">@role</span>', text)
    # Emoji (custom)
    text = re.sub(r"&lt;:(\w+):(\d+)&gt;", r'<img class="emoji" src="https://cdn.discordapp.com/emojis/\2.png?size=20" alt=":\1:">', text)
    text = re.sub(r"&lt;a:(\w+):(\d+)&gt;", r'<img class="emoji" src="https://cdn.discordapp.com/emojis/\2.gif?size=20" alt=":\1:">', text)
    # Newlines
    text = text.replace("\n", "<br>")
    return text


def _render_embed(embed: dict) -> str:
    """Render a Discord embed to HTML."""
    parts = ['<div class="embed">']
    color = embed.get("color")
    if color:
        parts[0] = f'<div class="embed" style="border-left-color: #{color:06x}">'

    title = embed.get("title", "")
    url = embed.get("url", "")
    desc = embed.get("description", "")

    if title:
        if url:
            parts.append(f'<div class="embed-title"><a href="{_escape_html(url)}" target="_blank">{_escape_html(title)}</a></div>')
        else:
            parts.append(f'<div class="embed-title">{_escape_html(title)}</div>')
    if desc:
        parts.append(f'<div class="embed-desc">{_escape_html(desc)}</div>')

    thumbnail = embed.get("thumbnail", {})
    if thumbnail.get("url"):
        parts.append(f'<img class="embed-thumb" src="{_escape_html(thumbnail["url"])}">')

    parts.append("</div>")
    return "\n".join(parts)


def _name_color(user_id: str) -> str:
    """Generate a consistent color for a username based on their ID."""
    colors = [
        "#f44336", "#e91e63", "#9c27b0", "#673ab7",
        "#3f51b5", "#2196f3", "#03a9f4", "#00bcd4",
        "#009688", "#4caf50", "#8bc34a", "#ff9800",
        "#ff5722", "#e67e22", "#1abc9c", "#3498db",
    ]
    idx = hash(user_id) % len(colors)
    return colors[idx]


def _time_diff_minutes(ts1: str, ts2: str) -> float:
    """Get time difference in minutes between two ISO timestamps."""
    try:
        t1 = datetime.fromisoformat(ts1.rstrip("Z")).timestamp()
        t2 = datetime.fromisoformat(ts2.rstrip("Z")).timestamp()
        return abs(t2 - t1) / 60
    except Exception:
        return 999


def _format_size(size_bytes: int) -> str:
    """Format file size in human readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _get_css() -> str:
    """Return CSS styles for the Discord-themed HTML export."""
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #313338;
    color: #dcddde;
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 15px;
    line-height: 1.4;
}
.container { max-width: 900px; margin: 0 auto; padding: 20px; }
.header {
    padding: 20px;
    border-bottom: 1px solid #3f4147;
    margin-bottom: 20px;
}
.header h1 { color: #fff; font-size: 24px; margin-bottom: 4px; }
.header .topic { color: #949ba4; font-size: 13px; margin-bottom: 8px; }
.header .meta { color: #72767d; font-size: 12px; }
.messages { padding: 0 10px; }
.message {
    display: flex;
    gap: 12px;
    padding: 4px 0;
    margin-top: 12px;
}
.message.compact {
    margin-top: 0;
    padding-left: 52px;
}
.avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 2px;
}
.msg-content { flex: 1; min-width: 0; }
.msg-content.continuation { padding-top: 0; }
.author {
    font-weight: 600;
    font-size: 15px;
    margin-right: 8px;
}
.timestamp { color: #72767d; font-size: 12px; }
.text { margin-top: 2px; word-wrap: break-word; }
.text a { color: #00aff4; text-decoration: none; }
.text a:hover { text-decoration: underline; }
.text code {
    background: #2b2d31;
    padding: 2px 4px;
    border-radius: 3px;
    font-size: 13px;
}
.text pre {
    background: #2b2d31;
    padding: 10px;
    border-radius: 4px;
    margin: 4px 0;
    overflow-x: auto;
}
.text pre code { padding: 0; background: none; }
.text strong { color: #fff; }
.spoiler {
    background: #4e4f54;
    color: transparent;
    padding: 0 4px;
    border-radius: 3px;
    cursor: pointer;
}
.spoiler:hover { color: #dcddde; }
.mention {
    background: rgba(88, 101, 242, 0.3);
    color: #dee0fc;
    padding: 0 3px;
    border-radius: 3px;
}
.emoji { width: 20px; height: 20px; vertical-align: middle; }
.attachment { margin-top: 4px; }
.att-image {
    max-width: 400px;
    max-height: 300px;
    border-radius: 4px;
    cursor: pointer;
}
.attachment.file a {
    color: #00aff4;
    text-decoration: none;
    background: #2b2d31;
    padding: 8px 12px;
    border-radius: 4px;
    display: inline-block;
}
.embed {
    border-left: 4px solid #4e5058;
    background: #2b2d31;
    padding: 10px;
    border-radius: 4px;
    margin-top: 4px;
    max-width: 500px;
}
.embed-title { font-weight: 600; margin-bottom: 4px; }
.embed-title a { color: #00aff4; text-decoration: none; }
.embed-desc { font-size: 14px; color: #b5bac1; }
.embed-thumb { max-width: 80px; border-radius: 4px; margin-top: 8px; }
.reactions { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
.reaction {
    background: #2b2d31;
    border: 1px solid #3f4147;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 13px;
}
"""


def main():
    if len(sys.argv) < 4:
        error("Usage: python discord_worker.py <token> <channel_url_or_id> <output_path> [limit]")

    token = sys.argv[1]
    channel_input = sys.argv[2]
    output_path = sys.argv[3]
    limit = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None

    status("正在解析频道信息...")
    channel_id = parse_channel_id(channel_input)

    status("正在获取频道信息...")
    channel_info = fetch_channel_info(channel_id, token)
    channel_name = channel_info.get("name", "unknown")
    guild_id = channel_info.get("guild_id", "")

    # Try to get guild name
    guild_name = ""
    if guild_id:
        try:
            guild_info = api_get(f"/guilds/{guild_id}", token)
            guild_name = guild_info.get("name", "")
        except Exception:
            pass

    status(f"频道: #{channel_name}" + (f" ({guild_name})" if guild_name else ""))

    status("正在获取消息..." + (f" (最多 {limit} 条)" if limit else " (全部)"))
    messages = fetch_messages(channel_id, token, limit)

    if not messages:
        error("未找到任何消息。频道可能为空或无权限访问。")

    status(f"共获取 {len(messages)} 条消息，正在生成 HTML...")
    html = render_html(messages, channel_info, guild_name)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    status("导出完成！")
    done(json.dumps({
        "message_count": len(messages),
        "channel_name": channel_name,
        "guild_name": guild_name,
        "filename": f"Discord_{guild_name}_{channel_name}.html" if guild_name else f"Discord_{channel_name}.html",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
