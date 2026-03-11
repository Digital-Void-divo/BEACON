"""
UPLINK — Bump Tracker for The Digital Wasteland
================================================
Tracks DISBOARD bumps and steals with /leaderboard and /bumpstats slash commands.
bump_data.json lives in your GitHub repo and is read/written via the GitHub API
on every successful bump. Railway only needs BOT_TOKEN and GITHUB_TOKEN set.

SETUP:
1. Set these environment variables in Railway:
   - BOT_TOKEN      → Your Discord bot token
   - GITHUB_TOKEN   → GitHub Personal Access Token (repo scope)
2. Update GITHUB_REPO below to match your repo (e.g. "yourname/digital-wasteland-bot")
3. Push all files to GitHub, Railway will auto-deploy

FUTURE EXPANSION POINTS (marked with # TODO: ACHIEVEMENTS):
  - add_achievement() helper is ready to uncomment and call from anywhere
  - Hook points already placed after every bump and steal
  - Placeholder spots in /leaderboard and /bumpstats for generated badge images
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import json
import os
import base64
import requests

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "yourname/digital-wasteland-bot"  # ← Update this before pushing
GITHUB_FILE  = "bump_data.json"

DISBOARD_BOT_ID      = 302050872383242240
BUMP_COOLDOWN_HOURS  = 2
STEAL_WINDOW_SECONDS = 10

# ─── GITHUB DATA HELPERS ──────────────────────────────────────────────────────

def github_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

def github_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"

_file_sha: str | None = None  # GitHub requires the current SHA to update a file

def load_data() -> dict:
    """Read bump_data.json from GitHub."""
    global _file_sha
    try:
        response = requests.get(github_api_url(), headers=github_headers(), timeout=10)
        if response.status_code == 200:
            payload = response.json()
            _file_sha = payload["sha"]
            return json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
        else:
            print(f"⚠️  GitHub load failed: {response.status_code}")
    except Exception as e:
        print(f"⚠️  GitHub load error: {e}")
    return {"bumps": {}, "steals": {}, "last_bump_time": None}

def save_data(data: dict):
    """Write bump_data.json back to GitHub as a commit."""
    global _file_sha
    content = base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")
    payload = {
        "message": "chore: update bump data",
        "content": content,
    }
    if _file_sha:
        payload["sha"] = _file_sha  # required for updates, not needed for first commit
    try:
        response = requests.put(github_api_url(), headers=github_headers(), json=payload, timeout=10)
        if response.status_code in (200, 201):
            _file_sha = response.json()["content"]["sha"]
        else:
            print(f"⚠️  GitHub save failed: {response.status_code} {response.text}")
    except Exception as e:
        print(f"⚠️  GitHub save error: {e}")

def get_user_record(data: dict, user_id: str) -> dict:
    return {
        "bumps": data["bumps"].get(user_id, 0),
        "steals": data["steals"].get(user_id, 0),
        # TODO: ACHIEVEMENTS
        # "achievements": data.get("achievements", {}).get(user_id, []),
    }

# TODO: ACHIEVEMENTS
# def add_achievement(data: dict, user_id: str, achievement_id: str, achievement_name: str):
#     """Award an achievement to a user. Safe to call multiple times — won't duplicate."""
#     data.setdefault("achievements", {}).setdefault(user_id, [])
#     existing_ids = [a["id"] for a in data["achievements"][user_id]]
#     if achievement_id not in existing_ids:
#         data["achievements"][user_id].append({
#             "id": achievement_id,
#             "name": achievement_name,
#             "awarded_at": datetime.now(timezone.utc).isoformat(),
#         })
#         return True  # newly awarded
#     return False  # already had it

# ─── BOT SETUP ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
pending_bumps: dict[int, tuple[int, datetime]] = {}

# ─── EVENTS ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ UPLINK online as {bot.user} (ID: {bot.user.id})")
    print(f"   Steal window: {STEAL_WINDOW_SECONDS}s | Slash commands synced")
    print(f"   Persisting data to: github.com/{GITHUB_REPO}/{GITHUB_FILE}")

@bot.event
async def on_message(message: discord.Message):
    # Track who triggered /bump before DISBOARD responds
    if (
        message.type == discord.MessageType.chat_input_command
        and message.interaction is not None
        and message.interaction.name == "bump"
    ):
        pending_bumps[message.channel.id] = (message.interaction.user.id, datetime.now(timezone.utc))

    # Detect DISBOARD's success embed
    if message.author.id == DISBOARD_BOT_ID and message.embeds:
        embed = message.embeds[0]
        description = embed.description or ""
        if "Bump done" in description or (embed.title and "Bump done" in embed.title):
            await handle_successful_bump(message)

    await bot.process_commands(message)

async def handle_successful_bump(disboard_message: discord.Message):
    channel_id = disboard_message.channel.id
    now = datetime.now(timezone.utc)

    # Attribute the bump to a user
    bump_entry = pending_bumps.pop(channel_id, None)
    if bump_entry is None:
        async for msg in disboard_message.channel.history(limit=10, before=disboard_message):
            if (
                msg.type == discord.MessageType.chat_input_command
                and msg.interaction is not None
                and msg.interaction.name == "bump"
            ):
                bump_entry = (msg.interaction.user.id, msg.created_at)
                break

    if bump_entry is None:
        print(f"⚠️  Could not attribute bump in #{disboard_message.channel.name}")
        return

    user_id, _ = bump_entry
    user_id_str = str(user_id)
    data = load_data()
    last_bump_iso = data.get("last_bump_time")

    # Award bump
    data["bumps"][user_id_str] = data["bumps"].get(user_id_str, 0) + 1

    # Check for steal
    is_steal = False
    if last_bump_iso:
        last_bump_time = datetime.fromisoformat(last_bump_iso)
        cooldown_reset = last_bump_time + timedelta(hours=BUMP_COOLDOWN_HOURS)
        steal_window_end = cooldown_reset + timedelta(seconds=STEAL_WINDOW_SECONDS)
        if cooldown_reset <= now <= steal_window_end:
            is_steal = True
            data["steals"][user_id_str] = data["steals"].get(user_id_str, 0) + 1

    # TODO: ACHIEVEMENTS — example hooks
    # if data["bumps"][user_id_str] == 10:
    #     add_achievement(data, user_id_str, "bump_10", "Grid Traveler")
    # if is_steal:
    #     add_achievement(data, user_id_str, "first_steal", "Signal Thief")

    data["last_bump_time"] = now.isoformat()
    save_data(data)

    # Confirmation embed
    try:
        member = disboard_message.guild.get_member(user_id) or await disboard_message.guild.fetch_member(user_id)
        display_name = member.display_name
    except Exception:
        display_name = f"<@{user_id}>"

    record = get_user_record(data, user_id_str)
    color = discord.Color.gold() if is_steal else discord.Color.teal()
    title = "⚡ STEAL — SIGNAL INTERCEPTED" if is_steal else "✅ UPLINK CONFIRMED"
    lines = [
        f"**{display_name}** transmitted the server beacon.",
        f"🔼 Total bumps: **{record['bumps']}**",
    ]
    if record["steals"]:
        lines.append(f"⚡ Steals: **{record['steals']}**")
    if is_steal:
        lines.append(f"\n*Signal intercepted within {STEAL_WINDOW_SECONDS}s of cooldown reset.*")

    embed = discord.Embed(title=title, description="\n".join(lines), color=color, timestamp=now)
    await disboard_message.channel.send(embed=embed, delete_after=30)

# ─── SLASH COMMANDS ───────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboard", description="View the bump leaderboard for The Digital Wasteland")
async def leaderboard(interaction: discord.Interaction):
    data = load_data()

    if not data["bumps"]:
        await interaction.response.send_message("No transmissions recorded yet. Use `/bump` to get started.", ephemeral=True)
        return

    sorted_bumpers = sorted(data["bumps"].items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []

    for i, (uid, count) in enumerate(sorted_bumpers[:10]):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"Unknown ({uid})"
        steals = data["steals"].get(uid, 0)
        medal = medals[i] if i < 3 else f"`{i+1}.`"
        steal_str = f"  ⚡ {steals} steals" if steals else ""
        lines.append(f"{medal} **{name}** — {count} bumps{steal_str}")

    # Next bump timer
    last_bump = data.get("last_bump_time")
    if last_bump:
        last_dt = datetime.fromisoformat(last_bump)
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
        title="📡 THE DIGITAL WASTELAND — UPLINK LEADERBOARD",
        description="\n".join(lines),
        color=discord.Color.teal(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"{footer}  •  ⚡ = steals")
    # TODO: ACHIEVEMENTS — add a generated banner image here
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="bumpstats", description="View bump stats for yourself or another member")
@app_commands.describe(member="The member to look up (defaults to you)")
async def bumpstats(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    data = load_data()
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
        title=f"📊 UPLINK STATS — {target.display_name}",
        color=discord.Color.teal(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🔼 Bumps", value=str(record["bumps"]), inline=True)
    embed.add_field(name="⚡ Steals", value=str(record["steals"]), inline=True)
    if rank:
        embed.add_field(name="🏆 Rank", value=f"#{rank} of {len(data['bumps'])}", inline=True)
    # TODO: ACHIEVEMENTS — add achievement badges with generated images here

    await interaction.response.send_message(embed=embed)

# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is not set.")
    bot.run(BOT_TOKEN)
