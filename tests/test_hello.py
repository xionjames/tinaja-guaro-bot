from tinaja_base.testing import run_command

from cogs.hello import Hello


async def test_hello_answers_world():
    assert await run_command(Hello(), 'hello') == ['World']
