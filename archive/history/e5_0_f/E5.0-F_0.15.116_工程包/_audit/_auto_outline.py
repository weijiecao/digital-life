import ast, pathlib
root=pathlib.Path('astrbot_plugin_digital_memory_store')
for name in ('auto_organize.py','auto_topics.py','auto_cross_update.py','auto_model.py','auto_topology.py','lexical_index.py','auto_vector_chunks.py'):
    p=root/name
    tree=ast.parse(p.read_text(encoding='utf-8'))
    print('='*70); print(name, len(p.read_text(encoding='utf-8').splitlines()),'lines')
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            doc=(ast.get_docstring(n) or '').split('\n')[0][:70]
            args=[a.arg for a in n.args.args]
            print(f"  L{n.lineno:<5} {n.name}({', '.join(args)})  {doc}")
