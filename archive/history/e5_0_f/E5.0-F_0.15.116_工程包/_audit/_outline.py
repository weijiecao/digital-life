import ast, pathlib, collections
root = pathlib.Path('astrbot_plugin_digital_memory_store')
rows=[]
for p in sorted(root.rglob('*.py')):
    src=p.read_text(encoding='utf-8')
    try: tree=ast.parse(src)
    except SyntaxError as e:
        print('SYNTAX ERROR', p, e); continue
    funcs=[n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    classes=[n.name for n in tree.body if isinstance(n,ast.ClassDef)]
    methods=sum(len([m for m in c.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef))]) for c in tree.body if isinstance(c,ast.ClassDef))
    rows.append((str(p), len(src.splitlines()), len(funcs), len(classes), methods, funcs[:6], classes))
for r in rows:
    print(f"{r[1]:5} lines | f={r[2]:3} c={r[3]} m={r[4]:4} | {r[0]}")
print('\n=== top-level classes + their method counts ===')
for p in sorted(root.rglob('*.py')):
    tree=ast.parse(p.read_text(encoding='utf-8'))
    for n in tree.body:
        if isinstance(n,ast.ClassDef):
            ms=[m.name for m in n.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef))]
            print(f"{p.name}::{n.name} ({len(ms)} methods)")
