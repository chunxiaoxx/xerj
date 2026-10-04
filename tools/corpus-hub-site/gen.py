#!/usr/bin/env python3
"""gen.py — generate the Corpus Hub static site (hub.xerj.org) from the registry.

Reads ONLY registry state on this branch (corpus-hub):
  tools/xerj-code/hub/*.json                 — manifests (lane A)
  tools/xerj-code/hub/backlog/backlog-100.json — categories/status/use-cases
  tools/xerj-code/hub/WAVES.md               — G7 scores per wave
  tools/xerj-code/hub/backlog/*-graded.json  — graded G7 suites (non-IT lane)
  tools/xerj-code/hub/backlog/impact-snapshot-*.json — benchmark A/B snapshot
  tools/packs/*/recipe.toml                  — lane B packs
Writes out/ (index, per-corpus, per-category, about, style, data.json).

Stdlib only; deterministic; no network. Rerun + redeploy on registry change.
"""
import datetime as dt
import html
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
HUB = ROOT / "tools" / "xerj-code" / "hub"
PACKS = ROOT / "tools" / "packs"
OUT = pathlib.Path(__file__).resolve().parent / "out"

CATS = {
    "SEC": ("Security advisories & identifiers", "Does this pattern have CVE/GHSA/KEV precedent; is this CVE exploited in the wild"),
    "STD": ("Standards, specs & regulations", "What does RFC 9105 / SP 800-88 / 29 CFR 1910.28 / the Consumer Rights Act actually say about ___"),
    "OPS": ("Production & incident knowledge", "How do real teams handle ___ / what caused real outages like ___"),
    "GOOD": ("Design guidance & exemplars", "What does a reviewed, idiomatic ___ look like"),
    "BAD": ("Failure precedent", "Show me real vulnerable functions and the fix that closed them"),
    "DATA": ("Reference implementations — data engines", "How duckdb/rocksdb/sqlite actually do planner/LSM/B+-tree"),
    "NET": ("Reference implementations — net/crypto/serialisation", "How quinn/rustls/zstd/protobuf actually do handshake/compression/framing"),
}

STATUSES = {"live": ("live", "ok"), "candidate": ("candidate", "warn"), "planned": ("planned", "mute"),
            "deferred": ("deferred", "warn"), "killed": ("killed", "bad"), "withdrawn": ("withdrawn", "bad")}


def esc(s):
    return html.escape(str(s), quote=True)


def human_bytes(n):
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_files(n):
    return f"{n:,} files" if n else "—"


def parse_waves_g7():
    """WAVES.md G7 tables -> {corpus: {median, verdict, wave}}"""
    scores = {}
    text = (HUB / "WAVES.md").read_text()
    for m in re.finditer(r"^## (Wave [^—\n]+).*?\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        wave = m.group(1).strip()
        for line in m.group(2).splitlines():
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 3 and c[0] and not c[0].startswith(("-", "corpus")):
                try:
                    median = float(c[1].split()[0])
                except (ValueError, IndexError):
                    continue
                verdict = "pass" if "pass" in c[2] else c[2] or "—"
                scores.setdefault(c[0], {"median": median, "verdict": verdict, "wave": wave})
    return scores


def parse_graded_suites():
    """backlog/*-graded.json -> {corpus: {queries:[{q,expect,top5,grade}], median}}"""
    out = {}
    for p in sorted((HUB / "backlog").glob("*graded*.json")):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        for slug, suite in data.get("graded", data.get("suites", data.get("corpora", {}))).items():
            if isinstance(suite, dict) and "queries" in suite:
                # score on the 0-5 scale the WAVES.md table uses (relevant=1,
                # partial=0.5) when the suite does not carry its own
                if "median" not in suite:
                    num = {"relevant": 1.0, "partial": 0.5}
                    suite["median"] = sum(num.get(q.get("grade"), 0.0) for q in suite["queries"])
                out[slug] = suite
    return out


def load_impact():
    snaps = sorted((HUB / "backlog").glob("impact-snapshot-*.json"))
    if not snaps:
        return {}, None
    data = json.loads(snaps[-1].read_text())
    return {r["corpus"]: r for r in data.get("rows", [])}, data.get("provenance", {})


def load_registry():
    backlog = json.loads((HUB / "backlog" / "backlog-100.json").read_text())
    rows = {r["slug"]: r for r in backlog["corpora"]}
    corpora = []
    # lane A manifests
    for p in sorted(HUB.glob("*.json")):
        if p.stem in ("TEMPLATE", "corpus-schema"):
            continue
        m = json.loads(p.read_text())
        slug = m["corpus"]
        row = rows.get(slug, {})
        corpora.append({
            "slug": slug, "lane": "A", "cat": row.get("cat", "?"),
            "status": row.get("status", "unknown"), "refresh": row.get("refresh", "pinned"),
            "usecase": row.get("domain", ""), "repos": m.get("repos", []),
            "added": m.get("cloned_at", ""), "kind": "reference corpus",
            "consumed": rows.get(slug, {}).get("sources", [{}])[0].get("url", ""),
        })
    # lane B recipes
    for p in sorted(PACKS.glob("*/recipe.toml")):
        if p.parent.name == "TEMPLATE-recipe":
            continue
        r = tomllib.loads(p.read_text())
        meta = r.get("recipe", {})
        slug = p.parent.name
        row = rows.get(slug, {})
        corpora.append({
            "slug": slug, "lane": "B", "cat": row.get("cat", "?"),
            "status": row.get("status", "unknown"), "refresh": row.get("refresh", "weekly"),
            "usecase": row.get("domain", ""), "repos": [],
            "sources": r.get("sources", []), "added": "", "kind": "curated pack",
            "consumed": meta.get("notes", "") or row.get("sources", [{}])[0].get("url", ""),
        })
    # backlog-only rows (planned/deferred/candidates with no manifest yet)
    have = {c["slug"] for c in corpora}
    for slug, row in rows.items():
        if slug not in have:
            corpora.append({
                "slug": slug, "lane": row.get("lane", "?"), "cat": row.get("cat", "?"),
                "status": row.get("status", "?"), "refresh": row.get("refresh", ""),
                "usecase": row.get("domain", ""), "repos": [], "sources": row.get("sources", []),
                "added": "", "kind": "backlog row", "consumed": row.get("sources", [{}])[0].get("url", ""),
            })
    return sorted(corpora, key=lambda c: c["slug"])


def corpus_stats(c):
    files = sum(r.get("files") or 0 for r in c["repos"])
    nbytes = sum(r.get("bytes") or 0 for r in c["repos"])
    licences = sorted({r["review"]["spdx"] for r in c["repos"] if r.get("review")})
    if not licences:
        licences = sorted({s.get("licence", "?") for s in c.get("sources", [])[:1]})
    return files, nbytes, licences


def g7_badge(slug, g7, graded):
    if slug in graded:
        med = graded[slug].get("median") or 0.0
        cls = "ok" if med >= 3 else "bad"
        return f'<span class="badge g7 {cls}" title="retrieval spot-check, median relevant of 5 queries">G7 {med:.1f}/5</span>'
    if slug in g7:
        g = g7[slug]
        cls = "ok" if g["verdict"] == "pass" else "bad"
        tip = esc(g["wave"])
        return f'<span class="badge g7 {cls}" title="retrieval spot-check ({tip})">G7 {g["median"]:.1f}/5</span>'
    return '<span class="badge g7 mute" title="not yet spot-checked">G7 —</span>'


def impact_badge(c, impact):
    r = impact.get(c["slug"])
    if not r or r.get("status") != "measured":
        return ""
    return (f'<span class="badge imp" title="drift-anchored agent A/B: bare pass → corpus pass">'
            f'A/B {esc(r["p"])} → {esc(r["x"])}</span>')


def card(c, g7, graded, impact):
    files, nbytes, licences = corpus_stats(c)
    st, stcls = STATUSES.get(c["status"], (c["status"], "mute"))
    return f'''<a class="card" href="corpus/{esc(c['slug'])}.html">
  <div class="cardhead"><span class="name">{esc(c['slug'])}</span><span class="dot {stcls}"></span></div>
  <div class="badges">
    <span class="badge cat">{esc(c['cat'])}</span><span class="badge lane">{esc(c['lane'])}</span>
    <span class="badge lic" title="licence">{esc(' + '.join(licences) or '?')}</span>
  </div>
  <p class="use">{esc(c['usecase'][:180])}{'…' if len(c['usecase']) > 180 else ''}</p>
  <div class="meta"><span>{human_bytes(nbytes)}</span><span>{human_files(files)}</span><span>{st}</span></div>
  <div class="badges">{g7_badge(c['slug'], g7, graded)}{impact_badge(c, impact)}</div>
</a>'''


STYLE = """/* corpus hub — brand tokens inherited from xerj.org */
:root{--bg:#f6f4ee;--ink:#11120f;--mute:#696762;--line:#cfcbbf;--faint:#e3dfd4;
--accent:#7f5200;--gold:#ffc400;--ok:#2e6b3a;--bad:#8c2f2f;--warn:#8a6d1a;
--font-data:'IBM Plex Sans','Inter',system-ui,sans-serif;--font-mono:'JetBrains Mono','IBM Plex Mono',monospace}
@media(prefers-color-scheme:dark){:root{--bg:#0b0b0d;--ink:#f4f2ec;--mute:#8a8680;--line:#3a3836;--faint:#2b2a28;--accent:#ffc400;--gold:#ffc400;--ok:#7fc08a;--bad:#e08a8a;--warn:#d4b45e}}
*{box-sizing:border-box}body{margin:0;font-family:var(--font-data);background:var(--bg);color:var(--ink);line-height:1.5}
a{color:inherit}code{font-family:var(--font-mono);font-size:.92em;background:var(--faint);padding:.1em .35em;border-radius:4px}
header.site{display:flex;align-items:baseline;gap:1rem;padding:1rem 2rem;border-bottom:1px solid var(--line);flex-wrap:wrap}
header.site .brand{font-weight:700;letter-spacing:.02em}header.site .brand b{color:var(--accent)}
header.site nav a{margin-right:1rem;text-decoration:none;color:var(--mute)}header.site nav a:hover{color:var(--ink)}
main{max-width:1180px;margin:0 auto;padding:1.5rem 2rem 4rem}
.hero h1{font-size:2.1rem;margin:.4rem 0}main p.lead{color:var(--mute);max-width:60ch}
.stats{display:flex;gap:2.5rem;flex-wrap:wrap;margin:1.2rem 0;border-block:1px solid var(--line);padding:.8rem 0}
.stats div b{display:block;font-size:1.35rem}.stats div span{color:var(--mute);font-size:.85rem}
.toolbar{display:flex;gap:.6rem;margin:1rem 0;flex-wrap:wrap;align-items:center}
.toolbar input{font:inherit;padding:.45em .7em;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);min-width:16rem}
.chip{font:inherit;border:1px solid var(--line);background:transparent;color:var(--mute);border-radius:999px;padding:.25em .8em;cursor:pointer}
.chip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:1rem;margin-top:1rem}
.card{display:block;text-decoration:none;border:1px solid var(--line);border-radius:12px;padding:.9rem 1rem;background:transparent;transition:border-color .15s}
.card:hover{border-color:var(--accent)}
.cardhead{display:flex;justify-content:space-between;align-items:center}.cardhead .name{font-family:var(--font-mono);font-weight:600}
.dot{width:.65em;height:.65em;border-radius:50%;display:inline-block}.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}.dot.mute{background:var(--mute)}
.badges{display:flex;gap:.4rem;flex-wrap:wrap;margin:.4rem 0}
.badge{font-size:.72rem;font-family:var(--font-mono);border:1px solid var(--line);border-radius:6px;padding:.1em .5em;color:var(--mute)}
.badge.cat{color:var(--accent);border-color:var(--accent)}
.badge.g7.ok,.badge.imp{color:var(--ok);border-color:var(--ok)}.badge.g7.bad{color:var(--bad);border-color:var(--bad)}.badge.g7.mute{opacity:.6}
.card .use{color:var(--mute);font-size:.88rem;margin:.4rem 0;min-height:2.6em}
.card .meta{display:flex;gap:1rem;color:var(--mute);font-size:.8rem;font-family:var(--font-mono)}
section.detail h2{border-bottom:1px solid var(--line);padding-bottom:.3rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{text-align:left;padding:.45em .6em;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600}td.mono{font-family:var(--font-mono);font-size:.82rem}
.note{color:var(--mute);font-size:.85rem}.kbox{background:var(--faint);border-radius:10px;padding:1rem 1.2rem;margin:.8rem 0}
.g7q{margin:.6rem 0;padding:.6rem .8rem;border-left:3px solid var(--line)}.g7q.pass{border-color:var(--ok)}.g7q.fail{border-color:var(--bad)}
footer{border-top:1px solid var(--line);color:var(--mute);padding:1.5rem 2rem;font-size:.85rem}
.pkg{color:var(--mute)}
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="{desc}">
<link rel="stylesheet" href="{rel}assets/style.css"></head>
<body><header class="site"><span class="brand">XERJ <b>·</b> corpus hub</span>
<nav><a href="{rel}index.html">corpora</a><a href="{rel}about.html">about the hub</a>
<a href="https://github.com/xerj-org/xerj/tree/corpus-hub/tools/xerj-code/hub">registry (git)</a>
<a href="https://xerj.org">xerj.org</a></nav></header>
{body}
<footer>Pinned, licenced, checksummed, measured. Numbers on this page trace to the
<a href="https://github.com/xerj-org/xerj/tree/corpus-hub">corpus-hub registry</a> and the runs cited there.
Licences are verified per source before anything ships live — see the rights section on each card.</footer>
</body></html>"""


def page(title, desc, body, rel=""):
    return PAGE.format(title=esc(title), desc=esc(desc), body=body, rel=rel)


def render_index(corpora, g7, graded, impact, prov):
    live = [c for c in corpora if c["status"] == "live"]
    total_bytes = sum(sum(r.get("bytes") or 0 for r in c["repos"]) for c in live)
    n_imp = sum(1 for c in live if impact.get(c["slug"], {}).get("status") == "measured")
    chips = "".join(f'<button class="chip" data-f="cat:{k}">{k} · {v[0].split("—")[0].strip() if "—" in v[0] else v[0]}</button>' for k, v in CATS.items())
    cards = "\n".join(card(c, g7, graded, impact) for c in corpora)
    prov_line = f'A/B snapshot: {esc(prov.get("date",""))}, branch <code>{esc(prov.get("branch",""))}</code>' if prov else ""
    body = f'''<main>
<div class="hero"><h1>Reference corpora for agents that answer from evidence</h1>
<p class="lead">A public registry of pinned, licenced, measured corpora — what an agent should retrieve
against when the answer has to be <em>current</em> rather than memorised. Every entry names its domain,
its sources and pins, its licence verdict, and what the retrieval tests actually scored.</p></div>
<div class="stats">
<div><b>{len(live)}</b><span>live corpora</span></div>
<div><b>{len(corpora)}</b><span>registry rows</span></div>
<div><b>{len(CATS)}</b><span>categories</span></div>
<div><b>{human_bytes(total_bytes)}</b><span>pinned text under management</span></div>
<div><b>{n_imp}</b><span>corpora with agent A/B results {prov_line}</span></div>
</div>
<div class="toolbar"><input id="q" type="search" placeholder="filter by name, use-case, licence…">
<button class="chip on" data-f="">all</button>{chips}</div>
<div class="grid" id="grid">{cards}</div>
</main>
<script>
const cards=[...document.querySelectorAll('.card')];
document.getElementById('q').addEventListener('input',e=>{{
  const s=e.target.value.toLowerCase();
  cards.forEach(c=>c.style.display=!s||c.textContent.toLowerCase().includes(s)?'':'none');
}});
let active='';
document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{{
  document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));b.classList.add('on');
  active=b.dataset.f;
  cards.forEach(c=>c.style.display=(!active||c.textContent.includes(active.split(':')[1]+' ')||c.textContent.includes(' '+active.split(':')[1]))?'':'none');
}}));
</script>'''
    return page("XERJ Corpus Hub — pinned, licenced, measured reference corpora",
                "Registry of reference corpora for agents: categories, licences, sources, retrieval scores", body)


def detail_body(c, g7, graded, impact, prov):
    files, nbytes, licences = corpus_stats(c)
    st, stcls = STATUSES.get(c["status"], (c["status"], "mute"))
    cat_desc = CATS.get(c["cat"], ("", ""))
    rows = []
    for r in c["repos"]:
        rv = r.get("review", {})
        rows.append(f'''<tr><td class="mono"><a href="{esc(r.get('url',''))}">{esc(r.get('repo',''))}</a></td>
<td class="mono">{esc(r.get('sha','')[:12])}…</td><td>{esc(rv.get('spdx','?'))}</td>
<td class="mono">{human_bytes(r.get('bytes'))} / {human_files(r.get('files'))}</td>
<td class="note">{esc(rv.get('note',''))[:400]}</td></tr>''')
    for s in c.get("sources", []):
        rows.append(f'''<tr><td class="mono"><a href="{esc(s.get('url',''))}">{esc(s.get('slug', s.get('url','')))}</a></td>
<td class="mono">recipe</td><td>{esc(s.get('licence','?'))}</td><td class="mono">curated pack</td>
<td class="note">{esc(s.get('licence_hint',''))[:400]}</td></tr>''')
    g = graded.get(c["slug"])
    if g:
        qs = "\n".join(f'''<div class="g7q {'pass' if q.get('grade') in ('relevant','partial') else 'fail'}">
<b>Q:</b> {esc(q['q'])}<br><b>expect:</b> <span class="note">{esc(q.get('expect',''))}</span><br>
<b>top-5:</b> <span class="note mono">{esc(' · '.join(q.get('top5', [])[:5]))}</span><br>
<b>grade:</b> {esc(str(q.get('grade','')))}</div>''' for q in g["queries"])
        g7sec = f'<section class="detail" id="g7"><h2>Retrieval spot-check (G7)</h2><p class="note">median {g.get("median","?")}/5 relevant — {esc(g.get("graded_at",""))}</p>{qs}</section>'
    elif c["slug"] in g7:
        e = g7[c["slug"]]
        g7sec = f'''<section class="detail" id="g7"><h2>Retrieval spot-check (G7)</h2>
<p><b>{e["median"]:.1f}/5</b> median relevant ({esc(e["wave"])} stratified sample) — verdict: <b>{esc(e["verdict"])}</b></p></section>'''
    else:
        g7sec = '''<section class="detail" id="g7"><h2>Retrieval spot-check (G7)</h2>
<p class="note">not yet spot-checked; queries are pre-registered in the registry before grading.</p></section>'''
    imp = impact.get(c["slug"])
    if imp and imp.get("status") == "measured":
        impsec = f'''<section class="detail" id="ab"><h2>Agent A/B (drift-anchored)</h2>
<table><tr><th>arm</th><th>score</th><th>drift tasks</th></tr>
<tr><td>bare (offline, honest-unknown)</td><td class="mono">{esc(imp["p"])}</td><td class="mono">{esc(imp.get("dp","—"))}</td></tr>
<tr><td>with this corpus</td><td class="mono">{esc(imp["x"])}</td><td class="mono">{esc(imp.get("dx","—"))}</td></tr></table>
<p class="note">snapshot {esc(prov.get("date",""))} from <code>{esc(prov.get("branch",""))}</code>; protocol: benchmarks/corpus-tasks/PROTOCOL.md</p></section>'''
    else:
        impsec = ""
    return f'''<main>
<p><a href="../index.html">← all corpora</a></p>
<h1 style="font-family:var(--font-mono)">{esc(c['slug'])} <span class="dot {stcls}"></span></h1>
<p class="note">{esc(c['kind'])} · status <b>{st}</b> · category <b>{esc(c['cat'])}</b> ({esc(cat_desc[0])}) · lane {esc(c['lane'])} · refresh {esc(c['refresh'])}</p>
<section class="detail"><h2>What an agent uses it for</h2><p>{esc(c['usecase'] or '—')}</p></section>
<div class="kbox"><b>Use it</b><br><code>xerj corpus add --from https://raw.githubusercontent.com/xerj-org/xerj/corpus-hub/tools/xerj-code/hub/{esc(c['slug'])}.json</code><br>
<code>xerj corpus index {esc(c['slug'])}</code> → <code>xerj code {esc(c['slug'])} "your question"</code></div>
<section class="detail"><h2>Sources &amp; pins</h2><table>
<tr><th>source</th><th>pin</th><th>licence</th><th>size</th><th>review note</th></tr>{''.join(rows)}</table>
<p class="note">{human_bytes(nbytes)} across {human_files(files)} · added {esc(c['added'][:10] or '—')}</p></section>
<section class="detail"><h2>Rights</h2><p>{esc(' + '.join(licences) or '?')} —
{esc('redistribution permitted with attribution per the licence review' if any(r.get('review',{}).get('use')=='adapt-with-attribution' for r in c['repos']) else 'see per-source review blocks')}
. The review block records a human opening each licence file at the pin; detector output is a hint, never the verdict.</p></section>
{g7sec}{impsec}
</main>'''


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "assets").mkdir(exist_ok=True)
    (OUT / "corpus").mkdir(exist_ok=True)
    (OUT / "category").mkdir(exist_ok=True)
    (OUT / "assets" / "style.css").write_text(STYLE)
    corpora = load_registry()
    g7 = parse_waves_g7()
    graded = parse_graded_suites()
    impact, prov = load_impact()
    (OUT / "index.html").write_text(render_index(corpora, g7, graded, impact, prov))
    for c in corpora:
        (OUT / "corpus" / f"{c['slug']}.html").write_text(
            page(f"{c['slug']} — XERJ Corpus Hub", c["usecase"][:150], detail_body(c, g7, graded, impact, prov), rel="../"))
        (c.setdefault("_pages", []))
    for k, (title, what) in CATS.items():
        cs = [c for c in corpora if c["cat"] == k]
        cards = "\n".join(card(c, g7, graded, impact) for c in cs)
        body = f'''<main><p><a href="../index.html">← all corpora</a></p>
<h1>{esc(k)} — {esc(title)}</h1><p class="lead">{esc(what)} — "{esc(what)} ___". {len(cs)} entries.</p>
<div class="grid">{cards}</div></main>'''
        (OUT / "category" / f"{k}.html").write_text(page(f"{k} — {title} — Corpus Hub", what, body, rel="../"))
    about = '''<main><h1>About the hub</h1>
<p class="lead">The corpus hub is the public registry of reference corpora for XERJ's reference-coding
workflow — and for any agent that must answer from pinned, current, licenced text instead of memory.</p>
<section class="detail"><h2>Every corpus passes seven gates</h2>
<table>
<tr><th>gate</th><th>what satisfies it</th></tr>
<tr><td>G1 domain</td><td>the row finishes "an agent working on ___ would query this for ___" in one specific sentence</td></tr>
<tr><td>G2 shape</td><td>the retrieval unit matches the question: clause-level sections for standards, function bodies for code, advisory records for precedent</td></tr>
<tr><td>G3 un-memorisation</td><td>answer-bearing content is niche/internal/post-cutoff/too detailed for recall — famous-and-small is killed as retrieval theatre</td></tr>
<tr><td>G4 licence</td><td>a human opened the licence at the pin and wrote the review block; detector output is a hint, never the verdict</td></tr>
<tr><td>G5 pin truth</td><td>manifest SHA = the commit the build used; rewrites are withdrawals, not re-pins</td></tr>
<tr><td>G6 registry validation</td><td>validate_hub.py green in CI on the corpus-hub branch</td></tr>
<tr><td>G7 retrieval spot-check</td><td>5 pre-registered domain queries, top-5 graded manually; median ≥ 3 relevant to pass</td></tr>
</table></section>
<section class="detail"><h2>Scores on the cards</h2>
<p><b>G7</b> is the retrieval spot-check above. <b>A/B</b> is the drift-anchored agent benchmark:
the same task run bare (offline, instructed to answer honestly rather than fabricate) and with the corpus;
the badge shows pass counts (bare → corpus). A tie is a publishable outcome; the registry records it either way.</p></section>
<section class="detail"><h2>Rights policy</h2>
<p>Per-source licence verdicts live in each manifest's review block. PD (US government works), OGL v3.0 (UK),
and permissive licences ship as <code>adapt-with-attribution</code>; GPL/AGPL/SSPL and other restrictive
sources are <code>approach-only</code> — readable as design evidence, never copied. Closed families
(ICC I-codes, Eurocodes, NEC/ASTM/ISO/DIN) are documented as closed and excluded.</p></section>
<section class="detail"><h2>Contribute</h2>
<p>The registry is a git branch: <a href="https://github.com/xerj-org/xerj/tree/corpus-hub">corpus-hub</a>.
Read <a href="https://github.com/xerj-org/xerj/blob/corpus-hub/tools/xerj-code/hub/CONTRIBUTING.md">CONTRIBUTING.md</a>
and the program doc (PROGRAM-100.md). This site is generated from the registry by
<code>tools/corpus-hub-site/gen.py</code> — no hand-written corpus pages, ever.</p></section>
</main>'''
    (OUT / "about.html").write_text(page("About — XERJ Corpus Hub", "How corpora are gated, scored and licenced", about, rel=""))
    # llms.txt — agent-facing index of the registry, generated like everything
    # else. Short first screen (study rule 7), no obligation language, every
    # step verifiable; one block per live corpus with its consume command.
    live = [c for c in corpora if c["status"] == "live"]
    imp2, _ = load_impact()
    lines = [
        "# XERJ Corpus Hub",
        "",
        f"> Reference corpora for agents that must answer from pinned, current, licenced text",
        f"> rather than memory. {len(live)} live corpora, registry-of-record:",
        f"> https://github.com/xerj-org/xerj/tree/corpus-hub/tools/xerj-code/hub (this file's",
        f"> source; it wins on any conflict with this generated page). Last updated: 2026-10-02.",
        "",
        "Consume any corpus (no build step, binary indexes from the pinned manifest):",
        "",
        "    xerj corpus add --from https://raw.githubusercontent.com/xerj-org/xerj/corpus-hub/tools/xerj-code/hub/<slug>.json",
        "    xerj corpus index <slug>",
        '    xerj code <slug> "your question"',
        "",
        "Retrieval is lexical-by-default. Scores on the cards are measured, never",
        "asserted: G7 = 5 pre-registered queries graded on top-5; A/B = drift-anchored",
        "agent benchmark (bare pass count -> with-corpus pass count).",
        "",
        "## Live corpora",
        "",
    ]
    for c in live:
        files, nbytes, licences = corpus_stats(c)
        g = graded.get(c["slug"])
        g7s = f"G7 {g['median']:.1f}/5" if g else (f"G7 {g7[c['slug']]['median']:.1f}/5" if c["slug"] in g7 else "G7 not yet graded")
        ab = ""
        r = imp2.get(c["slug"])
        if r and r.get("status") == "measured":
            ab = f"; agent A/B {r['p']} -> {r['x']}"
        lines.append(f"- [{c['slug']}](https://hub.xerj.org/corpus/{c['slug']}): {c['cat']} · {'+'.join(licences) or '?'} · {human_bytes(nbytes)} · {g7s}{ab} — {c['usecase'][:160]}")
    lines += ["", f"## Also in the registry", "",
              f"- {len(corpora) - len(live)} rows in candidate/planned/deferred/killed states (visible,",
              "  not hidden: hub policy is honest statuses) — browse all: https://hub.xerj.org"]
    (OUT / "llms.txt").write_text("\n".join(lines) + "\n")
    n = len(list((OUT / "corpus").glob("*.html")))
    print(f"generated: index, about, {len(CATS)} category pages, {n} corpus pages -> {OUT}")


if __name__ == "__main__":
    main()
