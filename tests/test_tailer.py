import os

from wafcollect.c1_ingestion.tailer import FileTailer, Line, MultilineJoiner, StateStore


def append(path, text):
    with open(path, "ab") as f:
        f.write(text.encode())


def texts(lines):
    return [l.text for l in lines]


def test_start_at_end_skips_existing_and_reads_new(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("old1\nold2\n")
    t = FileTailer(str(p), start_at="end")
    assert t.poll() == []
    append(p, "new1\nnew2\n")
    assert texts(t.poll()) == ["new1", "new2"]
    assert t.poll() == []


def test_start_at_beginning(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("l1\nl2\n")
    assert texts(FileTailer(str(p), start_at="beginning").poll()) == ["l1", "l2"]


def test_file_created_after_start(tmp_path):
    p = tmp_path / "later.log"
    t = FileTailer(str(p), start_at="end")
    assert t.poll() == []
    p.write_text("first\n")
    # file chưa tồn tại lúc khởi động -> mọi thứ ghi vào sau đó đều là log mới, kể cả start_at="end"
    assert texts(t.poll()) == ["first"]
    append(p, "second\n")
    assert texts(t.poll()) == ["second"]


def test_open_pins_end_position_at_startup_not_at_first_poll(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("old\n")
    t = FileTailer(str(p), start_at="end")
    t.open()                        # service khởi động tại đây
    append(p, "written-before-first-poll\n")
    assert texts(t.poll()) == ["written-before-first-poll"]


def test_partial_line_is_buffered_until_newline(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("")
    t = FileTailer(str(p), start_at="beginning")
    append(p, "half a li")
    assert t.poll() == []
    append(p, "ne\nnext")
    assert texts(t.poll()) == ["half a line"]
    append(p, "\n")
    assert texts(t.poll()) == ["next"]


def test_crlf_is_stripped(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"x\r\ny\r\n")
    assert texts(FileTailer(str(p), start_at="beginning").poll()) == ["x", "y"]


def test_rotation_rename_and_create_loses_nothing(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("")
    t = FileTailer(str(p), start_at="beginning")
    append(p, "a1\n")
    assert texts(t.poll()) == ["a1"]
    # dòng ghi vào file cũ NGAY TRƯỚC khi rotate, chưa kịp poll
    append(p, "a2\n")
    os.rename(p, tmp_path / "access.log.1")
    assert texts(t.poll()) == ["a2"]      # đọc nốt file cũ
    p.write_text("b1\nb2\n")              # file mới
    assert texts(t.poll()) == ["b1", "b2"]
    append(p, "b3\n")
    assert texts(t.poll()) == ["b3"]


def test_rotation_with_gap_where_file_is_missing(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("")
    t = FileTailer(str(p), start_at="beginning")
    os.rename(p, tmp_path / "access.log.1")
    assert t.poll() == []                 # chưa có file mới, không được crash
    p.write_text("n1\n")
    out = []
    for _ in range(3):
        out += texts(t.poll())
    assert out == ["n1"]


def test_copytruncate(tmp_path):
    p = tmp_path / "access.log"
    p.write_text("")
    t = FileTailer(str(p), start_at="beginning")
    append(p, "one\ntwo\n")
    assert texts(t.poll()) == ["one", "two"]
    with open(p, "r+b") as f:
        f.truncate(0)
    append(p, "three\n")
    assert texts(t.poll()) == ["three"]


def test_overlong_line_is_truncated_and_rest_discarded(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("")
    t = FileTailer(str(p), start_at="beginning", max_line_bytes=10)
    append(p, "X" * 25)                    # chưa có newline, đã vượt giới hạn
    got = t.poll()
    assert len(got) == 1 and got[0].truncated and got[0].text == "X" * 10
    append(p, "YYY\nok\n")                 # phần đuôi của dòng cũ bị bỏ, dòng sau vẫn đọc bình thường
    assert texts(t.poll()) == ["ok"]


def test_overlong_line_with_newline_in_same_chunk(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("Z" * 30 + "\nfine\n")
    got = FileTailer(str(p), start_at="beginning", max_line_bytes=8).poll()
    assert [(l.text, l.truncated) for l in got] == [("Z" * 8, True), ("fine", False)]


def test_invalid_utf8_does_not_crash(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"ok \xff\xfe bytes\n")
    (line,) = FileTailer(str(p), start_at="beginning").poll()
    assert "ok" in line.text


def test_offset_after_and_resume_no_loss_no_dup(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("l1\nl2\nl3\n")
    t1 = FileTailer(str(p), start_at="beginning")
    lines = t1.poll()
    assert [l.offset_after for l in lines] == [3, 6, 9]
    # giả lập: mới xử lý xong l1 rồi crash
    resume = {"inode": lines[0].inode, "offset": lines[0].offset_after}
    append(p, "l4\n")
    t2 = FileTailer(str(p), start_at="end", resume=resume)
    assert texts(t2.poll()) == ["l2", "l3", "l4"]


def test_resume_with_changed_inode_reads_new_file_from_start(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("new file line\n")
    t = FileTailer(str(p), start_at="end", resume={"inode": 999999, "offset": 5})
    assert texts(t.poll()) == ["new file line"]


def test_state_store_roundtrip_and_corrupt_file(tmp_path):
    sp = tmp_path / "state" / "c1.json"
    s = StateStore(str(sp))
    s.update("src", 42, 1000)
    s.flush(force=True)
    assert StateStore(str(sp)).get("src") == {"inode": 42, "offset": 1000}
    sp.write_text("{not json")
    assert StateStore(str(sp)).get("src") is None


# ---------- multiline
def L(t, off=0):
    return Line(t, off, 1)


def test_multiline_joiner_groups_continuations():
    clock = [0.0]
    j = MultilineJoiner(flush_after=1.0, clock=lambda: clock[0])
    out = []
    out += j.feed(L('1.1.1.1 - - [10/Oct/2000:13:55:36 +0000] "GET /a" 200 1', 10))
    out += j.feed(L("  continued", 20))
    out += j.feed(L('2.2.2.2 - - [10/Oct/2000:13:55:37 +0000] "GET /b" 200 1', 30))
    assert len(out) == 1 and out[0].text.endswith("\n  continued")
    assert out[0].line_count == 2 and out[0].offset_after == 20
    assert j.flush_if_stale() == []
    clock[0] = 5.0
    (last,) = j.flush_if_stale()
    assert last.text.startswith("2.2.2.2")


def test_multiline_joiner_recognises_vhost_prefix_as_start():
    j = MultilineJoiner()
    assert j.start.match('shop.example.com:443 1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1"')
    assert j.start.match('1.2.3.4 - - [10/Oct/2000:13:55:36 +0000] "GET / HTTP/1.1"')
