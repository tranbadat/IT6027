"""Unit test cho LogTailer (không cần RabbitMQ). Chạy: python test_tailer.py"""
import os
import tempfile

import tailer as tailer_mod


def _setup(dirpath: str) -> None:
    os.makedirs(os.path.join(dirpath, "nginx"))
    os.makedirs(os.path.join(dirpath, "apache"))


def _append(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def test_only_access_logs_tracked():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        _append(os.path.join(base, "nginx", "shop.local.access.log"), "line1\n")
        _append(os.path.join(base, "nginx", "shop.local.access.log.1"), "old\n")
        _append(os.path.join(base, "nginx", "error.log"), "err\n")
        tailer = tailer_mod.LogTailer(base, read_existing=True)
        records = tailer.poll()
        assert [r.domain for r in records] == ["shop.local"]
        assert records[0].log_source == "nginx"
        assert records[0].line == "line1"


def test_partial_line_buffered_until_newline():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        path = os.path.join(base, "nginx", "a.local.access.log")
        _append(path, "hello\npartial")
        tailer = tailer_mod.LogTailer(base, read_existing=True)
        assert [r.line for r in tailer.poll()] == ["hello"]
        _append(path, " world\n")
        assert [r.line for r in tailer.poll()] == ["partial world"]


def test_start_from_end_skips_existing():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        path = os.path.join(base, "nginx", "a.local.access.log")
        _append(path, "old1\nold2\n")
        tailer = tailer_mod.LogTailer(base, read_existing=False)
        assert tailer.poll() == []
        _append(path, "new1\n")
        assert [r.line for r in tailer.poll()] == ["new1"]


def test_truncation_reopens_from_start():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        path = os.path.join(base, "nginx", "a.local.access.log")
        _append(path, "l1\nl2\n")
        tailer = tailer_mod.LogTailer(base, read_existing=True)
        tailer.poll()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("hi\n")
        assert [r.line for r in tailer.poll()] == ["hi"]


def test_new_file_appearing_read_from_start():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        tailer = tailer_mod.LogTailer(base, read_existing=False)
        tailer.poll()  # chưa có file nào
        path = os.path.join(base, "apache", "blog.local.access.log")
        _append(path, "a1\na2\n")
        records = tailer.poll()
        assert [r.line for r in records] == ["a1", "a2"]
        assert records[0].log_source == "apache"


def test_disappearing_file_no_crash():
    with tempfile.TemporaryDirectory() as base:
        _setup(base)
        path = os.path.join(base, "nginx", "x.local.access.log")
        _append(path, "l\n")
        tailer = tailer_mod.LogTailer(base, read_existing=True)
        tailer.poll()
        os.remove(path)
        assert tailer.poll() == []


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("PASS", _name)
