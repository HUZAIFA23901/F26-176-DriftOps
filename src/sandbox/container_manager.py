"""Ephemeral Docker Sandbox Manager for DriftOps.

Provisions lightweight, isolated PostgreSQL containers on dynamic ports for
fast verification and dbt testing of candidate AST patches without polluting
staging or production environments.

Project ID: F26-176-DriftOps
Module: src/sandbox/container_manager.py
"""

import os
import socket
import time
import uuid
from typing import Any, Dict, Optional

import docker
from docker.errors import DockerException
import psycopg2

from src.config import get_settings


def get_free_port() -> int:
    """Find an available ephemeral port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


class SandboxContainer:
    """Ephemeral Docker container manager for PostgreSQL shadow databases."""

    def __init__(
        self,
        image_name: str = "postgres:15-alpine",
        db_name: str = "shadow_db",
        user: str = "postgres",
        password: str = "postgres",
    ) -> None:
        """Initialize Docker client and container configuration."""
        self.settings = get_settings()
        self.image_name = image_name or self.settings.SANDBOX_POSTGRES_IMAGE
        self.db_name = db_name
        self.user = user
        self.password = password
        self.container_name = f"driftops_sandbox_{uuid.uuid4().hex[:8]}"

        self.container: Optional[Any] = None
        self.host_port: Optional[int] = None
        self.connection_params: Optional[Dict[str, Any]] = None

        try:
            self.client = docker.from_env()
        except Exception as exc:
            raise DockerException(
                f"Failed to initialize Docker client. Verify Docker daemon is running: {exc}"
            ) from exc

    def start(self, timeout_seconds: int = 25) -> Dict[str, Any]:
        """Spin up an ephemeral Postgres container and wait for readiness.

        Args:
            timeout_seconds: Maximum seconds to wait for database connection readiness.

        Returns:
            Dictionary with connection parameters (host, port, user, password, dbname).

        Raises:
            TimeoutError: If the shadow database fails to become ready within timeout.
            DockerException: If container creation fails.
        """
        self.host_port = get_free_port()

        environment = {
            "POSTGRES_USER": self.user,
            "POSTGRES_PASSWORD": self.password,
            "POSTGRES_DB": self.db_name,
        }

        try:
            self.container = self.client.containers.run(
                self.image_name,
                name=self.container_name,
                detach=True,
                ports={"5432/tcp": self.host_port},
                environment=environment,
                remove=False,
            )
        except Exception as exc:
            self.stop_and_cleanup()
            raise DockerException(f"Failed to start sandbox container: {exc}") from exc

        # Healthcheck readiness loop via psycopg2
        start_time = time.time()
        ready = False
        while time.time() - start_time < timeout_seconds:
            try:
                conn = psycopg2.connect(
                    host="localhost",
                    port=self.host_port,
                    user=self.user,
                    password=self.password,
                    dbname=self.db_name,
                    connect_timeout=2,
                )
                conn.close()
                ready = True
                break
            except Exception:
                time.sleep(0.4)

        if not ready:
            self.stop_and_cleanup()
            raise TimeoutError(
                f"Sandbox PostgreSQL container failed to become ready on port {self.host_port} "
                f"within {timeout_seconds} seconds."
            )

        self.connection_params = {
            "host": "localhost",
            "port": self.host_port,
            "user": self.user,
            "password": self.password,
            "dbname": self.db_name,
        }
        return self.connection_params

    def seed_schema_and_patch(
        self,
        init_sql_path: str,
        patched_sql: str,
        model_name: str,
    ) -> None:
        """Seed initial raw schema and apply candidate SQL patch into shadow database.

        Args:
            init_sql_path: Path to database bootstrap script (e.g. testbed/init.sql).
            patched_sql: Candidate query patch to deploy.
            model_name: dbt model identifier (e.g. "stg_customers").

        Raises:
            RuntimeError: If container is not running.
            psycopg2.Error: If SQL execution fails.
        """
        if not self.connection_params:
            raise RuntimeError("Sandbox container is not running. Call start() first.")

        target_relation = model_name if "." in model_name else f"analytics.{model_name}"
        clean_patch = patched_sql.strip().rstrip(";")

        with psycopg2.connect(
            host=self.connection_params["host"],
            port=self.connection_params["port"],
            user=self.connection_params["user"],
            password=self.connection_params["password"],
            dbname=self.connection_params["dbname"],
        ) as conn:
            with conn.cursor() as cur:
                # 1. Execute initial bootstrap schema if provided
                if init_sql_path and os.path.exists(init_sql_path):
                    with open(init_sql_path, "r", encoding="utf-8") as f:
                        cur.execute(f.read())

                # 2. Ensure raw and analytics schemas exist
                cur.execute("CREATE SCHEMA IF NOT EXISTS raw;")
                cur.execute("CREATE SCHEMA IF NOT EXISTS analytics;")

                # 3. Deploy candidate patch as a view
                cur.execute(f"DROP VIEW IF EXISTS {target_relation} CASCADE;")
                cur.execute(f"DROP TABLE IF EXISTS {target_relation} CASCADE;")
                cur.execute(f"CREATE VIEW {target_relation} AS {clean_patch};")

            conn.commit()

    def stop_and_cleanup(self) -> None:
        """Forcibly stop and remove shadow container and associated volumes."""
        if self.container is not None:
            try:
                self.container.stop(timeout=2)
            except Exception:
                pass
            try:
                self.container.remove(v=True, force=True)
            except Exception:
                pass
            self.container = None

        self.connection_params = None

    def __enter__(self) -> "SandboxContainer":
        """Enter context manager, starting the container."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager, stopping and cleaning up container."""
        self.stop_and_cleanup()
