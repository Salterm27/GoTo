#!/usr/bin/env python3
"""
goto - tiny self-hosted go-links.

    goto/mail      -> https://mail.google.com
    goto/gh/foo    -> https://github.com/foo            (extra path is appended)
    goto/g/cats    -> https://google.com/search?q=cats  (when the URL contains %s)

One file, Python 3.8+ standard library only. It is both the server
(`goto serve`) and the command-line client (`goto add`, `goto ls`, ...).

Rules:
  * Every name - primary or alias - is unique across all gotos.
  * Every destination URL is unique: a second name for the same place
    must be an alias, not a duplicate entry.
"""

import argparse
import datetime as _dt
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit
import urllib.error
import urllib.request

VERSION = "1.1.0"
DEFAULT_DB = os.path.expanduser("~/.goto/goto.db")
DEFAULT_SERVER = "http://goto"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BLOCKED_SCHEMES = {"javascript", "data", "vbscript", "file"}
LOCAL_HOST_RE = re.compile(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|[^/]+\.local\b)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL,
    url_key     TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    last_used   TEXT
);
CREATE TABLE IF NOT EXISTS names (
    name       TEXT PRIMARY KEY,
    link_id    INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS names_one_primary ON names(link_id) WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS names_by_link ON names(link_id);
"""


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #

class GotoError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def norm_name(raw):
    name = (raw or "").strip().strip("/").lower()
    if not name:
        raise GotoError("A name is required.")
    if name.startswith("_"):
        raise GotoError("Names can't start with '_' (reserved for goto itself).")
    if not NAME_RE.match(name):
        raise GotoError(
            f"'{raw}' isn't a valid name. Use letters, numbers, '-', '_' or '.' "
            "(up to 64 characters, no spaces or slashes)."
        )
    return name


def norm_url(raw):
    """Return (url_to_store, url_key_for_uniqueness)."""
    url = (raw or "").strip()
    if not url:
        raise GotoError("A URL is required.")
    # Treat "scheme:..." as a scheme unless it's really "host:port".
    if "://" not in url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", url):
        url = ("http://" if LOCAL_HOST_RE.match(url) else "https://") + url
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in BLOCKED_SCHEMES:
        raise GotoError(f"'{scheme}:' URLs aren't allowed.")
    if scheme in ("http", "https") and not parts.netloc:
        raise GotoError(f"'{raw}' doesn't look like a valid URL.")
    if any(c.isspace() for c in url):
        raise GotoError("URLs can't contain spaces.")
    key = urlunsplit((scheme, parts.netloc.lower(), parts.path.rstrip("/"),
                      parts.query, parts.fragment))
    return url, key


def as_name_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    if not isinstance(value, (list, tuple)):
        raise GotoError("Aliases must be a list of names.")
    return [norm_name(v) for v in value if str(v).strip()]


def build_target(url, rest="", query=""):
    base, hashmark, frag = url.partition("#")
    if "%s" in base:
        base = base.replace("%s", rest)
    elif rest:
        base = base.rstrip("/") + "/" + rest
    if query:
        base += ("&" if "?" in base else "?") + query
    return base + hashmark + frag


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

class Store:
    def __init__(self, path):
        self.path = path
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.lock = threading.Lock()
        c = self._connect()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
        finally:
            c.close()

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    @contextmanager
    def read(self):
        c = self._connect()
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def write(self):
        with self.lock:
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                c.close()

    # -- internal ----------------------------------------------------------- #

    @staticmethod
    def _owner(c, name):
        return c.execute("SELECT link_id, is_primary FROM names WHERE name = ?", (name,)).fetchone()

    @staticmethod
    def _primary(c, link_id):
        row = c.execute("SELECT name FROM names WHERE link_id = ? AND is_primary = 1",
                        (link_id,)).fetchone()
        return row["name"] if row else "?"

    def _link(self, c, link_id):
        row = c.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        names = c.execute(
            "SELECT name FROM names WHERE link_id = ? ORDER BY is_primary DESC, name",
            (link_id,)).fetchall()
        return {
            "name": names[0]["name"],
            "aliases": [n["name"] for n in names[1:]],
            "url": row["url"],
            "description": row["description"],
            "hits": row["hits"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used": row["last_used"],
        }

    def _require(self, c, raw_name):
        try:
            name = norm_name(raw_name)
        except GotoError:
            raise GotoError(f"No goto named '{raw_name}'.", 404)
        row = self._owner(c, name)
        if not row:
            raise GotoError(f"No goto named '{name}'.", 404)
        return name, row

    def _check_names_free(self, c, names, own_id=None):
        if len(set(names)) != len(names):
            dup = next(n for n in names if names.count(n) > 1)
            raise GotoError(f"'{dup}' is listed more than once.")
        for n in names:
            row = self._owner(c, n)
            if row and row["link_id"] != own_id:
                owner = self._primary(c, row["link_id"])
                what = f"goto/{n} already exists" if owner == n else f"it's an alias of goto/{owner}"
                raise GotoError(f"'{n}' is already taken: {what}.", 409)

    def _check_url_free(self, c, key, own_id=None):
        row = c.execute("SELECT id FROM links WHERE url_key = ?", (key,)).fetchone()
        if row and row["id"] != own_id:
            owner = self._primary(c, row["id"])
            raise GotoError(
                f"That URL already has a goto: goto/{owner}. "
                f"Add an alias to it instead of creating a duplicate.", 409)

    def _set_names(self, c, link_id, primary, aliases):
        c.execute("DELETE FROM names WHERE link_id = ?", (link_id,))
        c.execute("INSERT INTO names (name, link_id, is_primary) VALUES (?, ?, 1)", (primary, link_id))
        for a in aliases:
            c.execute("INSERT INTO names (name, link_id, is_primary) VALUES (?, ?, 0)", (a, link_id))

    # -- public API --------------------------------------------------------- #

    def list(self, query=""):
        q = (query or "").strip().lower()
        with self.read() as c:
            ids = [r["link_id"] for r in c.execute(
                "SELECT link_id FROM names WHERE is_primary = 1 ORDER BY name")]
            links = [self._link(c, i) for i in ids]
        if q:
            links = [l for l in links if q in " ".join(
                [l["name"], *l["aliases"], l["url"], l["description"]]).lower()]
        return links

    def get(self, name):
        with self.read() as c:
            _, row = self._require(c, name)
            return self._link(c, row["link_id"])

    def create(self, name, url, aliases=None, description=""):
        name = norm_name(name)
        url, key = norm_url(url)
        aliases = as_name_list(aliases)
        with self.write() as c:
            self._check_names_free(c, [name] + aliases)
            self._check_url_free(c, key)
            ts = now()
            cur = c.execute(
                "INSERT INTO links (url, url_key, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (url, key, (description or "").strip(), ts, ts))
            self._set_names(c, cur.lastrowid, name, aliases)
            return self._link(c, cur.lastrowid)

    def update(self, name, new_name=None, url=None, description=None,
               aliases=None, add_aliases=None, remove_aliases=None):
        with self.write() as c:
            _, row = self._require(c, name)
            link_id = row["link_id"]
            current = self._link(c, link_id)

            if url is not None:
                url, key = norm_url(url)
                self._check_url_free(c, key, own_id=link_id)
                c.execute("UPDATE links SET url = ?, url_key = ? WHERE id = ?", (url, key, link_id))
            if description is not None:
                c.execute("UPDATE links SET description = ? WHERE id = ?",
                          (description.strip(), link_id))

            primary = norm_name(new_name) if new_name else current["name"]
            alias_list = as_name_list(aliases) if aliases is not None else list(current["aliases"])
            for a in as_name_list(add_aliases):
                if a not in alias_list:
                    alias_list.append(a)
            for a in as_name_list(remove_aliases):
                if a not in alias_list:
                    raise GotoError(f"'{a}' is not an alias of goto/{current['name']}.", 404)
                alias_list.remove(a)
            # Promoting an alias to be the main name is allowed; drop it from aliases.
            alias_list = [a for a in dict.fromkeys(alias_list) if a != primary]

            self._check_names_free(c, [primary] + alias_list, own_id=link_id)
            self._set_names(c, link_id, primary, alias_list)
            c.execute("UPDATE links SET updated_at = ? WHERE id = ?", (now(), link_id))
            return self._link(c, link_id)

    def delete(self, name):
        with self.write() as c:
            name, row = self._require(c, name)
            if not row["is_primary"]:
                owner = self._primary(c, row["link_id"])
                raise GotoError(
                    f"'{name}' is an alias of goto/{owner}. To remove just the alias run "
                    f"`goto alias rm {name}`; to delete the whole goto run `goto rm {owner}`.")
            link = self._link(c, row["link_id"])
            c.execute("DELETE FROM links WHERE id = ?", (row["link_id"],))
            return link

    def resolve(self, name, rest="", query=""):
        with self.write() as c:
            _, row = self._require(c, name)
            c.execute("UPDATE links SET hits = hits + 1, last_used = ? WHERE id = ?",
                      (now(), row["link_id"]))
            url = c.execute("SELECT url FROM links WHERE id = ?", (row["link_id"],)).fetchone()["url"]
        return build_target(url, rest, query)

    def import_links(self, items):
        imported, skipped = 0, []
        for item in items:
            if not isinstance(item, dict):
                skipped.append({"name": None, "error": "not an object"})
                continue
            try:
                self.create(item.get("name"), item.get("url"),
                            item.get("aliases") or [], item.get("description") or "")
                imported += 1
            except GotoError as e:
                skipped.append({"name": item.get("name"), "error": e.message})
        return {"imported": imported, "skipped": skipped}


# --------------------------------------------------------------------------- #
# Web server
# --------------------------------------------------------------------------- #

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>goto/</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>%E2%86%AA</text></svg>">
<style>
:root{--bg:#f7f7f5;--fg:#1c1b19;--muted:#77736c;--line:#e4e2dd;--card:#fff;--accent:#2457d6;--accent-fg:#fff;--danger:#c4321f;--chip:#efeee9;--warn:#fff6d6;--warn-line:#ecd98a}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--fg:#e9e7e2;--muted:#9b978f;--line:#2b2a27;--card:#1b1a18;--accent:#6c9bff;--accent-fg:#0b1530;--danger:#ff7a66;--chip:#2a2926;--warn:#2d2715;--warn-line:#5a4c1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:940px;margin:0 auto;padding:36px 16px 72px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font:700 30px/1 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0 0 6px;letter-spacing:-.5px}
h1 span{color:var(--accent)}
.sub{color:var(--muted);margin:0 0 24px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:22px}
form.grid{display:grid;grid-template-columns:1fr 2fr;gap:12px}
label{display:flex;flex-direction:column;font-size:12px;font-weight:500;color:var(--muted);gap:4px}
label small{font-weight:400}
input{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
input:focus{outline:2px solid var(--accent);outline-offset:-1px}
.namefield{display:flex;align-items:stretch}
.namefield span{display:flex;align-items:center;padding:0 8px;border:1px solid var(--line);border-right:0;border-radius:8px 0 0 8px;color:var(--muted);font-family:ui-monospace,Menlo,monospace;font-size:14px}
.namefield input{border-radius:0 8px 8px 0;flex:1;font-family:ui-monospace,Menlo,monospace}
.actions{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:7px 14px;cursor:pointer}
button:hover{border-color:var(--muted)}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-fg);font-weight:600}
button.danger{color:var(--danger)}
.msg{font-size:13px;color:var(--muted)} .msg.err{color:var(--danger)}
.banner{background:var(--warn);border:1px solid var(--warn-line);border-radius:10px;padding:10px 14px;margin-bottom:16px;font-size:14px}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}
.toolbar input{flex:1;max-width:320px}
.count{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:500}
a.name{font-family:ui-monospace,Menlo,monospace;font-weight:600;color:var(--fg);text-decoration:none}
a.name:hover{color:var(--accent)}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;margin:4px 4px 0 0;font:12px ui-monospace,Menlo,monospace;color:var(--muted)}
td.url a{color:var(--accent);text-decoration:none;word-break:break-all}
.desc{color:var(--muted);font-size:13px}
.num{color:var(--muted);font-variant-numeric:tabular-nums;text-align:right}
td.row-actions{white-space:nowrap;text-align:right}
td.row-actions button{padding:4px 10px;font-size:13px;margin-left:4px}
.empty{color:var(--muted);text-align:center;padding:36px}
.hint{color:var(--muted);font-size:13px;margin-top:20px}
@media (max-width:640px){form.grid{grid-template-columns:1fr}.hide-sm{display:none}td.row-actions button{margin:2px 0 0}}
</style></head>
<body><main>
<h1>goto<span>/</span></h1>
<p class="sub">Short links for everything. Type <code>goto/name</code> in your browser.</p>
<div id="banner" class="banner" hidden></div>
<div class="card">
<form id="form" class="grid" autocomplete="off">
  <label>Name<div class="namefield"><span>goto/</span><input id="f-name" required placeholder="mail" autocapitalize="off" spellcheck="false"></div></label>
  <label>Destination URL<input id="f-url" required placeholder="https://mail.google.com" autocapitalize="off" spellcheck="false"></label>
  <label>Aliases <small>(optional, comma separated)</small><input id="f-aliases" placeholder="m, email" autocapitalize="off" spellcheck="false"></label>
  <label>Description <small>(optional)</small><input id="f-desc" placeholder="Personal Gmail"></label>
  <div class="actions"><button class="primary" id="f-submit">Create</button><button type="button" id="f-cancel" hidden>Cancel</button><span id="msg" class="msg" role="status"></span></div>
</form>
</div>
<div class="toolbar"><input id="search" type="search" placeholder="Filter&hellip;  (press /)"><span id="count" class="count"></span></div>
<table><thead><tr><th>Name &amp; aliases</th><th>Destination</th><th class="num hide-sm">Uses</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<p class="hint">Tip: put <code>%s</code> in a URL to insert whatever follows the name &mdash; with <code>https://www.google.com/search?q=%s</code>, <code>goto/g/cats</code> searches for cats. Without <code>%s</code>, extra path is appended: <code>goto/gh/anthropics</code> &rarr; <code>https://github.com/anthropics</code>.</p>
</main>
<script>
const MISSING = __MISSING__;
const $ = s => document.querySelector(s);
let links = [], editing = null;

async function api(method, path, body) {
  const opts = {method, headers: {}};
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch('/_/api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
}
function el(tag, props, ...kids) {
  const e = document.createElement(tag); Object.assign(e, props || {});
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
function setMsg(t, err) { const m = $('#msg'); m.textContent = t || ''; m.className = 'msg' + (err ? ' err' : ''); }
function render() {
  const q = $('#search').value.trim().toLowerCase();
  const rows = $('#rows'); rows.replaceChildren();
  const shown = links.filter(l => !q || [l.name, ...l.aliases, l.url, l.description].join(' ').toLowerCase().includes(q));
  $('#count').textContent = links.length ? (q ? `${shown.length} of ${links.length}` : `${links.length} goto${links.length === 1 ? '' : 's'}`) : '';
  if (!shown.length) {
    rows.append(el('tr', {}, el('td', {colSpan: 4, className: 'empty', textContent: links.length ? 'No matches.' : 'No gotos yet. Create your first one above.'})));
    return;
  }
  for (const l of shown) {
    const nameCell = el('td', {}, el('a', {className: 'name', href: '/' + l.name, textContent: 'goto/' + l.name}));
    if (l.aliases.length) { const d = el('div'); l.aliases.forEach(a => d.append(el('span', {className: 'chip', textContent: a}))); nameCell.append(d); }
    const urlCell = el('td', {className: 'url'}, el('a', {href: l.url, textContent: l.url, target: '_blank', rel: 'noopener'}));
    if (l.description) urlCell.append(el('div', {className: 'desc', textContent: l.description}));
    const acts = el('td', {className: 'row-actions'},
      el('button', {type: 'button', textContent: 'Edit', onclick: () => startEdit(l)}),
      el('button', {type: 'button', className: 'danger', textContent: 'Delete', onclick: () => remove(l)}));
    rows.append(el('tr', {}, nameCell, urlCell, el('td', {className: 'num hide-sm', textContent: l.hits}), acts));
  }
}
async function load() { links = (await api('GET', 'links')).links; render(); }
function resetForm() { editing = null; $('#form').reset(); $('#f-submit').textContent = 'Create'; $('#f-cancel').hidden = true; }
function startEdit(l) {
  editing = l.name;
  $('#f-name').value = l.name; $('#f-url').value = l.url;
  $('#f-aliases').value = l.aliases.join(', '); $('#f-desc').value = l.description;
  $('#f-submit').textContent = 'Save changes'; $('#f-cancel').hidden = false;
  setMsg('Editing goto/' + l.name);
  window.scrollTo({top: 0, behavior: 'smooth'}); $('#f-url').focus();
}
async function remove(l) {
  const extra = l.aliases.length ? ` and its aliases (${l.aliases.join(', ')})` : '';
  if (!confirm(`Delete goto/${l.name}${extra}?`)) return;
  try {
    await api('DELETE', 'links/' + encodeURIComponent(l.name));
    if (editing === l.name) resetForm();
    setMsg('Deleted goto/' + l.name); await load();
  } catch (e) { setMsg(e.message, true); }
}
$('#form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = {
    name: $('#f-name').value, url: $('#f-url').value,
    aliases: $('#f-aliases').value.split(/[\s,]+/).filter(Boolean),
    description: $('#f-desc').value,
  };
  try {
    const wasEditing = editing;
    const saved = wasEditing ? await api('PATCH', 'links/' + encodeURIComponent(wasEditing), body)
                             : await api('POST', 'links', body);
    resetForm(); setMsg((wasEditing ? 'Saved' : 'Created') + ' goto/' + saved.name);
    $('#banner').hidden = true; await load();
  } catch (e) { setMsg(e.message, true); }
});
$('#f-cancel').onclick = () => { resetForm(); setMsg(''); };
$('#search').addEventListener('input', render);
document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) { e.preventDefault(); $('#search').focus(); }
});
if (MISSING) {
  const b = $('#banner'); b.hidden = false;
  b.textContent = `goto/${MISSING} doesn't exist yet. Add it below.`;
  $('#f-name').value = MISSING; $('#f-url').focus();
}
load().catch(e => setMsg('Could not load links: ' + e.message, true));
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "goto/" + VERSION
    store = None
    allowed_hosts = set()
    local_only = False

    # -- plumbing ----------------------------------------------------------- #

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def send_body(self, status, body, ctype, extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, obj):
        self.send_body(status, json.dumps(obj, indent=2) + "\n", "application/json; charset=utf-8")

    def send_page(self, status=200, missing=None):
        payload = json.dumps(missing).replace("<", "\\u003c")
        self.send_body(status, PAGE.replace("__MISSING__", payload), "text/html; charset=utf-8",
                       {"X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise GotoError("Request too large.", 413)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise GotoError("Body must be valid JSON.")
        if not isinstance(body, dict):
            raise GotoError("Body must be a JSON object.")
        return body

    @staticmethod
    def _hostname(value):
        value = (value or "").strip().lower()
        if value.startswith("["):
            return value[1:value.find("]")]
        if value.count(":") == 1:
            return value.split(":")[0]
        return value

    def request_allowed(self):
        """Blocks DNS-rebinding and cross-site requests against the API."""
        if "*" in self.allowed_hosts:
            return True
        if self._hostname(self.headers.get("Host")) not in self.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            if self._hostname(urlsplit(origin).netloc) not in self.allowed_hosts:
                return False
        return True

    # -- routing ------------------------------------------------------------ #

    def client_is_local(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return ip.is_loopback

    def route(self, method):
        if self.local_only and not self.client_is_local():
            return self.send_body(403, "goto only answers requests from this computer.\n",
                                  "text/plain; charset=utf-8")
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if path.startswith("/_/api/"):
                return self.api(method, path[len("/_/api/"):], parts.query)
            if method != "GET":
                raise GotoError("Method not allowed.", 405)
            if path in ("/", "/_", "/_/"):
                return self.send_page()
            if path == "/_/health":
                return self.send_body(200, "ok\n", "text/plain; charset=utf-8")
            if path == "/favicon.ico":
                return self.send_body(404, "", "text/plain")
            name, _, rest = path.lstrip("/").partition("/")
            name = unquote(name)
            try:
                target = self.store.resolve(name, rest, parts.query)
            except GotoError as e:
                if e.status == 404:
                    missing = name.lower() if NAME_RE.match(name.lower()) else None
                    return self.send_page(404, missing=missing)
                raise
            self.send_response(302)
            self.send_header("Location", quote(target, safe=":/?#[]@!$&'()*+,;=%~"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except GotoError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # pragma: no cover - last-resort safety net
            self.log_error("internal error: %r", e)
            self.send_json(500, {"error": "Internal error: %s" % e})

    def api(self, method, sub, query):
        if not self.request_allowed():
            raise GotoError("This host isn't allowed to change goto. Start the server with "
                            "--allow-host <hostname> to permit it.", 403)
        body = {}
        if method in ("POST", "PATCH", "PUT"):
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise GotoError("Content-Type must be application/json.", 415)
            body = self.read_json()
        segs = [unquote(s) for s in sub.strip("/").split("/") if s]
        store = self.store

        if segs == ["links"]:
            if method == "GET":
                q = parse_qs(query).get("q", [""])[0]
                return self.send_json(200, {"links": store.list(q)})
            if method == "POST":
                return self.send_json(201, store.create(
                    body.get("name"), body.get("url"), body.get("aliases"), body.get("description") or ""))
        elif len(segs) == 2 and segs[0] == "links":
            name = segs[1]
            if method == "GET":
                return self.send_json(200, store.get(name))
            if method in ("PATCH", "PUT"):
                return self.send_json(200, store.update(
                    name,
                    new_name=body.get("name"),
                    url=body.get("url"),
                    description=body.get("description"),
                    aliases=body.get("aliases"),
                    add_aliases=body.get("add_aliases"),
                    remove_aliases=body.get("remove_aliases")))
            if method == "DELETE":
                return self.send_json(200, {"deleted": store.delete(name)})
        elif segs == ["export"] and method == "GET":
            return self.send_json(200, {"goto_export": 1, "exported_at": now(), "links": store.list()})
        elif segs == ["import"] and method == "POST":
            items = body.get("links")
            if not isinstance(items, list):
                raise GotoError("Expected {\"links\": [...]}.")
            return self.send_json(200, store.import_links(items))
        else:
            raise GotoError("Not found.", 404)
        raise GotoError("Method not allowed.", 405)


class GotoServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second program bind the same port and silently
    # share or steal its traffic. Ask for exclusive use there instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DualStackServer(GotoServer):
    """Listens on IPv6 and IPv4 at once (needed so 'goto' works over both)."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


class IPv6Server(GotoServer):
    address_family = socket.AF_INET6


def make_servers(host, port):
    """Return [(server, label)] for the requested listen address.

    0.0.0.0 / ::  -> every address; one dual-stack socket when IPv6 exists.
    localhost     -> this computer only, on both 127.0.0.1 and ::1 (two sockets).
                     On Windows this works on port 80 without admin rights; on
                     macOS port 80 needs 0.0.0.0 + --local-only instead.
    anything else -> exactly that address.
    """
    if host in ("0.0.0.0", "::"):
        if socket.has_ipv6:
            try:
                return [(DualStackServer(("::", port), Handler), "[::]")]
            except (OSError, AttributeError):
                if host == "::":
                    raise
        return [(GotoServer(("0.0.0.0", port), Handler), "0.0.0.0")]
    if host == "localhost":
        servers = [(GotoServer(("127.0.0.1", port), Handler), "127.0.0.1")]
        if socket.has_ipv6:
            try:
                servers.append((IPv6Server(("::1", port), Handler), "[::1]"))
            except OSError:
                pass  # no IPv6 loopback; IPv4 alone is fine
        return servers
    if ":" in host:
        return [(IPv6Server((host, port), Handler), f"[{host}]")]
    return [(GotoServer((host, port), Handler), host)]


def redirect_output(log_path):
    """Send output to a log file. Needed under pythonw.exe (Windows, no console),
    where sys.stdout/sys.stderr are None and any write would crash a request."""
    if log_path:
        folder = os.path.dirname(os.path.abspath(log_path))
        os.makedirs(folder, exist_ok=True)
        stream = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = stream
    else:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")


def serve(args):
    redirect_output(args.log)
    store = Store(args.db)
    allowed = {"goto", "localhost", "127.0.0.1", "::1"}
    hostname = socket.gethostname().lower()
    allowed |= {hostname, hostname.split(".")[0], hostname.split(".")[0] + ".local"}
    for h in args.allow_host or []:
        allowed |= {x.strip().lower() for x in h.split(",") if x.strip()}
    Handler.store = store
    Handler.allowed_hosts = allowed
    Handler.local_only = args.local_only
    try:
        servers = make_servers(args.host, args.port)
    except OSError as e:
        hint = ""
        if sys.platform == "darwin" and args.port < 1024 and args.host not in ("0.0.0.0", "::"):
            hint = ("\n  macOS only lets normal users use ports below 1024 on all addresses: "
                    "use --host 0.0.0.0 --local-only")
        elif sys.platform == "win32":
            hint = ("\n  Another program is probably using this port (often IIS or another web "
                    "server). See which with: netstat -ano | findstr :%d" % args.port)
        msg = f"goto: can't listen on {args.host}:{args.port}: {e.strerror or e}{hint}"
        print(msg, file=sys.stderr, flush=True)
        sys.exit(1)
    scope = " (this computer only)" if args.local_only or args.host == "localhost" else ""
    where = ", ".join(f"http://{label}:{args.port}" for _, label in servers)
    print(f"goto {VERSION} serving on {where}{scope}  (db: {args.db})", flush=True)
    for httpd, _ in servers[1:]:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        servers[0][0].serve_forever()
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------- #
# Command-line client
# --------------------------------------------------------------------------- #

class Client:
    def __init__(self, server):
        self.server = server.rstrip("/")
        # Never route goto traffic through an HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + "/_/api/" + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=5) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error")
            except Exception:
                msg = f"HTTP {e.code}"
            die(msg)
        except urllib.error.URLError as e:
            die(f"can't reach the goto server at {self.server} ({e.reason}).\n"
                f"      Is it running? Try `goto doctor`.")


def die(msg):
    print(f"goto: {msg}", file=sys.stderr)
    sys.exit(1)


def fmt_link(l):
    s = "goto/" + l["name"]
    if l["aliases"]:
        s += "  (" + ", ".join(l["aliases"]) + ")"
    return s


def print_table(links):
    if not links:
        print("No gotos yet. Add one with: goto add mail https://mail.google.com")
        return
    left = [fmt_link(l) for l in links]
    width = min(max(len(x) for x in left), 40)
    for l, label in zip(links, left):
        print(f"{label:<{width}}  {l['url']}")


def print_link(l):
    print(fmt_link(l))
    print(f"  url:         {l['url']}")
    if l["description"]:
        print(f"  description: {l['description']}")
    print(f"  uses:        {l['hits']}" + (f"  (last {l['last_used']})" if l["last_used"] else ""))


def cmd_ls(c, a):
    q = "?q=" + quote(a.filter) if a.filter else ""
    print_table(c.call("GET", "links" + q)["links"])


def cmd_add(c, a):
    l = c.call("POST", "links", {"name": a.name, "url": a.url, "aliases": a.alias or [],
                                  "description": a.description or ""})
    print("Created " + fmt_link(l) + " -> " + l["url"])


def cmd_edit(c, a):
    body = {}
    if a.url:
        body["url"] = a.url
    if a.rename:
        body["name"] = a.rename
    if a.description is not None:
        body["description"] = a.description
    if a.aliases is not None:
        body["aliases"] = a.aliases
    if not body:
        die("nothing to change. Use --url, --rename, --description or --aliases.")
    l = c.call("PATCH", "links/" + quote(a.name, safe=""), body)
    print("Updated " + fmt_link(l) + " -> " + l["url"])


def cmd_rm(c, a):
    l = c.call("DELETE", "links/" + quote(a.name, safe=""))["deleted"]
    print("Deleted " + fmt_link(l))


def cmd_show(c, a):
    print_link(c.call("GET", "links/" + quote(a.name, safe="")))


def cmd_alias(c, a):
    if a.action == "add":
        l = c.call("PATCH", "links/" + quote(a.name, safe=""), {"add_aliases": a.aliases})
        print("Updated " + fmt_link(l))
    else:
        for alias in [a.name] + (a.aliases or []):
            owner = c.call("GET", "links/" + quote(alias, safe=""))
            l = c.call("PATCH", "links/" + quote(owner["name"], safe=""), {"remove_aliases": [alias]})
            print("Updated " + fmt_link(l))


def cmd_open(c, a):
    url = f"{c.server}/{quote(a.name, safe='')}"
    if a.rest:
        url += "/" + "/".join(quote(r, safe="") for r in a.rest)
    webbrowser.open(url)


def cmd_export(c, a):
    data = json.dumps(c.call("GET", "export"), indent=2) + "\n"
    if a.file and a.file != "-":
        with open(a.file, "w") as f:
            f.write(data)
        print(f"Exported to {a.file}", file=sys.stderr)
    else:
        sys.stdout.write(data)


def cmd_import(c, a):
    with (sys.stdin if a.file == "-" else open(a.file)) as f:
        data = json.load(f)
    items = data.get("links", []) if isinstance(data, dict) else data
    res = c.call("POST", "import", {"links": items})
    print(f"Imported {res['imported']} goto(s).")
    for s in res["skipped"]:
        print(f"  skipped {s['name']}: {s['error']}")


def cmd_doctor(c, a):
    ok = True
    host = urlsplit(c.server).hostname
    try:
        ip = socket.gethostbyname(host)
        print(f"[ok]   '{host}' resolves to {ip}")
    except OSError:
        ok = False
        print(f"[fail] '{host}' doesn't resolve. Add '127.0.0.1 {host}' to /etc/hosts "
              f"(the installer does this), or set GOTO_SERVER.")
    try:
        with c.opener.open(c.server + "/_/health", timeout=3) as r:
            print(f"[ok]   server answering at {c.server} ({r.status})")
    except Exception as e:
        ok = False
        print(f"[fail] no answer from {c.server}: {getattr(e, 'reason', e)}")
        if sys.platform == "darwin":
            print("       Check: launchctl print gui/$(id -u)/com.goto.server | head -20")
            print("       Logs:  tail ~/.goto/server.log")
        elif sys.platform == "win32":
            print("       Logs:  Get-Content $env:LOCALAPPDATA\\goto\\server.log -Tail 20")
            print("       Start: run install-windows.cmd again (safe to repeat)")
    sys.exit(0 if ok else 1)


COMMANDS = {"ls", "list", "add", "edit", "rm", "delete", "show", "alias", "open",
            "export", "import", "serve", "doctor", "help", "version"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `goto mail` == `goto open mail`
    if argv and not argv[0].startswith("-") and argv[0] not in COMMANDS:
        argv.insert(0, "open")
    if not argv:
        argv = ["ls"]

    p = argparse.ArgumentParser(prog="goto", description="Short go-links: goto/name -> URL.")
    p.add_argument("--server", default=os.environ.get("GOTO_SERVER", DEFAULT_SERVER),
                   help="goto server to talk to (default: $GOTO_SERVER or http://goto)")
    p.add_argument("--version", action="version", version="goto " + VERSION)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("ls", aliases=["list"], help="list gotos (optionally filtered)")
    s.add_argument("filter", nargs="?")
    s.set_defaults(fn=cmd_ls)

    s = sub.add_parser("add", help="create a goto")
    s.add_argument("name")
    s.add_argument("url")
    s.add_argument("-a", "--alias", action="append", help="alias (repeatable)")
    s.add_argument("-d", "--description")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("edit", help="change a goto's URL, name, description or aliases")
    s.add_argument("name")
    s.add_argument("--url")
    s.add_argument("--rename", metavar="NEW_NAME")
    s.add_argument("-d", "--description")
    s.add_argument("--aliases", nargs="*", metavar="ALIAS", help="replace all aliases")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("rm", aliases=["delete"], help="delete a goto and its aliases")
    s.add_argument("name")
    s.set_defaults(fn=cmd_rm)

    s = sub.add_parser("show", help="show one goto")
    s.add_argument("name")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("alias", help="add or remove aliases",
                       description="goto alias add NAME ALIAS...   |   goto alias rm ALIAS...")
    s.add_argument("action", choices=["add", "rm"])
    s.add_argument("name", help="the goto (for add) or the alias to remove (for rm)")
    s.add_argument("aliases", nargs="*")
    s.set_defaults(fn=cmd_alias)

    s = sub.add_parser("open", help="open a goto in your browser (or just: goto NAME)")
    s.add_argument("name")
    s.add_argument("rest", nargs="*")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("export", help="export all gotos as JSON")
    s.add_argument("file", nargs="?", default="-")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("import", help="import gotos from an export file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("doctor", help="check that goto is set up correctly")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("serve", help="run the goto server")
    s.add_argument("--host", default=os.environ.get("GOTO_HOST", "127.0.0.1"),
                   help="address to listen on: localhost = this computer only (IPv4+IPv6), "
                        "0.0.0.0 = every address (whole network)")
    s.add_argument("--port", type=int, default=int(os.environ.get("GOTO_PORT", "80")))
    s.add_argument("--db", default=os.environ.get("GOTO_DB", DEFAULT_DB))
    s.add_argument("--log", metavar="FILE", help="write the server log to FILE instead of the console")
    s.add_argument("--local-only", action="store_true",
                   help="only answer requests coming from this computer (use with --host 0.0.0.0)")
    s.add_argument("--allow-host", action="append", metavar="HOST",
                   help="extra hostname(s) allowed to use the editing API (comma separated; '*' = any)")

    sub.add_parser("help", help="show this help")
    sub.add_parser("version", help="show version")

    a = p.parse_args(argv)
    if a.cmd == "serve":
        return serve(a)
    if a.cmd in (None, "help"):
        return p.print_help()
    if a.cmd == "version":
        return print("goto " + VERSION)
    if a.cmd == "alias" and a.action == "add" and not a.aliases:
        die("usage: goto alias add NAME ALIAS [ALIAS...]")
    a.fn(Client(a.server), a)


if __name__ == "__main__":
    main()
