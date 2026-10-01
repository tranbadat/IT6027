"""
Mini "vulnerable shop" — website CỐ Ý có lỗ hổng, dùng làm target cho
dev/e2e_vulnshop.sh (chỉ chạy trên localhost của máy bạn).
KHÔNG chạy công khai trên Internet, KHÔNG deploy cho ai khác truy cập.

Lỗ hổng cố ý:
  - GET /product?id=   -> SQL Injection thật (nối chuỗi trực tiếp vào SQLite)
  - GET /search?q=      -> XSS phản hồi (echo trực tiếp vào HTML, không escape)
  - GET /file?name=     -> Path Traversal thật (đọc file theo tên người dùng gửi)
  - POST /login         -> đặt cookie session (PHPSESSID) sau khi "đăng nhập"

Cấu hình qua biến môi trường (đều có giá trị mặc định để chạy độc lập):
  APP_PORT       cổng lắng nghe (mặc định 3000)
  APP_FILES_DIR  thư mục "công khai" cho /file?name=   (mặc định ./files cạnh app.py)
  APP_DB_PATH    đường dẫn SQLite                        (mặc định /tmp/vulnapp.db)
  APP_SECRET     file "nhạy cảm" nằm NGOÀI APP_FILES_DIR để demo path traversal
                 (mặc định <thư mục cha của APP_FILES_DIR>/secret.txt)
"""
import os
import sqlite3
import uuid
from pathlib import Path
from flask import Flask, request, make_response

app = Flask(__name__)

HERE = Path(__file__).resolve().parent
DB = os.environ.get("APP_DB_PATH", "/tmp/vulnapp.db")
FILES_DIR = Path(os.environ.get("APP_FILES_DIR", str(HERE / "files"))).resolve()
SECRET_PATH = Path(os.environ.get("APP_SECRET", str(FILES_DIR.parent / "secret.txt")))


def init_db():
    con = sqlite3.connect(DB)
    con.execute("DROP TABLE IF EXISTS products")
    con.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price INTEGER)")
    con.executemany(
        "INSERT INTO products VALUES (?,?,?)",
        [(1, "Ao thun", 150000), (2, "Quan jean", 350000), (3, "Giay", 500000)],
    )
    con.commit()
    con.close()
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    readme = FILES_DIR / "readme.txt"
    if not readme.exists():
        readme.write_text("day la file cong khai binh thuong\n")
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text("SECRET=day la file nhay cam khong nen doc duoc qua web\n")


@app.route("/")
def home():
    return "Vulnerable Shop - demo target, chi chay local\n"


@app.route("/product")
def product():
    pid = request.args.get("id", "1")
    con = sqlite3.connect(DB)
    # CỐ Ý không dùng tham số hoá -> SQL Injection thật
    query = f"SELECT id, name, price FROM products WHERE id = {pid}"
    try:
        rows = con.execute(query).fetchall()
        body = "\n".join(str(r) for r in rows) or "(khong co ket qua)"
    except sqlite3.Error as e:
        body = f"SQL error: {e}"
    con.close()
    return body


@app.route("/search")
def search():
    q = request.args.get("q", "")
    # CỐ Ý echo thẳng ra HTML -> XSS phản hồi thật
    return f"<html><body>Ket qua tim kiem cho: {q}</body></html>"


@app.route("/file")
def file_read():
    name = request.args.get("name", "readme.txt")
    # CỐ Ý không chặn "../" -> Path Traversal thật
    path = str(FILES_DIR) + "/" + name
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError as e:
        return f"error: {e}", 404


@app.route("/login", methods=["POST"])
def login():
    resp = make_response("logged in\n")
    resp.set_cookie("PHPSESSID", uuid.uuid4().hex)
    return resp


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("APP_PORT", "3000")))
