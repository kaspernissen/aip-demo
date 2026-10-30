"""Assemble prep.html from front.html, sections/NN.html and back.html, then print to PDF."""
import pathlib, subprocess, sys

D = pathlib.Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

parts = [(D / "head.html").read_text()]
if (D / "front.html").exists():
    parts.append((D / "front.html").read_text())
# NN.html per slide; NNa-*.html etc. sort straight after their slide
files = sorted((D / "sections").glob("*.html"))
have = {f.name[:2] for f in files}
missing = [n for n in range(1, 42) if f"{n:02d}" not in have]
for f in files:
    parts.append(f.read_text())
if (D / "back.html").exists():
    parts.append((D / "back.html").read_text())
parts.append("</body></html>")
(D / "prep.html").write_text("\n".join(parts))
if missing:
    print("missing sections:", missing)

out = D / "prep.pdf"
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={out}", f"file://{D / 'prep.html'}"],
               check=True, capture_output=True)
print("wrote", out)
