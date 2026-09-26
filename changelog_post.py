#!/usr/bin/env python3
"""Daily changelog poster for the Fan Edit Fan Club #discord-moderators channel.

Reads changelog/entries.jsonl (one JSON object per line:
{"ts": "<ISO-8601>", "text": "..."}), posts every entry newer than the last
post as a release-notes-style bullet list, then watermarks
state/changelog.json so entries are never posted twice.

Entries are appended whenever a Fan Edit Fan Club platform, channel, or
automation ships a change. Quiet days post nothing.

Required secret: DISCORD_BOT_TOKEN (repo Settings -> Secrets and variables
-> Actions). A missing token fails the run loudly so it gets noticed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state")
ENTRIES = os.path.join(BASE, "changelog", "entries.jsonl")
STATE_FILE = os.path.join(STATE_DIR, "changelog.json")

DISCORD_API = "https://discord.com/api"
# Discord's Cloudflare front door 403s Python-urllib's default UA from
# datacenter IPs; the documented bot UA passes fine.
DISCORD_UA = "DiscordBot (https://faneditfanclub.local, 1.0)"


class DestinationError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def env(k: str, d: str = "") -> str:
    return os.environ.get(k, d) or d


# ---------------------------------------------------------------- discord

def _request(req: urllib.request.Request, label: str):
    """Open a request, retrying once on 429 honoring Retry-After."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace")
                if r.status not in (200, 201, 204):
                    raise DestinationError(
                        f"{label} HTTP {r.status}: {raw[:200]}")
                stripped = raw.strip()
                return json.loads(stripped) if stripped else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                retry = e.headers.get("Retry-After")
                try:
                    wait = float(retry) + 1 if retry else 5
                except (TypeError, ValueError):
                    wait = 5
                wait = min(wait, 90)
                log(f"{label} rate-limited; waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            raise DestinationError(
                f"{label} HTTP {e.code}: "
                f"{e.read()[:200].decode(errors='replace')}")
    raise DestinationError(f"{label}: retry exhausted")  # unreachable


def _post(url: str, body: bytes, headers: dict, label: str):
    return _request(urllib.request.Request(url, data=body, headers=headers,
                                          method="POST"), label)


def _get(url: str, headers: dict, label: str):
    return _request(urllib.request.Request(url, headers=headers, method="GET"),
                    label)


def _discord_headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": DISCORD_UA}


def _channel_key(n: str) -> str:
    """Normalize a channel name for matching: strip # and leading emoji."""
    return re.sub(r"^[^a-z0-9]+", "", n.lstrip("#").lower())


def resolve_discord_channel_id(token: str, channel_name: str) -> str:
    """Resolve a channel id from its name, caching it in state."""
    want = _channel_key(channel_name)
    cache = os.path.join(STATE_DIR, "discord_channels.json")
    if os.path.exists(cache):
        with open(cache) as f:
            cid = json.load(f).get(want, "")
        if cid:
            return cid
    headers = _discord_headers(token)
    seen: list = []
    try:
        guilds = _get(f"{DISCORD_API}/users/@me/guilds", headers,
                      "discord guilds")
    except DestinationError as e:
        raise DestinationError(f"discord auth failed (bad token?): {e}")
    for g in guilds if isinstance(guilds, list) else []:
        gid = g.get("id", "")
        try:
            channels = _get(f"{DISCORD_API}/guilds/{gid}/channels", headers,
                            "discord channels")
        except DestinationError:
            continue
        for c in channels if isinstance(channels, list) else []:
            name = str(c.get("name", ""))
            seen.append(f"{g.get('name', '?')}/#{name}")
            if _channel_key(name) == want and c.get("type") == 0:
                os.makedirs(STATE_DIR, exist_ok=True)
                cached = json.load(open(cache)) if os.path.exists(cache) else {}
                cached[want] = c["id"]
                with open(cache, "w") as f:
                    json.dump(cached, f)
                log(f"discord: resolved #{want} -> {c['id']}")
                return c["id"]
    raise DestinationError(
        f"discord: no text channel named #{want} visible to the bot. "
        f"Seen: {', '.join(seen[:20]) or 'nothing'}")


def resolve_discord_user_mention(token: str, username: str) -> str:
    """Return a clickable <@id> mention for a guild member.

    Falls back to plain @name text if the member can't be resolved.
    """
    headers = _discord_headers(token)
    try:
        guilds = _get(f"{DISCORD_API}/users/@me/guilds", headers,
                      "discord guilds")
    except DestinationError:
        return f"@{username}"
    for g in guilds if isinstance(guilds, list) else []:
        try:
            found = _get(
                f"{DISCORD_API}/guilds/{g.get('id', '')}/members/search?"
                + urllib.parse.urlencode({"query": username, "limit": "5"}),
                headers, "discord member search")
        except DestinationError:
            continue
        for m in found if isinstance(found, list) else []:
            u = m.get("user", {}) or {}
            if str(u.get("username", "")).lower() == username.lower() \
                    and u.get("id"):
                return f"<@{u['id']}>"
    log(f"discord: could not resolve @{username}, using plain text")
    return f"@{username}"


def post_discord(token: str, channel_id: str, text: str) -> bool:
    body = json.dumps({"content": text[:2000]}).encode()
    _post(f"{DISCORD_API}/channels/{channel_id}/messages",
          body, _discord_headers(token), "discord")
    return True


# ---------------------------------------------------------------- state

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def push_state() -> None:
    """Commit state/ back to GitHub so the watermark survives ephemeral runs."""
    pat = env("GITHUB_PAT")
    repo = env("GITHUB_REPO")
    branch = env("GITHUB_BRANCH", "main")
    if not pat or not repo:
        log("GITHUB_PAT/GITHUB_REPO not set; watermark will not persist "
            "between runs (entries may repost).")
        return
    authed = f"https://x-access-token:{pat}@github.com/{repo}.git"
    cmds = [
        ["git", "config", "user.email", "changelog@faneditfanclub.local"],
        ["git", "config", "user.name", "fanedit-daily-changelog"],
        ["git", "add", "state"],
        ["git", "commit", "-m", "changelog watermark update",
         "--allow-empty"],
        ["git", "pull", "--rebase", authed, branch],
        ["git", "push", authed, f"HEAD:{branch}"],
    ]
    for c in cmds:
        r = subprocess.run(c, cwd=BASE, capture_output=True, text=True,
                           timeout=90)
        if r.returncode != 0 and "nothing to commit" not in (
                r.stdout + r.stderr):
            log(f"git state push issue ({' '.join(c[:3])}): "
                f"{(r.stdout + r.stderr)[:300]}")
            if c[1] in ("pull", "push"):
                return
    log("state pushed to GitHub")


# ---------------------------------------------------------------- entries

def load_entries() -> list:
    out = []
    try:
        with open(ENTRIES, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    ts = dt.datetime.fromisoformat(str(e.get("ts", "")))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=dt.timezone.utc)
                except (TypeError, ValueError):
                    continue
                text = " ".join(str(e.get("text", "")).split())
                if text:
                    out.append((ts, text))
    except FileNotFoundError:
        log("no changelog entries file yet")
    out.sort(key=lambda x: x[0])
    return out


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    st = load_state()
    since = None
    if st.get("last_post_utc"):
        try:
            since = dt.datetime.fromisoformat(st["last_post_utc"])
            if since.tzinfo is None:
                since = since.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            since = None
    if since is None:
        since = now - dt.timedelta(hours=24)

    new = [(ts, t) for ts, t in load_entries() if ts > since]
    if not new:
        log("no new changelog entries - skipping post")
        return 0

    token = env("DISCORD_BOT_TOKEN")
    if not token:
        log("ERROR: DISCORD_BOT_TOKEN is not set. Add it as a repo secret "
            "(Settings -> Secrets and variables -> Actions) with the same "
            "value used by the other Fan Edit Fan Club automations.")
        return 1

    mention = resolve_discord_user_mention(token, "FanEditFanClub")
    lines = [f"**{mention} Updates \U0001f4e2**"]
    for _, text in new:
        lines.append(f"\u2022 {text}")
    msg = "\n".join(lines)[:1950]

    channel = env("CHANGELOG_CHANNEL", "discord-moderators")
    cid = resolve_discord_channel_id(token, channel)
    post_discord(token, cid, msg)
    log(f"posted {len(new)} changelog bullets to #{channel}")

    save_state({"last_post_utc": now.isoformat()})
    push_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
