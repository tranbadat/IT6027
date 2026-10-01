import queue
import socket
import threading
import time

from wafcollect.c1_ingestion.syslog_receiver import (
    TcpSyslogReceiver, UdpSyslogReceiver, parse_syslog,
)

LINE = '1.2.3.4 - - [21/Sep/2026:10:15:30 +0700] "GET / HTTP/1.1" 200 5 "-" "-"'


def test_parse_rfc3164():
    m = parse_syslog(f"<134>Sep 21 10:15:30 web01 nginx_shop: {LINE}")
    assert (m.host, m.tag, m.message) == ("web01", "nginx_shop", LINE)


def test_parse_rfc3164_with_pid_and_single_digit_day():
    m = parse_syslog(f"<134>Sep  1 10:15:30 web01 nginx[123]: {LINE}")
    assert (m.host, m.tag, m.message) == ("web01", "nginx", LINE)


def test_parse_rfc5424():
    m = parse_syslog(f"<134>1 2026-09-21T10:15:30+07:00 web01 shop_example - - - {LINE}")
    assert (m.host, m.tag, m.message) == ("web01", "shop_example", LINE)


def test_parse_rfc5424_with_structured_data():
    m = parse_syslog(f'<134>1 2026-09-21T10:15:30Z web01 app 1 ID47 [exampleSDID@32473 iut="3"] {LINE}')
    assert m.tag == "app" and m.message == LINE


def test_plain_message_without_header():
    m = parse_syslog(LINE)
    assert m.host is None and m.tag is None and m.message == LINE


def test_udp_receiver_end_to_end():
    q, stop = queue.Queue(), threading.Event()
    rx = UdpSyslogReceiver("127.0.0.1:0", q, stop)
    rx.start()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(f"<134>Sep 21 10:15:30 web01 shop: {LINE}\n{LINE}".encode(), ("127.0.0.1", rx.bound_port))
    msgs = [q.get(timeout=2), q.get(timeout=2)]   # datagram 2 dòng -> 2 message
    stop.set()
    assert msgs[0][0].tag == "shop" and msgs[0][0].message == LINE
    assert msgs[1][0].message == LINE and msgs[0][1] == "127.0.0.1"


def test_udp_full_queue_counts_drops_instead_of_blocking():
    q, stop = queue.Queue(maxsize=1), threading.Event()
    rx = UdpSyslogReceiver("127.0.0.1:0", q, stop)
    rx.start()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(5):
        s.sendto(LINE.encode(), ("127.0.0.1", rx.bound_port))
    time.sleep(0.5)
    stop.set()
    assert q.qsize() == 1 and rx.dropped >= 1


def test_tcp_receiver_line_framing():
    q, stop = queue.Queue(), threading.Event()
    rx = TcpSyslogReceiver("127.0.0.1:0", q, stop)
    rx.start()
    time.sleep(0.2)
    c = socket.create_connection(("127.0.0.1", rx.bound_port))
    c.sendall(f"{LINE}\n<134>Sep 21 10:15:30 web01 t: {LINE}\n".encode())
    a, b = q.get(timeout=2), q.get(timeout=2)
    c.close()
    stop.set()
    assert a[0].message == LINE and b[0].tag == "t"
