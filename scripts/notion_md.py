"""Mixdog hook handler for Notion MCP fetch results.

post: PostToolUse on notion-fetch. Small pages return inline as unwrapped
      Markdown; large pages are written to a per-session file and the result
      becomes a line-numbered outline pointing at it.
stop: Stop. Deletes this session's files, plus session folders left behind
      by sessions that never reached a Stop.

A failed run leaves the original tool result in place (Mixdog skips failed
non-PreToolUse handlers), so errors are not caught here.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time

INLINE_MAX_CHARS = 20_000
OUTLINE_MAX_ENTRIES = 120
TITLE_MAX_CHARS = 120
ORPHAN_MAX_AGE_S = 24 * 3600

MD_HEADING = re.compile(r"^(#{1,6}) (.+)$")
TOGGLE_HEADING = re.compile(r'^<toggle heading="h([1-6])">$')


def sessions_root():
    return os.path.join(os.environ["MIXDOG_PLUGIN_DATA"], "sessions")


def session_dir(session_id):
    return os.path.join(sessions_root(), re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown"))


def outline(lines):
    entries = []
    in_code = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = MD_HEADING.match(line)
        if m:
            entries.append((i + 1, len(m.group(1)), m.group(2).strip()))
            continue
        m = TOGGLE_HEADING.match(line.strip())
        if m:
            title = next((l.strip() for l in lines[i + 1 : i + 4] if l.strip()), "")
            entries.append((i + 1, int(m.group(1)), title))
    if len(entries) > OUTLINE_MAX_ENTRIES:
        entries = [e for e in entries if e[1] <= 3]
    return [f"L{n} {'#' * level} {title[:TITLE_MAX_CHARS]}" for n, level, title in entries]


def post(payload):
    resp = payload.get("tool_response")
    if not isinstance(resp, str):
        return None
    first, _, footer = resp.partition("\n")
    try:
        data = json.loads(first)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return None
    text = data["text"]

    head = [f"[notion-md] {data.get('title') or ''}".rstrip()]
    for key in ("url", "path", "page_last_edited_at"):
        if data.get(key):
            head.append(f"{key}: {data[key]}")
    if footer.strip():
        head.append(footer.strip())
    if len(text) <= INLINE_MAX_CHARS:
        return "\n".join(head) + "\n\n" + text

    page_id = re.search(r"[0-9a-f]{32}", str(data.get("url") or ""))
    name = page_id.group(0) if page_id else hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    folder = session_dir(payload.get("session_id"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    lines = text.split("\n")
    body = [
        "",
        f"Full page ({len(text):,} chars, {len(lines):,} lines) saved as Notion-flavored Markdown:",
        path,
        "- Read only the ranges you need (read with offset/limit, or grep); line numbers below match the file,"
        " and a section runs from its outline line up to the line before the next entry.",
        "- The file is the verbatim page text: copy update_page old_str from it unchanged.",
        "- The file is deleted when this turn ends; fetch the page again in a later turn.",
        "",
        "Outline:",
        *outline(lines),
    ]
    return "\n".join(head + body)


def stop(payload):
    root = sessions_root()
    if not os.path.isdir(root):
        return
    own = session_dir(payload.get("session_id"))
    now = time.time()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if path == own or now - os.path.getmtime(path) > ORPHAN_MAX_AGE_S:
            shutil.rmtree(path, ignore_errors=True)


def main():
    mode = sys.argv[1]
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if mode == "post":
        out = post(payload)
        if out is not None:
            sys.stdout.write(
                json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": out}})
            )
    elif mode == "stop":
        stop(payload)


main()
