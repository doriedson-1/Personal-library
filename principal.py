#!/usr/bin/env python3
"""Personal Library Manager — Flask web app backed by PostgreSQL.

Features: catalog with authors, ISBN autofill (Open Library), search,
lend/return, CSV import/export. Mobile-friendly, no JavaScript build step.
"""

import csv
import io
import os
import re

import psycopg2
import requests
from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, url_for)
from jinja2 import DictLoader
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
load_dotenv()   # loads .env into os.environ

DB_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": os.getenv("PGPORT", "5432"),
    "dbname": os.getenv("PGDATABASE", "library"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", ""),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS books (
    id               SERIAL PRIMARY KEY,
    title            VARCHAR(500) NOT NULL,
    author_id        INTEGER NOT NULL REFERENCES authors(id) ON DELETE RESTRICT,
    isbn             VARCHAR(17),
    genre            VARCHAR(100),
    publication_year SMALLINT,
    publisher        VARCHAR(255),
    status           VARCHAR(20) NOT NULL DEFAULT 'available',
    borrower         VARCHAR(255),
    added_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

CSV_FIELDS = ["title", "author", "isbn", "genre",
              "publication_year", "publisher", "status", "borrower"]

# ----------------------------------------------------------------- app setup

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")

# ----------------------------------------------------------------- templates

BASE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minha Biblioteca</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
         background:#f5f2ec; color:#26221c; }
  header { background:#284b3c; color:#fff; padding:.8rem 1rem; }
  header a { color:#fff; text-decoration:none; font-weight:600; margin-right:1.1rem; }
  main { max-width:760px; margin:1.2rem auto; padding:0 1rem; }
  h1 { font-size:1.4rem; }
  .card { background:#fff; border-radius:10px; padding:1rem;
          margin-bottom:1rem; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .card h2 { margin-top:0; font-size:1.05rem; }
  .card h2 small { color:#8a8375; font-weight:400; }
  .book { display:flex; gap:1rem; background:#fff; border-radius:10px;
          padding:1rem; margin-bottom:.8rem; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .cover { width:72px; align-self:flex-start; border-radius:4px; }
  .info { flex:1; min-width:0; }
  .info h2 { margin:0 0 .2rem; font-size:1.1rem; }
  .meta { margin:0 0 .4rem; color:#6b6459; font-size:.92rem; }
  .badge { display:inline-block; font-size:.78rem; padding:.15rem .55rem;
           border-radius:99px; background:#e7efe9; color:#28563f; margin:0 0 .55rem; }
  .badge.lent { background:#fdeec9; color:#7a5410; }
  .actions { display:flex; gap:.5rem; }
  button, .btn { border:0; border-radius:6px; padding:.45rem .9rem; font:inherit;
                 cursor:pointer; text-decoration:none; display:inline-block;
                 background:#e3e8e5; color:inherit; }
  button.primary { background:#284b3c; color:#fff; }
  button.danger { background:#fde2e1; color:#7c1d15; }
  .searchbar { display:flex; gap:.5rem; margin-bottom:1rem; }
  input { padding:.5rem .6rem; border:1px solid #d8d2c6; border-radius:6px;
          font:inherit; background:#fff; }
  .searchbar input { flex:1; }
  .row { display:flex; gap:.5rem; }
  .row input { flex:1; }
  label { display:block; margin-bottom:.7rem; font-weight:600; font-size:.9rem; }
  label input { width:100%; margin-top:.25rem; font-weight:400; }
  .flashes { list-style:none; padding:0; margin:0 0 1rem; }
  .flashes li { padding:.6rem .9rem; border-radius:6px; margin-bottom:.5rem; }
  .flashes .message { background:#e2efe7; color:#1d4a33; }
  .flashes .error { background:#fde2e1; color:#7c1d15; }
  .stats { color:#6b6459; margin-top:0; }
  code { background:#ece7dc; padding:.1rem .35rem; border-radius:4px; }
  .empty { color:#6b6459; }
</style>
</head>
<body>
<header>
  <nav>
    <a href="{{ url_for('index') }}">&#128218; Minha Biblioteca</a>
    <a href="{{ url_for('add') }}">Adicionar livro</a>
    <a href="{{ url_for('data') }}">Importar / Exportar</a>
  </nav>
</header>
<main>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
    <ul class="flashes">
      {% for category, message in messages %}<li class="{{ category }}">{{ message }}</li>{% endfor %}
    </ul>
    {% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</main>
</body>
</html>"""

INDEX = """{% extends "base.html" %}
{% block content %}
<p class="stats">{{ total }} livro(s) na biblioteca &middot; {{ lent }} emprestado(s)</p>
<form method="get" action="{{ url_for('index') }}" class="searchbar">
  <input type="search" name="q" value="{{ term }}"
         placeholder="Buscar t&iacute;tulo, autor, g&ecirc;nero, ISBN&hellip;">
  <button>Buscar</button>
</form>
{% for b in books %}
<article class="book">
  {% if b.isbn %}
  <img class="cover" alt=""
       src="https://covers.openlibrary.org/b/isbn/{{ b.isbn }}-M.jpg"
       onerror="this.remove()">
  {% endif %}
  <div class="info">
    <h2>{{ b.title }}</h2>
    <p class="meta">{{ b.author }}{% if b.year %} &middot; {{ b.year }}{% endif %}{% if b.genre %} &middot; {{ b.genre }}{% endif %}</p>
    {% if b.status == 'lent' %}
      <p class="badge lent">Emprestado para {{ b.borrower }}</p>
    {% else %}
      <p class="badge">Dispon&iacute;vel</p>
    {% endif %}
    <div class="actions">
      {% if b.status == 'lent' %}
      <form method="post" action="{{ url_for('return_book', book_id=b.id) }}">
        <button>Devolver</button>
      </form>
      {% else %}
      <a class="btn" href="{{ url_for('lend', book_id=b.id) }}">Emprestar</a>
      {% endif %}
      <form method="post" action="{{ url_for('delete_book', book_id=b.id) }}"
            onsubmit='return confirm({{ "Excluir este livro?"|tojson }})'>
        <button class="danger">Excluir</button>
      </form>
    </div>
  </div>
</article>
{% else %}
<p class="empty">Nenhum livro encontrado. <a href="{{ url_for('add') }}">Adicione um</a> ou importe um arquivo CSV.</p>
{% endfor %}
{% endblock %}"""

ADD = """{% extends "base.html" %}
{% block content %}
<h1>Adicionar um livro</h1>
<section class="card">
  <h2>1 &middot; Buscar por ISBN <small>(opcional &mdash; preenche os demais campos, requer internet)</small></h2>
  <form method="get" action="{{ url_for('add') }}" class="row">
    <input name="isbn" placeholder="ex.: 978-0-14-032872-1">
    <button>Buscar</button>
  </form>
</section>
<section class="card">
  <h2>2 &middot; Detalhes</h2>
  <form method="post" action="{{ url_for('add') }}">
    <label>T&iacute;tulo <input name="title" value="{{ form.title }}" required></label>
    <label>Autor(es) <input name="author" value="{{ form.author }}" required></label>
    <label>ISBN <input name="isbn" value="{{ form.isbn }}"></label>
    <label>G&ecirc;nero <input name="genre" value="{{ form.genre }}"></label>
    <label>Ano de publica&ccedil;&atilde;o <input name="publication_year" value="{{ form.publication_year }}"></label>
    <label>Editora <input name="publisher" value="{{ form.publisher }}"></label>
    <button class="primary">Salvar livro</button>
  </form>
</section>
{% endblock %}"""

LEND = """{% extends "base.html" %}
{% block content %}
<h1>Emprestar um livro</h1>
<section class="card">
  <p><strong>{{ book.title }}</strong> &mdash; {{ book.author }}</p>
  <form method="post" class="row">
    <input name="borrower" placeholder="Quem vai pegar emprestado?" required>
    <button class="primary">Emprestar</button>
  </form>
</section>
{% endblock %}"""

DATA = """{% extends "base.html" %}
{% block content %}
<h1>Importar / Exportar</h1>
<section class="card">
  <h2>Exportar</h2>
  <p>Baixe todo o seu cat&aacute;logo como um arquivo CSV.</p>
  <a class="btn" href="{{ url_for('export') }}">Baixar CSV</a>
</section>
<section class="card">
  <h2>Importar</h2>
  <p>Envie um CSV com a linha de cabe&ccedil;alho:<br>
     <code>title,author,isbn,genre,publication_year,publisher</code><br>
     Apenas <code>title</code> e <code>author</code> s&atilde;o obrigat&oacute;rios. Livros
     importados come&ccedil;am como dispon&iacute;veis. Livros existentes n&atilde;o s&atilde;o
     substitu&iacute;dos &mdash; as linhas s&atilde;o adicionadas.</p>
  <form method="post" action="{{ url_for('import_csv') }}" enctype="multipart/form-data" class="row">
    <input type="file" name="file" accept=".csv,text/csv" required>
    <button class="primary">Importar</button>
  </form>
</section>
{% endblock %}"""

app.jinja_loader = DictLoader(TEMPLATES := {
    "base.html": BASE, "index.html": INDEX,
    "add.html": ADD, "lend.html": LEND, "data.html": DATA,
})

# ------------------------------------------------------------- db access

_schema_ready = False

def get_db():
    """One connection per request, closed automatically at teardown."""
    global _schema_ready
    if "db" not in g:
        g.db = psycopg2.connect(**DB_CONFIG)
        if not _schema_ready:
            with g.db, g.db.cursor() as cur:
                cur.execute(SCHEMA)
            _schema_ready = True
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.errorhandler(psycopg2.OperationalError)
def db_down(exc):
    return f"<h1>Database connection failed</h1><pre>{exc}</pre>", 500

# ------------------------------------------------------------- queries

BOOK_COLS = """b.id, b.title, a.name AS author, b.isbn, b.genre,
               b.publication_year AS year, b.publisher, b.status, b.borrower"""


def list_books(conn, term=None):
    sql = (f"SELECT {BOOK_COLS} FROM books b "
           "JOIN authors a ON a.id = b.author_id")
    params = ()
    if term:
        sql += (" WHERE b.title ILIKE %s OR a.name ILIKE %s"
                " OR b.genre ILIKE %s OR b.isbn ILIKE %s")
        pattern = f"%{term}%"
        params = (pattern, pattern, pattern, pattern)
    sql += " ORDER BY b.title"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_book(conn, book_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT {BOOK_COLS} FROM books b "
                    "JOIN authors a ON a.id = b.author_id WHERE b.id = %s",
                    (book_id,))
        return cur.fetchone()


def add_book(conn, title, author, isbn=None, genre=None, year=None,
             publisher=None):
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO authors (name) VALUES (%s) "
                    "ON CONFLICT (name) DO NOTHING", (author.strip(),))
        cur.execute("SELECT id FROM authors WHERE name = %s", (author.strip(),))
        author_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO books (title, author_id, isbn, genre,
                                  publication_year, publisher)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (title.strip(), author_id, isbn, genre, year, publisher))
        return cur.fetchone()[0]


def get_stats(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE status = 'lent') "
                    "FROM books")
        return cur.fetchone()

# ------------------------------------------------------------- ISBN lookup

OPENLIBRARY_URL = "https://openlibrary.org/api/books"


def normalize_isbn(raw):
    if not raw:
        return None
    return re.sub(r"[^0-9Xx]", "", raw) or None


def lookup_isbn(raw_isbn):
    """Fetch book info from the Open Library API. Returns dict or None."""
    isbn = normalize_isbn(raw_isbn)
    if not isbn:
        return None
    try:
        resp = requests.get(
            OPENLIBRARY_URL,
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get(f"ISBN:{isbn}")
    except requests.RequestException:
        return None
    if not data:
        return None
    year = None
    if data.get("publish_date"):
        match = re.search(r"\d{4}", data["publish_date"])
        year = int(match.group()) if match else None
    return {
        "title": data.get("title"),
        "author": ", ".join(a["name"] for a in data.get("authors", [])) or None,
        "publisher": (data.get("publishers") or [{}])[0].get("name"),
        "year": year,
        "isbn": isbn,
    }

# ------------------------------------------------------------- routes

def form_field(name):
    return (request.form.get(name) or "").strip()


@app.route("/")
def index():
    conn = get_db()
    term = (request.args.get("q") or "").strip() or None
    books = list_books(conn, term)
    total, lent = get_stats(conn)
    return render_template("index.html", books=books, term=term or "",
                           total=total, lent=lent)


@app.route("/add", methods=["GET", "POST"])
def add():
    conn = get_db()
    if request.method == "POST":
        title, author = form_field("title"), form_field("author")
        if not title or not author:
            flash("Title and author are required.", "error")
            return redirect(url_for("add"))
        match = re.search(r"\d{4}", form_field("publication_year"))
        book_id = add_book(
            conn, title, author,
            isbn=normalize_isbn(form_field("isbn")),
            genre=form_field("genre") or None,
            year=int(match.group()) if match else None,
            publisher=form_field("publisher") or None,
        )
        flash(f"Added \u201c{title}\u201d as book #{book_id}.")
        return redirect(url_for("index"))

    # GET: show the form; if ?isbn= was given, try to autofill it
    form = {k: "" for k in ("title", "author", "isbn", "genre",
                            "publication_year", "publisher")}
    isbn = (request.args.get("isbn") or "").strip()
    if isbn:
        info = lookup_isbn(isbn)
        if info:
            form.update({"title": info["title"] or "",
                         "author": info["author"] or "",
                         "isbn": info["isbn"],
                         "publication_year": str(info["year"] or ""),
                         "publisher": info["publisher"] or ""})
            flash(f"Found \u201c{info['title']}\u201d on Open Library "
                  "\u2014 check the details and save.")
        else:
            flash("Couldn't find that ISBN (check the number, or your "
                  "server's internet connection). Fill in manually.", "error")
            form["isbn"] = isbn
    return render_template("add.html", form=form)


@app.route("/books/<int:book_id>/lend", methods=["GET", "POST"])
def lend(book_id):
    conn = get_db()
    book = get_book(conn, book_id)
    if book is None:
        flash("Book not found.", "error")
        return redirect(url_for("index"))
    if book["status"] == "lent":
        flash(f"\u201c{book['title']}\u201d is already lent to {book['borrower']}.",
              "error")
        return redirect(url_for("index"))
    if request.method == "POST":
        borrower = form_field("borrower")
        if not borrower:
            flash("Who is borrowing it?", "error")
            return redirect(request.url)
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE books SET status = 'lent', borrower = %s "
                        "WHERE id = %s", (borrower, book_id))
        flash(f"\u201c{book['title']}\u201d lent to {borrower}.")
        return redirect(url_for("index"))
    return render_template("lend.html", book=book)


@app.route("/books/<int:book_id>/return", methods=["POST"])
def return_book(book_id):
    conn = get_db()
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE books SET status = 'available', borrower = NULL "
                    "WHERE id = %s", (book_id,))
        done = cur.rowcount == 1
    flash("Book returned." if done else "Book not found.",
          "message" if done else "error")
    return redirect(url_for("index"))


@app.route("/books/<int:book_id>/delete", methods=["POST"])
def delete_book(book_id):
    conn = get_db()
    deleted = False
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM books WHERE id = %s RETURNING author_id",
                    (book_id,))
        row = cur.fetchone()
        if row:
            deleted = True
            cur.execute("SELECT 1 FROM books WHERE author_id = %s", (row[0],))
            if cur.fetchone() is None:
                cur.execute("DELETE FROM authors WHERE id = %s", (row[0],))
    flash("Book deleted." if deleted else "Book not found.",
          "message" if deleted else "error")
    return redirect(url_for("index"))


@app.route("/data")
def data():
    return render_template("data.html")


@app.route("/export.csv")
def export():
    books = list_books(get_db())
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM so Excel handles UTF-8 correctly
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(dict(b, publication_year=b["year"]) for b in books)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=library.csv"})


@app.route("/import", methods=["POST"])
def import_csv():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("data"))
    conn = get_db()
    added = skipped = 0
    try:
        stream = io.TextIOWrapper(file.stream, encoding="utf-8-sig", newline="")
        for raw in csv.DictReader(stream):
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in raw.items()}
            title, author = row.get("title"), row.get("author")
            if not title or not author:
                skipped += 1
                continue
            match = re.search(r"\d{4}", row.get("publication_year")
                              or row.get("year") or "")
            add_book(conn, title, author,
                     isbn=normalize_isbn(row.get("isbn")),
                     genre=row.get("genre") or None,
                     year=int(match.group()) if match else None,
                     publisher=row.get("publisher") or None)
            added += 1
    except (csv.Error, UnicodeDecodeError):
        flash("Could not read that file \u2014 is it a valid CSV?", "error")
        return redirect(url_for("data"))
    flash(f"Imported {added} book(s), skipped {skipped} row(s).")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # 0.0.0.0 so other devices on your network (e.g. your phone) can reach it
    app.run(host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "5000")))
