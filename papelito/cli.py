"""papelito command-line entry point."""
import sys
import argparse

from .config import load_config
from .pipeline.runner import run


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="papelito",
        description="Scoped literature search: give it keywords/topics and a venue allow-list, "
                     "get back a readable Markdown report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a review from a config file")
    run_p.add_argument("config", help="Path to a YAML review config (see examples/example_config.yaml)")

    args = parser.parse_args(argv)

    if args.command == "run":
        config = load_config(args.config)
        run(config)


if __name__ == "__main__":
    sys.exit(main())
