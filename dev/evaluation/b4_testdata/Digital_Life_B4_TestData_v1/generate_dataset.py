#!/usr/bin/env python3
"""Deterministic SYNTHETIC adversarial B4 data generator. No real users/messages."""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))

CONFIGS = [
    ('四姑娘山大峰','大峰','四姑娘山二峰','二峰','石老人海滩','QQ记忆助手','来源索引','权限网关','阿榆','四姑娘山雪谷','Python动态规划'),
    ('哈巴雪山登顶','登顶','哈巴大本营徒步','大本营','崂山北九水','课堂积分小程序','积分规则','头像升级','阿洛','哈巴山脚','德语发音'),
    ('黄山西海环线','西海','黄山云谷路线','云谷','八大关','照片整理工具','照片去重','相册标签','小墨','黄山松林','基金基础'),
    ('崂山巨峰线路','巨峰','崂山太清线路','太清','小麦岛公园','课程管理后台','考勤导入','续费提醒','阿晓','崂山海雾','Python递归'),
    ('武功山金顶穿越','金顶','武功山发云界穿越','发云界','城市植物园','健身训练日志','心率导入','动作记录','阿青','武功山草坡','英语阅读'),
    ('玉龙雪山徒步','玉龙徒步','玉龙雪山索道观景','索道观景','博物馆新馆','气象观测面板','温度曲线','降雨提醒','小石','玉龙雪山山屋','统计基础'),
    ('青城山前山路线','前山','青城山后山路线','后山','滨江步道','旅行日程助手','路线排序','预算统计','小晚','青城山古道','SQL连接'),
    ('稻城亚丁长线','亚丁长线','稻城亚丁短线','亚丁短线','老城区书店','图书借阅系统','借还登记','逾期提醒','阿岚','稻城草甸','算法复杂度'),
    ('五台山北台徒步','北台','五台山东台徒步','东台','天文馆','语音转写工具','分段转写','说话人标注','小满','五台山山口','概率论'),
    ('泰山日出步道','日出线','泰山夜爬路线','夜爬线','河边绿道','知识卡片生成器','卡片排版','题目去重','阿禾','泰山石阶','C++指针'),
]


def write_jsonl(name, records):
    with (ROOT / name).open('w', encoding='utf-8', newline='\n') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def opaque_source_id(scope: str, event_index: int | None, mention: int) -> str:
    # Stable, opaque IDs: DO NOT encode event identity or phase in readable source_id.
    key = f'dl-b4-v1|{scope}|{event_index}|{mention}'
    return f'{scope}-R{hashlib.sha256(key.encode()).hexdigest()[:14]}'


def event_messages(cfg):
    a, at, b, bt, outing, project, task_a, task_b, char, story_place, study = cfg
    return [
        ('plan', '登山计划A', [
            f'我准备走{a}，先研究一下怎么去。',
            f'{at}那趟我已经查了天气，住宿还没有订。',
            f'前面说的{at}行程可能要往后推，最近时间排不开。',
            f'更新一下：{at}没有取消，只是打算延到下个月。']),
        ('plan', '登山计划B', [
            f'另外我还想走{b}，这是单独安排的另一趟。',
            f'{bt}这条线路我在看车票，日期和第一趟不一样。',
            f'关于{bt}，目前还没有决定是不是出发。',
            f'{bt}先放在备选列表，暂时不订交通。']),
        ('experience', '同地点第一次出行', [
            f'3月2日我一个人去了{outing}，逛了好一会儿。',
            f'说起3月2日那趟，我主要是自己拍照，没有同行的人。',
            f'之前第一次去{outing}那天，回家后我整理了照片。',
            f'3月2日的相册已经整理完，还是独自去的那次。']),
        ('experience', '同地点第二次出行', [
            f'3月9日我又去了{outing}，这一次有朋友同行。',
            f'第二次去{outing}我们待到傍晚，人比前一次多。',
            f'周末跟朋友那趟是3月9日，不是3月2日的独行。',
            f'3月9日的合照也整理好了，这次是和朋友一起的。']),
        ('state_change', '同项目任务A', [
            f'我在{project}里开始处理{task_a}，先做基础结构。',
            f'{task_a}今天遇到一个边界问题，测试还没过。',
            f'关于{task_a}，现在主要路径已经跑通。',
            f'接下来还要给{task_a}补异常处理和回归测试。']),
        ('state_change', '同项目任务B', [
            f'{project}还有另一个任务是{task_b}，要独立实现。',
            f'{task_b}目前在调配置，和{task_a}是两项不同工作。',
            f'今天把{task_b}拆成单独模块，仍然需要验收。',
            f'准备专门测试{task_b}出错时会不会影响其他功能。']),
        ('narrative', '虚构故事', [
            f'我编了一个故事：{char}在{story_place}迷路了。',
            f'小说里{char}遇到暴风雪，那是虚构情节。',
            f'后来{char}找到一间山屋，这还是同一篇故事。',
            f'故事最后{char}平安回家，别把角色经历当成我亲身经历。']),
        ('state_change', '学习安排调整', [
            f'我开始复习{study}，原计划每天练习半小时。',
            f'昨天做{study}练习有点卡住，还是继续坚持。',
            f'后来把{study}调整成隔天复习，不再每天都做。',
            f'今天还在按隔天一次的节奏复习{study}。'])
    ]


def make_messages(layout):
    messages = []
    anchors = []
    events = []
    evidence_by_event = {}
    for sidx, cfg in enumerate(CONFIGS, 1):
        scope = f'S{sidx:02d}'
        event_data = event_messages(cfg)
        event_ids = [f'{scope}-E{i+1:02d}' for i in range(8)]
        for ei, (typ, desc, texts) in enumerate(event_data):
            eid = event_ids[ei]
            source_ids = [opaque_source_id(scope, ei, mi+1) for mi in range(4)]
            evidence_by_event[eid] = source_ids
            events.append({
                'event_id': eid, 'scope_id': scope, 'event_type': typ,
                'gold_description': desc, 'event_anchor': texts[0],
                'source_ids': source_ids,
                'is_synthetic': True,
                'notes': '同一 event_id 下的四条均为同一持续事件；同主题不代表同一事件。'
            })
            anchors.append({'scope_id': scope, 'event_id': eid, 'anchor_text': texts[0],
                            'event_type': typ, 'known_from_source_id': source_ids[0],
                            'is_synthetic': True})
        if layout == 'interleaved':
            patterns = [
                [0,4,2,6,1,5,3,7],
                [3,0,5,1,7,2,6,4],
                [6,1,4,2,0,7,5,3],
                [5,3,7,0,2,4,1,6]
            ]
            entries = [(ei, phase) for phase, order in enumerate(patterns) for ei in order]
        elif layout == 'contiguous':
            entries = [(ei, phase) for ei in range(8) for phase in range(4)]
        else:
            raise ValueError(layout)
        # Two unanchored follow-up messages; deliberately no gold event assignment.
        entries.insert(16, (None, 0))
        entries.insert(29, (None, 1))
        base = datetime(2026, 4, 1, 9, 0, tzinfo=CST) + timedelta(days=sidx)
        for seq, (ei, phase) in enumerate(entries, 1):
            if ei is None:
                aid = 1 if phase == 0 else 2
                text = '上次那件事后来怎么样了？' if aid == 1 else '那个计划现在有结果了吗？'
                mid = opaque_source_id(scope, None, aid)
                event_id = None
                kind = 'ambiguous_unresolved'
                typ = 'unknown'
            else:
                typ, _, texts = event_data[ei]
                mid = opaque_source_id(scope, ei, phase+1)
                event_id = event_ids[ei]
                text = texts[phase]
                kind = 'event_mention'
            # In the interleaved view, appearances of any event are separated by several
            # other active events; cross-batch analysis uses stream_seq rather than grouping.
            # Contiguous is an ablation layout for the *same* content and gold labels.
            stamp = base + timedelta(minutes=13 * seq)
            messages.append({
                'schema_version': '1.0', 'dataset': 'synthetic_b4_v1', 'is_synthetic': True,
                'layout': layout, 'source_system': 'synthetic_fixture',
                'scope_id': scope, 'conversation_id': f'{scope}-CONV',
                'source_id': mid, 'stream_seq': seq, 'recorded_at': stamp.isoformat(),
                'speaker': 'user', 'content_type': 'text', 'text': text,
                'sha256_text': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'gold_event_id': event_id, 'gold_event_type': typ,
                'record_kind': kind, 'gold_action': ('ABSTAIN' if ei is None else 'RESOLVE')
            })
    return messages, events, anchors, evidence_by_event


def make_pairs(evidence):
    pairs = []
    seen = set()

    def add(a, b, relation, difficulty, rationale):
        key = tuple(sorted((a,b)))
        if a == b or key in seen:
            return
        seen.add(key)
        pairs.append({
            'pair_id': f'P{len(pairs)+1:04d}', 'left_source_id': a, 'right_source_id': b,
            'relation': relation, 'same_event': (True if relation=='SAME_EVENT' else
                                                 None if relation=='UNRESOLVED' else False),
            'difficulty': difficulty, 'gold_reason': rationale,
            'is_synthetic': True
        })

    for sidx in range(1,11):
        sc = f'S{sidx:02d}'
        ids = [f'{sc}-E{i+1:02d}' for i in range(8)]
        # 200 positive pairs. Explicitly span different phases and batches.
        for ei, eid in enumerate(ids):
            ss = evidence[eid]
            add(ss[0], ss[2], 'SAME_EVENT','cross_batch_positive', '同一事件的首段与第三次提及')
            add(ss[1], ss[3], 'SAME_EVENT','cross_batch_positive', '同一事件的第二与第四次提及')
            if ei in (0,2,4,6):
                add(ss[0], ss[3], 'SAME_EVENT','long_gap_positive', '同一事件相隔多轮重新提及')
        idx_pairs = ((0,0),(1,1),(2,2),(3,3),(0,3))
        # 150 'RELATED' pairs: similar topic, shared location, or shared project,
        # BUT never the same event identity.
        for x,y,why in [(0,1,'同类登山计划，但路线/行程身份不同'),
                         (2,3,'同一地点但不同日期的两次出行'),
                         (4,5,'同一项目下的不同任务')]:
            aa, bb = evidence[ids[x]], evidence[ids[y]]
            for i,j in idx_pairs:
                add(aa[i],bb[j],'RELATED','hard_negative_related',why)
        # 150 within-scope DIFFERENT: fictional vs real; separate concerns.
        for x,y,why in [(0,6,'小说角色经历不是用户真实计划'),
                         (2,6,'虚构故事与真实出行不能合并'),
                         (4,7,'软件工程任务与个人学习习惯是不同事件')]:
            aa, bb = evidence[ids[x]], evidence[ids[y]]
            for i,j in idx_pairs:
                add(aa[i],bb[j],'DIFFERENT_EVENT','hard_negative_different',why)
        # 6 unresolved per scope. The source text has no unique antecedent.
        for amb in (1,2):
            for target in (0,1,4):
                add(opaque_source_id(sc, None, amb), evidence[ids[target]][0],
                    'UNRESOLVED','ambiguous_reference', '缺少唯一事件指代锚点，禁止硬归属')
        # Cross-scope negative (isolation), 5 per scope.
        other = f'S{(sidx%10)+1:02d}'
        for i,j in idx_pairs:
            add(evidence[ids[0]][i],evidence[f'{other}-E01'][j],
                'DIFFERENT_EVENT','cross_scope_negative','不同 scope 禁止跨用户事件合并')
    return pairs


def make_queries(evidence):
    queries = []
    for sidx in range(1,11):
        scope=f'S{sidx:02d}'
        for ei in range(8):
            eid=f'{scope}-E{ei+1:02d}'
            arr=evidence[eid]
            for idx in (2,3):
                queries.append({
                    'query_id':f'Q{len(queries)+1:04d}',
                    'scope_id':scope, 'source_id':arr[idx],
                    'gold_event_id':eid, 'expected':'ATTACH_EXISTING',
                    'must_be_retrievable_from_prior_sources':arr[:idx],
                    'k_values':[1,5,10], 'is_synthetic':True
                })
        for amb in (1,2):
            queries.append({
                'query_id':f'Q{len(queries)+1:04d}',
                'scope_id':scope,'source_id':opaque_source_id(scope,None,amb),
                'gold_event_id':None, 'expected':'ABSTAIN',
                'must_be_retrievable_from_prior_sources':[],
                'k_values':[1,5,10], 'is_synthetic':True
            })
    return queries


def make_scenarios():
    scenarios = [
        ('restart_idempotence','再次提交同一 source_id','第二次返回 ALREADY_ATTACHED；证据数不变'),
        ('multiple_same_conflict','对同一消息返回两个不同 event_id 的 SAME_EVENT','全部 CONFLICT；0 attach'),
        ('cannot_link_restart','已持久化 cannot-link 后停止并重启服务','不能在重启后 attach 冲突事件'),
        ('related_no_attach','模型输出 RELATED','不执行 SAME_EVENT attach'),
        ('cross_scope_injection','跨 scope 的候选作为 SAME_EVENT 提交','被 scope 校验拒绝；0 attach'),
        ('deleted_source','删除一条证据来源并触发 source inventory','来源失效；关联事件重新计算有效性，cannot-link 复核'),
        ('kill_before_commit','事件关联事务提交前强制终止进程','重启后无半写入；任务可重新执行'),
        ('kill_after_commit_before_cursor','提交后、游标更新前模拟终止（取决于事务设计）','重新执行幂等；不存在重复关系/丢处理'),
        ('provider_503','模拟 LLM 返回 503 和超时','任务可重试；游标不提前推进；V1 回复不受影响'),
        ('token_budget','超过上下文与每日预算','不得调用外部模型；延期处理和记录原因'),
        ('story_reality','虚构故事的登山与真实登山事件同时出现','禁止将虚构角色经历作为用户事实'),
        ('batch_boundary','分别按8、20、40条切批处理同一交错流','比较端到端召回、误合并、覆盖率和幂等性'),
    ]
    return [{'scenario_id':f'R{i:02d}', 'name':k,'input_condition':desc,
             'expected_outcome':out, 'is_synthetic':True}
            for i,(k,desc,out) in enumerate(scenarios,1)]


def main():
    inter, events, anchors, evidence = make_messages('interleaved')
    cont, _, _, evidence2 = make_messages('contiguous')
    assert evidence == evidence2
    pairs=make_pairs(evidence)
    queries=make_queries(evidence)
    # The model-visible streams carry NO gold labels, category tags, hints, or event IDs.
    public_fields = ('schema_version','dataset','is_synthetic','layout',
                     'source_system','scope_id','conversation_id','source_id',
                     'stream_seq','recorded_at','speaker','content_type','text','sha256_text')
    write_jsonl('messages_interleaved.jsonl', [{k:r[k] for k in public_fields} for r in inter])
    write_jsonl('messages_contiguous.jsonl', [{k:r[k] for k in public_fields} for r in cont])
    write_jsonl('message_gold.jsonl', [{
       'source_id':r['source_id'], 'scope_id':r['scope_id'],
       'gold_event_id':r['gold_event_id'],'gold_event_type':r['gold_event_type'],
       'gold_action':r['gold_action'],'record_kind':r['record_kind']
    } for r in inter])
    write_jsonl('gold_events.jsonl', events)
    # ORACLE ONLY; never preload gold anchors into the tested retriever.
    write_jsonl('gold_event_anchors_DO_NOT_FEED.jsonl', anchors)
    write_jsonl('pair_input.jsonl', [{k:p[k] for k in ('pair_id','left_source_id','right_source_id')} for p in pairs])
    write_jsonl('pair_gold.jsonl', pairs)
    write_jsonl('retrieval_queries_input.jsonl', [{
       'query_id':q['query_id'],'scope_id':q['scope_id'],'source_id':q['source_id']
    } for q in queries])
    write_jsonl('retrieval_query_gold.jsonl', queries)
    write_jsonl('reliability_scenarios.jsonl', make_scenarios())
    summary={
        'dataset_name':'Digital Life B4 Synthetic Event Identity v1.0',
        'date':'2026-11-08','synthetic':True,'real_chat_records':0,
        'scope_count':len(CONFIGS),'message_count_interleaved':len(inter),
        'message_count_contiguous':len(cont),'event_count':len(events),
        'pair_count':len(pairs),'pair_relations':dict(Counter(x['relation'] for x in pairs)),
        'pair_difficulties':dict(Counter(x['difficulty'] for x in pairs)),
        'query_count':len(queries),'query_expected':dict(Counter(x['expected'] for x in queries)),
        'reliability_scenarios':len(make_scenarios()),
        'source_commits': 'standalone synthetic fixture; no access to private chat logs',
        'answer_leakage_protection': 'opaque source IDs and separate input/gold files'
    }
    (ROOT/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
