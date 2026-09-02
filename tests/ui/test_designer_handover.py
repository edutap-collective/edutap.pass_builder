"""What travels between `edutap.pass_designer` and this service.

Three files leave the designer -- `class.json`, `object.json` and
`mappings.json` -- and one file goes the other way, the field catalogue both
sides have to agree on. Neither direction needs a translation, and these tests
are what keeps that true.
"""

from edutap.pass_builder.models.db import DataField
from edutap.pass_builder.models.enums import ValueType

from .test_tenants import make_tenant

# The designer's own export, reproduced verbatim from
# `exporter/mappings.build_mappings`. Its `unknown_fields` key is part of the
# file and is not part of the request model -- pydantic ignores it, which is
# what lets the file be posted as it stands.
DESIGNER_MAPPINGS = {
    "rules": [
        {
            "target_kind": "json_pointer",
            "target": "/cardTitle/defaultValue/value",
            "source_field": "person.display_name",
            "value_type": "text",
            "required": True,
            "default_value": None,
            "position": 0,
        }
    ],
    "unknown_fields": [],
}


async def _google_variant(ui, tenant_id: str) -> str:
    template = await ui.post(
        f"/tenants/{tenant_id}/templates",
        json={"key": "esc_id_v1", "name": "European Student Card"},
    )
    variant = await ui.post(
        f"/tenants/{tenant_id}/templates/{template.json()['id']}/variants",
        json={
            "key": "default",
            "name": "Google Smart Tap",
            "wallet_type": "GOOGLE_ST",
            "is_default": True,
        },
    )
    assert variant.status_code == 201, variant.text
    return variant.json()["id"]


async def test_the_designers_three_files_go_in_without_translation(ui, session):
    """`class.json` + `object.json` create the draft, `mappings.json` binds it.

    Two calls that already existed, and the designer writes exactly their
    payloads -- its README says the mapping file is "the shape
    `edutap.pass_builder` already defines", and this is what holds it to that.
    """
    tenant = await make_tenant(ui)
    variant_id = await _google_variant(ui, tenant["id"])
    session.add(
        DataField(
            key="person.display_name", value_type=ValueType.TEXT, label="Display name"
        )
    )
    await session.flush()

    version = await ui.post(
        f"/tenants/{tenant['id']}/variants/{variant_id}/versions",
        json={
            "class_json": {"id": "ISSUER.class"},
            "object_json": {
                "id": "ISSUER.specimen",
                "cardTitle": {"defaultValue": {"value": "${person.display_name}"}},
            },
        },
    )
    assert version.status_code == 201, version.text

    mappings = await ui.put(
        f"/tenants/{tenant['id']}/versions/{version.json()['id']}/mappings",
        json=DESIGNER_MAPPINGS,
    )

    assert mappings.status_code == 200, mappings.text
    assert mappings.json()["rules"][0]["source_field"] == "person.display_name"


async def test_the_catalogue_export_has_the_shape_the_designer_loads(ui, session):
    """One catalogue, not two.

    The designer lays a pass out against a field list and this service
    validates every mapping rule against one. Two files means a rule authored
    in the designer fails at publish time, and nothing before that says why.
    """
    tenant = await make_tenant(ui)
    session.add(
        DataField(
            key="person.photo",
            value_type=ValueType.IMAGE,
            label="Photograph",
            description="URL of the active photo version",
        )
    )
    await session.flush()

    response = await ui.get(f"/tenants/{tenant['id']}/fields/catalogue.json")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"fields"}
    entry = body["fields"][0]
    assert set(entry) == {"key", "value_type", "label", "required", "description"}
    assert entry["key"] == "person.photo"
    assert entry["value_type"] == "image"
