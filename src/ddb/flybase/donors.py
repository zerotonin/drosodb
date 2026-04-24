"""Canonical Donor rows per FlyBase stock collection.

When the user imports a genotype from a collection we haven't seen
before, we create a Donor record so the new Genotype has a non-null
`donor_id` linking it back to the originating stock center. One Donor
per collection, never duplicated.

The mapping is intentionally keyed by FlyBase's `collection_short_name`
(the value we read out of the TSV) so there's a single source of truth
between the catalog and the donor.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from ddb.models import Donor


@dataclass(frozen=True)
class CollectionDonor:
    """Donor fields prefilled for one stock collection."""

    short_name: str  # what we persist as Donor.name
    institution: str  # Donor.institution
    contact: str | None = None  # Donor.contact (homepage URL is fine)


# Keyed by FlyBase's `collection_short_name` from the stocks TSV.
# Short-names follow each centre's own preferred abbreviation.
COLLECTION_DONORS: dict[str, CollectionDonor] = {
    "Bloomington": CollectionDonor(
        short_name="BDSC",
        institution="Bloomington Drosophila Stock Center, Indiana University",
        contact="https://bdsc.indiana.edu/",
    ),
    "Vienna": CollectionDonor(
        short_name="VDRC",
        institution="Vienna Drosophila Resource Center, IMBA/IMP",
        contact="https://stockcenter.vdrc.at/",
    ),
    "Kyoto": CollectionDonor(
        short_name="DGGR",
        institution="Kyoto Drosophila Genomics and Genetic Resources, KIT",
        contact="https://kyotofly.kit.jp/",
    ),
    "NIG-Fly": CollectionDonor(
        short_name="NIG-Fly",
        institution="National Institute of Genetics Fly Stocks, Mishima",
        contact="https://shigen.nig.ac.jp/fly/nigfly/",
    ),
    "KDRC": CollectionDonor(
        short_name="KDRC",
        institution="Korea Drosophila Resource Center",
        contact=None,
    ),
    "FlyORF": CollectionDonor(
        short_name="FlyORF",
        institution="FlyORF — University of Zurich ORFeome collection",
        contact="https://www.flyorf.ch/",
    ),
    "NDSSC": CollectionDonor(
        short_name="NDSSC",
        institution="National Drosophila Species Stock Center, Cornell University",
        contact="https://www.drosophilaspecies.com/",
    ),
}


def ensure_collection_donor(session: Session, collection_short_name: str) -> Donor:
    """Return the canonical Donor row for a collection, creating it if
    absent. Commits so the returned `id` is safe to reference as a
    foreign key immediately.

    Falls back to a bare-bones Donor named after the collection when we
    don't have a curated entry for it (e.g. FlyBase adds a new
    collection between releases). That still gives the imported genotype
    something to point at and surfaces the unknown collection in the UI.
    """
    curated = COLLECTION_DONORS.get(collection_short_name)
    lookup_name = curated.short_name if curated else collection_short_name

    donor = session.exec(select(Donor).where(Donor.name == lookup_name)).first()
    if donor is not None:
        return donor

    donor = Donor(
        name=lookup_name,
        institution=curated.institution if curated else None,
        contact=curated.contact if curated else None,
    )
    session.add(donor)
    session.commit()
    session.refresh(donor)
    return donor
