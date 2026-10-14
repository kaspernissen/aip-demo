import io
import json
import urllib.error

from pds.ledger import payment_status


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body=None, exc=None, seen=None):
    def open_(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        if exc:
            raise exc
        return _Resp(json.dumps(body).encode())
    return open_


def test_reads_the_status_off_the_ledger_record():
    seen = []
    assert payment_status("PAY-000053", opener=_opener({"status": "awaiting_approval"}, seen=seen)) == "awaiting_approval"
    request, timeout = seen[0]
    assert request.full_url.endswith("/payments/PAY-000053")
    assert timeout <= 2.0


def test_an_unreadable_ledger_is_no_status():
    for exc in (urllib.error.URLError("refused"), TimeoutError(), ValueError("bad json")):
        assert payment_status("PAY-000053", opener=_opener(exc=exc)) is None


def test_a_record_without_a_string_status_is_no_status():
    assert payment_status("PAY-000053", opener=_opener({"status": 7})) is None
    assert payment_status("PAY-000053", opener=_opener(["not", "a", "record"])) is None


def test_a_payment_id_that_is_not_one_is_never_fetched():
    seen = []
    for bad in (None, "", "../admin", "PAY-1", "PAY-000053/execute", "PAY-000053?x=1"):
        assert payment_status(bad, opener=_opener({"status": "awaiting_approval"}, seen=seen)) is None
    assert seen == []
