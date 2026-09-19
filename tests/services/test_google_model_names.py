"""The Google model names the services pass to `edutap.wallet_google`.

Every test that touches the Google API hands in a fake, so none of them would notice
a model name the real registry does not know: the fake accepts any string, the real
`api.new` raises `KeyError` and the route answers 500. These tests ask the real
registry.
"""

import pytest
from edutap.wallet_google.registry import lookup_model_by_name

from edutap.pass_builder.services.render import _GOOGLE_OBJECT_MODEL
from edutap.pass_builder.services.templates import _GOOGLE_CLASS_MODEL


@pytest.mark.parametrize("name", [_GOOGLE_CLASS_MODEL, _GOOGLE_OBJECT_MODEL])
def test_the_model_name_is_registered_in_wallet_google(name):
    assert lookup_model_by_name(name).__name__ == name


def test_class_and_object_belong_to_the_same_pass_type():
    """A class is pushed by sync, its objects by render: they must be one type."""
    pass_type_of_class = _GOOGLE_CLASS_MODEL.removesuffix("Class")
    pass_type_of_object = _GOOGLE_OBJECT_MODEL.removesuffix("Object")
    assert pass_type_of_class == pass_type_of_object
