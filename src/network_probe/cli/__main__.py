"""Allow `python -m network_probe.cli ...` to run the network-status CLI."""

from network_probe.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
