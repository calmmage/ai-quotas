"""Optional owner-machine integration; no import dependency for the public CLI."""
def resolve(name):
    try:
        from keys.compat import resolve as shared
    except ModuleNotFoundError as exc:
        if exc.name != "keys":
            raise
        import runpy
        from pathlib import Path
        shared = runpy.run_path(str(Path.home() / "calmmage/projects/meta/engine/lib/py/keys/compat.py"))["resolve"]
    return shared(name)
