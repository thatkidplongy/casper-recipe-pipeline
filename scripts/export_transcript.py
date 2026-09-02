#!/usr/bin/env python3
"""Export a Claude Code session transcript to a committable pair of files.

Writes two artifacts into trajectory/:
  - <session>.json  full transcript as a JSON array, every record preserved
  - <session>.md    readable Markdown rendering of the conversation

Anything resembling an API key, token, or private key is redacted from both.

Stdlib only, on purpose: this must run whether or not the pipeline's own
dependencies are installed, and under any Python the container happens to have.

Usage:
    python3 scripts/export_transcript.py
    python3 scripts/export_transcript.py --transcript path/to/session.jsonl
    python3 scripts/export_transcript.py --out-dir trajectory
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PLACEHOLDER = "[REDACTED-API-KEY]"

# Ordered most specific first: a private key block must be collapsed before its
# base64 body is picked at by a narrower rule.
_PATTERNS = [
    # PEM private key blocks, body and all.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    # An orphan PEM marker, which is what a truncated log actually contains:
    # the paired rule above cannot fire without its END, so the marker and any
    # base64 body trailing it are taken here. A body without its header is
    # still key material.
    re.compile(
        r"-----(?:BEGIN|END) [A-Z ]*PRIVATE KEY-----(?:\s*[A-Za-z0-9+/=]{20,})*",
    ),
    # Anthropic.
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    # OpenAI project, service account, and admin keys.
    re.compile(r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"),
    # OpenAI legacy keys.
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    # GitHub classic and fine-grained tokens.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    # AWS access key ids.
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Google API keys.
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    # Slack tokens.
    re.compile(r"\bxox[baprse]-[A-Za-z0-9-]{10,}"),
    # JSON Web Tokens.
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # Bearer credentials in a header.
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
]

# Assignments such as OPENAI_API_KEY=... or "api_key": "...". The name is kept
# because it is not secret and it tells a reviewer what was scrubbed.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (["']?)
    (?P<name>[A-Za-z0-9_.-]*
        (?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIALS?|AUTH)
        [A-Za-z0-9_.-]*)
    \1
    (?P<sep>\s*[:=]\s*)
    (["']?)
    (?P<value>[A-Za-z0-9_\-.~+/]{16,}=*)
    \4
    """
)


def redact(text):
    """Replace anything that looks like a credential with a placeholder."""
    if not isinstance(text, str):
        return text

    for pattern in _PATTERNS:
        text = pattern.sub(PLACEHOLDER, text)

    def _hide_value(match):
        return f"{match.group('name')}{match.group('sep')}{PLACEHOLDER}"

    return _ASSIGNMENT.sub(_hide_value, text)


def redact_tree(node):
    """Redact every string value in a nested structure, preserving shape.

    Dictionary keys are structure rather than content, so they are left alone.
    """
    if isinstance(node, str):
        return redact(node)
    if isinstance(node, dict):
        return {key: redact_tree(value) for key, value in node.items()}
    if isinstance(node, list):
        return [redact_tree(item) for item in node]
    return node


def conversational_records(records):
    """Keep the turns a reader cares about, drop internal bookkeeping."""
    return [
        record
        for record in records
        if record.get("type") in ("user", "assistant") and record.get("message")
    ]


def _blocks(content):
    """Normalise message content to a list of blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _fence(body, language=""):
    """Fence a body, widening the fence if the body contains backticks."""
    body = body if isinstance(body, str) else json.dumps(body, indent=2, default=str)
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    bar = "`" * max(3, longest + 1)
    return f"{bar}{language}\n{body}\n{bar}"


def _render_block(block, max_chars):
    kind = block.get("type")

    if kind == "text":
        return (block.get("text") or "").strip()

    if kind == "thinking":
        body = (block.get("thinking") or "").strip()
        return f"<details><summary>Thinking</summary>\n\n{body}\n\n</details>" if body else ""

    if kind == "tool_use":
        name = block.get("name", "unknown tool")
        payload = json.dumps(block.get("input", {}), indent=2, default=str)
        return f"**Tool call: {name}**\n\n{_fence(_clip(payload, max_chars), 'json')}"

    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, default=str)
        return f"**Tool result**\n\n{_fence(_clip(content, max_chars))}"

    return ""


def _clip(text, max_chars):
    if max_chars and len(text) > max_chars:
        return f"{text[:max_chars]}\n... [truncated {len(text) - max_chars} characters]"
    return text


def _heading_for(role, blocks):
    """Name the turn as a reader would understand it.

    Tool output is delivered on a record whose role is "user", but the user did
    not say it. Labelling it as the user speaking misreads the conversation.
    """
    if role != "user":
        return "Assistant"
    if blocks and all(block.get("type") == "tool_result" for block in blocks):
        return "Tool result"
    return "User"


def render_markdown(records, session_id, max_chars=0):
    """Render conversational records as readable Markdown."""
    lines = [
        f"# Session transcript: {session_id}",
        "",
        "Exported by `scripts/export_transcript.py`. Credentials are redacted.",
        "",
    ]

    for record in conversational_records(records):
        blocks = _blocks(record.get("message", {}).get("content"))
        rendered = [r for r in (_render_block(b, max_chars) for b in blocks) if r]

        # A turn carrying only a signature or an empty thinking block renders to
        # nothing. Emitting its heading would leave the reader with a dangling
        # section, so the whole record is skipped.
        if not rendered:
            continue

        role = record.get("message", {}).get("role", record.get("type", "unknown"))
        timestamp = record.get("timestamp", "")

        lines.append(f"## {_heading_for(role, blocks)}" + (f"  <sub>{timestamp}</sub>" if timestamp else ""))
        lines.append("")

        for body in rendered:
            lines.append(body)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def read_jsonl(path):
    """Parse a JSONL transcript, skipping any line that will not parse."""
    records, skipped = [], 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return records, skipped


def locate_transcript(cwd=None):
    """Find this session's transcript inside the Claude Code project directory."""
    cwd = Path(cwd or os.getcwd()).resolve()
    slug = str(cwd).replace("/", "-")
    project_dir = Path.home() / ".claude" / "projects" / slug

    if not project_dir.is_dir():
        return None

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session_id:
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate

    transcripts = sorted(
        project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return transcripts[0] if transcripts else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", help="Path to the session .jsonl file")
    parser.add_argument("--out-dir", default="trajectory", help="Output directory")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="Clip tool payloads in the Markdown at N characters (0 means no limit)",
    )
    args = parser.parse_args(argv)

    source = Path(args.transcript) if args.transcript else locate_transcript()
    if not source or not source.is_file():
        parser.error(
            "could not locate a transcript; pass --transcript with an explicit path"
        )

    records, skipped = read_jsonl(source)
    if not records:
        parser.error(f"no records parsed from {source}")

    session_id = records[0].get("sessionId") or source.stem
    safe_records = redact_tree(records)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{session_id}.json"
    md_path = out_dir / f"{session_id}.md"

    json_path.write_text(
        json.dumps(safe_records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        render_markdown(safe_records, session_id, args.max_chars), encoding="utf-8"
    )

    turns = len(conversational_records(safe_records))
    print(f"source      : {source}")
    print(f"records     : {len(records)} parsed, {skipped} unparseable skipped")
    print(f"turns       : {turns}")
    print(f"json        : {json_path} ({json_path.stat().st_size} bytes)")
    print(f"markdown    : {md_path} ({md_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
