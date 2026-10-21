"""Narrow XML preprocessing for SEC ownership documents."""

import re


_DOCTYPE_TOKEN = re.compile(r"<!\s*DOCTYPE\b", flags=re.IGNORECASE)
_ENTITY_TOKEN = re.compile(r"<!\s*ENTITY\b", flags=re.IGNORECASE)
def strip_external_ownership_doctype(xml_text: str) -> str:
    """Strip one ownership DTD without resolving or processing declarations.

    SEC ownership filings commonly declare their external DTD. We never load
    that DTD. Entity declarations are prohibited, including inside a subset.
    """
    if _ENTITY_TOKEN.search(xml_text):
        raise ValueError("Entity declarations are not permitted")

    declarations = list(_DOCTYPE_TOKEN.finditer(xml_text))
    if not declarations:
        return xml_text
    if len(declarations) != 1:
        raise ValueError("Only one external ownership DTD declaration is permitted")

    start = declarations[0].start()
    root_match = re.match(r"<!\s*DOCTYPE\s+([A-Za-z_:][A-Za-z0-9_.:-]*)", xml_text[start:], re.IGNORECASE)
    if root_match is None or root_match.group(1) != "ownershipDocument":
        raise ValueError("Only an ownershipDocument DTD declaration is permitted")

    # Find the declaration end without treating a '>' inside quotes or an
    # internal subset as the end. No DTD content is passed to the XML parser.
    quote = None
    subset_depth = 0
    end = None
    for index in range(start + root_match.end(), len(xml_text)):
        char = xml_text[index]
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "[":
            subset_depth += 1
        elif char == "]" and subset_depth:
            subset_depth -= 1
        elif char == ">" and subset_depth == 0:
            end = index + 1
            break
    if end is None or quote or subset_depth:
        raise ValueError("Malformed ownershipDocument DTD declaration")

    stripped = xml_text[:start] + xml_text[end:]
    if _DOCTYPE_TOKEN.search(stripped):
        raise ValueError("Only one external ownership DTD declaration is permitted")
    return stripped
