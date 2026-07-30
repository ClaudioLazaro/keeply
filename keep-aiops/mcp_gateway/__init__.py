"""MCP Gateway — policy-enforcing tool bus for agent runtimes (ADR-0002).

Runs as a separate FastAPI process so the tool mesh is a real security
boundary: agents never touch provider SDKs in-process.
"""
