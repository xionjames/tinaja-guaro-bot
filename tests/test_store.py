import pytest

from cogs.assistant import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'bot.db')


def test_notes_are_searchable_and_kept_per_server(store):
    store.add_note(1, 10, 'Our VPN is WireGuard on port 51820')
    store.add_note(1, 10, 'La impresora está en la oficina dos')
    store.add_note(2, 20, 'WireGuard is banned here')

    found = store.search_notes(1, '¿Qué VPN usamos?')
    assert [n['text'] for n in found] == ['Our VPN is WireGuard on port 51820']
    assert store.search_notes(1, 'impresora')[0]['text'] == 'La impresora está en la oficina dos'
    assert store.count_notes(2) == 1


def test_search_ignores_fts_syntax(store):
    store.add_note(1, 10, 'nginx reverse proxy')
    assert store.search_notes(1, 'nginx AND "-* (') != []
    assert store.search_notes(1, '?!') == []


def test_only_the_author_deletes_a_note(store):
    note_id = store.add_note(1, 10, 'secret')
    assert not store.delete_note(1, 99, note_id)
    assert store.delete_note(1, 10, note_id)
    assert store.recent_notes(1) == []


def test_todos_move_between_pending_and_done(store):
    first = store.add_todo(1, 10, 'renew the TLS cert')
    store.add_todo(1, 10, 'update the wiki')
    store.add_todo(2, 20, 'other server')

    assert store.set_done(1, first)
    assert [t['text'] for t in store.todos(1)] == ['update the wiki']
    assert [t['text'] for t in store.todos(1, done=True)] == ['renew the TLS cert']
    assert not store.set_done(2, first)  # another Server's TODO

    assert store.set_done(1, first, done=False)
    assert store.count_todos(1) == 2
    assert store.count_todos(1, done=True) == 0


def test_only_the_author_deletes_a_todo(store):
    todo_id = store.add_todo(1, 10, 'x')
    assert not store.delete_todo(1, 99, todo_id)
    assert store.delete_todo(1, 10, todo_id)
