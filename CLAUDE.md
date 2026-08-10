# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository currently is

An unmodified clone of the [ContextLab LaTeX template](https://github.com/ContextLab/latex-base) (`README.md:3`), instantiated for a project named `risk-analysis`. Every file is still placeholder content: `paper/main.tex` is titled "Template paper" with "Insert abstract here", the only notebook (`code/notebooks/demo.ipynb`) plots a sine and cosine wave, and `data/raw` and `data/processed` contain only READMEs.

Treat the existing files as scaffolding to be replaced, not as prior work to preserve. The `README.md` still documents the template itself; the section after the `***` divider (line 23) is the suggested boilerplate to rewrite as the actual project README.

**The `paper/CDL-bibliography` submodule is not yet initialized** (`git submodule status` shows a leading `-`). LaTeX compilation will fail on `\bibliography{CDL-bibliography/cdl}` until `setup.sh` is run.

## Commands

```bash
sh setup.sh                  # init the CDL-bibliography submodule and check out its master branch
cd paper && sh compile.sh    # build main.pdf and supplement.pdf, then delete aux files
cd paper/admin && sh compile.sh   # build cover_letter.pdf
```

Both `compile.sh` scripts **must be run from their own directory** — they reference `figs/` relatively and end with `rm *.aux *.log ...` globs that would delete files elsewhere in the tree.

`paper/compile.sh` runs `latex` five times plus `bibtex` before the final `pdflatex`; this is deliberate, to settle `\ref`/`\cite` cross-references and line numbers. Don't shorten it to a single `pdflatex` pass.

Docker (see `README.md:45-63` for the full walkthrough):

```bash
docker build -t cdl .
docker run -it -p 9999:9999 --name cdl -v $PWD:/mnt cdl    # first time; repo mounts at /mnt
docker start --attach cdl                                   # subsequent times
jupyter notebook --port=9999 --no-browser --ip=0.0.0.0 --allow-root   # from inside the container
```

The image pins Python 3.7 with conda packages at exact versions (`Dockerfile:3-12`). Adding a dependency means editing the `Dockerfile` and rebuilding — there is no `requirements.txt` or environment file.

## Figure pipeline

Figures are assembled in two stages, and this is the part most likely to be done wrong:

1. Notebooks in `code/notebooks/` generate **individual panels** as transparent PDFs written to `paper/figs/source/` (e.g. `sin.pdf`, `cos.pdf`). Notebooks locate that directory relative to their own path — `Path(os.path.dirname(os.path.realpath("__file__"))).parent.parent / 'paper' / 'figs' / 'source'` — so they only resolve correctly when run from `code/notebooks/`.
2. `paper/figs/<name>.pdf` is a **composite** built in Adobe Illustrator that *links* to the source panels rather than embedding them. Regenerating a panel via its notebook updates the composite automatically; hand-editing a composite breaks that link.

`main.tex` includes the composite (`\includegraphics{figs/trig}`), never the panels directly. To change a figure, re-run the notebook that produces its panels — do not regenerate `figs/trig.pdf` from scratch.

`code/README.md` maps each notebook to the figure it produces; keep that mapping current when adding notebooks.

## Supplement cross-referencing

`main.tex` and `supplement.tex` are compiled as separate documents, so `\ref` cannot cross between them. The template handles this with hardcoded macros defined in both files:

- `main.tex:15` — `\newcommand{\demo}{S1}` (how the main text refers to the supplementary figure)
- `supplement.tex` — `\newcommand{\demo}{1}` (its own number, prefixed to `S1` by `\renewcommand{\thefigure}{S\arabic{figure}}`)

Add one such macro pair per supplementary figure, and update both files together whenever supplement figure order changes. `supplement.tex` also resets equation/figure/table/section/page counters and renames `\figurename` to "Supplementary Figure".

## Citations

Cite from the `CDL-bibliography` submodule (`paper/CDL-bibliography/cdl.bib`) rather than adding entries to a local `.bib` file. New references belong upstream in [ContextLab/CDL-bibliography](https://github.com/ContextLab/CDL-bibliography); the submodule tracks `master`.

`main.tex` uses `natbib` with `[sort&compress, numbers, super]` (superscript numeric citations); `supplement.tex` uses plain `natbib`. Both use `\bibliographystyle{apa}`.
