import os
from datetime import datetime

from langchain_core.tools import tool


@tool
def list_files(
    file_path: str = ".",
    file_extension: str = None,
    max_files: int = 100
) -> str:

    """
    Lists files recursively in a directory.
    """

    files = []

    for root, dirs, filenames in os.walk(file_path):

        for filename in filenames:

            if file_extension and not filename.endswith(file_extension):
                continue

            files.append(
                os.path.join(root, filename)
            )

            if len(files) >= max_files:
                return "\n".join(files) + "\n... truncated"

    return "\n".join(files)

@tool
def read_file(file_path: str) -> str:
    """Reads the content of a file."""
    with open(file_path, 'r') as file:
        return file.read()
