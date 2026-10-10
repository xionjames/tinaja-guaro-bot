# Tinaja Guaro Bot

A Discord bot for our community server: describe here what it does and for whom.

## Language

**Server**:
A Discord guild the bot has joined.
_Avoid_: Guild (except when talking about the Discord library)

**Member**:
A person's Discord account that belongs to a Server. Bot accounts are not Members.
_Avoid_: User

**Command**:
A `!!`-prefixed message a Member sends to ask the bot for something, e.g. `!!hello`.
_Avoid_: Slash command (the bot doesn't use them)

**Assistant**:
The bot's local language model, which answers a Member's question in Spanish or English. Ask with `!!ask <question>` or by @mentioning the bot.
_Avoid_: AI, ChatGPT

**Note**:
A piece of knowledge a Member saves for the whole Server with `!!note add`. The Assistant reads matching Notes before answering.
_Avoid_: Memo

**TODO**:
A task saved for the whole Server with `!!todo add`. It is pending until someone marks it done with `!!todo done <id>`.
_Avoid_: Task, ticket
