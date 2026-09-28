"""Server-side implementations for governed tools, one module per domain.

These functions receive a `ToolContext` holding an authenticated database user and, when the
caller is an AI Employee, that agent's row. They never accept role, department, or actor
identity from tool input.
"""
