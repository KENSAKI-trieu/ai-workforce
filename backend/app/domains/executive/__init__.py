"""The CEO assistant: the overview, the decisions waiting, and the work handed out.

It owns no business tables. Every figure is read through the service of the department it
belongs to, so each one keeps that department's own permission filter: the assistant
never shows a director more than their position already lets them see.
"""
