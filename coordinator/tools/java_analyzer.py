"""Java static analyzer — Tree-sitter two-pass. Phase 4."""

from __future__ import annotations

from tree_sitter import Node

from coordinator.tools.analyzer_base import LanguageAnalyzer, RelationshipSet, SymbolTable
from coordinator.tools.parser_registry import get_parser
from coordinator.tools.python_analyzer import (
    _ensure_external,
    _make_edge,
    _node_id,
    _register_node,
    _text,
)


def _f(node: Node, field: str) -> Node | None:
    return node.child_by_field_name(field)


def _ftext(node: Node, field: str, src: bytes) -> str | None:
    c = _f(node, field)
    return None if c is None else _text(c, src)


def _children_of_type(node: Node, *types: str) -> list[Node]:
    return [ch for ch in node.children if ch.type in types]


def _find_by_qname(qname: str, st: SymbolTable) -> str | None:
    nid = _node_id("java", qname)
    return nid if nid in st.nodes else None


# ---------------------------------------------------------------------------
# Java analysis context
# ---------------------------------------------------------------------------


class _JCtx:
    def __init__(self, file_path: str, package: str, src: bytes) -> None:
        self.file_path = file_path
        self.package = package
        self.src = src
        # [(class_id, class_name, qualified_class_name)]
        self.class_stack: list[tuple[str, str, str]] = []
        # [(method_id, method_name)]
        self.method_stack: list[tuple[str, str]] = []
        # import alias → qualified name
        self.imports: dict[str, str] = {}
        # type_name → qualified_name (from imports, classes in same package)
        self.type_map: dict[str, str] = {}
        # local variable type hints: var_name → type_name (within current method)
        self.local_vars: dict[str, str] = {}


# ---------------------------------------------------------------------------
# JavaAnalyzer
# ---------------------------------------------------------------------------


class JavaAnalyzer(LanguageAnalyzer):
    """Two-pass Java analyzer using Tree-sitter."""

    @property
    def language(self) -> str:
        return "java"

    # ------------------------------------------------------------------
    # Pass 1: declarations
    # ------------------------------------------------------------------

    def pass1_declarations(self, file_path: str, source: bytes, symbol_table: SymbolTable) -> None:
        parser = get_parser("java")
        tree = parser.parse(source)
        src = source

        # Extract package name
        package = _extract_package(tree.root_node, src)
        ctx = _JCtx(file_path, package, src)

        total_lines = source.count(b"\n") + 1
        file_id = _node_id("java", file_path)
        _register_node(
            symbol_table,
            file_id,
            "file",
            file_path.split("/")[-1],
            file_path,
            "java",
            file_path,
            1,
            total_lines,
            "",
            None,
        )

        if package:
            pkg_id = _node_id("java", package)
            if pkg_id not in symbol_table.nodes:
                _register_node(
                    symbol_table,
                    pkg_id,
                    "package",
                    package.split(".")[-1],
                    package,
                    "java",
                    file_path,
                    1,
                    total_lines,
                    "",
                    file_id,
                )
            symbol_table.file_nodes.setdefault(file_path, [])
            if pkg_id not in symbol_table.file_nodes[file_path]:
                symbol_table.file_nodes[file_path].append(pkg_id)

        # Extract imports for pass-1 type-map
        _extract_imports_p1(tree.root_node, ctx, symbol_table)

        # Walk type declarations
        for node in tree.root_node.children:
            if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                self._p1_type(node, ctx, symbol_table, parent_id=file_id)

    def _p1_type(
        self,
        node: Node,
        ctx: _JCtx,
        st: SymbolTable,
        parent_id: str | None,
    ) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        cname = _text(name_node, ctx.src)

        if ctx.class_stack:
            qname = f"{ctx.class_stack[-1][2]}.{cname}"
            actual_parent = ctx.class_stack[-1][0]
        else:
            qname = f"{ctx.package}.{cname}" if ctx.package else cname
            actual_parent = parent_id

        kind = "interface" if node.type == "interface_declaration" else "class"
        class_id = _node_id("java", qname)

        bases: list[str] = []
        ifaces: list[str] = []
        sc = _f(node, "superclass")
        if sc:
            bases.append(_text(sc, ctx.src))
        si = _f(node, "interfaces") or _f(node, "extends_interfaces")
        if si:
            for ch in si.children:
                if ch.type in ("type_identifier", "generic_type"):
                    nm = _ftext(ch, "name", ctx.src) or _text(ch, ctx.src)
                    ifaces.append(nm.split("<")[0])

        _register_node(
            st,
            class_id,
            kind,
            cname,
            qname,
            "java",
            ctx.file_path,
            node.start_point[0] + 1,
            node.end_point[0] + 1,
            cname,
            actual_parent,
            {"bases": bases, "interfaces": ifaces},
        )
        st.class_members.setdefault(class_id, {})
        ctx.type_map[cname] = qname

        ctx.class_stack.append((class_id, cname, qname))
        body = _f(node, "body") or _f(node, "class_body")
        if body:
            for ch in body.children:
                if ch.type in (
                    "method_declaration",
                    "constructor_declaration",
                    "abstract_method_declaration",
                ):
                    self._p1_method(ch, ctx, st)
                elif ch.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                    self._p1_type(ch, ctx, st, parent_id=class_id)
        ctx.class_stack.pop()

    def _p1_method(self, node: Node, ctx: _JCtx, st: SymbolTable) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        mname = _text(name_node, ctx.src)

        class_id, _, class_qname = ctx.class_stack[-1] if ctx.class_stack else (None, None, None)
        if class_qname is None:
            return

        # Compute arity from formal_parameters
        params_node = _f(node, "parameters") or _f(node, "formal_parameters")
        params: list[str] = []
        param_types: list[str] = []
        if params_node:
            for p in params_node.children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    ptype_node = _f(p, "type") or _f(p, "dimensions")
                    pname_node = _f(p, "name")
                    ptype = _text(ptype_node, ctx.src).split("<")[0] if ptype_node else "?"
                    pname = _text(pname_node, ctx.src) if pname_node else "?"
                    params.append(pname)
                    param_types.append(ptype)

        arity = len(params)
        qname = f"{class_qname}#{mname}/{arity}"
        method_id = _node_id("java", qname)

        kind = (
            "method"
            if node.type in ("method_declaration", "abstract_method_declaration")
            else "method"  # constructors also become "method"
        )

        ret_type_node = _f(node, "type")
        ret_type = _text(ret_type_node, ctx.src) if ret_type_node else "void"
        sig = f"{ret_type} {mname}({', '.join(param_types)})"

        _register_node(
            st,
            method_id,
            kind,
            mname,
            qname,
            "java",
            ctx.file_path,
            node.start_point[0] + 1,
            node.end_point[0] + 1,
            sig,
            class_id,
        )
        # Register multiple arities under same name
        st.class_members.setdefault(class_id, {})
        st.class_members[class_id][f"{mname}/{arity}"] = method_id
        # Also register plain name (last one wins, used for heuristic)
        st.class_members[class_id][mname] = method_id
        # Store param types for arity-based resolution
        st.nodes[method_id]["attributes"]["paramTypes"] = param_types

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
        parser = get_parser("java")
        tree = parser.parse(source)
        package = _extract_package(tree.root_node, source)
        ctx = _JCtx(file_path, package, source)
        ctx.imports = dict(symbol_table.file_imports.get(file_path, {}))
        ctx.type_map = {
            nd["name"]: nd["qualifiedName"]
            for nd in symbol_table.nodes.values()
            if nd.get("language") == "java"
            and nd.get("kind") in ("class", "interface")
            and nd["qualifiedName"].startswith(package + "." if package else "")
        }
        # Add imports to type_map
        for alias, qname in ctx.imports.items():
            ctx.type_map[alias] = qname

        file_id = _node_id("java", file_path)

        # CONTAINS edges
        for nid in symbol_table.file_nodes.get(file_path, []):
            nd = symbol_table.nodes.get(nid)
            if not nd:
                continue
            pid = nd.get("parentId")
            if pid and pid in symbol_table.nodes:
                relationships.add(
                    _make_edge(pid, nid, "CONTAINS", file_path, nd.get("startLine", 1), "exact")
                )

        # IMPORTS edges
        for alias, qname in ctx.imports.items():
            tgt = _find_by_qname(qname, symbol_table)
            if tgt:
                mod_id = _node_id("java", package) if package else file_id
                if mod_id in symbol_table.nodes:
                    relationships.add(_make_edge(mod_id, tgt, "IMPORTS", file_path, 1, "exact"))

        # Walk type declarations for inheritance + calls
        for node in tree.root_node.children:
            if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                self._p2_type(node, ctx, symbol_table, relationships)

    def _p2_type(
        self,
        node: Node,
        ctx: _JCtx,
        st: SymbolTable,
        rels: RelationshipSet,
    ) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        cname = _text(name_node, ctx.src)
        if ctx.class_stack:
            qname = f"{ctx.class_stack[-1][2]}.{cname}"
        else:
            qname = f"{ctx.package}.{cname}" if ctx.package else cname
        class_id = _node_id("java", qname)

        # INHERITS
        sc = _f(node, "superclass")
        if sc:
            bname = _text(sc, ctx.src).split("<")[0]
            tgt = _resolve_type(bname, ctx, st)
            if tgt and st.nodes.get(tgt, {}).get("kind") == "class":
                rels.add(
                    _make_edge(
                        class_id, tgt, "INHERITS", ctx.file_path, node.start_point[0] + 1, "exact"
                    )
                )
            elif tgt:
                rels.add(
                    _make_edge(
                        class_id,
                        tgt,
                        "INHERITS",
                        ctx.file_path,
                        node.start_point[0] + 1,
                        "heuristic",
                    )
                )

        # IMPLEMENTS
        si = _f(node, "interfaces") or _f(node, "extends_interfaces")
        if si:
            for ch in si.children:
                if ch.type in ("type_identifier", "generic_type"):
                    iname = (_ftext(ch, "name", ctx.src) or _text(ch, ctx.src)).split("<")[0]
                    tgt = _resolve_type(iname, ctx, st)
                    edge_kind = "IMPLEMENTS" if node.type == "class_declaration" else "INHERITS"
                    if tgt:
                        rels.add(
                            _make_edge(
                                class_id,
                                tgt,
                                edge_kind,
                                ctx.file_path,
                                node.start_point[0] + 1,
                                "exact",
                            )
                        )

        ctx.class_stack.append((class_id, cname, qname))
        body = _f(node, "body") or _f(node, "class_body")
        if body:
            for ch in body.children:
                if ch.type in ("method_declaration", "constructor_declaration"):
                    self._p2_method(ch, ctx, st, rels)
                elif ch.type in ("class_declaration", "interface_declaration"):
                    self._p2_type(ch, ctx, st, rels)
        ctx.class_stack.pop()

    def _p2_method(
        self,
        node: Node,
        ctx: _JCtx,
        st: SymbolTable,
        rels: RelationshipSet,
    ) -> None:
        name_node = _f(node, "name")
        if name_node is None:
            return
        mname = _text(name_node, ctx.src)
        if not ctx.class_stack:
            return

        # Find our method id by arity
        params_node = _f(node, "parameters") or _f(node, "formal_parameters")
        arity = 0
        local_types: dict[str, str] = {}
        if params_node:
            for p in params_node.children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    arity += 1
                    ptype_node = _f(p, "type")
                    pname_node = _f(p, "name")
                    if ptype_node and pname_node:
                        local_types[_text(pname_node, ctx.src)] = _text(ptype_node, ctx.src).split(
                            "<"
                        )[0]

        method_qname = f"{ctx.class_stack[-1][2]}#{mname}/{arity}"
        method_id = _node_id("java", method_qname)
        if method_id not in st.nodes:
            return

        ctx.method_stack.append((method_id, mname))
        ctx.local_vars = {**local_types}
        body = _f(node, "body")
        if body:
            self._p2_body(body, ctx, st, rels)
        ctx.method_stack.pop()

    def _p2_body(self, node: Node, ctx: _JCtx, st: SymbolTable, rels: RelationshipSet) -> None:
        if node.type == "local_variable_declaration":
            # Track variable types for resolution
            type_node = _f(node, "type")
            if type_node:
                tname = _text(type_node, ctx.src).split("<")[0]
                for ch in node.children:
                    if ch.type == "variable_declarator":
                        vname_node = _f(ch, "name")
                        if vname_node:
                            ctx.local_vars[_text(vname_node, ctx.src)] = tname
            for ch in node.children:
                self._p2_body(ch, ctx, st, rels)
            return

        if node.type == "method_invocation":
            self._p2_invocation(node, ctx, st, rels)

        for ch in node.children:
            self._p2_body(ch, ctx, st, rels)

    def _p2_invocation(
        self, node: Node, ctx: _JCtx, st: SymbolTable, rels: RelationshipSet
    ) -> None:
        if not ctx.method_stack:
            return
        src_id = ctx.method_stack[-1][0]
        line = node.start_point[0] + 1

        name_node = _f(node, "name")
        if name_node is None:
            return
        mname = _text(name_node, ctx.src)

        obj_node = _f(node, "object")
        args_node = _f(node, "arguments")
        arity = _count_args(args_node, ctx.src) if args_node else 0

        if obj_node is None or _text(obj_node, ctx.src) in ("this", "super"):
            # Call within the same class
            if not ctx.class_stack:
                return
            class_id = ctx.class_stack[-1][0]
            tgt = _find_class_method(class_id, mname, arity, st)
            if tgt:
                rels.add(_make_edge(src_id, tgt, "CALLS", ctx.file_path, line, "exact"))
                return
            # Try superclass
            class_nd = st.nodes.get(class_id, {})
            for base in class_nd.get("attributes", {}).get("bases", []):
                base_tgt = _resolve_type(base, ctx, st)
                if base_tgt:
                    base_method = _find_class_method(base_tgt, mname, arity, st)
                    if base_method:
                        rels.add(
                            _make_edge(src_id, base_method, "CALLS", ctx.file_path, line, "exact")
                        )
                        return
            # Heuristic
            h = _heuristic_method(mname, arity, st)
            if h:
                rels.add(_make_edge(src_id, h, "CALLS", ctx.file_path, line, "heuristic"))
                return
            ext = f"java:external.{mname}/{arity}"
            _ensure_external(st, ext, mname, "java")
            rels.add(_make_edge(src_id, ext, "CALLS", ctx.file_path, line, "external"))
        else:
            obj_text = _text(obj_node, ctx.src)
            # Check local variable type
            tname = ctx.local_vars.get(obj_text)
            if tname is None:
                # Might be a class name (static call)
                tname = obj_text
            # Resolve type to qualified name
            tgt_class = _resolve_type(tname, ctx, st)
            if tgt_class:
                tgt = _find_class_method(tgt_class, mname, arity, st)
                if tgt:
                    rels.add(_make_edge(src_id, tgt, "CALLS", ctx.file_path, line, "exact"))
                    return
                # Heuristic within resolved class hierarchy
                rels.add(_make_edge(src_id, tgt_class, "CALLS", ctx.file_path, line, "heuristic"))
                return
            # Heuristic name match
            h = _heuristic_method(mname, arity, st)
            if h:
                rels.add(_make_edge(src_id, h, "CALLS", ctx.file_path, line, "heuristic"))
                return
            ext = f"java:external.{obj_text}.{mname}/{arity}"
            _ensure_external(st, ext, mname, "java")
            rels.add(_make_edge(src_id, ext, "CALLS", ctx.file_path, line, "external"))


# ---------------------------------------------------------------------------
# Java-specific helpers
# ---------------------------------------------------------------------------


def _extract_package(root: Node, src: bytes) -> str:
    for ch in root.children:
        if ch.type == "package_declaration":
            for sub in ch.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    return _text(sub, src)
    return ""


def _extract_imports_p1(root: Node, ctx: _JCtx, st: SymbolTable) -> None:
    st.file_imports.setdefault(ctx.file_path, {})
    for ch in root.children:
        if ch.type == "import_declaration":
            # `import static? qualified_name ;`
            parts = [
                _text(sub, ctx.src)
                for sub in ch.children
                if sub.type in ("scoped_identifier", "identifier")
            ]
            if not parts:
                continue
            qname = parts[0]
            simple = qname.split(".")[-1]
            if simple != "*":
                st.file_imports[ctx.file_path][simple] = qname
                ctx.imports[simple] = qname


def _resolve_type(tname: str, ctx: _JCtx, st: SymbolTable) -> str | None:
    qname = ctx.type_map.get(tname)
    if qname:
        nid = _node_id("java", qname)
        if nid in st.nodes:
            return nid
    # Try same package
    if ctx.package:
        nid = _node_id("java", f"{ctx.package}.{tname}")
        if nid in st.nodes:
            return nid
    # Name index
    candidates = st.name_index.get(tname, [])
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_class_method(class_id: str, mname: str, arity: int, st: SymbolTable) -> str | None:
    members = st.class_members.get(class_id, {})
    # Exact arity
    mid = members.get(f"{mname}/{arity}")
    if mid and mid in st.nodes:
        return mid
    # Plain name fallback
    mid = members.get(mname)
    if mid and mid in st.nodes:
        return mid
    return None


def _heuristic_method(mname: str, arity: int, st: SymbolTable) -> str | None:
    exact = [
        nid
        for nid, nd in st.nodes.items()
        if nd.get("kind") == "method"
        and nd.get("name") == mname
        and nd.get("qualifiedName", "").endswith(f"/{arity}")
    ]
    if len(exact) == 1:
        return exact[0]
    any_arity = [
        nid
        for nid, nd in st.nodes.items()
        if nd.get("kind") == "method" and nd.get("name") == mname
    ]
    if len(any_arity) == 1:
        return any_arity[0]
    return None


def _count_args(args_node: Node, src: bytes) -> int:
    return sum(1 for ch in args_node.children if ch.type not in (",", "(", ")", "comment"))
