import unittest

from stock_analyzer.xml_safety import strip_external_ownership_doctype


class OwnershipXmlSafetyTests(unittest.TestCase):
    def test_strips_external_system_doctype_without_resolving(self):
        source = '<?xml version="1.0"?><!DOCTYPE ownershipDocument SYSTEM "ownership.dtd"><ownershipDocument/>'
        self.assertEqual(
            strip_external_ownership_doctype(source),
            '<?xml version="1.0"?><ownershipDocument/>',
        )

    def test_strips_external_public_doctype_without_resolving(self):
        source = '<!DOCTYPE ownershipDocument PUBLIC "-//SEC//DTD ownership//EN" "ownership.dtd"><ownershipDocument/>'
        self.assertEqual(
            strip_external_ownership_doctype(source),
            '<ownershipDocument/>',
        )

    def test_strips_non_entity_internal_subset(self):
        source = '<!DOCTYPE ownershipDocument SYSTEM "ownership.dtd" [<!ELEMENT ownershipDocument ANY>]><ownershipDocument/>'
        self.assertEqual(
            strip_external_ownership_doctype(source),
            '<ownershipDocument/>',
        )

    def test_leaves_xml_without_doctype_unchanged(self):
        source = '<ownershipDocument/>'
        self.assertEqual(strip_external_ownership_doctype(source), source)

    def test_rejects_internal_subset_and_entity_declarations(self):
        cases = (
            '<!DOCTYPE ownershipDocument [<!ENTITY x "unsafe">]><ownershipDocument/>',
            '<!ENTITY x "unsafe"><ownershipDocument/>',
            '<!DOCTYPE ownershipDocument SYSTEM "ownership.dtd"><!ENTITY x "unsafe"><ownershipDocument/>',
        )
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                strip_external_ownership_doctype(source)

    def test_rejects_malformed_or_multiple_doctypes(self):
        for source in (
            '<!DOCTYPE><ownershipDocument/>',
            '<!DOCTYPE ownershipDocument SYSTEM "one.dtd"><!DOCTYPE ownershipDocument SYSTEM "two.dtd"><ownershipDocument/>',
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                strip_external_ownership_doctype(source)


if __name__ == "__main__":
    unittest.main()
