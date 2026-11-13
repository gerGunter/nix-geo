"""Minimal, dependency-free GeoSite protobuf reader and non-destructive merger.

Xray and V2Fly store GeoSiteList as repeated field #1 GeoSite messages.
A GeoSite contains #1 code (string), #2 domain (Domain message).
We preserve the original RoscomVPN bytes unchanged and append renamed entries
from V2Fly, preserving their Domain protobuf payloads and attributes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    number: int
    wire: int
    start: int
    end: int
    payload: bytes | None


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
    raise ValueError("Invalid protobuf varint")


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Negative protobuf varint")
    output = bytearray()
    while value >= 128:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def encode_bytes_field(number: int, content: bytes) -> bytes:
    return encode_varint((number << 3) | 2) + encode_varint(len(content)) + content


def iter_fields(data: bytes):
    pos = 0
    while pos < len(data):
        start = pos
        tag, pos = decode_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid protobuf field number 0")
        payload = None
        if wire == 0:  # varint
            _, pos = decode_varint(data, pos)
        elif wire == 1:  # 64-bit
            pos += 8
        elif wire == 2:  # length-delimited
            length, pos = decode_varint(data, pos)
            if length > len(data) - pos:
                raise ValueError("Truncated length-delimited field")
            payload = data[pos:pos + length]
            pos += length
        elif wire == 5:  # 32-bit
            pos += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire}")
        if pos > len(data):
            raise ValueError("Truncated protobuf field")
        yield Field(number, wire, start, pos, payload)


def read_site(site_bytes: bytes) -> tuple[str, int]:
    codes = []
    domains = 0
    for field in iter_fields(site_bytes):
        if field.number == 1 and field.wire == 2:
            try:
                codes.append(field.payload.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid GeoSite code") from exc
        elif field.number == 2 and field.wire == 2:
            domains += 1
    if len(codes) != 1 or not codes[0] or not domains:
        raise ValueError("GeoSite entry lacks a single code or domain rules")
    return codes[0], domains


def read_sites(blob: bytes) -> dict[str, tuple[bytes, int]]:
    result = {}
    if not blob:
        raise ValueError("Empty GeoSite database")
    for field in iter_fields(blob):
        if field.number != 1 or field.wire != 2:
            raise ValueError(f"Unexpected GeoSiteList field {field.number}/{field.wire}")
        entry = field.payload
        code, domains = read_site(entry)
        key = code.casefold()
        if key in result:
            raise ValueError(f"Duplicate GeoSite category: {code}")
        result[key] = (entry, domains)
    if not result:
        raise ValueError("No GeoSite categories found")
    return result


def rename_site(entry: bytes, new_code: str) -> bytes:
    output = bytearray()
    seen = False
    for field in iter_fields(entry):
        if field.number == 1 and field.wire == 2:
            if seen:
                raise ValueError("Duplicate code field")
            output.extend(encode_bytes_field(1, new_code.encode("utf-8")))
            seen = True
        else:
            output.extend(entry[field.start:field.end])
    if not seen:
        raise ValueError("Missing GeoSite code")
    return bytes(output)


def combine(roscom_data: bytes, v2fly_data: bytes) -> tuple[bytes, dict]:
    original = read_sites(roscom_data)
    ads = read_sites(v2fly_data)
    for required in ("private", "category-ru", "whitelist", "category-ads"):
        if required not in original:
            raise ValueError(f"RoscomVPN is missing required category: {required}")
    mapping = {"category-ads": "NIX-ADS", "category-ads-all": "NIX-ADS-ALL"}
    output = bytearray(roscom_data)
    added = {}
    for source, target in mapping.items():
        if source not in ads:
            raise ValueError(f"V2Fly is missing required category: {source}")
        if target.casefold() in original:
            raise ValueError(f"RoscomVPN already has category: {target}")
        source_entry, count = ads[source]
        output.extend(encode_bytes_field(1, rename_site(source_entry, target)))
        added[target.lower()] = {"source_category": source, "domain_rules": count}
    combined = bytes(output)
    result = read_sites(combined)
    if not combined.startswith(roscom_data):
        raise AssertionError("Original RoscomVPN bytes were changed")
    if len(result) != len(original) + len(mapping):
        raise AssertionError("Incorrect merged category count")
    for name, (entry, count) in original.items():
        if result.get(name) != (entry, count):
            raise AssertionError(f"RoscomVPN category was changed: {name}")
    return combined, {"roscom_categories": len(original), "total_categories": len(result), "added": added}
