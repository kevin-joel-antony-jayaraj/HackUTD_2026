"""SiteSignal safety pipeline."""
import os


def _load_dotenv() -> None:
    """Read safety-pipeline/.env (git-ignored) so the server's key is picked up without exporting it.
    Values already set in the environment win. The key stays on the server; it is never sent to browsers."""
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

