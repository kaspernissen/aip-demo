# The speaker prep book

One section per slide of the deck (`sections/NN.html`), with the demo walkthrough in
`sections/23a-*` to `23e-*`, between the front matter (`front.html`: how to use it, the run of
show, the storyline threads) and the glossary (`back.html`). The last build is
`talk/export/speaker-prep-book.pdf`.

## Build

The slide images are renders of the deck's PDF export (Google Slides), not committed:

```bash
cd talk/prep
pdftoppm -r 144 -png "<the deck>.pdf" img/s      # img/s-01.png … img/s-41.png
python3 build.py                                 # prep.html, then prep.pdf through headless Chrome
```

`img/demo-*.png` are the walkthrough screenshots and are committed. When the deck changes, re-render
the slide images and check that each `sections/NN.html` still matches its slide.
