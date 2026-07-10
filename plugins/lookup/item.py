"""Ansible lookup plugin for 1Password Connect Server secrets."""

import hashlib
from dataclasses import dataclass
from typing import Any, NamedTuple

from ansible.errors import AnsibleLookupError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

_FIELD_DEFAULTS: list[str] = ["password", "credential"]


class ClientKey(NamedTuple):
    host: str
    token_hash: str


_client_cache: dict[ClientKey, Any] = {}
_vault_id_cache: dict[tuple[ClientKey, str], str] = {}


# ---------------------------------------------------------------------------
# op:// URI handling
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecretRef:
    """Parsed ``op://vault/item/[section/]field`` reference."""

    vault: str
    item: str
    field: str | None = None
    section: str | None = None

    @classmethod
    def parse(cls, uri: str) -> "SecretRef":
        if not uri.startswith("op://"):
            raise AnsibleLookupError(f"Not an op:// reference: {uri!r}")

        # Strip query string, split path segments.
        path = uri[5:].split("?", maxsplit=1)[0]
        parts = [p for p in path.split("/") if p]

        match len(parts):
            case 2:
                return cls(vault=parts[0], item=parts[1])
            case 3:
                return cls(vault=parts[0], item=parts[1], field=parts[2])
            case 4:
                return cls(
                    vault=parts[0],
                    item=parts[1],
                    section=parts[2],
                    field=parts[3],
                )
            case _:
                raise AnsibleLookupError(
                    f"Invalid op:// reference: {uri!r}  "
                    "Expected op://vault/item[/section]/[field]"
                )

    @classmethod
    def from_components(
        cls,
        vault: str,
        item: str,
        field: str | None = None,
        section: str | None = None,
    ) -> "SecretRef":
        for label, val in (("vault", vault), ("item", item)):
            if not val or "/" in val:
                raise AnsibleLookupError(
                    f"Invalid {label} value {val!r}: must be non-empty "
                    "and must not contain '/' characters."
                )
        if field and "/" in field:
            raise AnsibleLookupError(
                f"Invalid field value {field!r}: must not contain '/' characters."
            )
        if section and "/" in section:
            raise AnsibleLookupError(
                f"Invalid section value {section!r}: must not contain '/' characters."
            )
        return cls(vault=vault, item=item, field=field or None, section=section)


def _is_op_ref(value: str) -> bool:
    return value.startswith("op://")


# ---------------------------------------------------------------------------
# Connect client
# ---------------------------------------------------------------------------


def _get_client(host: str, token: str) -> tuple[Any, ClientKey]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = ClientKey(host, token_hash)
    if key not in _client_cache:
        try:
            from onepasswordconnectsdk.client import (  # type: ignore[import-untyped]
                new_client,
            )
        except ImportError as exc:
            raise AnsibleLookupError(
                "The 'onepasswordconnectsdk' package is required "
                "(pip install onepasswordconnectsdk>=2.0.0)."
            ) from exc

        display.vvv(f"[onepass_item] connecting to {host}")
        _client_cache[key] = new_client(host, token)
    return _client_cache[key], key


def _resolve_vault_id(client: Any, vault_name: str, client_key: ClientKey) -> str:
    """Resolve a vault name or ID to its canonical ID."""
    cache_key = (client_key, vault_name)
    if cache_key in _vault_id_cache:
        return _vault_id_cache[cache_key]

    for vault in client.get_vaults():
        # Match on ID directly or title (case-insensitive).
        if vault.id == vault_name or (vault.name or "").lower() == vault_name.lower():
            _vault_id_cache[cache_key] = vault.id
            return vault.id

    raise AnsibleLookupError(f"Vault not found: {vault_name!r}")


def _match_field(item: Any, field_name: str, section: str | None) -> str | None:
    """Try to extract a field value by label or purpose.

    Returns ``None`` when the field is not found at all.
    Returns ``""`` (empty string) when the field exists but has no value —
    callers must distinguish between these two cases.
    """
    # ID-based match (exact, case-sensitive) — bypasses section filtering since IDs are unique.
    for field in item.fields:
        if field.id == field_name:
            return field.value or ""

    # Label-based match.
    for field in item.fields:
        if (field.label or "").lower() != field_name.lower():
            continue

        if section is not None:
            section_id = getattr(field, "section", None)
            if section_id is None:
                continue
            field_section_id = (
                section_id.id if hasattr(section_id, "id") else str(section_id)
            )
            section_match = False
            for sec in getattr(item, "sections", []) or []:
                if sec.id == field_section_id:
                    if (sec.label or "").lower() == section.lower():
                        section_match = True
                    break
            if not section_match and field_section_id.lower() != section.lower():
                continue

        return field.value or ""

    # Purpose-based match (USERNAME, PASSWORD, etc.) — only without section.
    if section is None:
        for field in item.fields:
            if (field.purpose or "").lower() == field_name.lower():
                return field.value or ""

    return None


def _collect_all_fields(item: Any, section: str | None) -> dict[str, str]:
    """Return all fields from *item* as a ``{label: value}`` dict.

    When *section* is set, only fields belonging to that section are
    included.  Fields with an empty label fall back to their purpose
    (lowercased) as the key.  Fields with neither are skipped.
    """
    # Pre-build section ID → label mapping for filtering.
    section_labels: dict[str, str] = {}
    for sec in getattr(item, "sections", []) or []:
        section_labels[sec.id] = (sec.label or "").lower()

    result: dict[str, str] = {}
    for field in item.fields:
        # Section filter.
        if section is not None:
            field_section = getattr(field, "section", None)
            if field_section is None:
                continue
            sid = (
                field_section.id if hasattr(field_section, "id") else str(field_section)
            )
            label_match = section_labels.get(sid, "") == section.lower()
            id_match = sid.lower() == section.lower()
            if not label_match and not id_match:
                continue

        key = field.label or (field.purpose or "").lower() or None
        if key:
            result[key] = field.value or ""

    return result


def _resolve_ref(
    client: Any, ref: SecretRef, client_key: ClientKey
) -> str | dict[str, str]:
    """Resolve a single SecretRef to its field value or all fields.

    Returns a ``str`` in all normal cases.  When ``ref.field == "*"``
    (wildcard), returns a ``dict[str, str]`` mapping field labels to values
    instead — callers must handle this non-standard return type explicitly.
    """
    vault_id = _resolve_vault_id(client, ref.vault, client_key)
    display.vvv(f"[onepass_item] get_item({ref.item!r}, {vault_id!r})")
    item = client.get_item(ref.item, vault_id)

    # Wildcard — return all fields as a dict.
    if ref.field == "*":
        display.vvv("[onepass_item] collecting all fields")
        return _collect_all_fields(item, ref.section)

    # Explicit field — single attempt.
    if ref.field is not None:
        value = _match_field(item, ref.field, ref.section)
        if value is not None:
            return value
        location = f"vault {ref.vault!r}"
        if ref.section:
            location += f", section {ref.section!r}"
        raise AnsibleLookupError(
            f"Field {ref.field!r} not found in item {ref.item!r} ({location})"
        )

    # No field specified — try defaults in priority order.
    for candidate in _FIELD_DEFAULTS:
        value = _match_field(item, candidate, ref.section)
        if value is not None:
            display.vvv(f"[onepass_item] matched default field {candidate!r}")
            return value

    tried = ", ".join(repr(f) for f in _FIELD_DEFAULTS)
    raise AnsibleLookupError(
        f"No default field matched in item {ref.item!r} "
        f"(vault {ref.vault!r}). Tried: {tried}"
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class LookupModule(LookupBase):
    def run(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        terms: list[str],
        variables: dict[str, Any] | None = None,
        **kwargs: dict[str, Any],
    ):
        self.set_options(var_options=variables, direct=kwargs)

        host = self.get_option("host")
        token = self.get_option("token")

        refs = self._normalise_refs(terms, kwargs)
        client, client_key = _get_client(host, token)

        try:
            return [_resolve_ref(client, ref, client_key) for ref in refs]
        except AnsibleLookupError:
            raise  # Pass through; don't wrap in a second AnsibleLookupError.
        except Exception as exc:
            raise AnsibleLookupError(f"1Password Connect lookup failed: {exc}") from exc

    @staticmethod
    def _normalise_refs(terms: list[str], kwargs: dict[str, Any]) -> list[SecretRef]:
        vault: str | None = kwargs.get("vault")
        item: str | None = kwargs.get("item")
        field: str | None = kwargs.get("field")
        section: str | None = kwargs.get("section")

        # Mode 1: named parameters
        if vault and item:
            if terms:
                raise AnsibleLookupError(
                    "Positional terms and named parameters (vault=, "
                    "item=) are mutually exclusive."
                )
            return [SecretRef.from_components(vault, item, field, section)]

        # Mode 2: op:// URIs
        if terms and all(_is_op_ref(t) for t in terms):
            return [SecretRef.parse(t) for t in terms]

        # Mode 3: positional triple
        if len(terms) == 3 and not any(_is_op_ref(t) for t in terms):
            return [SecretRef.from_components(terms[0], terms[1], terms[2], section)]

        if not terms and not vault:
            raise AnsibleLookupError(
                "No secret references provided.  Supply op:// URIs, "
                "three positional strings (vault, item, field), or "
                "vault=/item=/field= named parameters."
            )
        raise AnsibleLookupError(
            f"Ambiguous input: {terms!r}.  All positional terms must "
            "be op:// URIs, or supply exactly three plain strings "
            "(vault, item, field)."
        )
