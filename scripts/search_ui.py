#!/usr/bin/env python3
"""Small local web UI for manually searching the arXiv Elasticsearch indexes.

This is an optional demo/helper tool. It is intentionally implemented with only
Python's standard library so it does not add project dependencies.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


DEFAULT_ES_HOST = "http://127.0.0.1:9200"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8080
INDEXES = {
    "baseline": "arxiv_papers_baseline",
    "optimized": "arxiv_papers_optimized",
}
TEXT_FIELDS = ["title^3", "abstract^2", "title_abstract", "authors"]


def int_param(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def build_search_body(params: dict[str, str]) -> dict[str, Any]:
    query = params.get("q", "").strip()
    mode = params.get("mode", "keyword")
    size = int_param(params.get("size"), default=10, minimum=1, maximum=50)
    category = params.get("category", "").strip()
    year = params.get("year", "").strip()
    exclude = params.get("exclude", "").strip()

    if not query:
        raise ValueError("Search text is required.")

    if mode == "phrase":
        main_query: dict[str, Any] = {
            "multi_match": {
                "query": query,
                "type": "phrase",
                "fields": ["title^3", "abstract^2", "title_abstract"],
                "slop": 0,
            }
        }
    elif mode == "fuzzy":
        main_query = {
            "multi_match": {
                "query": query,
                "fields": TEXT_FIELDS,
                "fuzziness": "AUTO",
                "prefix_length": 1,
            }
        }
    elif mode == "contain":
        main_query = {"match": {"title_abstract": {"query": query, "operator": "and"}}}
    else:
        main_query = {
            "multi_match": {
                "query": query,
                "fields": TEXT_FIELDS,
                "operator": "or",
            }
        }

    bool_query: dict[str, Any] = {"must": [main_query], "filter": []}
    if exclude:
        bool_query["must_not"] = [{"match": {"title_abstract": exclude}}]
    if category:
        bool_query["filter"].append({"term": {"categories": category}})
    if year:
        try:
            bool_query["filter"].append({"term": {"year": int(year)}})
        except ValueError as exc:
            raise ValueError("Year must be a number.") from exc
    if not bool_query["filter"]:
        bool_query.pop("filter")

    return {
        "size": size,
        "track_total_hits": True,
        "_source": [
            "paper_id",
            "title",
            "abstract",
            "authors",
            "categories",
            "primary_category",
            "year",
            "update_date",
        ],
        "query": {"bool": bool_query},
        "highlight": {
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
            "fields": {
                "title": {"number_of_fragments": 0},
                "abstract": {"fragment_size": 180, "number_of_fragments": 2},
                "title_abstract": {"fragment_size": 180, "number_of_fragments": 2},
            },
        },
    }


def request_json(host: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{host.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Elasticsearch returned non-object JSON.")
    return result


def format_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source", {})
    highlight = hit.get("highlight", {})
    snippet = ""
    for field in ("abstract", "title_abstract", "title"):
        values = highlight.get(field)
        if values:
            snippet = " … ".join(values)
            break
    if not snippet:
        snippet = html.escape(str(source.get("abstract", ""))[:420])
    return {
        "paper_id": source.get("paper_id"),
        "title": source.get("title"),
        "abstract": source.get("abstract"),
        "authors": source.get("authors"),
        "categories": source.get("categories"),
        "primary_category": source.get("primary_category"),
        "year": source.get("year"),
        "update_date": source.get("update_date"),
        "score": hit.get("_score"),
        "snippet": snippet,
    }


def run_search(es_host: str, params: dict[str, str]) -> dict[str, Any]:
    index_key = params.get("index", "baseline")
    index = INDEXES.get(index_key, INDEXES["baseline"])
    body = build_search_body(params)
    started = time.perf_counter()
    response = request_json(es_host, "POST", f"/{urllib.parse.quote(index)}/_search", body)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    total_obj = response.get("hits", {}).get("total", {})
    total = total_obj.get("value", total_obj) if isinstance(total_obj, dict) else total_obj
    return {
        "status": "passed",
        "index": index,
        "elapsed_ms": elapsed_ms,
        "elasticsearch_took_ms": response.get("took"),
        "total_hits": total,
        "hits": [format_hit(hit) for hit in response.get("hits", {}).get("hits", [])],
        "query_body": body,
    }


HTML_PAGE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ArXiv Search UI</title>
  <style>
    :root { color-scheme: light; --bg:#f6f7fb; --card:#fff; --text:#172033; --muted:#687085; --line:#dfe3ee; --brand:#315efb; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    main { max-width: 1120px; margin: 0 auto; padding: 28px 18px 60px; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    p { color: var(--muted); line-height: 1.75; }
    .panel, .result { background: var(--card); border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 8px 28px rgba(22, 34, 60, .06); }
    .panel { padding: 18px; margin: 18px 0; }
    form { display: grid; grid-template-columns: 2fr 160px 160px 120px 120px 110px; gap: 10px; align-items: end; }
    label { display:block; font-size: 12px; color: var(--muted); margin-bottom: 5px; }
    input, select, button { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 12px; padding: 11px 12px; font-size: 14px; background: #fff; color: var(--text); }
    button { background: var(--brand); border-color: var(--brand); color: white; cursor: pointer; font-weight: 700; }
    button:disabled { opacity: .6; cursor: wait; }
    .advanced { margin-top: 10px; display:grid; grid-template-columns: 1fr 160px 160px 1fr; gap:10px; }
    .meta { display:flex; flex-wrap:wrap; gap: 8px; margin: 14px 0; color: var(--muted); }
    .pill { background:#eef1ff; color:#263d9e; border-radius:999px; padding:5px 10px; font-size: 12px; }
    .result { padding: 16px 18px; margin: 12px 0; direction: ltr; text-align: left; }
    .result h3 { margin: 0 0 8px; font-size: 18px; }
    .result .sub { color:var(--muted); font-size:13px; margin-bottom:10px; }
    .snippet { line-height: 1.6; color:#263143; }
    mark { background:#fff2a8; padding: 0 2px; border-radius: 3px; }
    pre { direction:ltr; text-align:left; overflow:auto; background:#101827; color:#e7eefc; padding:14px; border-radius:14px; font-size:12px; }
    .error { color:#b42318; background:#fff1f0; border:1px solid #ffd1cc; padding:12px; border-radius:12px; }
    @media (max-width: 900px) { form, .advanced { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<main>
  <h1>جست‌وجوی مقالات arXiv</h1>
  <p>این UI فقط ابزار دمو و کار عملی است؛ داده‌ها از Elasticsearch محلی پروژه خوانده می‌شوند.</p>
  <section class="panel">
    <form id="searchForm">
      <div>
        <label>عبارت جست‌وجو</label>
        <input id="q" name="q" value="query optimization" placeholder="مثلاً database optimization" autofocus />
      </div>
      <div>
        <label>روش</label>
        <select id="mode" name="mode">
          <option value="keyword">keyword</option>
          <option value="phrase">phrase</option>
          <option value="contain">contain</option>
          <option value="fuzzy">fuzzy</option>
        </select>
      </div>
      <div>
        <label>ایندکس</label>
        <select id="index" name="index">
          <option value="baseline">baseline</option>
          <option value="optimized">optimized</option>
        </select>
      </div>
      <div>
        <label>تعداد</label>
        <input id="size" name="size" type="number" value="10" min="1" max="50" />
      </div>
      <div>
        <label>دسته‌بندی</label>
        <input id="category" name="category" placeholder="cs.DB" />
      </div>
      <button id="btn" type="submit">جست‌وجو</button>
    </form>
    <div class="advanced">
      <div>
        <label>حذف کلمه/عبارت</label>
        <input id="exclude" name="exclude" placeholder="مثلاً blockchain" />
      </div>
      <div>
        <label>سال</label>
        <input id="year" name="year" type="number" placeholder="2024" />
      </div>
    </div>
  </section>
  <div id="summary" class="meta"></div>
  <div id="error"></div>
  <section id="results"></section>
  <details class="panel">
    <summary>نمایش DSL ارسالی به Elasticsearch</summary>
    <pre id="dsl">{}</pre>
  </details>
</main>
<script>
const form = document.getElementById("searchForm");
const btn = document.getElementById("btn");
const results = document.getElementById("results");
const summary = document.getElementById("summary");
const errorBox = document.getElementById("error");
const dsl = document.getElementById("dsl");

async function search() {
  const params = new URLSearchParams();
  for (const id of ["q", "mode", "index", "size", "category", "year", "exclude"]) {
    const value = document.getElementById(id).value.trim();
    if (value) params.set(id, value);
  }
  btn.disabled = true;
  errorBox.innerHTML = "";
  summary.innerHTML = "<span class='pill'>در حال جست‌وجو...</span>";
  results.innerHTML = "";
  try {
    const res = await fetch("/api/search?" + params.toString());
    const data = await res.json();
    if (!res.ok || data.status !== "passed") throw new Error(data.error || "Search failed");
    summary.innerHTML = `
      <span class="pill">index: ${data.index}</span>
      <span class="pill">total hits: ${data.total_hits}</span>
      <span class="pill">client latency: ${data.elapsed_ms} ms</span>
      <span class="pill">ES took: ${data.elasticsearch_took_ms} ms</span>`;
    dsl.textContent = JSON.stringify(data.query_body, null, 2);
    results.innerHTML = data.hits.map((h, i) => `
      <article class="result">
        <h3>${i + 1}. ${escapeHtml(h.title || "")}</h3>
        <div class="sub">${escapeHtml(h.paper_id || "")} · ${escapeHtml(String(h.year || ""))} · score ${Number(h.score || 0).toFixed(3)} · ${(h.categories || []).map(escapeHtml).join(", ")}</div>
        <div class="sub">${escapeHtml(h.authors || "")}</div>
        <div class="snippet">${h.snippet || ""}</div>
      </article>`).join("");
  } catch (err) {
    errorBox.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    summary.innerHTML = "";
  } finally {
    btn.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

form.addEventListener("submit", event => { event.preventDefault(); search(); });
search();
</script>
</body>
</html>
"""


class SearchUIHandler(BaseHTTPRequestHandler):
    es_host = DEFAULT_ES_HOST

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            encoded = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if parsed.path == "/api/search":
            params = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
            try:
                self.send_json(200, run_search(self.es_host, params))
            except (ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                self.send_json(400, {"status": "failed", "error": str(exc)})
            return
        self.send_error(404)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_UI_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--es-host", default=DEFAULT_ES_HOST)
    args = parser.parse_args(argv)
    SearchUIHandler.es_host = args.es_host
    server = ThreadingHTTPServer((args.host, args.port), SearchUIHandler)
    print(f"Search UI: http://{args.host}:{args.port}")
    print(f"Elasticsearch: {args.es_host}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping search UI.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
