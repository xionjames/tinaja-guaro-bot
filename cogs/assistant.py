"""The Assistant: a local LLM behind an OpenAI-compatible API, plus the Server's Notes and TODOs.

Everything lives in one module because `tinaja-bot run` can't import a helper from another cog file;
the loader still loads each Cog class defined here.
"""

import functools
import os
import re
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

import aiohttp
from discord.ext import commands
from tinaja_base.channel import recent_messages
from tinaja_base.config import Config

SYSTEM_PROMPT = (
    'You are Tinaja Guaro Bot, a friendly technology assistant on a Discord server. '
    'Always answer in the language of the question (Spanish or English). '
    'Be brief and practical; use Markdown code blocks for code or commands. '
    "If the server's notes below are relevant, rely on them; if you don't know something, say so."
)
PERSONA_FILE = 'PERSONA.md'
# Small models drift between languages with a long bilingual prompt; a reminder right after the question fixes it
LANGUAGE_REMINDERS = {
    'es': (
        '(Responde en español, como el pana guaro que eres: incluye 2 o 3 palabras del vocabulario venezolano, '
        'bien usadas y repartidas. Si la pregunta es ambigua, pregunta primero. Empieza directo con el tema y '
        'termina con información útil.)'
    ),
    'en': '(Answer in English only, in plain English: no Spanish words or slang. If the question is ambiguous, ask first.)',
}
SPANISH_WORDS = {
    'que',
    'qué',
    'como',
    'cómo',
    'el',
    'la',
    'los',
    'las',
    'de',
    'del',
    'es',
    'en',
    'un',
    'una',
    'y',
    'por',
    'para',
    'con',
    'mi',
    'me',
    'se',
    'no',
    'hay',
    'está',
    'esto',
    'eso',
    'cual',
    'cuál',
    'puedo',
}
ENGLISH_WORDS = {
    'hi',
    'hello',
    'hey',
    'thanks',
    'thank',
    'please',
    'what',
    'how',
    'the',
    'a',
    'an',
    'is',
    'are',
    'do',
    'does',
    'i',
    'my',
    'to',
    'of',
    'in',
    'on',
    'and',
    'for',
    'with',
    'can',
    'it',
    'this',
    'that',
    'which',
    'why',
    'should',
}
DISCORD_LIMIT = 2000
HISTORY_SCAN = 20  # recent channel messages the Assistant looks through...
HISTORY_MAX_AGE = timedelta(hours=1)  # ...as long as they're this recent...
HISTORY_LIMIT = 5  # ...keeping at most this many as context
HISTORY_HEADER = (
    'Recent messages in the channel, only context: use them if the message below refers to them, never answer them.'
)
HISTORY_CHARS = 600  # each one cut to this, so a long answer doesn't fill a small model's context
LIST_LIMIT = 10


class Store:
    """The Server's Notes and TODOs in one SQLite file, opened on first use"""

    def __init__(self, path):
        self.path = path
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = sqlite3.connect(self.path)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS notes
                    USING fts5(text, guild_id UNINDEXED, author_id UNINDEXED, created_at UNINDEXED);
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    done_at REAL
                );
                """
            )
        return self._db

    def _write(self, sql, args):
        with self.db:
            return self.db.execute(sql, args)

    # Notes: the id is the FTS rowid

    def add_note(self, guild_id, author_id, text):
        sql = 'INSERT INTO notes (text, guild_id, author_id, created_at) VALUES (?, ?, ?, ?)'
        return self._write(sql, (text, guild_id, author_id, time.time())).lastrowid

    def recent_notes(self, guild_id, limit=LIST_LIMIT):
        sql = 'SELECT rowid AS id, * FROM notes WHERE guild_id = ? ORDER BY rowid DESC LIMIT ?'
        return self.db.execute(sql, (guild_id, limit)).fetchall()

    def search_notes(self, guild_id, query, limit=5):
        # Quote every word so FTS5 syntax in a Member's text (AND, -, *, ...) can't break the query
        words = re.findall(r'\w+', query)
        if not words:
            return []
        match = ' OR '.join(f'"{word}"' for word in words)
        sql = 'SELECT rowid AS id, * FROM notes WHERE notes MATCH ? AND guild_id = ? ORDER BY rank LIMIT ?'
        return self.db.execute(sql, (match, guild_id, limit)).fetchall()

    def delete_note(self, guild_id, author_id, note_id):
        sql = 'DELETE FROM notes WHERE rowid = ? AND guild_id = ? AND author_id = ?'
        return self._write(sql, (note_id, guild_id, author_id)).rowcount > 0

    def count_notes(self, guild_id):
        return self.db.execute('SELECT count(*) FROM notes WHERE guild_id = ?', (guild_id,)).fetchone()[0]

    # TODOs

    def add_todo(self, guild_id, author_id, text):
        sql = 'INSERT INTO todos (guild_id, author_id, text, created_at) VALUES (?, ?, ?, ?)'
        return self._write(sql, (guild_id, author_id, text, time.time())).lastrowid

    def todos(self, guild_id, done=False, limit=LIST_LIMIT):
        order = 'done_at DESC' if done else 'id'
        sql = f'SELECT * FROM todos WHERE guild_id = ? AND done = ? ORDER BY {order} LIMIT ?'
        return self.db.execute(sql, (guild_id, int(done), limit)).fetchall()

    def set_done(self, guild_id, todo_id, done=True):
        sql = 'UPDATE todos SET done = ?, done_at = ? WHERE id = ? AND guild_id = ?'
        return self._write(sql, (int(done), time.time() if done else None, todo_id, guild_id)).rowcount > 0

    def delete_todo(self, guild_id, author_id, todo_id):
        sql = 'DELETE FROM todos WHERE id = ? AND guild_id = ? AND author_id = ?'
        return self._write(sql, (todo_id, guild_id, author_id)).rowcount > 0

    def count_todos(self, guild_id, done=False):
        sql = 'SELECT count(*) FROM todos WHERE guild_id = ? AND done = ?'
        return self.db.execute(sql, (guild_id, int(done))).fetchone()[0]


store = Store(os.getenv('DATA_DB') or 'bot.db')


async def chat(messages):
    """Ask the LLM; any OpenAI-compatible server works (Ollama, LM Studio, llama.cpp, Docker Model Runner...)"""
    base_url = (os.getenv('LLM_BASE_URL') or 'http://localhost:8080/v1').rstrip('/')
    headers = {'Authorization': f'Bearer {os.getenv("LLM_API_KEY") or "none"}'}
    body = {
        'model': os.getenv('LLM_MODEL') or 'default_model',
        'messages': messages,
        'temperature': float(os.getenv('LLM_TEMPERATURE') or 0.7),  # some servers default to 0: same words every time
    }
    timeout = aiohttp.ClientTimeout(total=float(os.getenv('LLM_TIMEOUT') or 120))
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f'{base_url}/chat/completions', json=body, headers=headers) as response,
        ):
            response.raise_for_status()
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError) as error:
        print(f'LLM request failed: {error!r}')
        return "Sorry, I can't reach my brain right now / No puedo contactar al modelo ahora mismo."
    answer = data['choices'][0]['message']['content'] or ''
    # Reasoning models (e.g. Qwen3) may include their thinking; Members only want the answer
    return re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip()


def system_prompt():
    """PERSONA.md when there is one (read every time, so edits apply live), else the neutral prompt"""
    try:
        persona = Path(os.getenv('PERSONA_FILE') or PERSONA_FILE).read_text(encoding='utf-8')
    except FileNotFoundError:
        return SYSTEM_PROMPT
    return re.sub(r'<!--.*?-->', '', persona, flags=re.DOTALL).strip() or SYSTEM_PROMPT


def language(text):
    """'es' or 'en', a cheap guess good enough to remind the model which language to answer in.

    Spanish wins ties: it's the Server's main language, and short messages ('hola', 'ok') have no clues.
    """
    text = text.lower()
    if re.search(r'[¿¡ñáéíóú]', text):
        return 'es'
    words = set(re.findall(r'\w+', text))
    return 'en' if len(words & ENGLISH_WORDS) > len(words & SPANISH_WORDS) else 'es'


def drop_filler_ending(answer):
    """Small models like to sign off with a short exclamation ('¡Vaina, todo fino!'); drop that last line"""
    lines = answer.rstrip().splitlines()
    if len(lines) > 1 and re.fullmatch(r'¡[^!\n]{1,40}!', lines[-1].strip()) and len(lines[-1].split()) <= 5:
        return '\n'.join(lines[:-1]).rstrip()
    return answer


def chunks(text, size=DISCORD_LIMIT):
    """Split text into Discord-sized messages, preferring line breaks"""
    while len(text) > size:
        cut = text.rfind('\n', 0, size)
        cut = cut if cut > 0 else size
        yield text[:cut]
        text = text[cut:].lstrip('\n')
    if text:
        yield text


def guild_id(ctx):
    return ctx.guild.id


def note_line(note):
    return f'`#{note["id"]}` {note["text"]}'


def todo_line(todo):
    return f'`#{todo["id"]}` {"~~" + todo["text"] + "~~" if todo["done"] else todo["text"]}'


def listing(title, lines, empty):
    return '\n'.join([f'**{title}**', *lines]) if lines else empty


class Notes(commands.Cog):
    @commands.group(name='note', invoke_without_command=True)
    @commands.guild_only()
    async def note(self, ctx):
        await ctx.send(
            f'Usage: `{ctx.prefix}note add <text>`, `{ctx.prefix}note search <words>`, '
            f'`{ctx.prefix}note list`, `{ctx.prefix}note delete <id>`'
        )

    @note.command(name='add')
    async def add(self, ctx, *, text):
        note_id = store.add_note(guild_id(ctx), ctx.author.id, text)
        await ctx.send(f'Saved note `#{note_id}`')

    @note.command(name='search')
    async def search(self, ctx, *, query):
        notes = store.search_notes(guild_id(ctx), query, limit=LIST_LIMIT)
        await ctx.send(listing(f'Notes matching "{query}"', [note_line(n) for n in notes], 'No matching notes'))

    @note.command(name='list')
    async def list_(self, ctx):
        notes = store.recent_notes(guild_id(ctx))
        await ctx.send(listing('Latest notes', [note_line(n) for n in notes], 'No notes yet'))

    @note.command(name='delete')
    async def delete(self, ctx, note_id: int):
        deleted = store.delete_note(guild_id(ctx), ctx.author.id, note_id)
        await ctx.send(f'Deleted note `#{note_id}`' if deleted else f'No note `#{note_id}` of yours here')


class Todos(commands.Cog):
    @commands.group(name='todo', invoke_without_command=True)
    @commands.guild_only()
    async def todo(self, ctx):
        await self.list_(ctx)

    @todo.command(name='add')
    async def add(self, ctx, *, text):
        todo_id = store.add_todo(guild_id(ctx), ctx.author.id, text)
        await ctx.send(f'Added TODO `#{todo_id}`')

    @todo.command(name='list')
    async def list_(self, ctx):
        todos = store.todos(guild_id(ctx))
        await ctx.send(listing('Pending TODOs', [todo_line(t) for t in todos], 'Nothing pending 🎉'))

    @todo.command(name='done-list')
    async def done_list(self, ctx):
        todos = store.todos(guild_id(ctx), done=True)
        await ctx.send(listing('Recently done', [todo_line(t) for t in todos], 'Nothing done yet'))

    @todo.command(name='done')
    async def done(self, ctx, todo_id: int):
        changed = store.set_done(guild_id(ctx), todo_id)
        await ctx.send(f'Done: `#{todo_id}` ✅' if changed else f'No TODO `#{todo_id}` here')

    @todo.command(name='undo')
    async def undo(self, ctx, todo_id: int):
        changed = store.set_done(guild_id(ctx), todo_id, done=False)
        await ctx.send(f'`#{todo_id}` is pending again' if changed else f'No TODO `#{todo_id}` here')

    @todo.command(name='delete')
    async def delete(self, ctx, todo_id: int):
        deleted = store.delete_todo(guild_id(ctx), ctx.author.id, todo_id)
        await ctx.send(f'Deleted TODO `#{todo_id}`' if deleted else f'No TODO `#{todo_id}` of yours here')

    @commands.command(name='summary', aliases=['resumen'])
    @commands.guild_only()
    async def summary(self, ctx):
        gid = guild_id(ctx)
        notes = store.recent_notes(gid, limit=3)
        todos = store.todos(gid, limit=5)
        counts = (
            f'**{store.count_notes(gid)}** notes, **{store.count_todos(gid)}** pending TODOs, '
            f'**{store.count_todos(gid, done=True)}** done'
        )
        lines = [counts]
        if notes:
            lines += ['**Latest notes**', *map(note_line, notes)]
        if todos:
            lines += ['**Pending**', *map(todo_line, todos)]
        await ctx.send('\n'.join(lines))


class Assistant(commands.Cog):
    @commands.command(name='ask')
    async def ask(self, ctx, *, question):
        async with ctx.typing():
            answer = await self.answer(ctx.guild, ctx.channel, ctx.message, ctx.me, question)
        for part in chunks(answer):
            await ctx.send(part)

    @commands.Cog.listener()
    async def on_mention(self, message, text):
        # Fired when a Member @mentions the bot with anything that isn't a Command
        if not text:
            return
        me = message.guild.me if message.guild else message.channel.me
        async with message.channel.typing():
            answer = await self.answer(message.guild, message.channel, message, me, text)
        for part in chunks(answer):
            await message.reply(part)

    async def answer(self, guild, channel, message, me, question):
        return drop_filler_ending(await chat(await self.prompt(guild, channel, message, me, question)))

    async def prompt(self, guild, channel, message, me, question):
        system = [system_prompt()]
        if guild is not None:
            if notes := store.search_notes(guild.id, question):
                system += ["\nServer's notes:", *(f'- {n["text"]}' for n in notes)]
            if todos := store.todos(guild.id):
                system += ["\nServer's pending TODOs:", *(f'- #{t["id"]} {t["text"]}' for t in todos)]
        # Name who's asking, or the model takes the last name it saw in the history for the asker
        asker = message.author.display_name
        system.append(f'\nYou are answering {asker}. Messages from other people are only context.')
        # The history goes inside this one message, clearly marked as context: sent as chat turns of its own,
        # a small model answers an earlier question there instead of a short new message like 'hola'
        user = []
        if history := await conversation(channel, message, me, question):
            user += [HISTORY_HEADER, *history, '']
        user += [f'Message from {asker}, answer this one:', question, '', LANGUAGE_REMINDERS[language(question)]]
        return [{'role': 'system', 'content': '\n'.join(system)}, {'role': 'user', 'content': '\n'.join(user)}]


@functools.cache
def command_prefix():
    """The prefix from bot.toml: a Cog gets no Config, and on_mention has no ctx.prefix"""
    try:
        return Config.load(require_token=False).prefix
    except FileNotFoundError:
        return '!!'


async def conversation(channel, message, me, question):
    """The recent conversation as 'name: text' lines, the bot's own replies marked '(you)'.

    Only the asker's own exchanges with the bot, plus other people's messages when the asker points at them:
    a Discord reply to one, an @mention, or their name in the question. A small model answers any question
    it sees, so a bare 'hola' must not see someone else's unanswered question.
    Other Commands (e.g. '!!note list') and the bot's replies to them are left out, they aren't conversation.
    """
    pointed_ids = {user.id for user in message.mentions if user.id != me.id}
    reference = message.reference.resolved if message.reference else None
    recent = [
        m
        for m in await recent_messages(channel, limit=HISTORY_SCAN, include_bots=True)
        if m.id != message.id and m.created_at >= message.created_at - HISTORY_MAX_AGE
    ]
    for m in recent:
        name = m.author.display_name.lower()
        if not m.author.bot and len(name) >= 3 and re.search(rf'\b{re.escape(name)}\b', question.lower()):
            pointed_ids.add(m.author.id)
    if reference is not None and all(m.id != reference.id for m in recent):
        recent.insert(0, reference)  # a reply to an older message: bring it in

    lines = []
    with_bot_reply = False  # whether the bot's next message answers someone included
    for m in recent:
        if m.author.id == me.id:
            if with_bot_reply:
                lines.append(f'{me.display_name} (you): {m.content[:HISTORY_CHARS]}')
            continue
        if m.author.bot:
            continue
        text = ' '.join(re.sub(r'<@!?\d+>', '', m.content).split())
        prefix = command_prefix()
        command = None
        if text.startswith(prefix):
            command, _, text = text[len(prefix) :].partition(' ')
        included = m.author.id == message.author.id or m.author.id in pointed_ids or m == reference
        with_bot_reply = included and command in (None, 'ask')
        if with_bot_reply and text:
            lines.append(f'{m.author.display_name}: {text[:HISTORY_CHARS]}')
    return lines[-HISTORY_LIMIT:]
