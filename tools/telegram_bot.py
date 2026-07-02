"""
Telegram Bot — Twitter/X Scraper Integration
Sends follower/following lists and complaint/issue tweets to a Telegram group.
"""

import asyncio
import html as html_mod
import json
import os
import sys
from datetime import datetime

from telegram import Bot
from telegram.error import TelegramError

COMPLAINT_KEYWORDS = [
    "not working", "broken", "issue", "problem", "bug", "error", "fix",
    "help", "support", "complaint", "failed", "failure", "crash", "down",
    "can't", "cannot", "unable", "won't", "doesn't work", "stopped working",
    "terrible", "awful", "horrible", "worst", "disappointed", "frustrat",
    "refund", "scam", "fraud", "hate", "useless", "waste", "ridiculous",
    "not responding", "no response", "ignored", "disaster", "pathetic",
    "unacceptable", "please help", "anyone else", "same issue", "same problem",
]

SNAPSHOT_DIR = "outputs/snapshots"


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


# ─── Snapshot helpers ──────────────────────────────────────────────────────────

def load_snapshot(target_account: str, list_type: str) -> set:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = f"{SNAPSHOT_DIR}/{target_account}_{list_type}.json"
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_snapshot(target_account: str, list_type: str, usernames: set):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = f"{SNAPSHOT_DIR}/{target_account}_{list_type}.json"
    with open(path, "w") as f:
        json.dump(list(usernames), f)


def find_new_users(current_users: list, target_account: str, list_type: str) -> list:
    current_usernames = {u.get("username", "") for u in current_users}
    previous_usernames = load_snapshot(target_account, list_type)

    if not previous_usernames:
        save_snapshot(target_account, list_type, current_usernames)
        return []

    new_usernames = current_usernames - previous_usernames
    new_users = [u for u in current_users if u.get("username") in new_usernames]
    save_snapshot(target_account, list_type, current_usernames)
    return new_users


# ─── Send messages ─────────────────────────────────────────────────────────────

async def send_message(text: str, parse_mode: str = None, disable_web_page_preview: bool = True):
    bot = get_bot()
    chat_id = get_chat_id()
    try:
        kwargs = {"chat_id": chat_id, "text": text, "disable_web_page_preview": disable_web_page_preview}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        await bot.send_message(**kwargs)
    except TelegramError as e:
        print(f"  ✗ Telegram error: {e}")


async def send_header(title: str, subtitle: str = ""):
    msg = f"{'='*40}\n🐦 {title}"
    if subtitle:
        msg += f"\n{subtitle}"
    msg += f"\n{'='*40}"
    await send_message(msg)


# ─── User line formatting ──────────────────────────────────────────────────────

def _format_user_line_html(u: dict, icon: str = "•") -> str:
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


# ─── Paginated user list send (no snapshot — snapshot is separate) ─────────────

async def send_users_page(users: list, list_type: str, target_account: str, offset: int, total_all: int):
    """
    Send a 500-user page to Telegram in batches of 25.
    offset / total_all describe position within the FULL cached list.
    Does NOT run snapshot comparison (that only happens on first full scrape).
    """
    bot     = get_bot()
    chat_id = get_chat_id()
    count   = len(users)
    page_end = offset + count

    icon = "👥" if list_type == "followers" else "➡️"
    await send_message(
        f"{'─'*38}\n{icon} {list_type.upper()} of @{target_account}\n"
        f"Showing {offset+1:,}–{page_end:,} of {total_all:,} total\n"
        f"{'─'*38}"
    )

    BATCH = 25
    for i in range(0, count, BATCH):
        batch = users[i:i + BATCH]
        lines = [f"{icon} #{offset+i+1}–{offset+min(i+BATCH, count)} of {total_all:,}\n"]
        lines += [_format_user_line_html(u, "•") for u in batch]
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n…(truncated)"
        try:
            await bot.send_message(
                chat_id=chat_id, text=msg,
                parse_mode="HTML", disable_web_page_preview=True
            )
            await asyncio.sleep(0.5)
        except TelegramError as e:
            print(f"  ✗ Telegram error: {e}")
            await asyncio.sleep(2)


# ─── Follower/Following differentiation ───────────────────────────────────────

def differentiate_connections(followers: list, following: list) -> dict:
    follower_names   = {u.get("username", "").lower() for u in followers}
    following_names  = {u.get("username", "").lower() for u in following}
    mutual_names     = follower_names & following_names
    follower_only_names  = follower_names - following_names
    following_only_names = following_names - follower_names

    follower_index  = {u.get("username","").lower(): u for u in followers}
    following_index = {u.get("username","").lower(): u for u in following}

    return {
        "mutuals":        [follower_index[n]  for n in sorted(mutual_names)],
        "followers_only": [follower_index[n]  for n in sorted(follower_only_names)],
        "following_only": [following_index[n] for n in sorted(following_only_names)],
    }


async def _send_user_list_batched(users: list, header_title: str, icon: str, batch_size: int = 25):
    bot     = get_bot()
    chat_id = get_chat_id()
    total   = len(users)

    await send_message(f"{'─'*38}\n{icon} {header_title}\n{'─'*38}")

    for i in range(0, total, batch_size):
        batch = users[i : i + batch_size]
        lines = [f"{icon} {i+1}–{min(i+batch_size, total)} of {total:,}\n"]
        lines += [_format_user_line_html(u, "•") for u in batch]
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n…(truncated)"
        try:
            await bot.send_message(
                chat_id=chat_id, text=msg,
                parse_mode="HTML", disable_web_page_preview=True
            )
            await asyncio.sleep(0.5)
        except TelegramError as e:
            print(f"  ✗ Telegram error: {e}")
            await asyncio.sleep(2)


async def send_connection_analysis(username: str, followers: list, following: list):
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
        await _send_user_list_batched(mutuals, f"MUTUALS ({len(mutuals):,}) — @{username}", "🤝")
        await send_message(f"✅ Mutuals done — {len(mutuals):,} accounts")

    if followers_only:
        await _send_user_list_batched(followers_only, f"FOLLOWERS ONLY ({len(followers_only):,}) — @{username}", "👁")
        await send_message(f"✅ Followers-only done — {len(followers_only):,} accounts")

    if following_only:
        await _send_user_list_batched(following_only, f"FOLLOWING ONLY ({len(following_only):,}) — @{username}", "📤")
        await send_message(f"✅ Following-only done — {len(following_only):,} accounts")

    await send_message(
        f"🏁 Analysis complete for @{username}\n"
        f"🤝 {len(mutuals):,} mutuals | 👁 {len(followers_only):,} fans | 📤 {len(following_only):,} one-sided"
    )


# ─── Legacy full-list send (used by scheduled checks, keeps snapshot logic) ────

async def send_users_to_telegram(users: list, list_type: str, target_account: str, batch_size: int = 20):
    bot     = get_bot()
    chat_id = get_chat_id()
    total   = len(users)

    new_users = find_new_users(users, target_account, list_type)
    if new_users:
        await send_new_users_alert(new_users, list_type, target_account)

    await send_header(
        f"{list_type.upper()} LIST: @{target_account}",
        f"Total: {total:,} accounts | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    for i in range(0, total, batch_size):
        batch = users[i:i + batch_size]
        lines = [f"👥 {list_type} {i+1}–{min(i+batch_size, total)} of {total:,} (@{target_account})\n"]
        for u in batch:
            lines.append(_format_user_line_html(u, "•"))
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n... (truncated)"
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


# ─── New user alert ────────────────────────────────────────────────────────────

async def send_new_users_alert(new_users: list, list_type: str, target_account: str):
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
            + (f"\nJoined X: {created}" if created else "")
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


# ─── Complaints / tweets ───────────────────────────────────────────────────────

async def send_complaints_to_telegram(tweets: list, search_query: str, complaints_only: bool = True):
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

        if len(msg) > 4000:
            msg = msg[:4000] + "..."

        try:
            await bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
            await asyncio.sleep(0.4)
        except TelegramError as e:
            print(f"  ✗ Telegram error on tweet {i}: {e}")
            await asyncio.sleep(2)

    await send_message(
        f"✅ Done — {total:,} {'complaints' if complaints_only else 'tweets'} for: \"{search_query}\""
    )


# ─── Tweet replies ─────────────────────────────────────────────────────────────

async def send_replies_to_telegram(replies: list, tweet_url: str, tweet_author: str = ""):
    """
    Send tweet replies to Telegram.
    Each reply shows the commenter's profile link + their comment text,
    so it looks like the commenter wrote it directly.
    """
    bot     = get_bot()
    chat_id = get_chat_id()
    total   = len(replies)

    header = f"💬 REPLIES to tweet by @{tweet_author}" if tweet_author else "💬 REPLIES"
    await send_message(
        f"{'='*40}\n{header}\n"
        f"🔗 {tweet_url}\n"
        f"Total replies found: {total:,}\n"
        f"{'='*40}"
    )

    if total == 0:
        await send_message("No replies found for that tweet.")
        return

    for i, reply in enumerate(replies):
        username  = reply.get("user", {}).get("screen_name") or reply.get("username", "unknown")
        name      = reply.get("user", {}).get("name") or reply.get("name", username)
        text      = reply.get("text", reply.get("rawContent", "")).strip()
        likes     = reply.get("likes", reply.get("likeCount", 0)) or 0
        retweets  = reply.get("retweets", reply.get("retweetCount", 0)) or 0
        timestamp = reply.get("timestamp", reply.get("date", ""))
        reply_url = reply.get("tweet_url", reply.get("url", ""))
        verified  = "✓" if reply.get("user", {}).get("blue_verified") or reply.get("blue_verified") else ""
        link      = profile_link(username)

        safe_name = html_mod.escape(name) if name else html_mod.escape(username)
        safe_user = html_mod.escape(username)
        safe_text = html_mod.escape(text)

        msg = (
            f'💬 <a href="{link}">@{safe_user}</a> {verified}'
            + (f" ({safe_name})" if name and name.lower() != username.lower() else "")
            + f"\n\n{safe_text}"
            + f"\n\n❤️ {likes}  🔁 {retweets}"
        )
        if timestamp:
            msg += f"  🕐 {timestamp}"
        if reply_url:
            msg += f"\n🔗 {reply_url}"

        if len(msg) > 4000:
            msg = msg[:4000] + "..."

        try:
            await bot.send_message(
                chat_id=chat_id, text=msg,
                parse_mode="HTML", disable_web_page_preview=True
            )
            await asyncio.sleep(0.4)
        except TelegramError as e:
            print(f"  ✗ Telegram error on reply {i}: {e}")
            await asyncio.sleep(2)

    await send_message(f"✅ Done — {total:,} replies sent.")


# ─── Profile info ──────────────────────────────────────────────────────────────

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
        bot = get_bot()
        me = await bot.get_me()
        print(f"  Bot: @{me.username} ({me.first_name})")
        await send_message(
            f"✅ Twitter/X Scraper Bot connected!\n"
            f"Bot: @{me.username}\n"
            f"Ready to go."
        )
        print("  Connection OK — test message sent to group.")
        return True
    except TelegramError as e:
        print(f"  ✗ Connection FAILED: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_connection())
