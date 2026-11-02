"""Search the official MCP registry and turn entries into server configs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..tools.http import HttpClient
from .manager import MCPServerConfig

REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"


class RegistryEnvVar(BaseModel):
    name: str
    description: str = ""
    required: bool = False
    secret: bool = False


class RegistryServer(BaseModel):
    name: str
    description: str = ""
    version: str = ""
    repository: str | None = None
    packages: list[dict[str, Any]] = Field(default_factory=list)
    remotes: list[dict[str, Any]] = Field(default_factory=list)
    status: str | None = None

    def env_vars(self) -> list[RegistryEnvVar]:
        out = []
        for pkg in self.packages:
            for var in pkg.get("environmentVariables") or pkg.get("environment_variables") or []:
                out.append(RegistryEnvVar(name=var.get("name", ""), description=var.get("description", ""),
                                          required=bool(var.get("isRequired")), secret=bool(var.get("isSecret"))))
        return out


def parse_registry(data: dict[str, Any]) -> list[RegistryServer]:
    out = []
    for item in data.get("servers", []):
        server = item.get("server", item)
        meta = (item.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        repo = server.get("repository") or {}
        out.append(RegistryServer(
            name=server.get("name", ""), description=server.get("description", ""), version=server.get("version", ""),
            repository=repo.get("url") if isinstance(repo, dict) else None,
            packages=server.get("packages") or [], remotes=server.get("remotes") or [], status=meta.get("status"),
        ))
    return out


async def search_registry(http: HttpClient, query: str, limit: int = 30) -> list[RegistryServer]:
    resp = await http.get(REGISTRY, params={"search": query, "version": "latest", "limit": limit}, cache_ttl=3600)
    return [s for s in parse_registry(resp.json()) if s.status in (None, "active")]


def short_name(registry_name: str) -> str:
    return registry_name.rsplit("/", 1)[-1].replace(".", "-")[:40] or "server"


def config_from_registry(server: RegistryServer, prefer: str | None = None) -> MCPServerConfig:
    """pypi → uvx, npm → npx -y, oci → docker run, remote → URL."""
    # Only required variables become ${VAR} references: an unset optional one would expand to "" and override
    # the server's own default.
    env = {v.name: "${" + v.name + "}" for v in server.env_vars() if v.required}
    base = dict(name=short_name(server.name), description=server.description, origin=f"registry:{server.name}", env=env)
    packages = sorted(server.packages, key=lambda p: {"pypi": 0, "npm": 1, "oci": 2}.get(p.get("registryType", ""), 9))
    if prefer == "remote" or (not packages and server.remotes):
        remote = next((r for r in server.remotes if r.get("type") in ("streamable-http", "http")), None)
        if remote is not None:
            headers = {h.get("name"): "${" + h.get("name", "").upper().replace("-", "_") + "}"
                       for h in remote.get("headers") or [] if h.get("name")}
            return MCPServerConfig(**base, url=remote["url"], headers=headers)
    for pkg in packages:
        kind, ident, version = pkg.get("registryType"), pkg.get("identifier"), pkg.get("version")
        transport = (pkg.get("transport") or {}).get("type", "stdio")
        if transport != "stdio" or not ident:
            continue
        if kind == "pypi":
            return MCPServerConfig(**base, command="uvx", args=[f"{ident}=={version}" if version else ident])
        if kind == "npm":
            return MCPServerConfig(**base, command="npx", args=["-y", f"{ident}@{version}" if version else ident])
        if kind == "oci":
            return MCPServerConfig(**base, command="docker", args=["run", "-i", "--rm", ident])
    raise ValueError(f"{server.name} 没有可用的 stdio 包或远程地址")
