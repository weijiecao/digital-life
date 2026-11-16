import ast
p='astrbot_plugin_digital_memory_store/storage.py'
t=ast.parse(open(p,encoding='utf-8').read())
for c in t.body:
    if isinstance(c,ast.ClassDef):
        for m in c.body:
            if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)):
                doc=(ast.get_docstring(m) or '').split('\n')[0][:60]
                print(f"L{m.lineno:<5} {m.name:<42} {doc}")
