import json,re
cfg=json.load(open('astrbot_plugin_digital_memory_store/_conf_schema.json',encoding='utf-8'))
print('config keys:',len(cfg))
src=open('astrbot_plugin_digital_memory_store/main.py',encoding='utf-8').read()
print('arg branches:',len(re.findall(r"if arg(?:==|\.startswith\()",src)))
print('subcommands in _work+dlm:',len(set(re.findall(r"arg\.startswith\('([^']+)'\)",src))))
