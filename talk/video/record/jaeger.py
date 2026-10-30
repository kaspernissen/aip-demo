"""Record the Jaeger walk-through of the Large claim's held submit trace, one span at a time by span id."""
import json, time, pathlib, shutil, urllib.request
from playwright.sync_api import sync_playwright

D = pathlib.Path(__file__).resolve().parents[1] / "work"   # git-ignored; capture.py fills it first
OUT = D / "raw_jaeger"; shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir()
ev = json.loads((D / "events.json").read_text())
tid = ev["large_trace"]

# pick the spans the narration names, from Jaeger's own API
t = json.load(urllib.request.urlopen(f"http://localhost:16686/api/traces/{tid}"))["data"][0]
svc = {k: v["serviceName"] for k, v in t["processes"].items()}
def pick(service, op, **tags):
    for s in t["spans"]:
        tg = {x["key"]: x["value"] for x in s["tags"]}
        if svc[s["processID"]] == service and s["operationName"] == op and all(tg.get(k) == v for k, v in tags.items()):
            return s["spanID"]
    raise SystemExit(f"no span {service} {op} {tags}")
steps = [
    ("submit", pick("customer-ui", "northstar.claim.submit"), "details", 3000),
    ("chat", pick("claims-agent", "chat claude-sonnet-5"), "genai", 13000),
    ("execute", pick("settlement-agent", "execute_tool execute_payment"), "details", 7600),
    ("decision", pick("policy-decision-service", "northstar.policy.decision", **{"northstar.policy.decision": "deny"}), "details", 16300),
]
marks = {"steps": [s[:2] for s in steps]}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(OUT),
                        record_video_size={"width": 1920, "height": 1080})
    pg = ctx.new_page(); t0 = time.time()
    pg.goto(f"http://localhost:16686/trace/{tid}"); pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(4000); marks["loaded"] = round(time.time() - t0, 2)
    find = pg.get_by_placeholder("Find...")
    for key, span_id, tab, hold in steps:
        find.click(); find.fill(""); find.type(span_id, delay=30); pg.wait_for_timeout(600)
        pg.mouse.click(1614, 75); pg.wait_for_timeout(1000)        # scroll to the match
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        top = pg.evaluate("() => document.querySelector('.span-name-wrapper.is-matching-filter').getBoundingClientRect().top")
        pg.evaluate(f"window.scrollBy({{top: {top - 300}, behavior: 'smooth'}})"); pg.wait_for_timeout(1200)
        for _ in range(4):   # the toggle is flaky: click until a detail row is really open
            pg.locator(".span-name-wrapper.is-matching-filter .span-name").first.click(force=True)
            pg.wait_for_timeout(1300)
            if pg.locator(".detail-row").count():
                break
        marks.setdefault("opened", {})[key] = pg.locator(".detail-row").count()
        detail = pg.locator(".detail-row").first
        if tab == "details":
            for _ in range(3):
                hdr = detail.locator(".AccordionAttributes--header[aria-checked=false]", has_text="Tags:").first
                if not hdr.count():
                    break
                hdr.click(force=True); pg.wait_for_timeout(900)
            marks.setdefault("tags_open", {})[key] = detail.locator(".AccordionAttributes--header[aria-checked=true]").count()
        # bring the open detail to the upper half of the screen
        top = pg.evaluate("() => { const d = document.querySelector('.detail-row'); return d ? d.getBoundingClientRect().top : 300 }")
        pg.evaluate(f"window.scrollBy({{top: {top - 330}, behavior: 'smooth'}})"); pg.wait_for_timeout(1200)
        marks[key] = round(time.time() - t0, 2)
        pg.wait_for_timeout(hold)
        pg.screenshot(path=str(D / f"jaeger-{key}.png"))
        for _ in range(4):   # close it again
            if not pg.locator(".detail-row").count():
                break
            pg.locator(".span-name-wrapper.is-matching-filter .span-name").first.click(force=True)
            pg.wait_for_timeout(700)
    find.fill(""); pg.wait_for_timeout(500)
    marks["end"] = round(time.time() - t0, 2)
    video = pg.video; pg.close(); ctx.close(); b.close()
    marks["video"] = video.path()

ev["jaeger2"] = marks
(D / "events.json").write_text(json.dumps(ev, indent=1))
print(json.dumps(marks, indent=1))
