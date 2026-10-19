"""Dotted QR as SVG: data modules as dots, the three finder patterns as rounded squares."""
import sys, segno
def svg(data, ink="#0A1128"):
    q = segno.make(data, error="h", micro=False)
    m = [list(r) for r in q.matrix]; n = len(m)
    fs = [(0, 0), (n - 7, 0), (0, n - 7)]
    def in_finder(r, c): return any(fr <= r < fr + 7 and fc <= c < fc + 7 for fr, fc in fs)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="geometricPrecision">']
    for r in range(n):
        for c in range(n):
            if m[r][c] and not in_finder(r, c):
                out.append(f'<circle cx="{c+.5}" cy="{r+.5}" r=".44" fill="{ink}"/>')
    for fr, fc in fs:
        out.append(f'<rect x="{fc+.5}" y="{fr+.5}" width="6" height="6" rx="1.6" fill="none" stroke="{ink}" stroke-width="1"/>')
        out.append(f'<rect x="{fc+2}" y="{fr+2}" width="3" height="3" rx=".9" fill="{ink}"/>')
    out.append('</svg>')
    return "\n".join(out), q.version
if __name__ == "__main__":
    s, v = svg(sys.argv[1]); open(sys.argv[2], "w").write(s); print(sys.argv[2], "version", v)
