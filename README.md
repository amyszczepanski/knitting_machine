# Brother KH-930/940 Knitting Machine Disk Emulator

> **Work in progress** — this project is under active development and is not yet production-ready. Use with caution.

This project allows a computer to emulate the external floppy disk drive used by Brother KH-930/940 knitting machines (and similar models), communicating over a serial cable. Since these drives are rare, expensive, and use a physical disk format incompatible with standard PC drives, software emulation is a practical alternative.

The emulator presents itself to the knitting machine as a floppy drive, enabling saving and restoring of pattern data. Most of the saved data file format has been reverse-engineered, and the tools used in that process are also included here.

## Status

This is a work in progress. The core emulation and pattern format handling are functional, but the project is not yet production-ready.

## Background and Credits

This work builds on a lineage of open-source contributions:

- Original reverse-engineering and emulator work by **John R. Hogerhuis**
- Extended by **Steve Conklin**
- Further extended by **Becky Stern**, **Limor Fried**, **Travis Goodspeed**, and others

Relevant prior work and write-ups:
- http://blog.makezine.com/archive/2010/11/how-to_hacking_the_brother_kh-930e.html
- http://blog.craftzine.com/archive/2010/11/hack_your_knitting_machine.html
- http://travisgoodspeed.blogspot.com/2010/12/hacking-knitting-machines-keypad.html

This modernized version — including migration to a `uv`-managed Python package, type annotations, a FastAPI-based interface, and general code cleanup — was developed with the assistance of AI tools.

## Quick Start

```bash
uv run uvicorn app.api:app --reload
```

The application exposes a web API for uploading and downloading knitting patterns and interacting with the disk emulator.

## Repository Structure

- `app/` — main application package (emulator, format handling, API, image conversion)
- `docs/` — documentation

## Development

Install dependencies and run the type checker and linter:

```bash
uv sync --dev
uv run mypy app/
uv run flake8 app/
uv run black --check app/
```
