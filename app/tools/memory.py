from pathlib import Path

from langchain_core.tools import tool

MEMORY_DIR = Path("memory")


@tool
def search_memory(query: str) -> str:
    """
    Search all memory files for information matching the query.
    Returns matching files and their contents.
    """

    if not MEMORY_DIR.exists():
        return "No memory directory exists."

    terms = query.lower().split()

    matches = []

    for file in MEMORY_DIR.rglob("*.md"):
        content = file.read_text(
            encoding="utf-8"
        ).lower()

        score = sum(
            1 for term in terms
            if term in content or term in file.name.lower()
        )

        if score > 0:
            matches.append(
                (
                    score,
                    f"# {file.relative_to(MEMORY_DIR)}\n\n"
                    f"{content}"
                )
            )

    if not matches:
        return "No relevant memories found."

    # Highest matching files first
    matches.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return "\n\n---\n\n".join(
        result
        for score, result in matches
    )

@tool
def read_memory(path: str) -> str:
    """
    Read a memory markdown file.
    """

    file = MEMORY_DIR / path

    if not file.exists():
        return f"{path} does not exist."

    return file.read_text(encoding="utf-8")


@tool
def remember(
    category: str,
    title: str,
    content: str
) -> str:
    """
    Store an important long-term memory.

    Creates a new memory file for the category if one does not exist.
    Otherwise appends the memory to the existing file.

    Categories should describe the type of memory:
    - architecture
    - decisions
    - coding
    - preferences
    - project
    """

    # Sanitize category into a filename
    filename = category.lower().replace(" ", "_") + ".md"

    file = MEMORY_DIR / filename

    # Ensure memory directory exists
    file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Prevent path traversal
    resolved_file = file.resolve()
    resolved_root = MEMORY_DIR.resolve()

    if resolved_root not in resolved_file.parents:
        return "Invalid memory location."

    entry = f"""
    ## {title}

    {content.strip()}
    """

    if not file.exists():
        file.write_text(
            f"# {category.title()}\n{entry}",
            encoding="utf-8"
        )

        return f"Created memory file: {file}"

    # Avoid exact duplicate entries
    existing = file.read_text(
        encoding="utf-8"
    )

    if entry.strip() in existing:
        return "Memory already exists."

    with file.open(
        "a",
        encoding="utf-8"
    ) as f:
        f.write("\n" + entry.strip() + "\n")

    return f"Updated memory file: {file}"

@tool
def list_memory() -> str:
    """
    List available memory files.
    """

    if not MEMORY_DIR.exists():
        return "No memory directory."

    return "\n".join(
        str(f.relative_to(MEMORY_DIR))
        for f in MEMORY_DIR.rglob("*.md")
    )