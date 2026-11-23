"""Write validated JSON to local files."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_json_file(path: Path, payload: dict[str, Any], reprocess: bool) -> bool:
    """Write a new JSON file or atomically replace it when requested."""
    data = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    if reprocess:
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError:
        return False
    try:
        with stream:
            stream.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return True
