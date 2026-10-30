"""Turn the recorded asciinema casts into 1920x1080 clips paced to their narration.

The typed prompt keeps its real timing; the rest of the output streams in proportion to the
text printed and finishes 3.5 s before the narration does, so the final screen (the 403, the
Kyverno denial) holds while it is said. Needs agg and ffmpeg.
"""
import json, pathlib, subprocess

WORK = pathlib.Path(__file__).resolve().parents[1] / "work"
OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "clips"
# seconds of narration per terminal step (ffprobe of public/audio/n*.mp3)
TARGETS = {"00-start": 26.1, "03b-inject": 27.8, "04a-evidence": 20.3, "05-keystone": 23.0}

OUT.mkdir(parents=True, exist_ok=True)
(WORK / "term").mkdir(exist_ok=True)
for name, narr in TARGETS.items():
    lines = (WORK / "casts" / f"{name}.cast").read_text().splitlines()
    hdr = json.loads(lines[0]); ev = [json.loads(l) for l in lines[1:] if json.loads(l)[1] == "o"]
    hdr["height"] = 26
    head = [e for e in ev if e[0] <= 1.2]; rest = [e for e in ev if e[0] > 1.2]
    total = sum(len(d) for _, _, d in rest) or 1
    span = max(narr - 1.2 - 3.5, 1.0); acc = 0; out = [list(e) for e in head]
    for t, k, d in rest:
        acc += len(d); out.append([round(1.2 + span * acc / total, 4), k, d])
    cast = WORK / "term" / f"{name}.cast"; gif = WORK / "term" / f"{name}.gif"
    cast.write_text(json.dumps(hdr) + "\n" + "\n".join(json.dumps(e) for e in out) + "\n")
    subprocess.run(["agg", "--font-size", "28", "--idle-time-limit", "60", "--last-frame-duration", "3.6",
                    "--theme", "github-dark", str(cast), str(gif)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(gif), "-vf",
                    "scale=1860:1050:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,fps=30,format=yuv420p",
                    "-c:v", "libx264", "-crf", "18", str(OUT / f"{name}.mp4")], check=True)
    print(name)
