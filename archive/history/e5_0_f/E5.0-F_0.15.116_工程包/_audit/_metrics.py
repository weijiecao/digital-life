import pathlib, statistics
root=pathlib.Path('astrbot_plugin_digital_memory_store')
tot=0; long=0; semi=0; lines_all=0
per=[]
for p in sorted(root.rglob('*.py')):
    txt=p.read_text(encoding='utf-8'); ls=txt.splitlines()
    if not ls: continue
    lines_all+=len(ls); tot+=len(txt)
    l200=sum(1 for l in ls if len(l)>200)
    s=sum(1 for l in ls if l.count(';')>=2)
    long+=l200; semi+=s
    per.append((len(ls), len(txt)//max(1,len(ls)), l200, s, p.name))
print('total py lines', lines_all, 'bytes', tot)
print('lines >200 chars:', long, '  lines with >=2 semicolons:', semi)
print('\nworst files: lines, avg bytes/line, >200chars, multi-semicolon')
for r in sorted(per, reverse=True)[:8]:
    print(f"  {r[0]:5} {r[1]:4} {r[2]:4} {r[3]:5}  {r[4]}")
# dead subtree size
dead=[root/'engine'/'e50b'/'evaluate.py',root/'engine'/'e50b'/'snapshot.py',root/'engine'/'e50g'/'adapter.py',root/'engine'/'e421'/'progressive.py',root/'engine'/'e50b'/'fixture'/'reviewed_fixture.json']
print('\ndead subtree files:')
s=0
for f in dead:
    if f.exists(): s+=f.stat().st_size; print(' ',f.relative_to(root), f.stat().st_size)
print('  total bytes', s)
