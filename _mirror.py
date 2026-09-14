import re, os, sys, gzip, zlib, urllib.request, urllib.parse, hashlib
from collections import deque

SITE = "merki.store"
OUT = r"C:\Users\fatuk\www\merki-store"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

ASSET_HOSTS = ("tildacdn.", "fonts.googleapis.com", "fonts.gstatic.com", "tilda.ws")
URL_RE = re.compile("https?://[A-Za-z0-9._-]+(?:/[^\s\"'<>()]*)?")

def fetch(url, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        enc = r.headers.get("Content-Encoding", "")
        ctype = r.headers.get("Content-Type", "")
    if enc == "gzip" or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif enc == "deflate":
        data = zlib.decompress(data)
    return data, ctype

def is_asset(url):
    p = urllib.parse.urlparse(url)
    if len(p.path.strip("/")) < 1 and not p.query: return False
    return any(h in p.netloc for h in ASSET_HOSTS)

def is_page(url):
    p = urllib.parse.urlparse(url)
    if p.netloc not in (SITE, "www." + SITE): return False
    path = p.path
    if not path or path == "/": return True
    if "." in path.rsplit("/", 1)[-1] and not path.endswith(".html"): return False
    if path.startswith("/tilda/"): return False
    return True

def page_local(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    if not path: return "index.html"
    path = re.sub(r"\.html$", "", path)
    return path.replace("/", "_") + ".html"

def asset_local(url):
    p = urllib.parse.urlparse(url)
    host = p.netloc.split(".")[0]           # static / thb / neo / fonts
    if "gstatic" in p.netloc: host = "gfonts"
    path = p.path.lstrip("/")
    if p.netloc == "fonts.googleapis.com":   # css2?family=... -> hash name
        h = hashlib.md5(url.encode()).hexdigest()[:8]
        path = f"fonts-{h}.css"
    path = path.replace("/-/", "/_/")        # '-/resizeb/20x' -> '_/resizeb/20x'
    return f"assets/{host}/{path}"

assets = {}     # url -> local path
pages = {}      # url -> local name
queue = deque(["https://merki.store/"])
seen_pages = set()

def norm_page(u):
    p = urllib.parse.urlparse(u)
    return "https://merki.store" + (p.path.rstrip("/") or "/")

def collect_assets(text):
    for m in URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;")
        if is_asset(u) and u not in assets:
            assets[u] = asset_local(u)

# 1. crawl pages
page_html = {}
while queue:
    url = norm_page(queue.popleft())
    if url in seen_pages: continue
    seen_pages.add(url)
    print("PAGE", url)
    try:
        data, _ = fetch(url)
    except Exception as e:
        print("  !! failed:", e); continue
    html = data.decode("utf-8", "replace")
    page_html[url] = html
    pages[url] = page_local(url)
    for m in URL_RE.finditer(html):
        u = m.group(0)
        if is_page(u):
            nu = norm_page(u)
            if nu not in seen_pages: queue.append(nu)
    collect_assets(html)

# 2. download assets (CSS may reference more assets -> loop)
downloaded = {}
pending = deque(assets.keys())
while pending:
    url = pending.popleft()
    if url in downloaded: continue
    local = assets[url]
    dest = os.path.join(OUT, local)
    try:
        data, ctype = fetch(url)
    except Exception as e:
        print("  !! asset failed:", url, e); downloaded[url] = None; continue
    if local.endswith(".css") or "text/css" in ctype:
        css = data.decode("utf-8", "replace")
        base = url
        def css_url(m):
            raw = m.group(2).strip()
            if raw.startswith("data:"): return m.group(0)
            full = urllib.parse.urljoin(base, raw)
            if is_asset(full):
                if full not in assets:
                    assets[full] = asset_local(full); pending.append(full)
                rel = os.path.relpath(assets[full], os.path.dirname(local)).replace("\\", "/")
                return f"url({m.group(1)}{rel}{m.group(1)})"
            return m.group(0)
        css = re.sub(r'url\((["\']?)([^)"\']+)\1\)', css_url, css)
        data = css.encode("utf-8")
    downloaded[url] = local
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f: f.write(data)
print(f"assets: {len([v for v in downloaded.values() if v])} ok, {len([v for v in downloaded.values() if not v])} failed")

# 3. rewrite + write pages
STRIP = [
    r'<!-- Yandex\.Metrika counter -->.*?<!-- /Yandex\.Metrika counter -->',
    r'<script[^>]*>[^<]*mc\.yandex\.ru[^<]*</script>',
    r'<noscript><div><img src="https://mc\.yandex\.ru[^<]*</div></noscript>',
    r'<script[^>]*tilda-stat[^>]*>.*?</script>',
    r'<script[^>]*>[^<]*tildastat[^<]*</script>',
    r'<link[^>]+rel="(preconnect|dns-prefetch)"[^>]*>',
]
for url, html in page_html.items():
    for pat in STRIP:
        html = re.sub(pat, "", html, flags=re.S)
    # page links -> local html
    def link_sub(m):
        u = m.group(1)
        if is_page(u):
            return f'href="{pages.get(norm_page(u), page_local(u))}"'
        return m.group(0)
    html = re.sub(r'href="(https?://(?:www\.)?merki\.store[^"]*)"', link_sub, html)
    # assets -> local
    for a_url in sorted(assets, key=len, reverse=True):
        loc = downloaded.get(a_url)
        if loc: html = html.replace(a_url, loc)
    dest = os.path.join(OUT, pages[url])
    with open(dest, "w", encoding="utf-8") as f: f.write(html)
    print("WROTE", pages[url])
