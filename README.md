# Tinaja Guaro Bot
A Discord bot built on [tinaja-bot-base](https://github.com/xionjames/tinaja-bot-base), written in Python.

## Project layout
- `bot.toml`: name, command prefix, intents, plain-text replies and @mention behaviour
- `PERSONA.md`: the Assistant's personality and vocabulary (its system prompt)
- `CONTEXT.md`: the glossary of the server's language, answered by `!!glossary [term]`
- `cogs/`: Python commands; every `commands.Cog` class in this folder is loaded
- `.env`: the bot token (never commit it; copy `env.sample`)

## Run manually
- Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the dependencies
```bash
uv sync
```
- Create a new `.env` file using `env.sample` as a template to set the required credentials.
- Run the bot
```bash
uv run tinaja-bot run
```

## Local LLM, Notes and TODOs
The Assistant (`cogs/assistant.py`) talks to any OpenAI-compatible server, so the model is separate from the bot.
[tinaja-guaro-bot-llm](../tinaja-guaro-bot-llm) packages a small Spanish/English model for it (CPU, CUDA, or MLX on a
Mac, which is the fastest there since Docker on macOS has no GPU access):
```bash
cd ../tinaja-guaro-bot-llm && ./mlx/serve.sh   # or `docker compose up -d cpu`; see that project's README
curl http://localhost:8080/v1/models            # check it answers
```
The defaults in `env.sample` already point at it (`LLM_BASE_URL=http://localhost:8080/v1`, `LLM_MODEL=default_model`).

| Command | What it does |
| --- | --- |
| `!!ask <question>` / `@Bot <question>` | Ask the Assistant; it sees matching Notes, pending TODOs and the recent channel |
| `!!note add <text>` / `search <words>` / `list` / `delete <id>` | The Server's Notes (only the author can delete) |
| `!!todo add <text>` / `list` / `done-list` / `done <id>` / `undo <id>` / `delete <id>` | The Server's TODOs (`!!todo` alone lists what is pending) |
| `!!summary` (or `!!resumen`) | Counts and latest Notes and pending TODOs |

Notes and TODOs live in the SQLite file `DATA_DB` (default `bot.db`).

The Assistant's personality, a 30-something geek guaro from Barquisimeto, is `PERSONA.md`: the whole file is the
system prompt, with its rules, Venezuelan vocabulary, Spanglish and example answers. Edit it freely; it's read on every
question, so changes apply without a restart.

## Docker
```bash
uv run tinaja-bot check                # is the bot ready to build? (build runs this first)
uv run tinaja-bot build                # docker build, tagged ghcr.io/<owner>/<repo>:latest and :<git sha>
docker run -it --env-file .env -e LLM_BASE_URL=http://host.docker.internal:8080/v1 \
  -e DATA_DB=/code/data/bot.db -v ./data:/code/data ghcr.io/<owner>/<repo>:latest
uv run tinaja-bot publish              # docker push; first: echo $CR_PAT | docker login ghcr.io -u <user> --password-stdin
```
Pushing to `main` on GitHub runs `.github/workflows/build-and-push.yaml`, which tests the bot, runs
`tinaja-bot check` and only then publishes a multi-arch image to `ghcr.io/<owner>/<repo>`.

## Run tests
```bash
uv run pytest
```

## Lint and format
```bash
uv run ruff check .       # lint (add --fix to apply safe fixes)
uv run ruff format .      # format
```
