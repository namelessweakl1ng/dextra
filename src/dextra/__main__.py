"""Allow `python -m dextra` to invoke the CLI."""

from dextra.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
