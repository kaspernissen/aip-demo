"""Run the demo flow against the live cluster and record every step.

Browser steps: Playwright video, one file per page, with event timestamps so the
Large claim can be cut customer -> reviewer -> customer. Terminal steps: asciinema.
Writes work/raw, work/casts and work/events.json; terminal.py and the Remotion project turn them into the video.
"""
import json, os, re, shutil, subprocess, time, pathlib
from playwright.sync_api import sync_playwright

REPO = pathlib.Path(__file__).resolve().parents[3]
D = REPO / "talk" / "video" / "work"   # git-ignored
D.mkdir(exist_ok=True)
RAW = D / "raw"; CASTS = D / "casts"
for d in (RAW, CASTS):
    shutil.rmtree(d, ignore_errors=True); d.mkdir()
W, H = 1920, 1080
events = {}


def term(name, cmd):
    """Record one demo-driver command in a pty, shown as if typed at the prompt."""
    cast = CASTS / f"{name}.cast"
    shown = f"printf '\\033[1;32m$\\033[0m {cmd}\\n'; sleep 1; ./{cmd}; sleep 3"
    t0 = time.time()
    subprocess.run(["asciinema", "rec", "-q", "--overwrite", "--cols", "104", "--rows", "36",
                    "-c", f"bash -c \"{shown}\"", str(cast)], cwd=REPO, check=True,
                   env={**os.environ, "TERM": "xterm-256color"})
    events[name] = {"secs": round(time.time() - t0, 1)}
    return cast.read_text()


def mark(page_key, label, t0):
    events.setdefault(page_key, {})[label] = round(time.time() - t0, 2)


def main():
    term("00-start", "scripts/demo start")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H},
                                  record_video_dir=str(RAW), record_video_size={"width": W, "height": H},
                                  device_scale_factor=1)

        # customer page: Standard, Large (part 1), then Tricky; stays open throughout
        cust = ctx.new_page(); c0 = time.time()
        cust.goto("http://localhost:8080"); cust.wait_for_load_state("networkidle")
        cust.wait_for_timeout(2500)
        mark("customer", "standard_start", c0)
        cust.get_by_role("button", name="Standard claim").click(); cust.wait_for_timeout(1500)
        cust.get_by_role("button", name="Submit claim").click()
        cust.wait_for_selector("text=Settled", timeout=180_000)
        cust.wait_for_timeout(4000)
        mark("customer", "standard_end", c0)

        mark("customer", "large_start", c0)
        cust.get_by_role("button", name="Large claim").click(); cust.wait_for_timeout(1500)
        cust.get_by_role("button", name="Submit claim").click()
        cust.wait_for_selector("text=Awaiting human approval", timeout=180_000)
        cust.wait_for_timeout(4000)
        mark("customer", "large_waiting", c0)

        rev = ctx.new_page(); r0 = time.time()
        rev.goto("http://localhost:8081"); rev.wait_for_load_state("networkidle")
        rev.wait_for_timeout(1000)
        mark("reviewer", "start", r0)
        rev.reload(); rev.wait_for_selector("button.approve", timeout=30_000)
        rev.wait_for_timeout(4000)
        rev.locator("input[name=reviewer_id]").first.click()
        rev.locator("input[name=reviewer_id]").first.type("kasper.nissen", delay=90)
        rev.wait_for_timeout(1200)
        rev.locator("button.approve").first.click()
        rev.wait_for_timeout(3500)
        mark("reviewer", "end", r0)

        cust.bring_to_front()
        mark("customer", "large_resume", c0)
        cust.wait_for_selector("text=Settled", timeout=60_000)
        cust.wait_for_timeout(4000)
        mark("customer", "large_end", c0)

        mark("customer", "tricky_start", c0)
        cust.get_by_role("button", name="Tricky claim").click(); cust.wait_for_timeout(2500)
        cust.get_by_role("button", name="Submit claim").click()
        cust.wait_for_selector("text=Settled", timeout=180_000)
        cust.wait_for_timeout(4000)
        mark("customer", "tricky_end", c0)
        cust_video = cust.video; rev_video = rev.video
        cust.close(); rev.close()

        term("03b-inject", "scripts/demo inject")
        ev = term("04a-evidence", "scripts/demo evidence")

        # the Large claim's held submit trace: evidence links its approval, so ask Jaeger by claim id
        clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\]8;;[^\x07\x1b]*(\x07|\x1b\\\\)", "", ev)
        m = re.search(r"(CLM-INTAKE-[0-9A-F]+)\s+EUR 8,500", clean)
        events["large_claim"] = m.group(1) if m else None
        import urllib.request, urllib.parse
        q = urllib.parse.urlencode({"service": "customer-ui", "operation": "northstar.claim.submit",
                                    "tags": json.dumps({"northstar.claim.id": events["large_claim"]}),
                                    "limit": 5, "lookback": "2h"})
        tid = json.load(urllib.request.urlopen("http://localhost:16686/api/traces?" + q))["data"][0]["traceID"]
        events["large_trace"] = tid
        jag = ctx.new_page(); j0 = time.time()
        jag.goto(f"http://localhost:16686/trace/{tid}"); jag.wait_for_load_state("networkidle")
        jag.wait_for_timeout(4000)
        mark("jaeger", "loaded", j0)
        for label in ["chat claude-sonnet-5", "execute_tool execute_payment", "northstar.policy.decision"]:
            try:
                row = jag.locator(".endpoint-name", has_text=label).first
                for _ in range(40):
                    if row.count() and row.is_visible():
                        break
                    jag.mouse.move(240, 700); jag.mouse.wheel(0, 500); jag.wait_for_timeout(250)
                row.scroll_into_view_if_needed(); jag.wait_for_timeout(800)
                row.click(); jag.wait_for_timeout(1500)
                mark("jaeger", label, j0)
                jag.mouse.move(1200, 900); jag.mouse.wheel(0, 450); jag.wait_for_timeout(5000)
            except Exception as e:
                events.setdefault("jaeger_misses", []).append(f"{label}: {e!r}"[:200])
        mark("jaeger", "end", j0)
        jag_video = jag.video; jag.close()
        ctx.close(); browser.close()
        events["videos"] = {"customer": cust_video.path(), "reviewer": rev_video.path(), "jaeger": jag_video.path()}

    term("05-keystone", "scripts/demo keystone")
    (D / "events.json").write_text(json.dumps(events, indent=1))
    print(json.dumps(events, indent=1))


if __name__ == "__main__":
    try:
        main()
    finally:
        (D / "events.json").write_text(json.dumps(events, indent=1))
