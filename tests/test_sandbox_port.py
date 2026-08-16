import socket

from sssf.sandbox import allocate_port


def test_allocates_at_or_above_base():
    p = allocate_port(31000)
    assert p >= 31000


def test_skips_busy_ports():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 31050))
    blocker.listen(1)
    try:
        p = allocate_port(31050)
        assert p != 31050
        assert p > 31050
    finally:
        blocker.close()


def test_skips_used_set():
    p = allocate_port(31100, used={31100, 31101})
    assert p >= 31102
