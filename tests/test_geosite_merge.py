import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from geosite_merge import (combine, encode_bytes_field, read_sites, rename_site)
from build import verify_digest
import hashlib


def site(code, domains=("example.com",)):
    # GeoSite: code (field #1), domain(s) (field #2)
    value = encode_bytes_field(1, code.encode())
    for domain in domains:
        # Domain: type Domain=2 (field #1, varint), value (field #2, string)
        domain_bytes = b"\x08\x02" + encode_bytes_field(2, domain.encode())
        value += encode_bytes_field(2, domain_bytes)
    return encode_bytes_field(1, value)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.roscom = b"".join(site(s) for s in ["private", "category-ru", "whitelist", "category-ads", "apple", "riot"])
        self.v2fly = site("category-ads", ["ads.example", "tracker.example"]) + site("category-ads-all", ["ads.example", "extra.example"])

    def test_merge_keeps_all_roscom_entries(self):
        result, info = combine(self.roscom, self.v2fly)
        self.assertTrue(result.startswith(self.roscom))
        original = read_sites(self.roscom)
        merged = read_sites(result)
        for code, record in original.items():
            self.assertEqual(merged[code], record)
        self.assertEqual(info["roscom_categories"], 6)
        self.assertEqual(info["total_categories"], 8)
        self.assertEqual(merged["nix-ads"][1], 2)
        self.assertEqual(merged["nix-ads-all"][1], 2)

    def test_missing_category_fails(self):
        with self.assertRaisesRegex(ValueError, "category-ads-all"):
            combine(self.roscom, site("category-ads"))

    def test_duplicate_nix_category_fails(self):
        with self.assertRaisesRegex(ValueError, "already has category"):
            combine(self.roscom + site("nix-ads"), self.v2fly)

    def test_invalid_proto_fails(self):
        with self.assertRaises(ValueError):
            read_sites(b"\x0a\xff")

    def test_rename_retains_domain_wire_bytes(self):
        src = read_sites(self.v2fly)["category-ads"][0]
        output = rename_site(src, "NIX-ADS")
        self.assertEqual(read_sites(encode_bytes_field(1, output))["nix-ads"][1], 2)
        self.assertIn(b"tracker.example", output)

    def test_checksum(self):
        data = b"test"
        checksum = hashlib.sha256(data).hexdigest().encode() + b"  dlc.dat\n"
        self.assertEqual(verify_digest(data, checksum, "test"), hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_digest(b"wrong", checksum, "test")


if __name__ == "__main__":
    unittest.main()
