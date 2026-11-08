from pathlib import Path

from dotenv import load_dotenv

# sitesignal-backend/.env; variables already set in the shell take priority
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
