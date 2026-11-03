"""Write the OpenAPI document that the front end generates its API client from."""

import json
import sys
from pathlib import Path

from app.main import app


def main(target: Path) -> None:
    target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
