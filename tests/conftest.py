import pytest


@pytest.fixture(autouse=True)
def _no_kernel_socket_table(monkeypatch):
    """Synthetic ASGI sockets keep secure defaults until a test opts in."""
    import treasury.service as _m

    monkeypatch.setattr(_m, "_PROC_NET_TCP", ())
    monkeypatch.delenv("TREASURY_LOCAL_UNVERIFIED", raising=False)
    monkeypatch.delenv("TREASURY_LOCAL_RELAY", raising=False)


@pytest.fixture
def local_http_opt_in(monkeypatch):
    """Explicit operator choice for behavior tests using synthetic local TCP."""
    monkeypatch.setenv("TREASURY_LOCAL_RELAY", "1")
    monkeypatch.setenv("TREASURY_LOCAL_UNVERIFIED", "1")
