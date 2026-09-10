"""Python static analyzer — Tree-sitter two-pass. Phase 3."""

from __future__ import annotations

import hashlib
from typing import Any

from tree_sitter import Node

from coordinator.tools.analyzer_base import LanguageAnalyzer, RelationshipSet, SymbolTable
from coordinator.tools.parser_registry import get_parser

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _f(node: Node, field: str) -> Node | None:
    return node.child_by_field_name(field)


def _ftext(node: Node, field: str, src: bytes) -> str | None:
    c = _f(node, field)
    return None if c is None else _text(c, src)


def _node_id(lang: str, qname: str) -> str:
    return f"{lang}:{qname}"


def _edge_id(src_id: str, tgt_id: str, kind: str) -> str:
    return hashlib.sha256(f"{src_id}\x00{tgt_id}\x00{kind}".encode()).hexdigest()[:20]


def _add_to_name_index(st: SymbolTable, name: str, node_id: str) -> None:
    st.name_index.setdefault(name, [])
    if node_id not in st.name_index[name]:
        st.name_index[name].append(node_id)


def _make_edge(
    src_id: str,
    tgt_id: str,
    kind: str,
    path: str,
    line: int,
    resolution: str,
) -> dict[str, Any]:
    return {
        "id": _edge_id(src_id, tgt_id, kind),
        "sourceId": src_id,
        "targetId": tgt_id,
        "kind": kind,
        "resolution": resolution,
        "confidence": 1.0 if resolution == "exact" else 0.6 if resolution == "heuristic" else 0.3,
        "path": path,
        "startLine": line,
        "endLine": line,
        "attributes": {"callSiteCount": 1},
    }


# ---------------------------------------------------------------------------
# Path → module name
# ---------------------------------------------------------------------------

_STRIP_PREFIXES = ("src/", "lib/", "app/", "python/", "source/", "main/")


def _path_to_module(file_path: str) -> str:
    p = file_path
    for pfx in _STRIP_PREFIXES:
        if p.startswith(pfx):
            p = p[len(pfx) :]
    if p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".").replace("\\", ".")


# ---------------------------------------------------------------------------
# Analysis context (mutable, passed by reference through the walk)
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, file_path: str, module_name: str, src: bytes) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.src = src
        # [(class_id, class_name, qualified_class_name)]
        self.class_stack: list[tuple[str, str, str]] = []
        # [(func_id, func_name)]
        self.func_stack: list[tuple[str, str]] = []
        # alias → qualified
        self.imports: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Pass 1 helpers
# ---------------------------------------------------------------------------


def _register_node(
    st: SymbolTable,
    node_id: str,
    kind: str,
    name: str,
    qname: str,
    lang: str,
    path: str,
    start: int,
    end: int,
    sig: str,
    parent_id: str | None,
    attrs: dict | None = None,
) -> None:
    st.nodes[node_id] = {
        "id": node_id,
        "kind": kind,
        "name": name,
        "qualifiedName": qname,
        "language": lang,
        "path": path,
        "startLine": start,
        "endLine": end,
        "signature": sig,
        "parentId": parent_id,
        "attributes": attrs or {},
    }
    st.file_nodes.setdefault(path, [])
    if node_id not in st.file_nodes[path]:
        st.file_nodes[path].append(node_id)
    _add_to_name_index(st, name, node_id)


# ---------------------------------------------------------------------------
# PythonAnalyzer
# ---------------------------------------------------------------------------


class PythonAnalyzer(LanguageAnalyzer):
    """Two-pass Python analyzer using Tree-sitter."""

    @property
    def language(self) -> str:
        return "python"

    # ------------------------------------------------------------------
    # Pass 1: declarations
    # ------------------------------------------------------------------

    def pass1_declarations(self, file_path: str, source: bytes, symbol_table: SymbolTable) -> None:
        parser = get_parser("python")
        tree = parser.parse(source)
        module_name = _path_to_module(file_path)
        ctx = _Ctx(file_path, module_name, source)

        total_lines = source.count(b"\n") + 1
        file_id = _node_id("python", file_path)
        mod_id = _node_id("python", module_name)

        _register_node(
            symbol_table,
            file_id,
            "file",
            file_path.split("/")[-1],
            file_path,
            "python",
            file_path,
            1,
            total_lines,
            "",
            None,
        )
        _register_node(
            symbol_table,
            mod_id,
            "module",
            module_name.split(".")[-1],
            module_name,
            "python",
            file_path,
            1,
            total_lines,
            "",
            file_id,
        )

        self._p1_walk(tree.root_node, ctx, symbol_table)

    def _p1_walk(self, node: Node, ctx: _Ctx, st: SymbolTable) -> None:
        t = node.type
        if t == "class_definition":
            self._p1_class(node, ctx, st)
        elif t == "function_definition" and not ctx.class_stack:
            self._p1_func(node, ctx, st, "module")
        elif t == "decorated_definition":
            defn = _f(node, "definition")
            if defn:
                if defn.type == "class_definition":
                    self._p1_class(defn, ctx, st)
                elif defn.type == "function_definition" and not ctx.class_stack:
                    self._p1_func(defn, ctx, st, "module")
        elif t in ("import_statement", "import_from_statement"):
            self._p1_import(node, ctx, st)
        else:
            for child in node.children:
                self._p1_walk(child, ctx, st)

    def _p1_class(self, node: Node, ctx: _Ctx, st: SymbolTable) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        cname = _text(name_node, ctx.src)

        if ctx.class_stack:
            parent_qname = ctx.class_stack[-1][2]
            qname = f"{parent_qname}.{cname}"
            parent_id = ctx.class_stack[-1][0]
        else:
            qname = f"{ctx.module_name}.{cname}" if ctx.module_name else cname
            parent_id = _node_id("python", ctx.module_name) if ctx.module_name else None

        class_id = _node_id("python", qname)
        bases: list[str] = []
        sc = _f(node, "superclasses")
        if sc:
            for ch in sc.children:
                if ch.type in ("identifier", "attribute"):
                    bases.append(_text(ch, ctx.src))

        _register_node(
            st,
            class_id,
            "class",
            cname,
            qname,
            "python",
            ctx.file_path,
            node.start_point[0] + 1,
            node.end_point[0] + 1,
            cname,
            parent_id,
            {"bases": bases},
        )
        st.class_members.setdefault(class_id, {})

        ctx.class_stack.append((class_id, cname, qname))
        body = _f(node, "body")
        if body:
            for ch in body.children:
                if ch.type == "function_definition":
                    self._p1_func(ch, ctx, st, "class")
                elif ch.type == "decorated_definition":
                    defn = _f(ch, "definition")
                    if defn and defn.type == "function_definition":
                        self._p1_func(defn, ctx, st, "class")
                elif ch.type == "class_definition":
                    self._p1_class(ch, ctx, st)
        ctx.class_stack.pop()

    def _p1_func(self, node: Node, ctx: _Ctx, st: SymbolTable, parent_kind: str) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        fname = _text(name_node, ctx.src)

        if parent_kind == "class" and ctx.class_stack:
            parent_qname = ctx.class_stack[-1][2]
            qname = f"{parent_qname}.{fname}"
            kind = "method"
            parent_id = ctx.class_stack[-1][0]
        else:
            qname = f"{ctx.module_name}.{fname}" if ctx.module_name else fname
            kind = "function"
            parent_id = _node_id("python", ctx.module_name) if ctx.module_name else None

        func_id = _node_id("python", qname)
        params = _extract_python_params(node, ctx.src)
        sig = f"{fname}({', '.join(params)})"

        _register_node(
            st,
            func_id,
            kind,
            fname,
            qname,
            "python",
            ctx.file_path,
            node.start_point[0] + 1,
            node.end_point[0] + 1,
            sig,
            parent_id,
        )
        if parent_kind == "class" and ctx.class_stack:
            st.class_members[ctx.class_stack[-1][0]][fname] = func_id

    def _p1_import(self, node: Node, ctx: _Ctx, st: SymbolTable) -> None:
        imp = st.file_imports.setdefault(ctx.file_path, {})
        src = ctx.src

        if node.type == "import_statement":
            for ch in node.children:
                if ch.type == "aliased_import":
                    name = _ftext(ch, "name", src) or ""
                    alias = _ftext(ch, "alias", src) or name.split(".")[-1]
                    imp[alias] = name
                elif ch.type in ("dotted_name", "identifier"):
                    name = _text(ch, src)
                    if name not in ("import",):
                        imp[name.split(".")[0]] = name

        elif node.type == "import_from_statement":
            mod_node = _f(node, "module_name")
            if mod_node is None:
                return
            mod = _text(mod_node, src).lstrip(".")
            for ch in node.children:
                if ch.type == "aliased_import":
                    name = _ftext(ch, "name", src) or ""
                    alias = _ftext(ch, "alias", src) or name
                    imp[alias] = f"{mod}.{name}" if mod else name
                elif ch.type in ("dotted_name", "identifier"):
                    n = _text(ch, src)
                    if n not in ("from", "import", mod) and n:
                        imp[n] = f"{mod}.{n}" if mod else n
                elif ch.type == "wildcard_import":
                    imp["*"] = mod

    # ------------------------------------------------------------------
    # Pass 2: relationships
    # ------------------------------------------------------------------

    def pass2_relationships(
        self,
        file_path: str,
        source: bytes,
        symbol_table: SymbolTable,
        relationships: RelationshipSet,
    ) -> None:
        parser = get_parser("python")
        tree = parser.parse(source)
        module_name = _path_to_module(file_path)
        ctx = _Ctx(file_path, module_name, source)
        ctx.imports = dict(symbol_table.file_imports.get(file_path, {}))

        mod_id = _node_id("python", module_name)
        file_id = _node_id("python", file_path)

        # CONTAINS: file → module
        relationships.add(_make_edge(file_id, mod_id, "CONTAINS", file_path, 1, "exact"))

        # CONTAINS: parent → children, IMPORTS
        for nid in symbol_table.file_nodes.get(file_path, []):
            nd = symbol_table.nodes.get(nid)
            if not nd:
                continue
            pid = nd.get("parentId")
            if pid and pid in symbol_table.nodes:
                relationships.add(
                    _make_edge(pid, nid, "CONTAINS", file_path, nd.get("startLine", 1), "exact")
                )

        # IMPORTS edges from module to imported symbols
        for alias, qname in ctx.imports.items():
            if alias == "*":
                continue
            tgt = _find_by_qname(qname, symbol_table)
            if tgt:
                relationships.add(_make_edge(mod_id, tgt, "IMPORTS", file_path, 1, "exact"))

        self._p2_walk(tree.root_node, ctx, symbol_table, relationships)

    def _p2_walk(self, node: Node, ctx: _Ctx, st: SymbolTable, rels: RelationshipSet) -> None:
        t = node.type
        if t == "class_definition":
            self._p2_class(node, ctx, st, rels)
            return
        if t in ("function_definition", "async_function_definition"):
            self._p2_func(node, ctx, st, rels)
            return
        if t == "decorated_definition":
            defn = _f(node, "definition")
            if defn:
                self._p2_walk(defn, ctx, st, rels)
            return
        if t == "call":
            self._p2_call(node, ctx, st, rels)
        for ch in node.children:
            self._p2_walk(ch, ctx, st, rels)

    def _p2_class(self, node: Node, ctx: _Ctx, st: SymbolTable, rels: RelationshipSet) -> None:
        """Create inheritance relationships and analyze a Python class body."""
        name_node = _f(node, "name")
        if name_node is None:
            return

        class_name = _text(name_node, ctx.src)

        if ctx.class_stack:
            qualified_name = f"{ctx.class_stack[-1][2]}.{class_name}"
        else:
            qualified_name = (
                f"{ctx.module_name}.{class_name}" if ctx.module_name else class_name
            )

        class_id = _node_id("python", qualified_name)
        if class_id not in st.nodes:
            return

        # INHERITS edges
        superclasses = _f(node, "superclasses")
        if superclasses:
            for child in superclasses.children:
                if child.type not in ("identifier", "attribute"):
                    continue

                base_name = _text(child, ctx.src)
                target_id = _resolve_name(base_name, ctx, st)

                if target_id:
                    target_kind = st.nodes.get(target_id, {}).get("kind")

                    if target_kind == "external_symbol":
                        resolution = "external"
                    elif target_kind == "class":
                        resolution = "exact"
                    else:
                        resolution = "heuristic"

                    rels.add(
                        _make_edge(
                            class_id,
                            target_id,
                            "INHERITS",
                            ctx.file_path,
                            node.start_point[0] + 1,
                            resolution,
                        )
                    )
                    continue

                external_id = f"python:external.{base_name}"
                _ensure_external(st, external_id, base_name, "python")
                rels.add(
                    _make_edge(
                        class_id,
                        external_id,
                        "INHERITS",
                        ctx.file_path,
                        node.start_point[0] + 1,
                        "external",
                    )
                )

        ctx.class_stack.append((class_id, class_name, qualified_name))
        body = _f(node, "body")
        if body:
            for child in body.children:
                self._p2_walk(child, ctx, st, rels)
        ctx.class_stack.pop()

    def _p2_func(self, node: Node, ctx: _Ctx, st: SymbolTable, rels: RelationshipSet) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        fname = _text(name_node, ctx.src)
        if ctx.class_stack:
            func_id = st.class_members.get(ctx.class_stack[-1][0], {}).get(fname)
        else:
            qname = f"{ctx.module_name}.{fname}" if ctx.module_name else fname
            func_id = _node_id("python", qname)
        if func_id is None:
            return
        ctx.func_stack.append((func_id, fname))
        body = _f(node, "body")
        if body:
            self._p2_walk(body, ctx, st, rels)
        ctx.func_stack.pop()

    def _p2_call(self, node: Node, ctx: _Ctx, st: SymbolTable, rels: RelationshipSet) -> None:
        """Resolve a Python call expression and create a CALLS edge."""
        if ctx.func_stack:
            source_id = ctx.func_stack[-1][0]
        elif ctx.class_stack:
            source_id = ctx.class_stack[-1][0]
        else:
            source_id = _node_id("python", ctx.module_name)

        if source_id not in st.nodes:
            return

        line = node.start_point[0] + 1
        function_node = _f(node, "function")
        if function_node is None:
            return

        # Direct call: helper()
        if function_node.type == "identifier":
            name = _text(function_node, ctx.src)
            target_id = _resolve_name(name, ctx, st)

            if target_id:
                target_kind = st.nodes.get(target_id, {}).get("kind")
                resolution = "external" if target_kind == "external_symbol" else "exact"
                rels.add(
                    _make_edge(
                        source_id,
                        target_id,
                        "CALLS",
                        ctx.file_path,
                        line,
                        resolution,
                    )
                )
                return

            external_id = f"python:external.{name}"
            _ensure_external(st, external_id, name, "python")
            rels.add(
                _make_edge(
                    source_id,
                    external_id,
                    "CALLS",
                    ctx.file_path,
                    line,
                    "external",
                )
            )
            return

        # Attribute call: service.process()
        if function_node.type == "attribute":
            object_node = _f(function_node, "object")
            attribute_node = _f(function_node, "attribute")
            if object_node is None or attribute_node is None:
                return

            object_text = _text(object_node, ctx.src)
            method_name = _text(attribute_node, ctx.src)
            target_id = _resolve_attr_call(object_text, method_name, ctx, st)

            if target_id:
                target_kind = st.nodes.get(target_id, {}).get("kind")
                if target_kind == "external_symbol":
                    resolution = "external"
                elif object_text in ("self", "cls"):
                    resolution = "exact"
                else:
                    resolution = "heuristic"

                rels.add(
                    _make_edge(
                        source_id,
                        target_id,
                        "CALLS",
                        ctx.file_path,
                        line,
                        resolution,
                    )
                )
                return

            external_id = f"python:external.{object_text}.{method_name}"
            _ensure_external(st, external_id, method_name, "python")
            rels.add(
                _make_edge(
                    source_id,
                    external_id,
                    "CALLS",
                    ctx.file_path,
                    line,
                    "external",
                )
            )


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_name(name: str, ctx: _Ctx, st: SymbolTable) -> str | None:
    """Resolve a Python identifier to a valid graph node ID."""
    # 1. Current module.
    qname = f"{ctx.module_name}.{name}" if ctx.module_name else name
    nid = _node_id("python", qname)
    if nid in st.nodes:
        return nid

    # 2. Imported name or alias.
    if name in ctx.imports:
        imported_qname = ctx.imports[name]
        imported_id = _find_by_qname(imported_qname, st)
        if imported_id:
            return imported_id

        external_id = f"python:external.{imported_qname}"
        _ensure_external(st, external_id, imported_qname, "python")
        return external_id

    # 3. Unique repository-wide name match.
    candidates = st.name_index.get(name, [])
    if len(candidates) == 1:
        candidate_id = candidates[0]
        if candidate_id in st.nodes:
            return candidate_id

    return None


def _resolve_attr_call(obj_text: str, mname: str, ctx: _Ctx, st: SymbolTable) -> str | None:
    """Resolve obj.method → node ID."""
    # self/cls → current class method
    if obj_text in ("self", "cls") and ctx.class_stack:
        cid = ctx.class_stack[-1][0]
        return st.class_members.get(cid, {}).get(mname)

    # obj is imported alias
    if obj_text in ctx.imports:
        qname = ctx.imports[obj_text]
        # Direct function/method lookup
        direct = _find_by_qname(f"{qname}.{mname}", st)
        if direct:
            return direct
        # qname might be a module; look for any class method matching mname
        for nid, nd in st.nodes.items():
            if nd.get("kind") in ("method", "function") and nd.get("name") == mname:
                qn = nd.get("qualifiedName", "")
                if qn.startswith(qname):
                    return nid

    # Heuristic: unique method name match
    candidates = [
        nid
        for nid, nd in st.nodes.items()
        if nd.get("kind") in ("method", "function") and nd.get("name") == mname
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_by_qname(qname: str, st: SymbolTable) -> str | None:
    nid = _node_id("python", qname)
    return nid if nid in st.nodes else None


def _ensure_external(st: SymbolTable, nid: str, name: str, lang: str) -> None:
    if nid not in st.nodes:
        st.nodes[nid] = {
            "id": nid,
            "kind": "external_symbol",
            "name": name,
            "qualifiedName": nid.split(":", 1)[1] if ":" in nid else nid,
            "language": lang,
            "path": "",
            "startLine": 0,
            "endLine": 0,
            "signature": "",
            "parentId": None,
            "attributes": {},
        }


def _extract_python_params(node: Node, src: bytes) -> list[str]:
    params_node = _f(node, "parameters")
    if params_node is None:
        return []
    params = []
    for p in params_node.children:
        if p.type in (
            "identifier",
            "typed_parameter",
            "default_parameter",
            "typed_default_parameter",
            "list_splat_pattern",
            "dictionary_splat_pattern",
        ):
            raw = _text(p, src)
            name = raw.split(":")[0].split("=")[0].lstrip("*").strip()
            if name and name not in (",", "(", ")"):
                params.append(name)
    return params
