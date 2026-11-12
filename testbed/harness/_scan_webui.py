import io
import re
from pathlib import Path

root = Path(r'C:\Users\205\Desktop\NapCat.Shell\static')
routes = set()
for path in root.rglob('*.js'):
    text = path.read_text(encoding='utf-8', errors='replace')
    for match in re.finditer(r'["\'`](/?(?:api|API)/[A-Za-z0-9_/\-]+)["\'`]', text):
        routes.add(match.group(1))
for route in sorted(routes):
    print(route)
print('--- OB11 mentions ---')
for path in root.rglob('*.js'):
    text = path.read_text(encoding='utf-8', errors='replace')
    for match in re.finditer(r'OB11[A-Za-z]*', text):
        print(path.name, match.group(0))
        break
