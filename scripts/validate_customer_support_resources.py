from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commerce_support.resources.validation import validate_support_resource_package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the packaged customer-support knowledge and evaluation resources."
    )
    parser.add_argument(
        "--package",
        type=Path,
        help="Resource package directory (defaults to the installed customer-support/v1 package).",
    )
    args = parser.parse_args()
    errors = validate_support_resource_package(args.package)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"Resource validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Customer-support resource package validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
