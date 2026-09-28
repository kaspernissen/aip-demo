/* Broadsheet page furniture for every slide.
 * Content slides get the folio (.page-foot): page number · the speaker's handles, at the
 * press furniture's 65% ink, bottom-right in the sheet margin. The number comes from the
 * CSS page counter, so it stays true when slides are inserted or reordered.
 * Opt out per slide with data-nochrome, or by class: cover, divider and bleed slides carry
 * their own furniture. Speaker handles come from data-handles on <deck-stage>.
 * Also handles ?slide=N deep links (used for review screenshots).
 */
const stage = document.querySelector('deck-stage');
const note = stage && stage.getAttribute('data-handles');
if (note) stage.style.setProperty('--page-foot-note', JSON.stringify(note));
for (const s of document.querySelectorAll('deck-stage > section')) {
  if (s.hasAttribute('data-nochrome')) continue;
  if (s.matches('.cover, .divider, .bleed')) continue;
  if (s.querySelector(':scope > .page-foot')) continue;
  const f = document.createElement('div');
  f.className = 'page-foot';
  f.setAttribute('aria-hidden', 'true');
  s.appendChild(f);
}
const want = parseInt(new URLSearchParams(location.search).get('slide'), 10);
if (Number.isFinite(want)) {
  requestAnimationFrame(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home' }));
    for (let n = 1; n < want; n++) window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
  });
}
