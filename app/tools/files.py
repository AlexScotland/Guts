import os

from langchain_core.tools import tool


@tool
def write_file(path: str, content: str) -> str:
    """
    Write text content to a file on disk, creating parent directories if needed
    and overwriting any existing file. Use this to put generated code (a new
    tool, a script, a data file) onto disk so it can then be validated with
    validate_python and executed with run_python.

    'path' may be relative (resolved from the working directory) or absolute.
    'content' must be a string. Returns a short confirmation with the resolved
    absolute path and byte count, or an error string on failure. It does NOT
    run the file - use run_python for execution.
    """
    if not path or not str(path).strip():
        return "Error: 'path' is required."
    if not isinstance(content, str):
        return f"Error: 'content' must be a string, got {type(content).__name__}."

    target = os.path.abspath(os.path.expanduser(str(path)))
    try:
        d = os.path.dirname(target)
        if d:
            os.makedirs(d, exist_ok=True)
        blob = content.encode("utf-8")
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(blob)} bytes to {target}"
    except Exception as e:
        return f"Error writing {path}: {e}"