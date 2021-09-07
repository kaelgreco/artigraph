from collections.abc import Mapping
from typing import Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

from arti.internal.type_hints import lenient_issubclass
from arti.types import Boolean, Enum, List, Map, String, Struct, Timestamp, Type, _Float, _Int
from arti.types.pydantic import (
    get_ignored_fields,
    get_post_field_conversion_hook,
    pydantic_type_system,
)


class MyModel(BaseModel):
    x: int
    y: str
    tags: list[str]


class NestedModel(BaseModel):
    name: str
    struct: Struct
    timestamp: Timestamp
    value: MyModel


def test_pydantic_conversion() -> None:
    arti_type = pydantic_type_system.to_artigraph(MyModel)
    assert isinstance(arti_type, Struct)
    assert set(arti_type.fields) == {"x", "y", "tags"}
    assert isinstance(arti_type.fields["x"], _Int)
    assert isinstance(arti_type.fields["y"], String)
    assert isinstance(arti_type.fields["tags"], List)
    assert isinstance(arti_type.fields["tags"].value_type, String)


def test_pydantic_field_naming() -> None:
    arti_type = pydantic_type_system.to_artigraph(Timestamp)
    assert isinstance(arti_type, Struct)
    precision = arti_type.fields["precision"]
    assert isinstance(precision, Enum)
    assert precision.name == "precision"


def test_get_ignored_fields() -> None:
    class X(BaseModel):
        i: int

    fields = get_ignored_fields(X)
    assert isinstance(fields, frozenset)
    assert fields == frozenset()

    class Y(BaseModel):
        i: int

        @classmethod
        def _pydantic_type_system_ignored_fields_hook_(cls) -> frozenset[str]:
            return frozenset(["i"])

    fields = get_ignored_fields(Y)
    assert isinstance(fields, frozenset)
    assert fields == frozenset(["i"])


def test_get_post_field_conversion_hook() -> None:
    type_ = Struct(fields={"x": String()})

    class X(BaseModel):
        i: int

    hook = get_post_field_conversion_hook(X)
    assert hook(type_, name="test", required=False) is type_

    class Y(BaseModel):
        i: int

        @classmethod
        def _pydantic_type_system_post_field_conversion_hook_(
            cls, type_: Type, *, name: str, required: bool
        ) -> Type:
            return type_.copy(update={"name": name})

    hook = get_post_field_conversion_hook(Y)
    converted = hook(type_, name="test", required=False)
    assert isinstance(converted, Struct)  # satisfy mypy
    assert converted.name == "test"


# NOTE: In addition to likely being over-engineered (and grossly too similar in structure, but just
# different enough behavior), the compare_model_to_* helpers only cover the subset of field types
# necessary to check the specific models under test. In practice, converting deeply nested Structs
# to pydantic models will probably be of little use (one wouldn't have easy access to the sub-model
# classes to instantiate). Alternatively, perhaps we store the model class (or some reference to it)
# in the Struct.metadata and prefer to return those.


_scalar_type_mapping = {
    bool: Boolean,
    float: _Float,
    int: _Int,
    str: String,
}


def compare_model_to_type(model: type[BaseModel], type_: Type) -> None:  # noqa: C901
    assert isinstance(type_, Struct)
    assert type_.name == model.__name__
    ignored_fields = get_ignored_fields(model)
    for k, expected_field in model.__fields__.items():
        if k in ignored_fields:
            assert k not in type_.fields
            continue
        expected_type, spec = expected_field.outer_type_, type_.fields[k]
        origin = get_origin(expected_type)
        if origin is not None:
            args = get_args(expected_type)
            if lenient_issubclass(origin, Mapping):
                assert isinstance(spec, Map)
                for (sub_type, sub_spec) in zip(args, (spec.key_type, spec.value_type)):
                    if lenient_issubclass(sub_type, BaseModel):
                        compare_model_to_type(sub_type, sub_spec)
                    else:
                        expected_spec_type = _scalar_type_mapping.get(sub_type)
                        assert expected_spec_type is not None and isinstance(
                            sub_spec, expected_spec_type
                        )
            elif lenient_issubclass(origin, list):
                assert isinstance(spec, List)
                sub_type, sub_spec = args[0], spec.value_type
                if lenient_issubclass(sub_type, BaseModel):
                    compare_model_to_type(sub_type, sub_spec)
                else:
                    expected_spec_type = _scalar_type_mapping.get(sub_type)
                    assert expected_spec_type is not None and isinstance(
                        sub_spec, expected_spec_type
                    )
            elif origin is Literal:
                assert isinstance(spec, Enum)
                assert isinstance(spec.type, String)
                assert set(args) == spec.items
            else:
                raise NotImplementedError(f"Don't know how to check {expected_type}")
        elif lenient_issubclass(expected_type, BaseModel):
            compare_model_to_type(expected_type, spec)
        elif (expected_spec_type := _scalar_type_mapping.get(expected_type)) is not None:
            assert isinstance(spec, expected_spec_type)
        else:
            raise NotImplementedError(f"Don't know how to check {expected_type}")


def compare_model_to_generated(  # noqa: C901
    model: type[BaseModel], generated: type[BaseModel]
) -> None:
    assert issubclass(generated, BaseModel)
    assert generated.__name__ == model.__name__
    ignored_fields = get_ignored_fields(model)
    for k, v in model.__fields__.items():
        if k in ignored_fields:
            assert k not in generated.__fields__
            continue
        expected_type, got_type = v.outer_type_, generated.__fields__[k].outer_type_
        expected_origin, got_origin = get_origin(expected_type), get_origin(got_type)
        if expected_origin is not None:
            expected_args, got_args = get_args(expected_type), get_args(got_type)
            if lenient_issubclass(expected_origin, Mapping):
                assert lenient_issubclass(got_origin, Mapping)
                for (expected_arg, got_arg) in zip(expected_args, got_args):
                    if lenient_issubclass(expected_arg, BaseModel):
                        compare_model_to_generated(expected_arg, got_arg)
                    else:
                        assert lenient_issubclass(got_arg, expected_arg)
            elif lenient_issubclass(expected_origin, list):
                assert lenient_issubclass(got_origin, list)
                expected_arg, got_arg = expected_args[0], got_args[0]
                if lenient_issubclass(expected_arg, BaseModel):
                    compare_model_to_generated(expected_arg, got_arg)
                else:
                    assert lenient_issubclass(got_arg, expected_arg)
            elif expected_origin is Literal:
                assert got_origin is Literal
                assert set(expected_args) == set(got_args)
            else:
                raise NotImplementedError(f"Don't know how to check {expected_type}")
        elif lenient_issubclass(expected_type, BaseModel):
            compare_model_to_generated(expected_type, got_type)
        elif expected_type is got_type:
            pass
        else:
            raise NotImplementedError(f"Don't know how to check {expected_type}")


@pytest.mark.parametrize(
    ("model",),
    (
        (MyModel,),
        (NestedModel,),
    ),
)
def test_pydantic_type_system(model: type[BaseModel]) -> None:
    arti_type = pydantic_type_system.to_artigraph(model)
    compare_model_to_type(model, arti_type)

    pydantic_model = pydantic_type_system.to_system(arti_type)
    compare_model_to_generated(model, pydantic_model)