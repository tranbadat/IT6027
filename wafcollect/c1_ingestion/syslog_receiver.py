"""Nhận access log qua syslog (khi web server và collector ở máy khác nhau).

Cấu hình phía Nginx:  access_log syslog:server=collector:5514,tag=shop_example,severity=info waf;
Hỗ trợ:
  - UDP: mỗi datagram một message (có thể chứa nhiều dòng, tách theo '\\n')
  - TCP: framing theo dòng ('\\n'). Chưa hỗ trợ octet-counting của RFC 6587.
  - header RFC 3164 (`<134>Sep 21 10:15:30 host nginx: ...`) và RFC 5424 (`<134>1 2026-09-21T10:15:30Z host nginx - - - ...`)
"""
from __future__ import annotations

import logging
import queue
import re
import socket
import socketserver
import threading
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("c1.syslog")

_PRI = re.compile(r"^<(\d{1,3})>")
_RFC5424 = re.compile(r"^(\d) (\S+) (\S+) (\S+) (\S+) (\S+) (?:-|\[(?:[^\]\\]|\\.)*\](?:\[(?:[^\]\\]|\\.)*\])*) ?(.*)$", re.DOTALL)
_RFC3164 = re.compile(r"^(?:[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}) (\S+) (?:([^\s:\[]+)(?:\[\d+\])?: ?)?(.*)$", re.DOTALL)


@dataclass
class SyslogMessage:
    host: Optional[str]
    tag: Optional[str]
    message: str


def parse_syslog(text: str) -> SyslogMessage:
    """Tách header syslog; nếu không nhận ra định dạng thì coi cả chuỗi là message."""
    text = text.rstrip("\r\n")
    pri = _PRI.match(text)
    body = text[pri.end():] if pri else text
    m5 = _RFC5424.match(body)
    if m5:
        _ver, _ts, host, app, _procid, _msgid, msg = m5.groups()
        return SyslogMessage(None if host == "-" else host, None if app == "-" else app, msg)
    m3 = _RFC3164.match(body)
    if m3:
        host, tag, msg = m3.groups()
        return SyslogMessage(host, tag, msg)
    return SyslogMessage(None, None, body)


def split_host_port(listen: str) -> tuple[str, int]:
    host, _, port = listen.rpartition(":")
    return host or "0.0.0.0", int(port)


class UdpSyslogReceiver(threading.Thread):
    def __init__(self, listen: str, out: "queue.Queue[tuple[SyslogMessage, str]]", stop: threading.Event) -> None:
        super().__init__(name=f"syslog-udp-{listen}", daemon=True)
        self.host, self.port = split_host_port(listen)
        self.out, self.stop = out, stop
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)
        self.bound_port = self.sock.getsockname()[1]
        self.dropped = 0

    def run(self) -> None:
        log.info("syslog UDP lắng nghe %s:%s", self.host, self.bound_port)
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            peer = addr[0]
            for line in data.decode("utf-8", "replace").split("\n"):
                if not line.strip():
                    continue
                try:
                    self.out.put_nowait((parse_syslog(line), peer))
                except queue.Full:
                    self.dropped += 1  # UDP không có backpressure; đếm để cảnh báo
        self.sock.close()


class TcpSyslogReceiver(threading.Thread):
    def __init__(self, listen: str, out: "queue.Queue[tuple[SyslogMessage, str]]", stop: threading.Event) -> None:
        super().__init__(name=f"syslog-tcp-{listen}", daemon=True)
        host, port = split_host_port(listen)
        stop_ev = stop
        q = out

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(1.0)
                peer = self.client_address[0]
                while not stop_ev.is_set():
                    try:
                        line = self.rfile.readline(70000)
                    except (socket.timeout, TimeoutError):
                        continue
                    except OSError:
                        return
                    if not line:
                        return
                    text = line.decode("utf-8", "replace")
                    if not text.strip():
                        continue
                    msg = parse_syslog(text)
                    while not stop_ev.is_set():
                        try:
                            q.put((msg, peer), timeout=0.5)  # blocking -> TCP tự có backpressure
                            break
                        except queue.Full:
                            continue

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        socketserver.ThreadingTCPServer.daemon_threads = True
        self.server = socketserver.ThreadingTCPServer((host, port), Handler)
        self.bound_port = self.server.server_address[1]
        self.stop = stop

    def run(self) -> None:
        log.info("syslog TCP lắng nghe cổng %s", self.bound_port)
        t = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True)
        t.start()
        self.stop.wait()
        self.server.shutdown()
        self.server.server_close()
