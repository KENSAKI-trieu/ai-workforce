"""Tools the Marketing agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# `start_marketing_campaign` runs the backend's campaign pipeline up to the outline and
# ends the turn with a link to it: the author reviews every stage on the Marketing page.
# `web_search` reads the public web through Google Search; its results are outside data.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "web_search",
    "start_marketing_campaign",
)
