import discord
from discord.ext import commands
from discord import app_commands
from collections import Counter
import re
import sqlite3
import aiohttp
import asyncio
import os
from dotenv import load_dotenv
from typing import Optional

class LeaderboardBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        
        self.db = sqlite3.connect("word_counts.db")
        self.cursor = self.db.cursor()
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS word_counts (
                user_id INTEGER,
                word TEXT,
                count INTEGER,
                PRIMARY KEY (user_id, word)
            )
        ''')
        self.db.commit()

    async def setup_hook(self):
        await self.tree.sync()

    async def close(self):
        self.db.close()
        await super().close()

client = LeaderboardBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
        
    words = re.findall(r"[a-z0-9']+", message.content.lower())
    if not words:
        return
        
    counts = Counter(words)
    for word, count in counts.items():
        client.cursor.execute('''
            INSERT INTO word_counts (user_id, word, count)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, word) DO UPDATE SET count = count + ?
        ''', (message.author.id, word, count, count))
        
    client.db.commit()

@client.tree.command(name="sync_history", description="Scan all past messages to build the initial database.")
async def sync_history(interaction: discord.Interaction):
    await interaction.response.send_message("Starting a server scan")
    
    # Wipe existing data so we don't double count
    client.cursor.execute("DELETE FROM word_counts")
    client.db.commit()
    
    master_counts = Counter()
    channels_scanned = 0
    
    all_channels = interaction.guild.text_channels + interaction.guild.voice_channels + interaction.guild.threads
    
    for channel in all_channels:
        if hasattr(channel, "permissions_for") and not channel.permissions_for(interaction.guild.me).read_message_history:
            continue
            
        last_message = None
        
        while True:
            try:
                async for message in channel.history(limit=None, before=last_message):
                    last_message = message
                    if message.author.bot:
                        continue
                        
                    words = re.findall(r"[a-z0-9']+", message.content.lower())
                    for word in words:
                        master_counts[(message.author.id, word)] += 1
                        
                channels_scanned += 1
                break
                
            except discord.Forbidden:
                break
            except (aiohttp.ClientPayloadError, discord.HTTPException, aiohttp.ClientOSError):
                print(f"Network drop in #{channel.name}. Retrying from last known message...")
                await asyncio.sleep(5)
            except AttributeError:
                break

    if master_counts:
        db_data = [(user_id, word, count) for (user_id, word), count in master_counts.items()]
        client.cursor.executemany('''
            INSERT INTO word_counts (user_id, word, count)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, word) DO UPDATE SET count = word_counts.count + excluded.count
        ''', db_data)
        client.db.commit()

    await interaction.channel.send(f"<@{interaction.user.id}> Sync complete!")

@client.tree.command(name="leaderboard", description="Find who said a word the most")
@app_commands.describe(
    word="The word to search for",
    recent="Scan the last X messages per channel (leave blank for all-time cache)",
    strict="Require an exact word match (True) or allow substrings (False)"
)
async def leaderboard(interaction: discord.Interaction, word: str, recent: Optional[int] = None, strict: bool = False):
    word_lower = word.lower().strip()
    
    # ALL-TIME
    if recent is None:
        if strict:
            # Exact Match
            client.cursor.execute('''
                SELECT user_id, count as total_count FROM word_counts 
                WHERE word = ? 
                ORDER BY total_count DESC 
                LIMIT 10
            ''', (word_lower,))
        else:
            # Substring 
            client.cursor.execute('''
                SELECT user_id, SUM(count) as total_count FROM word_counts 
                WHERE word LIKE ? 
                GROUP BY user_id
                ORDER BY total_count DESC 
                LIMIT 10
            ''', (f'%{word_lower}%',))
            
        top_users = client.cursor.fetchall()
        
        if not top_users:
            await interaction.response.send_message(f"Nobody has said `{word}` (Strict: {strict}).")
            return
            
        embed = discord.Embed(title=f"Word Leaderboard: '{word}'", color=discord.Color.green())
        description = "*Data loaded instantly from all-time database.*\n"
        description += f"*Match Type: {'Strict' if strict else 'Broad Substring'}*\n\n"
        
        for rank, (user_id, count) in enumerate(top_users, start=1):
            description += f"**{rank}.** <@{user_id}>: {count} times\n"
            
        embed.description = description
        await interaction.response.send_message(embed=embed)
        
    # RECENT SCAN
    else:
        await interaction.response.defer()
        counts = Counter()
        channels_scanned = 0
        
        for channel in interaction.guild.text_channels:
            if not channel.permissions_for(interaction.guild.me).read_message_history:
                continue
                
            try:
                async for message in channel.history(limit=recent):
                    if message.author.bot:
                        continue
                        
                    msg_words = re.findall(r"[a-z0-9']+", message.content.lower())
                    
                    if strict:
                        # Only count exact token matches
                        match_count = sum(1 for w in msg_words if w == word_lower)
                    else:
                        # Count if the word is found anywhere inside the token
                        match_count = sum(1 for w in msg_words if word_lower in w)
                        
                    if match_count > 0:
                        counts[message.author.id] += match_count
                channels_scanned += 1
            except discord.Forbidden:
                pass

        if not counts:
            await interaction.followup.send(f"Nobody said `{word}` in the last {recent} messages (Strict: {strict}).")
            return
            
        top_users = counts.most_common(10)
        embed = discord.Embed(title=f"Word Leaderboard: '{word}'", color=discord.Color.blue())
        description = f"*Scanned last {recent} messages across {channels_scanned} channels.*\n"
        description += f"*Match Type: {'Strict' if strict else 'Broad Substring'}*\n\n"
        
        for rank, (user_id, count) in enumerate(top_users, start=1):
            description += f"**{rank}.** <@{user_id}>: {count} times\n"
            
        embed.description = description
        await interaction.followup.send(embed=embed)
        
client.run(os.getenv('DISCORD_TOKEN'))