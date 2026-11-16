import ast, re, collections
names=[]
for f in ('test_store.py','test_plugin.py'):
    t=ast.parse(open(f,encoding='utf-8').read())
    for c in t.body:
        if isinstance(c,ast.ClassDef):
            for m in c.body:
                if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and m.name.startswith('test_'):
                    names.append(m.name)
themes={'auto/topic':r'auto|topic','cross/update':r'cross|update|correct|hint','model':r'model','backup/recover':r'backup|recover|restore|verify','gap':r'gap','context/budget':r'context|budget|packet|followup','capture':r'capture|import|whitespace','plugin/smoke':r'plugin|command|hook'}
for k,p in themes.items():
    hits=[n for n in names if re.search(p,n)]
    print(f'{k:<14} {len(hits):3}')
print('total',len(names))
print()
print('\n'.join(sorted(h for h in names if re.search(r'auto|topic',h))))
