"""Parser registry — maps language identifiers to Tree-sitter Language objects.

Verifies that both Python and Java grammars load correctly at import time.
"""

from __future__ import annotations

from tree_sitter import Language, Parser
from tree_sitter_language_pack import get_language

# ---------------------------------------------------------------------------
# Grammar registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, Language] = {}


def _load_grammar(lang: str) -> Language:
    if lang not in _REGISTRY:
        _REGISTRY[lang] = get_language(lang)
    return _REGISTRY[lang]


def get_parser(language: str) -> Parser:
    """Return a configured Tree-sitter Parser for the given language."""
    lang_obj = _load_grammar(language)
    return Parser(lang_obj)


def supported_languages() -> list[str]:
    return ["python", "java"]


# ---------------------------------------------------------------------------
# Smoke-test on import: fail loudly if grammars are broken
# ---------------------------------------------------------------------------
def _smoke_test() -> None:
    for lang in supported_languages():
        parser = get_parser(lang)
        sample = b"class Foo {}" if lang == "java" else b"class Foo: pass"
        tree = parser.parse(sample)
        if tree.root_node is None:  # pragma: no cover
            raise RuntimeError(f"Tree-sitter grammar for {lang!r} failed smoke test")


_smoke_test()
