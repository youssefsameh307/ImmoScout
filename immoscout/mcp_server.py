"""Model Context Protocol (MCP) server for ImmobilienScout24.

Exposes ImmoScout search as tools any MCP client (Claude Desktop, Claude Code,
Cursor, …) can call natively, so an AI agent can look up real estate for you.

Run it:

    pip install "immoscout[mcp]"
    immoscout-mcp

Or wire it into Claude Desktop's ``mcpServers`` config with command ``immoscout-mcp``.
"""
from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]

import anyio

from . import apply as apply_mod
from .client import ImmoscoutClient
from .filters import SearchFilter

_INSTALL_HINT = (
    "The MCP server needs the 'mcp' extra. Install it with:\n"
    '    pip install "immoscout[mcp]"'
)


def _build_server() -> FastMCP:
    mcp = FastMCP("immoscout")
    client = ImmoscoutClient()

    @mcp.tool()
    def search_listings(
        region: str = "/de/berlin/berlin",
        real_estate_type: str = "apartmentrent",
        price_min: float | None = None,
        price_max: float | None = None,
        rooms_min: float | None = None,
        rooms_max: float | None = None,
        living_space_min: float | None = None,
        max_results: int = 20,
    ) -> dict:
        """Search ImmobilienScout24 real-estate listings.

        Args:
            region: ImmoScout geocode path, e.g. "/de/berlin/berlin" or "/de/bayern/muenchen".
            real_estate_type: apartmentrent, apartmentbuy, houserent, housebuy, flatshareroom.
            price_min / price_max: monthly rent (or purchase price) bounds in EUR.
            rooms_min / rooms_max: number of rooms.
            living_space_min: minimum living space in m².
            max_results: how many listings to return (paginates as needed).
        """
        query = SearchFilter(
            region=region,
            real_estate_type=real_estate_type,
            price_min=price_min,
            price_max=price_max,
            rooms_min=rooms_min,
            rooms_max=rooms_max,
            living_space_min=living_space_min,
        )
        first = client.search(query)
        listings = []
        for listing in client.search_all(query):
            listings.append(listing.model_dump(exclude={"raw"}))
            if len(listings) >= max_results:
                break
        return {"total_results": first.total_results, "returned": len(listings), "listings": listings}

    @mcp.tool()
    async def apply_to_listing(
        expose_id: str,
        message: str,
        fields: dict[str, str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """Contact the landlord of a listing via its ImmoScout contact form.

        Without ``confirm`` it only fills the form and returns the values plus a
        screenshot path — nothing is sent. Show that to the user and only call again
        with ``confirm=True`` after they approve this specific message.

        Name, e-mail, phone, address, household, job and income are prefilled from the
        user's ImmoScout profile; override any via ``fields``, e.g.
        {"moveInDateType": "CONCRETE", "moveInDate": "01.02.2027", "hasPets": "FALSE"}.
        Allowed keys/values: salutation (DIVERS|FEMALE|MALE|COMPANY), firstName, lastName,
        emailAddress, phoneNumber, street, houseNumber, postcode, city,
        moveInDateType (CONCRETE|FLEXIBLE|FROM_NOW), moveInDate (DD.MM.YYYY),
        numberOfPersons (ONE_PERSON|TWO_PERSON|FAMILY|BIG_GROUP), hasPets (TRUE|FALSE),
        employmentRelationship (PUBLIC_EMPLOYEE|WORKER|SELF_EMPLOYED|OFFICER|TRAINEE|
        STUDENT|DOCTORAND|HOUSEWIFE|UNEMPLOYED|RETIREE|OTHER),
        income (BELOW_500|OVER_500_UPTO_1000|…|OVER_4000_UPTO_5000|OVER_5000),
        applicationPackageCompleted (TRUE|FALSE).

        Each listing is contacted at most once; there is a daily cap and a minimum gap
        between sends. Write the message in German unless the user says otherwise.
        """
        try:
            return await anyio.to_thread.run_sync(
                lambda: apply_mod.apply(expose_id, message, fields, confirm)
            )
        except apply_mod.ApplyError as exc:
            return {"expose_id": expose_id, "status": "error", "error": str(exc)}

    @mcp.tool()
    def list_applications() -> list[dict]:
        """Listings already contacted (or attempted), newest last."""
        return [
            {k: e[k] for k in ("at", "expose_id", "status") if k in e} for e in apply_mod.load_log()
        ]

    @mcp.tool()
    def get_expose(expose_id: str) -> dict:
        """Fetch full details for a single listing by its ImmoScout expose ID."""
        return client.get_expose(expose_id).model_dump(exclude={"raw"})

    @mcp.tool()
    def count_listings(
        region: str = "/de/berlin/berlin",
        real_estate_type: str = "apartmentrent",
        price_min: float | None = None,
        price_max: float | None = None,
        rooms_min: float | None = None,
        living_space_min: float | None = None,
    ) -> dict:
        """Count matching listings without fetching them. ``region`` may be a place name."""
        total = client.count(
            SearchFilter(
                region=region,
                real_estate_type=real_estate_type,
                price_min=price_min,
                price_max=price_max,
                rooms_min=rooms_min,
                living_space_min=living_space_min,
            )
        )
        return {"total_results": total}

    @mcp.tool()
    def suggest_regions(query: str) -> list[dict]:
        """Resolve a place name to ImmoScout region paths for use with search_listings.

        Example: suggest_regions("münchen") -> [{"label": "München", "region": "/de/bayern/muenchen", ...}]
        """
        return [geo.model_dump(exclude={"raw"}) for geo in client.suggest_regions(query)]

    return mcp


def main() -> None:
    if FastMCP is None:
        raise SystemExit(_INSTALL_HINT)
    _build_server().run()


if __name__ == "__main__":
    main()
