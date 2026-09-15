"""
Client for the Fabric Livy API - execute Spark SQL against Lakehouse tables.

Usage:
    with LivyClient(workspace_id, lakehouse_id) as livy:
        livy.sql("CREATE TABLE my_table (id STRING, name STRING) USING DELTA")
        livy.sql("INSERT INTO my_table VALUES ('1', 'test')")
        result = livy.sql("SELECT * FROM my_table")
        print(result)

API docs: https://learn.microsoft.com/en-us/fabric/data-engineering/get-started-api-livy-session
"""
import json
import time
import requests
from config import FABRIC_API_BASE, FABRIC_WORKSPACE_ID
from fabric_http import json_headers, transport_retry


class LivyClient:

    def __init__(self, workspace_id: str = FABRIC_WORKSPACE_ID,
                 lakehouse_id: str = None):
        self.workspace_id = workspace_id
        self.lakehouse_id = lakehouse_id
        self.base_url = (
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}"
            f"/lakehouses/{lakehouse_id}/livyapi/versions/2023-12-01/sessions"
        )
        self.session_id = None
        self.session_url = None

    def __enter__(self):
        self.create_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_session()

    def create_session(self, poll_interval: int = 5):
        """Create a new Spark session and wait for it to become idle."""
        print("Creating Livy session...")
        resp = self._transport_retry("POST", self.base_url, json={})
        if resp.status_code not in (200, 202):
            raise RuntimeError(f"Failed to create session: {resp.status_code} {resp.text}")

        session = resp.json()
        self.session_id = session["id"]
        self.session_url = f"{self.base_url}/{self.session_id}"
        print(f"  Session {self.session_id} created (state: {session.get('state')})")

        self._wait_for_session_idle(poll_interval)
        return self.session_id

    def close_session(self):
        """Delete the current Spark session."""
        if not self.session_url:
            return
        print(f"Closing Livy session {self.session_id}...")
        resp = self._transport_retry("DELETE", self.session_url)
        print(f"  Session closed (status: {resp.status_code})")
        self.session_id = None
        self.session_url = None

    def sql(self, statement: str) -> str | None:
        """Execute a Spark SQL statement and return the text output."""
        return self.execute(f'spark.sql("{self._escape(statement)}").show()', kind="spark")

    def _transport_retry(self, method: str, url: str, *, attempts: int = 4, **kwargs):
        """`fabric_http.transport_retry` with this session's headers.

        A long-lived Livy session is a long-lived TCP conversation, and the path to the
        Fabric endpoint drops one intermittently: `ConnectionResetError(104)` mid-run, with
        no response ever received. Unretried, that single reset kills the statement and
        surfaces as a failure on a build that is perfectly fine. A response with a status
        line is never retried here except throttling — an HTTP error belongs to the caller.
        """
        try:
            return transport_retry(method, url, attempts=attempts,
                                   headers=self._headers(), **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"{method} {url.rsplit('/', 1)[-1]} failed after {attempts} attempt(s): "
                f"{type(exc).__name__}") from exc

    def execute(self, code: str, kind: str = "spark") -> str | None:
        """Submit arbitrary code and return the text output."""
        if not self.session_url:
            raise RuntimeError("No active session. Call create_session() first.")

        statements_url = f"{self.session_url}/statements"
        resp = self._transport_retry("POST", statements_url,
                                     json={"code": code, "kind": kind})

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to submit statement: {resp.status_code} {resp.text}")

        stmt = resp.json()
        stmt_id = stmt["id"]
        stmt_url = f"{statements_url}/{stmt_id}"

        # Poll until complete, bounded: a statement stuck outside a terminal state must
        # surface as an error, not hold the caller forever.
        deadline = time.time() + 3600
        while stmt.get("state") not in ("available", "error", "cancelled"):
            if time.time() > deadline:
                raise TimeoutError(f"Livy statement {stmt_id} did not finish within 3600s")
            time.sleep(3)
            stmt = self._transport_retry("GET", stmt_url).json()

        if stmt.get("state") == "error":
            error_info = stmt.get("output", {})
            raise RuntimeError(f"Statement failed: {json.dumps(error_info, indent=2)}")

        # Extract text output
        output = stmt.get("output", {})
        if output.get("status") == "error":
            raise RuntimeError(
                f"Spark error: {output.get('ename')}: {output.get('evalue')}"
            )

        data = output.get("data", {})
        return data.get("text/plain")

    def _headers(self) -> dict:
        return json_headers()

    def _wait_for_session_idle(self, poll_interval: int = 5, timeout: int = 900):
        """Poll until session state is 'idle' (bounded — a session that never starts must
        fail loudly, not hang)."""
        print("  Waiting for session to become idle...")
        deadline = time.time() + timeout
        while True:
            resp = self._transport_retry("GET", self.session_url)
            state = resp.json().get("state", "unknown")
            if state == "idle":
                print("  Session is idle and ready.")
                return
            if state in ("dead", "killed", "error"):
                raise RuntimeError(f"Session entered bad state: {state}")
            if time.time() > deadline:
                raise TimeoutError(f"Livy session {self.session_id} not idle after {timeout}s "
                                   f"(state: {state})")
            time.sleep(poll_interval)

    @staticmethod
    def _escape(s: str) -> str:
        """Escape a string for embedding in a Spark SQL call."""
        return s.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    print("livy_client — run Spark SQL against a Lakehouse over the Fabric Livy API.")
    print("Usage example in the module docstring; see 3_wiki/ontology/coding-guidance.md "
          "for the worked flow.")
