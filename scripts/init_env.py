"""Create local DB credentials once, without printing the secret or enabling any LLM API."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    print("Existing .env preserved.")
else:
    path.write_text(
        "LLM_ENABLED=false\nNEO4J_URI=bolt://localhost:7687\nNEO4J_USERNAME=neo4j\nNEO4J_PASSWORD="
        + secrets.token_urlsafe(24) + "\n", encoding="utf-8"
    )
    print("Created .env with a generated Neo4j password. Secret not displayed.")
