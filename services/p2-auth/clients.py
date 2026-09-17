"""P2 — Xác thực client_id/client_secret (chế độ dev hoặc allowlist AUTH_CLIENTS)."""
import hmac

from common import config

# Mọi client hợp lệ nhận role này (dự án nội bộ, chưa cần phân quyền chi tiết).
DEFAULT_ROLES = ("admin",)


class ClientError(Exception):
    """client_id/secret không hợp lệ."""


def load_clients():
    """Đọc AUTH_CLIENTS='id:secret,...' -> dict id->secret. Bỏ qua entry lỗi."""
    clients = {}
    for entry in config.env_list("AUTH_CLIENTS", ""):
        client_id, sep, secret = entry.partition(":")
        client_id = client_id.strip()
        secret = secret.strip()
        if sep and client_id and secret:
            clients[client_id] = secret
    return clients


def authenticate(client_id, client_secret, allow_any, clients):
    """Trả client_id nếu hợp lệ, ngược lại ném ClientError.

    allow_any=True (dev): chấp nhận mọi id/secret không rỗng.
    allow_any=False: khớp theo clients (so sánh secret bằng compare_digest chống timing).
    """
    if not client_id or not client_secret:
        raise ClientError("client_id and client_secret are required")
    if allow_any:
        return client_id
    expected = clients.get(client_id)
    if expected is None or not hmac.compare_digest(expected, client_secret):
        raise ClientError("unknown client_id or wrong client_secret")
    return client_id


def roles_for(client_id):
    """Danh sách role cho client. Hiện dùng mặc định; trả list mới mỗi lần gọi."""
    return list(DEFAULT_ROLES)
