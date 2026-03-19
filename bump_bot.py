"""
B34C0N — Bump Tracker for The Digital Wasteland
================================================
Tracks DISBOARD bumps and steals with slash commands.
bump_data.json lives in your GitHub repo and is read/written via the GitHub API
on every successful bump. Railway only needs BOT_TOKEN and GITHUB_TOKEN set.

SETUP:
1. Set these environment variables in Railway:
   - BOT_TOKEN      → Your Discord bot token
   - GITHUB_TOKEN   → GitHub Personal Access Token (repo scope)
2. Update GITHUB_REPO below to match your repo (e.g. "yourname/digital-wasteland-bot")
3. Push all files to GitHub, Railway will auto-deploy

COMMANDS:
  /bumpboard      — View the bump leaderboard
  /bumpstats      — View stats for yourself or another member
  /beaconscrape   — (Admin only) Scan full channel history and calculate all bumps + steals

FUTURE EXPANSION POINTS (marked with # TODO: ACHIEVEMENTS):
  - add_achievement() helper is ready to uncomment and call from anywhere
  - Hook points already placed after every bump and steal
  - Placeholder spots in /bumpboard and /bumpstats for generated badge images
"""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import json
import os
import base64
import re
import aiohttp

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "Digital-Void-divo/BEACON"
GITHUB_FILE  = "bump_data.json"

DISBOARD_BOT_ID      = 302050872383242240
BUMP_COOLDOWN_HOURS  = 2
STEAL_WINDOW_SECONDS = 30

# ─── GITHUB DATA HELPERS ──────────────────────────────────────────────────────

def github_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

def github_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"

_file_sha: str | None = None

async def load_data() -> dict:
    """Read bump_data.json from GitHub. Returns empty state if file doesn't exist yet."""
    global _file_sha
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(github_api_url(), headers=github_headers(), timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    payload = await response.json()
                    _file_sha = payload["sha"]
                    return json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
                elif response.status == 404:
                    # File doesn't exist yet — will be created on first save
                    _file_sha = None
                    print("[B34C0N] bump_data.json not found on GitHub — will create on first save.")
                else:
                    print(f"⚠️  GitHub load failed: {response.status}")
    except Exception as e:
        print(f"⚠️  GitHub load error: {e}")
    return {"bumps": {}, "steals": {}, "last_bump_time": None, "names": {}}

async def save_data(data: dict):
    """Write bump_data.json to GitHub. Creates the file if it doesn't exist yet."""
    global _file_sha
    async with aiohttp.ClientSession() as session:
        # If we have no SHA cached, fetch it — the file may already exist on GitHub
        if not _file_sha:
            try:
                async with session.get(github_api_url(), headers=github_headers(), timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        _file_sha = (await r.json()).get("sha")
            except Exception as e:
                print(f"⚠️  GitHub SHA prefetch error: {e}")
        encoded = base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")
        payload = {
            "message": "chore: update bump data",
            "content": encoded,
        }
        if _file_sha:
            payload["sha"] = _file_sha
        try:
            async with session.put(github_api_url(), headers=github_headers(), json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status in (200, 201):
                    _file_sha = (await response.json())["content"]["sha"]
                    print(f"[B34C0N] bump_data.json saved to GitHub (SHA: {_file_sha[:7]})")
                elif response.status == 404:
                    print(
                        f"⚠️  GitHub save failed (404 Not Found). Check that:\n"
                        f"   1. GITHUB_REPO='{GITHUB_REPO}' matches your actual repo (owner/repo-name)\n"
                        f"   2. Your GITHUB_TOKEN has 'repo' (or 'contents:write') scope\n"
                        f"   Raw response: {await response.text()}"
                    )
                else:
                    print(f"⚠️  GitHub save failed: {response.status} {await response.text()}")
        except Exception as e:
            print(f"⚠️  GitHub save error: {e}")

def get_user_record(data: dict, user_id: str) -> dict:
    return {
        "bumps": data["bumps"].get(user_id, 0),
        "steals": data["steals"].get(user_id, 0),
        # TODO: ACHIEVEMENTS
        # "achievements": data.get("achievements", {}).get(user_id, []),
    }

async def resolve_display_name(guild: discord.Guild, user_id: int, data: dict) -> str:
    """
    Get a member's display name. Tries the live server first, falls back to
    the cached name in bump_data.json, then falls back to the raw user ID.
    Also updates the cache whenever a live name is found.
    """
    uid_str = str(user_id)
    member = guild.get_member(user_id)
    if member:
        # Update cache with latest name
        data.setdefault("names", {})[uid_str] = member.display_name
        return member.display_name
    # Try fetching from Discord API (works for users still on Discord, even if left server)
    try:
        user = await guild.fetch_member(user_id)
        data.setdefault("names", {})[uid_str] = user.display_name
        return user.display_name
    except discord.NotFound:
        pass
    # Fall back to cached name
    cached = data.get("names", {}).get(uid_str)
    if cached:
        return f"{cached} (left)"
    return f"Unknown ({uid_str})"

def is_steal(current_ts: datetime, previous_ts: datetime) -> bool:
    """
    Returns True if current_ts falls within STEAL_WINDOW_SECONDS after
    the BUMP_COOLDOWN_HOURS window from previous_ts.
    Both timestamps must be timezone-aware (UTC).
    """
    # Ensure both are UTC-aware
    if current_ts.tzinfo is None:
        current_ts = current_ts.replace(tzinfo=timezone.utc)
    if previous_ts.tzinfo is None:
        previous_ts = previous_ts.replace(tzinfo=timezone.utc)

    cooldown_reset   = previous_ts + timedelta(hours=BUMP_COOLDOWN_HOURS)
    steal_window_end = cooldown_reset + timedelta(seconds=STEAL_WINDOW_SECONDS)
    return cooldown_reset <= current_ts <= steal_window_end

# TODO: ACHIEVEMENTS
# def add_achievement(data: dict, user_id: str, achievement_id: str, achievement_name: str):
#     data.setdefault("achievements", {}).setdefault(user_id, [])
#     existing_ids = [a["id"] for a in data["achievements"][user_id]]
#     if achievement_id not in existing_ids:
#         data["achievements"][user_id].append({
#             "id": achievement_id,
#             "name": achievement_name,
#             "awarded_at": datetime.now(timezone.utc).isoformat(),
#         })
#         return True
#     return False

# ─── BOT SETUP ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_interaction_user_id(message: discord.Message) -> int | None:
    """Get the user ID of whoever triggered the slash command that produced this message."""
    if hasattr(message, "interaction_metadata") and message.interaction_metadata is not None:
        try:
            return message.interaction_metadata.user.id
        except AttributeError:
            pass
    return None

def get_interaction_name(message: discord.Message) -> str | None:
    """Get the slash command name that produced this message."""
    if hasattr(message, "interaction_metadata") and message.interaction_metadata is not None:
        return getattr(message.interaction_metadata, "name", None)
    return None

# ─── EVENTS ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ B34C0N online as {bot.user} (ID: {bot.user.id})")
    print(f"   Steal window: {STEAL_WINDOW_SECONDS}s | Slash commands synced")
    print(f"   Persisting data to: github.com/{GITHUB_REPO}/{GITHUB_FILE}")

@bot.event
async def on_message(message: discord.Message):
    # Detect DISBOARD's success embed
    if message.author.id == DISBOARD_BOT_ID and message.embeds:
        embed = message.embeds[0]
        description = embed.description or ""
        if "Bump done" in description or (embed.title and "Bump done" in embed.title):
            await handle_successful_bump(message)

    await bot.process_commands(message)

async def handle_successful_bump(disboard_message: discord.Message):
    now = datetime.now(timezone.utc)

    # DISBOARD's confirmation message carries interaction_metadata identifying the bumper
    user_id = get_interaction_user_id(disboard_message)
    if user_id is None:
        print(f"⚠️  Could not attribute bump in #{disboard_message.channel.name}")
        return
    user_id_str = str(user_id)
    data = await load_data()
    last_bump_iso = data.get("last_bump_time")

    # Award bump
    data["bumps"][user_id_str] = data["bumps"].get(user_id_str, 0) + 1

    # Check for steal
    bump_is_steal = False
    if last_bump_iso:
        last_ts = datetime.fromisoformat(last_bump_iso)
        if is_steal(now, last_ts):
            bump_is_steal = True
            data["steals"][user_id_str] = data["steals"].get(user_id_str, 0) + 1

    # TODO: ACHIEVEMENTS
    # if data["bumps"][user_id_str] == 10:
    #     add_achievement(data, user_id_str, "bump_10", "Grid Traveler")
    # if bump_is_steal:
    #     add_achievement(data, user_id_str, "first_steal", "Signal Thief")

    data["last_bump_time"] = now.isoformat()

    # Confirmation embed
    display_name = await resolve_display_name(disboard_message.guild, user_id, data)
    await save_data(data)  # save again to persist any name cache updates

    record = get_user_record(data, user_id_str)
    color = discord.Color.gold() if bump_is_steal else discord.Color.teal()
    title = "⚡ STEAL — SIGNAL INTERCEPTED" if bump_is_steal else "✅ B34C0N CONFIRMED"
    lines = [
        f"**{display_name}** transmitted the server beacon.",
        f"🔼 Total bumps: **{record['bumps']}**",
    ]
    if record["steals"]:
        lines.append(f"⚡ Steals: **{record['steals']}**")
    if bump_is_steal:
        lines.append(f"\n*Signal intercepted within {STEAL_WINDOW_SECONDS}s of cooldown reset.*")

    embed = discord.Embed(title=title, description="\n".join(lines), color=color, timestamp=now)
    await disboard_message.channel.send(embed=embed, delete_after=30)

# ─── SLASH COMMANDS ───────────────────────────────────────────────────────────

@bot.tree.command(name="bumpboard", description="View the bump leaderboard for The Digital Wasteland")
async def bumpboard(interaction: discord.Interaction):
    await interaction.response.defer()
    data = await load_data()

    if not data["bumps"]:
        await interaction.followup.send("No transmissions recorded yet. Use `/bump` to get started.", ephemeral=True)
        return

    sorted_bumpers = sorted(data["bumps"].items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []

    rank = 0
    for uid, count in sorted_bumpers:
        if len(lines) >= 10:
            break
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else data.get("names", {}).get(uid)
        if not name:
            continue  # skip unknown users
        rank += 1
        steals = data["steals"].get(uid, 0)
        medal = medals[rank - 1] if rank <= 3 else f"`{rank}.`"
        steal_str = f"  ⚡ {steals} steals" if steals else ""
        lines.append(f"{medal} **{name}** — {count} bumps{steal_str}")

    last_bump = data.get("last_bump_time")
    if last_bump:
        last_dt = datetime.fromisoformat(last_bump)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        next_bump = last_dt + timedelta(hours=BUMP_COOLDOWN_HOURS)
        now = datetime.now(timezone.utc)
        if now < next_bump:
            remaining = next_bump - now
            mins = int(remaining.total_seconds() // 60)
            footer = f"⏳ Next transmission in {mins}m"
        else:
            footer = "✅ Beacon ready — /bump now!"
    else:
        footer = "No transmissions recorded yet"

    embed = discord.Embed(
        title="📡 THE DIGITAL WASTELAND — B34C0N LEADERBOARD",
        description="\n".join(lines),
        color=discord.Color.teal(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"{footer}  •  ⚡ = steals")
    # TODO: ACHIEVEMENTS — add a generated banner image here
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="bumpstats", description="View bump stats for yourself or another member")
@app_commands.describe(member="The member to look up (defaults to you)")
async def bumpstats(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    target = member or interaction.user
    data = await load_data()
    uid = str(target.id)
    record = get_user_record(data, uid)

    rank = None
    if record["bumps"] > 0:
        sorted_bumpers = sorted(data["bumps"].items(), key=lambda x: x[1], reverse=True)
        for i, (u, _) in enumerate(sorted_bumpers):
            if u == uid:
                rank = i + 1
                break

    embed = discord.Embed(
        title=f"📊 B34C0N STATS — {target.display_name}",
        color=discord.Color.teal(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🔼 Bumps", value=str(record["bumps"]), inline=True)
    embed.add_field(name="⚡ Steals", value=str(record["steals"]), inline=True)
    if rank:
        embed.add_field(name="🏆 Rank", value=f"#{rank} of {len(data['bumps'])}", inline=True)
    # TODO: ACHIEVEMENTS — add achievement badges with generated images here

    content = target.mention if target != interaction.user else None
    await interaction.followup.send(content=content, embed=embed)


@bot.tree.command(name="beaconscrape", description="[Admin] Scan full channel history to calculate all bumps and steals")
@app_commands.checks.has_permissions(administrator=True)
async def beaconscrape(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(
        "🔍 Scanning channel history... this may take a moment for large channels.",
        ephemeral=True
    )

    channel = interaction.channel
    bump_events = []

    print(f"[beaconscrape] Starting scan of #{channel.name}...")

    # Fetch ALL messages in a single pass into memory — no nested API calls
    all_messages = []
    last_update = datetime.now(timezone.utc)
    bumps_found = 0

    async for message in channel.history(limit=10000, oldest_first=True):
        all_messages.append(message)

        # Count bump confirmations cheaply as we go
        if (
            message.author.id == DISBOARD_BOT_ID
            and message.embeds
        ):
            embed = message.embeds[0]
            desc = embed.description or ""
            if "Bump done" in desc or (embed.title and "Bump done" in embed.title):
                bumps_found += 1

        # Send a progress update every 15 seconds
        now = datetime.now(timezone.utc)
        if (now - last_update).total_seconds() >= 15:
            await interaction.edit_original_response(
                content=f"🔍 Still scanning... **{len(all_messages):,}** messages read, **{bumps_found}** bumps found so far."
            )
            last_update = now

    print(f"[beaconscrape] Fetched {len(all_messages)} total messages. Attributing bumps...")
    await interaction.edit_original_response(
        content=f"⚙️ Fetch complete — **{len(all_messages):,}** messages scanned. Attributing bumps and calculating steals..."
    )

    bump_events = []

    for idx, message in enumerate(all_messages):
        if message.author.id != DISBOARD_BOT_ID:
            continue
        if not message.embeds:
            continue

        embed = message.embeds[0]
        description = embed.description or ""
        if "Bump done" not in description and not (embed.title and "Bump done" in embed.title):
            continue

        # DISBOARD's confirmation carries interaction_metadata — that's the bumper
        user_id = get_interaction_user_id(message)
        display_name = None
        if user_id == DISBOARD_BOT_ID:
            user_id = None  # sanity: never credit DISBOARD itself

        # Ensure timestamp is UTC-aware
        ts = message.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        bump_events.append({"timestamp": ts, "user_id": user_id, "display_name": display_name})

    if not bump_events:
        await interaction.followup.send("❌ No DISBOARD bump confirmations found in this channel.", ephemeral=True)
        return

    print(f"[beaconscrape] Found {len(bump_events)} bump events. Calculating steals...")

    new_data = {"bumps": {}, "steals": {}, "last_bump_time": None, "names": {}}

    for i, event in enumerate(bump_events):
        uid = str(event["user_id"]) if event["user_id"] else "unknown"
        ts  = event["timestamp"]

        # Cache display name if we have one
        if event["user_id"] and event.get("display_name"):
            new_data["names"][uid] = event["display_name"]

        # Award bump
        new_data["bumps"][uid] = new_data["bumps"].get(uid, 0) + 1

        # Check for steal against previous bump's timestamp
        if i > 0:
            prev_ts = bump_events[i - 1]["timestamp"]
            gap_s = (ts - prev_ts).total_seconds()
            if is_steal(ts, prev_ts):
                new_data["steals"][uid] = new_data["steals"].get(uid, 0) + 1
                print(f"[beaconscrape] ⚡ Steal! user={uid} gap={gap_s:.0f}s")
            elif gap_s >= BUMP_COOLDOWN_HOURS * 3600 - 60:
                # Near-miss diagnostic: within 60s of the steal window
                window_start = BUMP_COOLDOWN_HOURS * 3600
                window_end   = window_start + STEAL_WINDOW_SECONDS
                print(f"[beaconscrape]    near-miss: user={uid} gap={gap_s:.0f}s (steal window={window_start}–{window_end}s)")

    # Record last bump time for live tracking to continue correctly
    new_data["last_bump_time"] = bump_events[-1]["timestamp"].isoformat()

    # Remove DISBOARD from results and totals
    disboard_id_str = str(DISBOARD_BOT_ID)
    new_data["bumps"].pop(disboard_id_str, None)
    new_data["steals"].pop(disboard_id_str, None)

    # Separate out unattributed bumps
    unattributed = new_data["bumps"].pop("unknown", 0)
    new_data["steals"].pop("unknown", None)

    await save_data(new_data)

    # Build result summary
    total_bumps  = sum(new_data["bumps"].values())
    total_steals = sum(new_data["steals"].values())
    sorted_bumpers = sorted(new_data["bumps"].items(), key=lambda x: x[1], reverse=True)

    lines = []
    display_bumps = 0
    display_steals = 0
    for uid, count in sorted_bumpers[:10]:
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else new_data.get("names", {}).get(uid)
        if not name:
            continue  # skip users who left and have no cached name
        steals = new_data["steals"].get(uid, 0)
        steal_str = f"  ⚡ {steals} steals" if steals else ""
        lines.append(f"**{name}** — {count} bumps{steal_str}")
        display_bumps += count
        display_steals += steals

    embed = discord.Embed(
        title="📡 B34C0N SCRAPE COMPLETE",
        description="\n".join(lines) if lines else "No attributable bumps found.",
        color=discord.Color.teal(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Total Bumps", value=str(display_bumps), inline=True)
    embed.add_field(name="Total Steals", value=str(display_steals), inline=True)
    embed.add_field(name="Scanned Events", value=str(len(bump_events)), inline=True)

    print(f"[beaconscrape] Done. {total_bumps} bumps, {total_steals} steals, {unattributed} unattributed.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@beaconscrape.error
async def beaconscrape_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Administrator permissions to run this command.", ephemeral=True)

# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is not set.")
    bot.run(BOT_TOKEN)
