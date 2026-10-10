import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from tinaja_base.testing import FakeChannel, FakeContext, fake_member
from tinaja_base.testing import fake_message as base_fake_message

from cogs import assistant
from cogs.assistant import Assistant, Notes, Store, Todos, chunks

BOT = fake_member('Tinaja Guaro Bot', bot=True, id=42)
GUILD = SimpleNamespace(id=1, me=BOT)
ANA = fake_member('ana', id=7)
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


LUIS = fake_member('luis', id=8)


def fake_message(content, minutes_ago=0, reply_to=None, **kwargs):
    message = base_fake_message(content, **kwargs)
    message.created_at = NOW - timedelta(minutes=minutes_ago)  # Discord messages know when they were sent...
    message.reference = reply_to and SimpleNamespace(message_id=reply_to.id, resolved=reply_to)  # ...and replies
    return message


def typing_channel(messages=()):
    channel = FakeChannel(messages)
    channel.typing = contextlib.nullcontext
    return channel


def context(message=None, channel=None):
    ctx = FakeContext(channel=channel or typing_channel(), message=message)
    ctx.guild = GUILD
    ctx.me = BOT
    ctx.prefix = '!!'
    ctx.typing = contextlib.nullcontext
    return ctx


async def run(cog, group, name, *args, ctx=None, **kwargs):
    ctx = ctx or context()
    command = next(c for c in cog.walk_commands() if c.qualified_name == f'{group} {name}'.strip())
    await command.callback(cog, ctx, *args, **kwargs)
    return ctx.sent


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    test_store = Store(tmp_path / 'bot.db')
    monkeypatch.setattr(assistant, 'store', test_store)
    return test_store


@pytest.fixture
def prompts(monkeypatch):
    sent = []

    async def fake_chat(messages):
        sent.append(messages)
        return 'Usamos WireGuard'

    monkeypatch.setattr(assistant, 'chat', fake_chat)
    return sent


async def test_ask_sends_notes_and_todos_to_the_llm(store, prompts):
    store.add_note(1, 10, 'Our VPN is WireGuard on port 51820')
    store.add_todo(1, 10, 'renew the TLS cert')
    question = fake_message('!!ask ¿qué VPN usamos?', author=ANA, id=2)

    sent = await run(
        Assistant(), 'ask', '', ctx=context(question, typing_channel([question])), question='¿qué VPN usamos?'
    )

    assert sent == ['Usamos WireGuard']
    system, user = prompts[0]
    assert user['role'] == 'user'
    assert user['content'].startswith(
        'Message from ana, answer this one:\n¿qué VPN usamos?\n\n(Responde en español, como el pana guaro que eres'
    )
    assert 'You are answering ana.' in system['content']
    assert 'WireGuard on port 51820' in system['content']
    assert 'renew the TLS cert' in system['content']


async def test_the_history_is_context_before_the_message_to_answer(prompts):
    channel = typing_channel(
        [
            fake_message('<@42> ¿cómo instalo Docker en Ubuntu?', author=ANA, id=1),
            fake_message('Con `apt install docker.io`.', author=BOT, id=2),
            fake_message('!!note list', author=ANA, id=3),  # another Command: not conversation
            fake_message('**Latest notes**\n`#1` nginx', author=BOT, id=4),  # ...nor the bot's answer to it
            fake_message('hello from another bot', author=fake_member('other', bot=True, id=99), id=5),
            fake_message('!!ask ¿y en Windows?', author=ANA, id=6),
        ]
    )
    question = channel.messages[-1]

    await run(Assistant(), 'ask', '', ctx=context(question, channel), question='¿y en Windows?')

    _, user = prompts[0]  # one user message: no earlier question for the model to answer instead
    lines = user['content'].splitlines()
    assert lines[0].startswith('Recent messages in the channel, only context')
    assert lines[1:4] == [
        'ana: ¿cómo instalo Docker en Ubuntu?',
        'Tinaja Guaro Bot (you): Con `apt install docker.io`.',
        '',
    ]
    assert lines[4:6] == ['Message from ana, answer this one:', '¿y en Windows?']


async def test_a_mention_without_history_is_just_the_message(prompts):
    message = fake_message('<@42> hola', author=ANA, id=1)
    message.guild = GUILD
    message.channel = typing_channel([message])

    await Assistant().on_mention(message, 'hola')

    _, user = prompts[0]
    assert user['content'].startswith('Message from ana, answer this one:\nhola\n')


async def test_mention_replies_with_the_answer(prompts):
    message = fake_message('<@42> what is a reverse proxy?', id=3)
    message.guild = GUILD
    message.channel = typing_channel([message])

    await Assistant().on_mention(message, 'what is a reverse proxy?')

    assert message.replies == ['Usamos WireGuard']


def test_long_answers_are_split_for_discord():
    parts = list(chunks('a' * 1500 + '\n' + 'b' * 1500))
    assert parts == ['a' * 1500, 'b' * 1500]
    assert all(len(p) <= 2000 for p in chunks('x' * 4500))


async def test_note_commands():
    assert await run(Notes(), 'note', 'add', text='nginx is on port 8080') == ['Saved note `#1`']
    assert await run(Notes(), 'note', 'search', query='nginx') == [
        '**Notes matching "nginx"**\n`#1` nginx is on port 8080'
    ]
    assert await run(Notes(), 'note', 'list') == ['**Latest notes**\n`#1` nginx is on port 8080']
    assert await run(Notes(), 'note', 'delete', 1) == ['Deleted note `#1`']
    assert await run(Notes(), 'note', 'list') == ['No notes yet']


async def test_todo_commands():
    assert await run(Todos(), 'todo', 'add', text='renew the TLS cert') == ['Added TODO `#1`']
    assert await run(Todos(), 'todo', 'list') == ['**Pending TODOs**\n`#1` renew the TLS cert']
    assert await run(Todos(), 'todo', 'done', 1) == ['Done: `#1` ✅']
    assert await run(Todos(), 'todo', 'list') == ['Nothing pending 🎉']
    assert await run(Todos(), 'todo', 'done-list') == ['**Recently done**\n`#1` ~~renew the TLS cert~~']


async def test_summary_of_the_server(store):
    store.add_note(1, 10, 'a note')
    store.add_todo(1, 10, 'a todo')
    [summary] = await run(Todos(), 'summary', '')
    assert summary.startswith('**1** notes, **1** pending TODOs, **0** done')
    assert '`#1` a note' in summary and '`#1` a todo' in summary


def test_persona_file_is_the_system_prompt(tmp_path, monkeypatch):
    persona = tmp_path / 'PERSONA.md'
    persona.write_text('<!-- notes for editors -->\nEres un pana guaro.\n', encoding='utf-8')
    monkeypatch.setenv('PERSONA_FILE', str(persona))
    assert assistant.system_prompt() == 'Eres un pana guaro.'


def test_without_persona_the_assistant_is_neutral(tmp_path, monkeypatch):
    monkeypatch.setenv('PERSONA_FILE', str(tmp_path / 'missing.md'))
    assert assistant.system_prompt() == assistant.SYSTEM_PROMPT


@pytest.mark.parametrize(
    'text, expected',
    [
        ('¿qué VPN usamos?', 'es'),
        ('mi laptop esta lenta, que reviso', 'es'),
        ('How do I list open ports on Linux?', 'en'),
        ('what is a reverse proxy', 'en'),
        ('hola', 'es'),
        ('ok', 'es'),  # no clues: the Server's main language
        ('thanks!', 'en'),
    ],
)
def test_language_guess(text, expected):
    assert assistant.language(text) == expected


def test_summary_also_answers_to_resumen():
    command = next(c for c in Todos().get_commands() if c.name == 'summary')
    assert command.aliases == ['resumen']


@pytest.mark.parametrize(
    'answer, expected',
    [
        ('Docker aísla apps en contenedores.\n¡Vaina, todo fino!', 'Docker aísla apps en contenedores.'),
        ('Docker aísla apps.\n\n¡Naguará, es full práctico!', 'Docker aísla apps.'),
        ('¡Naguará, claro que sí!', '¡Naguará, claro que sí!'),  # a one-line answer stays
        ('Primero esto.\n¡Ojo: haz un backup antes de borrar la partición de arranque!', None),  # long, informative
    ],
)
def test_filler_sign_offs_are_dropped(answer, expected):
    assert assistant.drop_filler_ending(answer) == (expected or answer)


async def test_only_the_last_five_messages_of_the_last_hour_count(prompts):
    old = [fake_message(f'!!ask vieja {i}', minutes_ago=61, author=ANA, id=i) for i in range(1, 3)]
    recent = [fake_message(f'!!ask reciente {i}', minutes_ago=10 - i, author=ANA, id=10 + i) for i in range(1, 7)]
    question = fake_message('!!ask ¿y ahora?', author=ANA, id=99)

    await run(
        Assistant(), 'ask', '', ctx=context(question, typing_channel([*old, *recent, question])), question='¿y ahora?'
    )

    _, user = prompts[0]
    history = user['content'].split('\n\n')[0].splitlines()[1:]
    assert history == [f'ana: reciente {i}' for i in range(2, 7)]


async def ask_with_history(history, question, prompts, **kwargs):
    message = fake_message(f'!!ask {question}', author=ANA, id=1000, **kwargs)
    await run(Assistant(), 'ask', '', ctx=context(message, typing_channel([*history, message])), question=question)
    _, user = prompts[0]
    return user['content'].split('\n\n')[0].splitlines()[1:] if user['content'].startswith('Recent') else []


LUIS_ASKS = [
    fake_message('<@42> ¿cómo configuro el firewall en Ubuntu?', author=LUIS, id=1),
    fake_message('Con `ufw`.', author=BOT, id=2),
]


async def test_other_peoples_messages_stay_out_unless_pointed_at(prompts):
    assert await ask_with_history(LUIS_ASKS, 'hola', prompts) == []


async def test_naming_someone_brings_in_their_messages_and_the_bots_replies(prompts):
    history = await ask_with_history(LUIS_ASKS, '¿qué opinas de lo que dijo luis?', prompts)
    assert history == ['luis: ¿cómo configuro el firewall en Ubuntu?', 'Tinaja Guaro Bot (you): Con `ufw`.']


async def test_mentioning_someone_brings_in_their_messages(prompts):
    history = await ask_with_history(LUIS_ASKS, '¿tiene razón?', prompts, mentions=[LUIS])
    assert history[0] == 'luis: ¿cómo configuro el firewall en Ubuntu?'


async def test_replying_to_a_message_brings_it_in_even_when_older(prompts):
    old = fake_message('mi teoría: es el DNS', minutes_ago=200, author=LUIS, id=1)
    history = await ask_with_history([], '¿esto tiene sentido?', prompts, reply_to=old)
    assert history == ['luis: mi teoría: es el DNS']


async def test_the_askers_own_thread_survives_other_peoples_chatter(prompts):
    own = [
        fake_message('!!ask ¿cómo instalo Docker en Ubuntu?', author=ANA, id=1),
        fake_message('Con `apt install docker.io`.', author=BOT, id=2),
    ]
    chatter = [fake_message(f'charla {i}', author=LUIS, id=10 + i) for i in range(8)]
    history = await ask_with_history([*own, *chatter], '¿y en Windows?', prompts)
    assert history == ['ana: ¿cómo instalo Docker en Ubuntu?', 'Tinaja Guaro Bot (you): Con `apt install docker.io`.']
