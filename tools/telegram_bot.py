"""
Telegram Bot — Twitter/X Scraper Integration
Sends follower/following lists and complaint/issue tweets to a Telegram group.

HOW TO USE:
  python tools/telegram_bot.py           — test connection
  python tools/scrape_and_send.py ...   — scrape and send
"""

import asyncio
import html as html_mod
import json
import os
import sys
from datetime import datetime

from telegram import Bot
from telegram.error import TelegramError

# ─── Complaint/issue keywords ────────────────────────────────────────────────
COMPLAINT_KEYWORDS = [
    "not working", "broken", "issue", "problem", "bug", "error", "fix",
    "help", "support", "complaint", "failed", "failure", "crash", "down",
    "can't", "cannot", "unable", "won't", "doesn't work", "stopped working",
    "terrible", "awful", "horrible", "worst", "disappointed", "frustrat",
    "refund", "scam", "fraud", "hate", "useless", "waste", "ridiculous",
    "not responding", "no response", "ignored", "disaster", "pathetic",
    "unacceptable", "please help", "anyone else", "same issue", "same problem",
]

# ─── Snapshot file for new-follower tracking ──────────────────────────────────
SNAPSHOT_DIR = "outputs/snapshots"

MAX_MSG_LEN = 3900  # stay safely below Telegram's 4096-char limit


def get_bot() -> Bot:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in environment secrets.")
        sys.exit(1)
    return Bot(token=token)


def get_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("ERROR: TELEGRAM_CHAT_ID not set in environment secrets.")
        sys.exit(1)
    return chat_id


def is_complaint(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in COMPLAINT_KEYWORDS)


def profile_link(username: str) -> str:
    return f"https://x.com/{username}"


# ─── Snapshot helpers for new-follower detection ──────────────────────────────

def load_snapshot(target_account: str, list_type: str) -> set:
    """Load previously saved follower/following usernames from disk."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = f"{SNAPSHOT_DIR}/{target_account}_{list_type}.json"
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_snapshot(target_account: str, list_type: str, usernames: set):
    """Save current follower/following usernames to disk."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = f"{SNAPSHOT_DIR}/{target_account}_{list_type}.json"
    with open(path, "w") as f:
        json.dump(list(usernames), f)


def find_new_users(current_users: list, target_account: str, list_type: str) -> list:
    """
    Compare current list against last saved snapshot.
    Returns list of user dicts that are NEW since the last run.
    Also saves the new snapshot for next time.
    """
    current_usernames = {u.get("username", "") for u in current_users}
    previous_usernames = load_snapshot(target_account, list_type)
    if not previous_usernames:
        save_snapshot(target_account, list_type, current_usernames)
        return []
    new_usernames = current_usernames - previous_usernames
    new_users = [u for u in current_users if u.get("username") in new_usernames]
    save_snapshot(target_account, list_type, current_usernames)
    return new_users


# ─── Send messages ────────────────────────────────────────────────────────────

async def send_message(text: str):
    bot = get_bot()
    chat_id = get_chat_id()
    try:
        await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        print(f"  ✓ Sent: {text[:60]}...")
    except TelegramError as e:
        print(f"  ✗ Telegram error: {e}")


async def send_header(title: str, subtitle: str = ""):
    msg = f"{'='*40}\n🐦 {title}"
    if subtitle:
        msg += f"\n{subtitle}"
    msg += f"\n{'='*40}"
    await send_message(msg)


# ─── Follower / Following differentiation ────────────────────────────────────

def differentiate_connections(followers: list, following: list) -> dict:
    """
    Split two user lists into three exclusive categories:
      mutuals        — appear in both lists (follow each other)
      followers_only — follow the target but target does NOT follow back
      following_only — target follows them but they do NOT follow back
    """
    follower_names   = {u.get("username", "").lower() for u in followers}
    following_names  = {u.get("username", "").lower() for u in following}
    mutual_names         = follower_names & following_names
    follower_only_names  = follower_names - following_names
    following_only_names = following_names - follower_names
    follower_index  = {u.get("username", "").lower(): u for u in followers}
    following_index = {u.get("username", "").lower(): u for u in following}
    return {
        "mutuals":        [follower_index[n]  for n in sorted(mutual_names)],
        "followers_only": [follower_index[n]  for n in sorted(follower_only_names)],
        "following_only": [following_index[n] for n in sorted(following_only_names)],
    }


def _format_user_line_html(u: dict, icon: str = "•") -> str:
    """Return one HTML-formatted line; username is a clickable hyperlink."""
    username  = u.get("username", "unknown")
    name      = u.get("name", "")
    followers = u.get("followers_count", 0) or 0
    verified  = "✓" if u.get("blue_verified") else ""
    protected = "🔒" if u.get("protected") else ""
    link      = profile_link(username)
    safe_name = html_mod.escape(name) if name else ""
    line = f'{icon} <a href="{link}">@{html_mod.escape(username)}</a> {verified}{protected}'
    if safe_name and safe_name.lower() != username.lower():
        line += f" ({safe_name})"
    if followers:
        line += f" — {followers:,} flw"
    return line


def _build_html_batches(header: str, lines: list, max_len: int = MAX_MSG_LEN) -> list:
    """
    Safely split a list of HTML lines into messages that never exceed max_len.
    Adds lines one-by-one so we never truncate inside an <a> tag.
    """
    messages = []
    current = [header]
    current_len = len(header)
    for line in lines:
        needed = len(line) + 1  # +1 for the joining newline
        if current_len + needed > max_len:
            messages.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += needed
    if current:
        messages.append("\n".join(current))
    return messages


async def _send_user_list_batched(
    users: list, header_title: str, icon: str, batch_size: int = 25
):
    """Send a list of users to the group in safe HTML batched messages."""
    bot     = get_bot()
    chat_id = get_chat_id()
    total   = len(users)

    await send_message(f"{'─'*38}\n{icon} {header_title}\n{'─'*38}")

    for i in range(0, total, batch_size):
        batch  = users[i : i + batch_size]
        header = f"{icon} {i+1}–{min(i+batch_size, total)} of {total:,}"
        lines  = [_format_user_line_html(u, "•") for u in batch]

        # Build sub-messages that never cut inside an HTML tag
        sub_msgs = _build_html_batches(header, lines)
        for msg in sub_msgs:
            try:
                await bot.send_message(
                    chat_id=chat_id, text=msg,
                    parse_mode="HTML", disable_web_page_preview=True
                )
                await asyncio.sleep(0.5)
            except TelegramError as e:
                print(f"  ✗ Telegram error (batch {i}): {e}")
                await asyncio.sleep(2)


async def send_connection_analysis(username: str, followers: list, following: list):
    """
    Fetch-compare-send: computes mutuals / followers-only / following-only
    and sends them as clearly labelled sections to Telegram.
    """
    diff           = differentiate_connections(followers, following)
    mutuals        = diff["mutuals"]
    followers_only = diff["followers_only"]
    following_only = diff["following_only"]

    summary = (
        f"{'='*40}\n"
        f"📊 CONNECTION ANALYSIS: @{username}\n"
        f"{'='*40}\n"
        f"👥 Followers total  : {len(followers):,}\n"
        f"➡️  Following total  : {len(following):,}\n"
        f"{'─'*40}\n"
        f"🤝 Mutuals          : {len(mutuals):,}  (follow each other)\n"
        f"👁  Followers only   : {len(followers_only):,}  (fan — not followed back)\n"
        f"📤 Following only   : {len(following_only):,}  (they don't follow back)\n"
        f"{'='*40}\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    await send_message(summary)

    if mutuals:
        await _send_user_list_batched(
            mutuals, f"MUTUALS ({len(mutuals):,}) — @{username}", "🤝"
        )
        await send_message(f"✅ Mutuals done — {len(mutuals):,} accounts")

    if followers_only:
        await _send_user_list_batched(
            followers_only, f"FOLLOWERS ONLY ({len(followers_only):,}) — @{username}", "👁"
        )
        await send_message(f"✅ Followers-only done — {len(followers_only):,} accounts")

    if following_only:
        await _send_user_list_batched(
            following_only, f"FOLLOWING ONLY ({len(following_only):,}) — @{username}", "📤"
        )
        await send_message(f"✅ Following-only done — {len(following_only):,} accounts")

    await send_message(
        f"🏁 Analysis complete for @{username}\n"
        f"🤝 {len(mutuals):,} mutuals | 👁 {len(followers_only):,} fans | 📤 {len(following_only):,} one-sided"
    )


# ─── Send followers/following ─────────────────────────────────────────────────

async def send_users_to_telegram(
    users: list, list_type: str, target_account: str, batch_size: int = 20
):
    """
    Send full follower or following list to Telegram in safe HTML batches.
    Also detects and separately announces NEW users since the last run.
    """
    bot     = get_bot()
    chat_id = get_chat_id()
    total   = len(users)

    # ── Detect new users first ────────────────────────────────────────────────
    new_users = find_new_users(users, target_account, list_type)
    if new_users:
        await send_new_users_alert(new_users, list_type, target_account)

    # ── Send full list in batches ─────────────────────────────────────────────
    await send_header(
        f"{list_type.upper()} LIST: @{target_account}",
        f"Total: {total:,} accounts | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    print(f"Sending {total} {list_type} to Telegram in batches of {batch_size}...")

    for i in range(0, total, batch_size):
        batch  = users[i : i + batch_size]
        header = f"👥 {list_type} {i+1}–{min(i+batch_size, total)} of {total:,} (@{html_mod.escape(target_account)})"
        lines  = [_format_user_line_html(u, "•") for u in batch]

        sub_msgs = _build_html_batches(header, lines)
        for msg in sub_msgs:
            try:
                await bot.send_message(
                    chat_id=chat_id, text=msg,
                    parse_mode="HTML", disable_web_page_preview=True
                )
                await asyncio.sleep(0.5)
            except TelegramError as e:
                print(f"  ✗ Telegram error on batch {i}: {e}")
                await asyncio.sleep(2)

    await send_message(f"✅ Done — {list_type} list for @{target_account} ({total:,} total)")
    print(f"Done sending {total} {list_type}.")


# ─── New user alert ───────────────────────────────────────────────────────────

async def send_new_users_alert(new_users: list, list_type: str, target_account: str):
    """
    Send an alert showing users who newly joined the followers/following list
    since the last time the script was run.
    """
    count = len(new_users)
    await send_message(
        f"🆕 NEW {list_type.upper()} ALERT — @{target_account}\n"
        f"{count} new {'follower' if count == 1 else 'followers'} since last check!\n"
        f"{'='*40}"
    )

    for u in new_users:
        username  = u.get("username", "unknown")
        name      = u.get("name", "")
        followers = u.get("followers_count", 0) or 0
        following = u.get("following_count", 0) or 0
        bio       = u.get("description", "")
        created   = u.get("created_at", "")
        verified  = "✓ Verified" if u.get("blue_verified") else ""
        protected = "🔒 Private" if u.get("protected") else ""
        link      = profile_link(username)

        msg = (
            f"🆕 New {list_type[:-1] if list_type.endswith('s') else list_type}!\n\n"
            f'👤 <a href="{link}">@{html_mod.escape(username)}</a> {verified}{protected}'
            + (f"\nName: {html_mod.escape(name)}" if name and name.lower() != username.lower() else "")
            + f"\nFollowers: {followers:,} | Following: {following:,}"
            + (f"\nBio: {html_mod.escape(bio[:150])}" if bio else "")
            + (f"\nJoined X: {html_mod.escape(created)}" if created else "")
        )

        try:
            bot = get_bot()
            await bot.send_message(
                chat_id=get_chat_id(), text=msg,
                parse_mode="HTML", disable_web_page_preview=True
            )
            await asyncio.sleep(0.4)
        except TelegramError as e:
            print(f"  ✗ Error sending new user alert: {e}")


# ─── Send tweets (complaints / issues) ────────────────────────────────────────

async def send_complaints_to_telegram(tweets: list, search_query: str, complaints_only: bool = True):
    """
    Send tweets filtered for complaints/issues to Telegram.
    Each tweet shows username + profile link, text, engagement stats, and tweet link.
    """
    bot     = get_bot()
    chat_id = get_chat_id()

    if complaints_only:
        filtered = [t for t in tweets if is_complaint(t.get("text", ""))]
        label = "COMPLAINTS / ISSUES"
    else:
        filtered = tweets
        label = "TWEETS"

    total = len(filtered)
    await send_header(
        f"{label}: \"{search_query}\"",
        f"Found {total:,} | Scanned {len(tweets):,} total | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    if total == 0:
        await send_message(f"No {'complaints' if complaints_only else 'tweets'} found for: {search_query}")
        return

    print(f"Sending {total} {'complaints' if complaints_only else 'tweets'} to Telegram...")

    for i, tweet in enumerate(filtered):
        username  = tweet.get("user", {}).get("screen_name") or tweet.get("username", "unknown")
        name      = tweet.get("user", {}).get("name") or tweet.get("name", "")
        text      = tweet.get("text", tweet.get("rawContent", ""))
        likes     = tweet.get("likes", tweet.get("likeCount", 0)) or 0
        retweets  = tweet.get("retweets", tweet.get("retweetCount", 0)) or 0
        comments  = tweet.get("comments", tweet.get("replyCount", 0)) or 0
        tweet_url = tweet.get("tweet_url", tweet.get("url", ""))
        timestamp = tweet.get("timestamp", tweet.get("date", ""))
        user_link = profile_link(username)

        msg = (
            f"{'🚨' if complaints_only else '🐦'} @{username}"
            + (f" ({name})" if name and name != username else "")
            + f"\n🔗 {user_link}"
            + f"\n\n{text}"
            + f"\n\n❤️ {likes}  🔁 {retweets}  💬 {comments}"
        )
        if tweet_url:
            msg += f"\n🐦 {tweet_url}"
        if timestamp:
            msg += f"\n🕐 {timestamp}"

        if len(msg) > MAX_MSG_LEN:
            msg = msg[:MAX_MSG_LEN] + "..."  # plain text — safe to truncate

        try:
            await bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
            await asyncio.sleep(0.4)
        except TelegramError as e:
            print(f"  ✗ Telegram error on tweet {i}: {e}")
            await asyncio.sleep(2)

    await send_message(
        f"✅ Done — {total:,} {'complaints' if complaints_only else 'tweets'} for: \"{search_query}\""
    )
    print(f"Done sending {total} tweets.")


# ─── Send profile info ─────────────────────────────────────────────────────────

async def send_profile_info(profiles: list, title: str = "USER PROFILES"):
    await send_header(title, f"{len(profiles)} profiles")

    for p in profiles:
        username  = p.get("username", "unknown")
        name      = p.get("name", "")
        bio       = p.get("description", "")
        followers = p.get("followers_count", 0) or 0
        following = p.get("following_count", 0) or 0
        created   = p.get("created_at", "")
        verified  = "✓ Verified" if p.get("blue_verified") else ""
        protected = "🔒 Private" if p.get("protected") else "🌐 Public"
        link      = profile_link(username)

        msg = (
            f"👤 @{username}"
            + (f" | {name}" if name else "")
            + f"\n{protected} {verified}"
            + f"\nFollowers: {followers:,} | Following: {following:,}"
            + (f"\nBio: {bio[:200]}" if bio else "")
            + (f"\nJoined: {created}" if created else "")
            + f"\n🔗 {link}"
        )
        try:
            bot = get_bot()
            await bot.send_message(chat_id=get_chat_id(), text=msg, disable_web_page_preview=True)
            await asyncio.sleep(0.4)
        except TelegramError as e:
            print(f"  ✗ Error: {e}")


# ─── Test connection ───────────────────────────────────────────────────────────

async def test_connection():
    print("Testing Telegram connection...")
    try:
        bot  = get_bot()
        me   = await bot.get_me()
        print(f"  Bot: @{me.username} ({me.first_name})")
        await send_message(
            f"✅ Twitter/X Scraper Bot connected!\n"
            f"Bot: @{me.username}\n"
            f"Features: followers, following, complaints, new user alerts\n"
            f"Ready to go."
        )
        print("  Connection OK — test message sent to group.")
        return True
    except TelegramError as e:
        print(f"  ✗ Connection FAILED: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_connection())
