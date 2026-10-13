"""The Extension Desk MCP server (stdio).

    uv run extension-desk-mcp
    npx @modelcontextprotocol/inspector uv run extension-desk-mcp

Read tools return records plus their `citations`. Acting tools only queue
items for officer review; none of them sends, orders or flags anything
outside the queue, and none can approve its own output.
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from extension_desk.data import DataStore
from extension_desk.mcp_server import tools
from extension_desk.mcp_server.tools import Recommendation
from extension_desk.review_queue import ReviewQueue

INSTRUCTIONS = """Extension Desk: data and drafting tools for an agricultural extension office
in Zimbabwe (pilot districts: chiredzi, mangwe).

Every claim you make about a household must cite record IDs returned by the read tools.
Acting tools (draft_advisory, flag_for_followup, schedule_followup) never contact anyone:
they put an item in the officer review queue, and they reject any recommendation, flag or
visit whose citations are missing, unknown, or about a different household or district."""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
QUEUE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                        openWorldHint=False)


def build_server(store: DataStore | None = None, queue: ReviewQueue | None = None) -> FastMCP:
    store = store or DataStore.load()
    queue = queue or ReviewQueue()
    mcp = FastMCP("extension-desk", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ)
    def get_rainfall(district: str, start: str = "", end: str = "") -> dict:
        """Dekadal (10-day) CHIRPS rainfall for a district against its 1991-2020 normal:
        period total, normal, departure in mm, anomaly % (null when the normal is under 5 mm),
        and each dekad's record. `start`/`end` are YYYY-MM or YYYY-MM-DD; with neither, the
        latest 6 dekads. `includes_preliminary` is true when recent dekads are preliminary data."""
        return tools.get_rainfall(store, district, start, end)

    @mcp.tool(annotations=READ)
    def get_prices(district: str, crop: str = "", market: str = "", since: str = "") -> dict:
        """Monthly retail prices (USD/kg) for a district's markets, grouped into series by market
        and commodity, with first and latest price and % change. `crop` matches by name (e.g.
        "sorghum", "maize" matches maize grain and maize meal). `since` is YYYY-MM or YYYY-MM-DD.
        Some markets are neighbouring-district proxies; each point's `source` says where it is from."""
        return tools.get_prices(store, district, crop, market, since)

    @mcp.tool(annotations=READ)
    def get_plot_records(household_id: str = "", district: str = "") -> dict:
        """Households with their plots (crop, maturity, area, soil, irrigation, planting date).
        Give exactly one of `household_id` (e.g. "hh:001") or `district`. All data is synthetic."""
        return tools.get_plot_records(store, household_id, district)

    @mcp.tool(annotations=READ)
    def get_pest_alerts(district: str, since: str = "") -> dict:
        """Pest alerts for a district, newest first: pest, crops affected, severity, date."""
        return tools.get_pest_alerts(store, district, since)

    @mcp.tool(annotations=QUEUE)
    def draft_advisory(household_id: str, recommendations: list[Recommendation]) -> dict:
        """Queue a DRAFT advisory for officer review. Nothing is sent. Each recommendation needs
        at least one citation: a record ID for this household, one of its plots, or rainfall,
        price or pest records for its district. If any recommendation fails, nothing is queued."""
        return tools.draft_advisory(store, queue, household_id, recommendations)

    @mcp.tool(annotations=QUEUE)
    def flag_for_followup(household_id: str, reason: str, citations: list[str]) -> dict:
        """Queue a flag asking an officer to follow up with a household. Nothing is sent and no
        credit or other flag is raised outside the queue. Needs at least one valid citation."""
        return tools.flag_for_followup(store, queue, household_id, reason, citations)

    @mcp.tool(annotations=QUEUE)
    def schedule_followup(household_id: str, visit_date: str, reason: str,
                          citations: list[str]) -> dict:
        """Propose an officer visit on `visit_date` (YYYY-MM-DD, today or later). The visit is
        queued for approval, not booked. Needs at least one valid citation."""
        return tools.schedule_followup(store, queue, household_id, visit_date, reason, citations)

    return mcp


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
