/* Parses mermaid diagrams with the same library that will draw them.
 *
 * Reads `[{"id": "...", "source": "..."}]` on stdin, writes
 * `[{"id": "...", "error": null | "..."}]` on stdout. The caller is
 * tests/unit/test_mermaid_diagrams.py, which collects every diagram in the
 * tree and reports the failures.
 *
 * Why a node script at all, when every other guard in tests/unit is pure
 * Python: a mermaid diagram is only valid if mermaid says so. Reimplementing
 * the grammar in Python would produce a checker that disagrees with the
 * renderer, which is the shape of validation this repo has already been
 * bitten by -- `test_catalog_links_are_absolute_urls.py` exists because
 * PyYAML accepted a file Backstage rejected, and a hand-rolled mermaid
 * checker would be the same mistake with a different parser.
 *
 * jsdom because mermaid needs a DOM even to parse: `mermaid.parse` reaches
 * DOMPurify, which reaches `window`. It is a devDependency of this package
 * and is not in the app bundle.
 */
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const mermaid = (await import('mermaid')).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });

const read = async stream => {
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
};

const diagrams = JSON.parse(await read(process.stdin));
const results = [];

for (const { id, source } of diagrams) {
  try {
    await mermaid.parse(source);
    results.push({ id, error: null });
  } catch (e) {
    results.push({ id, error: String(e?.message ?? e).split('\n').slice(0, 4).join(' | ') });
  }
}

process.stdout.write(JSON.stringify(results));
