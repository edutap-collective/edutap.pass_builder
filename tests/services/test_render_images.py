import pytest

from edutap.pass_builder.engine.spec import BoundValue, RuleSpec
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.enums import TargetKind, ValueType
from edutap.pass_builder.services.render import resolve_image_references

PHOTO_URL = "http://image_service:8000/persons/u1/photo/current"


class FakeImageService:
    def __init__(self, payload: bytes = b"\x89PNG-real") -> None:
        self.payload = payload
        self.requested: list[str] = []

    async def fetch(self, url: str) -> bytes:
        self.requested.append(url)
        return self.payload


def image_rule() -> RuleSpec:
    return RuleSpec(
        target_kind=TargetKind.IMAGE,
        target="thumbnail.png",
        source_field="person.photo",
        value_type=ValueType.IMAGE,
    )


def text_rule() -> RuleSpec:
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE,
        target="name",
        source_field="person.name",
        value_type=ValueType.TEXT,
    )


async def test_an_image_reference_becomes_the_bytes_behind_it():
    """The reason the whole mechanism exists: a `.pkpass` carries the file."""
    images = FakeImageService()
    bound = [BoundValue(rule=image_rule(), value=PHOTO_URL)]

    resolved = await resolve_image_references(bound, images)

    assert resolved[0].value == b"\x89PNG-real"
    assert images.requested == [PHOTO_URL]


async def test_a_non_image_rule_is_left_alone_and_fetches_nothing():
    images = FakeImageService()
    bound = [BoundValue(rule=text_rule(), value="Ada Lovelace")]

    resolved = await resolve_image_references(bound, images)

    assert resolved[0].value == "Ada Lovelace"
    assert images.requested == []


async def test_a_value_that_is_already_bytes_is_not_fetched():
    """`preview` generates a PNG header and reaches no network."""
    images = FakeImageService()
    bound = [BoundValue(rule=image_rule(), value=b"\x89PNG")]

    resolved = await resolve_image_references(bound, images)

    assert resolved[0].value == b"\x89PNG"
    assert images.requested == []


async def test_an_image_rule_without_a_client_is_a_500_not_a_silent_hole():
    """Loud beats a pass built with nothing where the picture goes.

    Until 2026-09-01 the silent hole was the only outcome available: the value
    arrived as a string, `apply_apple` writes the asset only for bytes, and
    the version had already published green.
    """
    bound = [BoundValue(rule=image_rule(), value=PHOTO_URL)]

    with pytest.raises(ProblemError) as excinfo:
        await resolve_image_references(bound, None)

    assert excinfo.value.slug == "image_service_not_configured"
    assert excinfo.value.status == 500


async def test_the_original_bound_values_are_not_mutated():
    """`model_copy`, not assignment: the caller's list stays what it was."""
    images = FakeImageService()
    original = BoundValue(rule=image_rule(), value=PHOTO_URL)

    resolved = await resolve_image_references([original], images)

    assert original.value == PHOTO_URL
    assert resolved[0] is not original
