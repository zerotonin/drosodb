from sqlmodel import Session, select

from ddb.models import Donor, Genotype, User, Vial, VialParentage


def test_create_genotype_and_vial(session: Session) -> None:
    user = User(username="bart", full_name="Bart Geurten")
    donor = Donor(name="Bloomington")
    session.add_all([user, donor])
    session.commit()

    geno = Genotype(
        name="w[1118]; +; +",
        chromosome_x="w[1118]",
        phenotype="white eyes",
        donor_id=donor.id,
        created_by_id=user.id,
    )
    session.add(geno)
    session.commit()

    vial = Vial(print_code="A1B2", genotype_id=geno.id, owner_id=user.id)
    session.add(vial)
    session.commit()

    fetched = session.exec(select(Vial).where(Vial.print_code == "A1B2")).one()
    assert fetched.genotype_id == geno.id
    assert fetched.is_active is True


def test_vial_parentage_cross(session: Session) -> None:
    user = User(username="bart")
    session.add(user)
    session.commit()
    g = Genotype(name="cross-progeny", created_by_id=user.id)
    session.add(g)
    session.commit()

    mom = Vial(print_code="MOM01", genotype_id=g.id)
    dad = Vial(print_code="DAD01", genotype_id=g.id)
    session.add_all([mom, dad])
    session.commit()

    child = Vial(print_code="KID01", genotype_id=g.id)
    session.add(child)
    session.commit()

    session.add_all(
        [
            VialParentage(child_id=child.id, parent_id=mom.id, role="mother"),
            VialParentage(child_id=child.id, parent_id=dad.id, role="father"),
        ]
    )
    session.commit()

    parents = session.exec(select(VialParentage).where(VialParentage.child_id == child.id)).all()
    assert {p.role for p in parents} == {"mother", "father"}
