# AGENTS.md

Guide for coding agents working on this repository. For human-oriented detail (running in Docker, setting up the
local LLM server, the full Command table) see [README.md](README.md).

## Project
Tinaja Guaro Bot is a Discord bot for our community Server, built on
[tinaja-bot-base](https://github.com/xionjames/tinaja-bot-base). Besides plain-text replies it has an Assistant that
answers questions through a local, OpenAI-compatible LLM server, plus Notes and TODOs that each Server's Members save
in a SQLite file.

## Layout
- `bot.toml`: bot name, Command prefix (`!!`), intents, plain-text replies and @mention behaviour
- `PERSONA.md`: the Assistant's system prompt (personality, rules, vocabulary)
- `CONTEXT.md`: the glossary of the Server's language, answered by `!!glossary [term]`
- `cogs/`: Python Commands; every `commands.Cog` class in this folder is loaded
  - `cogs/assistant.py`: the Assistant, Notes, TODOs and their SQLite `Store`
  - `cogs/hello.py`: the `!!hello` example Command
- `tests/`: pytest tests, one file per cog (`test_assistant.py`, `test_hello.py`, `test_store.py`)
- `env.sample`: template for `.env` (Discord token, LLM server, `DATA_DB`, `PERSONA_FILE`)
- `Dockerfile`: the runtime image
- `.github/workflows/build-and-push.yaml`: CI; tests and checks the bot, then builds and publishes the image

## Commands
Run them from the repository root. CI runs the last four on every push and pull request, so all of them must pass
before a change is done.
```bash
uv sync                          # install dependencies (CI uses `uv sync --locked`)
uv run tinaja-bot run            # run the bot; needs a .env copied from env.sample
uv run pytest                    # tests
uv run ruff check .              # lint (add --fix to apply safe fixes)
uv run ruff format --check .     # formatting check (`uv run ruff format .` to apply)
uv run tinaja-bot check          # is the bot ready to build?
```

## Code conventions
- Python >= 3.14.
- Ruff: line length 120, single quotes, lint rules `I` (import sorting), `UP` (pyupgrade) and `B` (bugbear) on top
  of the defaults. Configuration is in `pyproject.toml`.
- pytest runs with `asyncio_mode = "auto"`: async tests need no decorator.
- Tests use the fakes from `tinaja_base.testing` (`FakeContext`, `FakeChannel`, `fake_member`, `fake_message`,
  `run_command`) instead of a real Discord connection.

## Project rules
- `tinaja-bot run` can't import a helper from another cog file, so `cogs/assistant.py` stays one module.
  Don't split it.
- Use the `CONTEXT.md` glossary terms in code, docs and replies: Server (not guild), Member (not user), Command
  (not slash command), Assistant (not AI), Note (not memo), TODO (not task).
- `PERSONA.md` is the Assistant's system prompt and is read on every question. Edit it deliberately and only when the
  change is about the Assistant's voice or rules.
- Never commit `.env` or `bot.db`; both are in `.gitignore`.
- The `Dockerfile` copies only `bot.toml`, `CONTEXT.md`, `PERSONA.md` and `cogs/`. A new file the bot needs at
  runtime must be added to its `COPY` lines.

## Working style
- Ask before assuming. When a request is ambiguous, say what is unclear instead of guessing.
- Write the minimum code that solves the problem; no abstractions for single-use code.
- Only touch files related to the task. Flag anything adjacent that looks wrong instead of fixing it silently.
- Say when you're not sure. Don't invent commands, APIs or libraries.
