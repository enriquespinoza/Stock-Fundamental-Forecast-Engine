"""Narrow XML preprocessing for SEC ownership documents."""

import re


_DOCTYPE_TOKEN = re.compile(r"<!\s*DOCTYPE\b", flags=re.IGNORECASE)
_ENTITY_TOKEN = re.compile(r"<!\s*ENTITY\b", flags=re.IGNORECASE)
_EXTERNAL_OWNERSHIP_DOCTYPE = re.compile(
    r"<!DOCTYPE\s+ownershipDocument\s+"
    r"(?:SYSTEM\s+(?:\"[^\"]*\"|'[^']*')|"
    r"PUBLIC\s+(?:\"[^\"]*\"|'[^']*')\s+(?:\"[^\"]*\"|'[^']*'))\s*>",
    flags=re.IGNORECASE,
)


def strip_external_ownership_doctype(xml_text: str) -> str:
    """Strip one external ownership DTD declaration without resolving it.

    SEC ownership filings commonly declare their external DTD. We never load
    that DTD; internal subsets and all entity declarations remain prohibited.
    """
    if _ENTITY_TOKEN.search(xml_text):
        raise ValueError("Entity declarations are not permitted")

    declarations = list(_DOCTYPE_TOKEN.finditer(xml_text))
    if not declarations:
        return xml_text
    if len(declarations) != 1:
        raise ValueError("Only one external ownership DTD declaration is permitted")

    match = _EXTERNAL_OWNERSHIP_DOCTYPE.search(xml_text)
    if match is None or match.start() != declarations[0].start():
        raise ValueError("Only an external ownership DTD declaration is permitted")
    stripped = xml_text[:match.start()] + xml_text[match.end():]
    if _DOCTYPE_TOKEN.search(stripped):
        raise ValueError("Only one external ownership DTD declaration is permitted")
    return stripped
