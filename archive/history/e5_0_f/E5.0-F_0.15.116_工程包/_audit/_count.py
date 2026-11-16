import ast
for f in ('test_store.py','test_plugin.py'):
    t=ast.parse(open(f,encoding='utf-8').read())
    n=sum(1 for c in t.body if isinstance(c,ast.ClassDef) for m in c.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and m.name.startswith('test'))
    cls=[c.name for c in t.body if isinstance(c,ast.ClassDef)]
    print(f, 'test methods:', n, 'classes:', cls)
