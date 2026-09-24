"""Load personal collection data from a gitignored JSON file.

comics-status.py and verify-comics-library.py used to hard-code a real collection:
ComicVine volume IDs, library folder names and per-series notes. That made two
otherwise reusable scripts carry personal data into a public repo. The values now
live in collection-config.json, which is gitignored, with the shape documented in
collection-config.example.json.

Missing config is not an error. A fresh clone has no collection, so the scripts run
with empty sets and say so once, rather than crashing or silently behaving as if
nothing is excluded.
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "collection-config.json")
_warned = False


def _load():
    global _warned
    if not os.path.exists(CONFIG_PATH):
        if not _warned:
            print(f"note: {os.path.basename(CONFIG_PATH)} not present; running with "
                  f"no collection exclusions. See collection-config.example.json.")
            _warned = True
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        print(f"warning: could not read {os.path.basename(CONFIG_PATH)}: {e}")
        return {}


def _strip_doc(obj):
    """Drop the self-documenting _doc/_comment keys so callers see only data."""
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [x for x in obj if not (isinstance(x, str) and x.startswith("_doc"))]
    return obj


def collections():
    """{group name: [ComicVine volume id, ...]} owned as collected editions."""
    return _strip_doc(_load().get("collections", {}))


def drift_ok():
    """{ComicVine volume id: reason} deliberately absent from the reading plan."""
    return _strip_doc(_load().get("drift_ok", {}))


def untracked_ok():
    """Set of library folder names the tracker deliberately does not follow."""
    return set(_strip_doc(_load().get("untracked_ok", [])))
