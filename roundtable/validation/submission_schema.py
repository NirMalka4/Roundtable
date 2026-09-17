"""Build the self-contained parameter schema for the output-submission tool."""

from __future__ import annotations

import base64
import copy
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from .gates import SchemaLoadError, load_schema_document

_DEF_PREFIX = "__roundtable_external_"
_SCHEMA_MAP_KEYWORDS = frozenset({"$defs", "properties", "patternProperties", "dependentSchemas"})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "contentSchema",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def _definition_name(relpath: str) -> str:
    normalized = str(PurePosixPath(relpath.replace("\\", "/")))
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
    return _DEF_PREFIX + encoded


def _split_ref(ref: str) -> tuple[str, str]:
    path, marker, fragment = ref.partition("#")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or path.startswith("//"):
        raise SchemaLoadError(f"remote $ref is disabled: {ref!r}")
    if marker and fragment and not fragment.startswith("/"):
        raise SchemaLoadError(f"unsupported non-pointer $ref fragment: {ref!r}")
    return path, f"#{fragment}" if marker else ""


def _join_path(current: str, target: str) -> str:
    base = PurePosixPath(current).parent
    parts: list[str] = []
    for part in (base / target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise SchemaLoadError(f"$ref escapes schema dir: {target!r}")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _schema_path(parts: tuple[str | int, ...]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


class _Bundler:
    def __init__(self, schema_dir: Path, root_path: str, root: dict[str, Any]) -> None:
        self.schema_dir = schema_dir
        self.root_path = root_path
        self.root = copy.deepcopy(root)
        self.external: dict[str, Any] = {}
        self.external_sources: dict[str, str] = {}
        self.visiting: list[str] = []
        root_defs = self.root.get("$defs")
        self.root_def_names = set(root_defs) if isinstance(root_defs, dict) else set()

    def bundle(self) -> dict[str, Any]:
        rewritten = self._rewrite(self.root, self.root_path, root_scope=True)
        defs = dict(rewritten.get("$defs") or {})
        collision = defs.keys() & self.external.keys()
        if collision:
            raise SchemaLoadError(f"$defs collision while bundling: {sorted(collision)}")
        defs.update(self.external)
        if defs:
            rewritten["$defs"] = defs
        self._assert_closed(rewritten)
        Draft202012Validator.check_schema(rewritten)
        return rewritten

    def _rewrite(
        self,
        node: Any,
        current_path: str,
        *,
        root_scope: bool = False,
        schema_path: tuple[str | int, ...] = (),
    ) -> Any:
        if not isinstance(node, dict):
            return copy.deepcopy(node)

        rewritten: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$dynamicRef":
                raise SchemaLoadError(
                    f"$dynamicRef is unsupported in submission schema at "
                    f"{_schema_path((*schema_path, key))}"
                )
            if key == "$id" and not root_scope:
                continue
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                rewritten[key] = {
                    child_key: self._rewrite(
                        child,
                        current_path,
                        root_scope=root_scope,
                        schema_path=(*schema_path, key, child_key),
                    )
                    for child_key, child in value.items()
                }
                continue
            if key in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
                rewritten[key] = [
                    self._rewrite(
                        child,
                        current_path,
                        root_scope=root_scope,
                        schema_path=(*schema_path, key, index),
                    )
                    for index, child in enumerate(value)
                ]
                continue
            if key in _SCHEMA_SINGLE_KEYWORDS:
                rewritten[key] = self._rewrite(
                    value,
                    current_path,
                    root_scope=root_scope,
                    schema_path=(*schema_path, key),
                )
                continue
            if key != "$ref":
                rewritten[key] = copy.deepcopy(value)
                continue
            if not isinstance(value, str):
                raise SchemaLoadError(f"$ref must be a string: {value!r}")
            target, fragment = _split_ref(value)
            if not target:
                rewritten[key] = (
                    fragment
                    if root_scope
                    else f"#/$defs/{_definition_name(current_path)}" + fragment.removeprefix("#")
                )
                continue
            resolved = _join_path(current_path, target)
            name = self._include(resolved)
            rewritten[key] = f"#/$defs/{name}{fragment.removeprefix('#')}"
        return rewritten

    def _include(self, relpath: str) -> str:
        name = _definition_name(relpath)
        if name in self.root_def_names:
            raise SchemaLoadError(f"$defs collision while bundling: {name!r}")
        if relpath in self.visiting:
            chain = " -> ".join((*self.visiting, relpath))
            raise SchemaLoadError(f"unsafe recursive external $ref: {chain}")
        owner = self.external_sources.get(name)
        if owner is not None and owner != relpath:
            raise SchemaLoadError(
                f"external definition {name!r} is owned by {owner!r}, not {relpath!r}"
            )
        if owner is not None:
            return name
        self.external_sources[name] = relpath
        self.visiting.append(relpath)
        try:
            document = load_schema_document(relpath, self.schema_dir)
            self.external[name] = self._rewrite(document, relpath)
        finally:
            self.visiting.pop()
        return name

    @staticmethod
    def _assert_closed(root: Any) -> None:
        def resolve(ref: str) -> Any:
            target = root
            for encoded in ref.removeprefix("#/").split("/"):
                token = encoded.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or token not in target:
                    raise SchemaLoadError(f"unresolved $ref in submission schema: {ref!r}")
                target = target[token]
            return target

        def walk(
            node: Any,
            trail: frozenset[str] = frozenset(),
            schema_path: tuple[str | int, ...] = (),
        ) -> None:
            if not isinstance(node, dict):
                return
            if "$dynamicRef" in node:
                raise SchemaLoadError(
                    f"$dynamicRef is unsupported in submission schema at "
                    f"{_schema_path((*schema_path, '$dynamicRef'))}"
                )
            ref = node.get("$ref")
            if isinstance(ref, str):
                if not ref.startswith("#/"):
                    raise SchemaLoadError(f"unresolved $ref in submission schema: {ref!r}")
                if ref in trail:
                    raise SchemaLoadError(f"unsafe recursive local $ref: {ref!r}")
                walk(resolve(ref), trail | {ref}, schema_path)
            for key, value in node.items():
                if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                    for child_key, child in value.items():
                        walk(child, trail, (*schema_path, key, child_key))
                elif key in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
                    for index, child in enumerate(value):
                        walk(child, trail, (*schema_path, key, index))
                elif key in _SCHEMA_SINGLE_KEYWORDS:
                    walk(value, trail, (*schema_path, key))

        walk(root)


def build_submission_schema(output_schema: str, schema_dir: Path) -> dict[str, Any]:
    """Wrap and close one graph entry's active output schema."""

    root = load_schema_document(output_schema, schema_dir)
    bundled = _Bundler(schema_dir.resolve(), output_schema, root).bundle()
    bundled.pop("$id", None)
    definitions = bundled.pop("$defs", None)
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["output"],
        "properties": {"output": bundled},
    }
    if definitions:
        wrapper["$defs"] = definitions
    _Bundler._assert_closed(wrapper)
    Draft202012Validator.check_schema(wrapper)
    return wrapper
