import os

import requests
from langchain_core.tools import tool

# The SearXNG host endpoint is read from the environment, never hard-coded.
# Accept a couple of common names so the tool works regardless of which one
# the deployment sets.
_SEARXNG_ENV_VARS = ("SEARXNG_HOST", "SEARXNG_URL", "SEARXNG_ENDPOINT")

DEFAULT_TIMEOUT = 10  # seconds


def _get_searxng_host() -> str:
    """Resolve the SearXNG base URL from the environment.

    Returns the first non-empty value among the known env vars, with any
    trailing slash stripped. Raises a clear error if none is set.
    """
    for var in _SEARXNG_ENV_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip().rstrip("/")
    raise RuntimeError(
        "SearXNG host is not configured. Set one of the environment "
        f"variables: {', '.join(_SEARXNG_ENV_VARS)} "
        "(e.g. SEARXNG_HOST=https://searxng.example.com)."
    )


@tool
def web_search(query: str, max_results: int = 10) -> str:
    """
    Perform a web search via the SearXNG API and return the top results.

    The SearXNG host endpoint is read from an environment variable
    (SEARXNG_HOST, or SEARXNG_URL / SEARXNG_ENDPOINT as fallbacks) rather
    than being hard-coded.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return (default 10).

    Returns:
        A formatted list of results (title, url, snippet) or an error message.
    """
    if not query or not str(query).strip():
        return "Error: 'query' is required."

    try:
        host = _get_searxng_host()
    except RuntimeError as e:
        return f"Error: {e}"

    url = f"{host}/search"
    params = {
        "q": str(query).strip(),
        "format": "json",
        "language": "en",
    }

    try:
        response = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Error: request to SearXNG failed: {e}"
    except ValueError as e:
        return f"Error: could not parse SearXNG response as JSON: {e}"

    results = data.get("results", [])
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}"]
    for i, r in enumerate(results[:max_results], start=1):
        title = r.get("title", "(no title)")
        link = r.get("url", "")
        snippet = (r.get("content") or "").strip()
        lines.append(f"\n{i}. {title}\n   URL: {link}")
        if snippet:
            lines.append(f"   {snippet}")

    return "\n".join(lines)
