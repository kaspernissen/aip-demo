# Generates the animated MCP / A2A SVG in slides/10-open-act1.html ("Meet MCP and A2A"; SMIL, loops).
# Usage, from talk/:  python3 build/gen_proto_svg.py > /tmp/proto.svg, then paste it over <svg class="x10-pa">.
E = 0.001

def win(a, b, T):
    """keyTimes/values for a packet visible and moving only inside [a,b] of a T-second cycle."""
    ka, kb = a / T, b / T
    return ka, kb

def packet(cls, label, x0, y0, x1, y1, a, b, T, w, pos="right"):
    ka, kb = win(a, b, T)
    kt = f"0;{ka:.4f};{kb:.4f};1"
    tr = f"{x0} {y0};{x0} {y0};{x1} {y1};{x1} {y1}"
    okt = f"0;{max(ka-E,0):.4f};{ka:.4f};{kb:.4f};{min(kb+E,1):.4f};1"
    return (f'<g class="pk {cls}" opacity="0">'
            f'<animateTransform attributeName="transform" type="translate" dur="{T}s" repeatCount="indefinite" calcMode="linear" keyTimes="{kt}" values="{tr}"/>'
            f'<animate attributeName="opacity" dur="{T}s" repeatCount="indefinite" keyTimes="{okt}" values="0;0;1;1;0;0"/>'
            f'<circle r="11" cx="0" cy="0"/>'
            + (f'<rect x="22" y="-22" width="{w}" height="44" rx="10"/><text x="{22 + w/2}" y="8" text-anchor="middle">{label}</text></g>' if pos == "right" else
               f'<rect x="{-w/2}" y="{-74 if pos == "above" else 26}" width="{w}" height="44" rx="10"/><text x="0" y="{-44 if pos == "above" else 56}" text-anchor="middle">{label}</text></g>'))

def state(label, x, y, a, b, T, cls, w=230):
    ka, kb = a / T, b / T
    okt = f"0;{max(ka-E,0):.4f};{ka:.4f};{kb:.4f};{min(kb+E,1):.4f};1"
    return (f'<g class="st {cls}" opacity="0"><animate attributeName="opacity" dur="{T}s" repeatCount="indefinite" keyTimes="{okt}" values="0;0;1;1;0;0"/>'
            f'<rect x="{x - w/2}" y="{y}" width="{w}" height="46" rx="23"/><text x="{x}" y="{y+31}" text-anchor="middle">{label}</text></g>')

T1 = 8.0
mcp = [
    '<text class="hd" x="0" y="34">MCP</text><text class="sb" x="0" y="70">reaches down: agent &#8594; tool</text>',
    '<rect class="nd m" x="0" y="110" width="300" height="110" rx="20"/><text class="nt" x="150" y="175" text-anchor="middle">agent</text>',
    '<rect class="nd m" x="0" y="430" width="300" height="110" rx="20"/><text class="nt" x="150" y="485" text-anchor="middle">tool server</text><text class="ns" x="150" y="516" text-anchor="middle">payments</text>',
    '<path class="ln m" d="M120 220 V430"/><path class="ln m" d="M180 430 V220"/>',
    packet("m", "tools/list", 120, 232, 120, 418, 0.3, 1.6, T1, 210),
    packet("m back", "execute_payment &#183; get_payment &#183; &#8230;", 180, 418, 180, 232, 1.9, 3.3, T1, 470),
    packet("m", "tools/call execute_payment &#183; 5800", 120, 232, 120, 418, 3.7, 5.2, T1, 470),
    packet("m back", "result", 180, 418, 180, 232, 5.5, 6.8, T1, 130),
]
T2 = 10.0
ax, bx, y = 840, 1372, 300
a2a = [
    f'<text class="hd" x="{ax}" y="34">A2A</text><text class="sb" x="{ax}" y="70">reaches across: agent &#8596; agent</text>',
    f'<rect class="nd a" x="{ax}" y="{y}" width="260" height="110" rx="20"/><text class="nt" x="{ax+130}" y="{y+65}" text-anchor="middle">claims agent</text>',
    f'<rect class="nd a" x="{bx}" y="{y}" width="260" height="110" rx="20"/><text class="nt" x="{bx+130}" y="{y+65}" text-anchor="middle">settlement agent</text>',
    # the agent card, pinned above the settlement agent
    f'<g class="card"><rect x="{bx+50}" y="130" width="160" height="104" rx="12"/><text class="ct" x="{bx+130}" y="168" text-anchor="middle">agent card</text><text class="cs" x="{bx+130}" y="198" text-anchor="middle">name &#183; skills</text><text class="cs" x="{bx+130}" y="222" text-anchor="middle">where to reach it</text></g>',
    f'<path class="ln a dash" d="M{bx+130} 234 V{y}"/>',
    f'<path class="ln a" d="M{ax+260} {y+38} H{bx}"/><path class="ln a" d="M{bx} {y+76} H{ax+260}"/>',
    packet("a", "read the card", ax+130, y-12, bx+50-14, 182, 0.3, 1.6, T2, 190, "above"),
    packet("a", "task: settle this claim", ax+270, y+38, bx-10, y+38, 1.9, 3.4, T2, 330, "above"),
    state("working", bx+130, y+138, 3.4, 5.0, T2, "w"),
    state("input-required", bx+130, y+138, 5.0, 6.8, T2, "i"),
    state("completed", bx+130, y+138, 6.8, 9.6, T2, "c"),
    packet("a back", "push: completed", bx-10, y+76, ax+270, y+76, 7.0, 8.6, T2, 230, "below"),
]
svg = ('<svg class="x10-pa" viewBox="0 -24 1632 584" role="img" aria-label="Animated. MCP: an agent asks a tool server for its tools, gets the list, calls execute_payment with an amount, gets a result. A2A: a claims agent reads the settlement agent\'s card, sends it a task; the task goes working, input-required, completed, and a push notification comes back.">'
       '<g font-family="var(--font-body)">' + "".join(mcp) + '<line class="sep" x1="800" y1="20" x2="800" y2="540"/>' + "".join(a2a) + '</g></svg>')
print(svg)
