"""`/dlm` 命令分发与草稿生成/渲染（由 main.DigitalMemoryStore 继承）。"""
import asyncio
import time
import json
import hashlib
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
try:
    from astrbot.api import logger as _logger
except Exception:
    import logging as _logging
    _logger=_logging.getLogger(__name__)
from .storage import Store,MAX_CAPTURE_CHARS
from . import panel
from .preflight import check_environment
from .model_drafts import make_prompt,make_contrast_prompt,make_brief_prompt,make_segment_merge_prompt,validate_brief_output
from .model_groups import make_group_prompt
from .new_groups import make_new_group_prompt
from .model_judge import (build_prompt as build_judge_prompt, parse_reply as parse_judge_reply,
                          format_hint as format_judge_hint, is_anaphoric, accept_selection)


class CommandMixin:
    async def _generate_segmented_brief(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        title=arg[len('分段摘要 '):].strip()
        if not 1<=len(title)<=80:return '格式：/dlm 分段摘要 事件标题。'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        if self.capture_pending_by_scope.get(identity,0):return '消息仍在采集队列中，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                snap=await loop.run_in_executor(self.pool,self._segment_snapshot_work,*identity,title)
                raw_parts=[];parts=[];reused=[]
                for i in range(len(snap['segments'])):
                    if self.capture_pending_by_scope.get(identity,0):raise ValueError('pending capture')
                    current=await loop.run_in_executor(self.pool,self._segment_snapshot_work,*identity,title,snap['as_of'])
                    if current!=snap:raise ValueError('changed')
                    sub=self.store.model_segment_part(snap,i)
                    raw=None
                    if self.segment_cache_enabled:
                        raw=await loop.run_in_executor(self.pool,self._segment_cache_read_work,*identity,snap,i)
                    hit=raw is not None
                    if not hit:
                        response=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                            prompt=make_brief_prompt(sub)),timeout=30)
                        raw=getattr(response,'completion_text',None)
                    validated=validate_brief_output(raw,sub)
                    if not hit and self.segment_cache_enabled:
                        if self.capture_pending_by_scope.get(identity,0):raise ValueError('pending capture')
                        await loop.run_in_executor(self.pool,self._segment_cache_save_work,*identity,snap,i,raw)
                    parts.append(validated);raw_parts.append(raw);reused.append(hit)
                if self.capture_pending_by_scope.get(identity,0):raise ValueError('pending capture')
                current=await loop.run_in_executor(self.pool,self._segment_snapshot_work,*identity,title,snap['as_of'])
                if current!=snap:raise ValueError('changed')
                response=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                    prompt=make_segment_merge_prompt(snap,parts)),timeout=30)
                if self.capture_pending_by_scope.get(identity,0):raise ValueError('pending capture')
                draft=await loop.run_in_executor(self.pool,self._segment_save_work,*identity,snap,raw_parts,
                                                  getattr(response,'completion_text',None),reused)
            except Exception:
                return '分段摘要未完成：来源、时间、关系组、更新状态或模型输出不满足要求；原文未更改。'
            return self._render_brief_draft(draft)

    @staticmethod
    def _render_brief_draft(draft,page=1):
        snap=draft['snapshot'];data=draft['result'];sources=snap['sources'];pages=(len(sources)+4)//5
        if type(page) is not int or not 1<=page<=pages:return '页码超出范围，共 '+str(pages)+' 页。'
        status='已确认' if draft['accepted_brief_id'] is not None else '未确认'
        if not draft['current']:status+='，来源或更新状态已变化，请重新生成'
        lines=['模型摘要草稿 '+draft['draft_id']+'（'+status+'）：','事件：'+snap['title'],
               '候选摘要：'+data['summary'],'引用 '+data['source_id']+'：'+data['quote'],
               '来源原话第 '+str(page)+'/'+str(pages)+' 页（共 '+str(len(sources))+' 条）：']
        selected=sources[(page-1)*5:page*5];ids={x['id'] for x in selected}
        if draft.get('segment_results'):
            for i,(part,result) in enumerate(zip(snap['segments'],draft['segment_results']),1):
                if ids.intersection(part):
                    label='（复用缓存）' if draft.get('segment_reused',[False]*len(snap['segments']))[i-1] else ''
                    lines.append('分段 '+str(i)+label+' 摘要：'+result['summary']+'；引用 '+result['source_id']+'：'+result['quote'])
        lines.extend(x['id']+' ['+x['recorded_at']+']：'+x['content'] for x in selected)
        relations=[r for r in snap['relations'] if r['newer'] in ids]
        dates={k:v for k,v in snap['dates'].items() if k in ids}
        if relations:lines.append('本页新记录的关系：'+json.dumps(relations,ensure_ascii=False))
        if dates:lines.append('本页人工日期标注：'+json.dumps(dates,ensure_ascii=False))
        if page<pages:lines.append('下一页：/dlm 摘要草稿 '+draft['draft_id']+'|'+str(page+1))
        lines.append('请核对全部页面、关系和摘要；引用匹配不证明摘要正确或完整。确认：/dlm 确认摘要 '+draft['draft_id'])
        return '\n'.join(lines)

    async def _generate_model_brief(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        title=arg[len('模型摘要 '):].strip()
        if not 1<=len(title)<=80:return '格式：/dlm 模型摘要 事件标题（1至20条已关联原话）。'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        if self.capture_pending_by_scope.get(identity,0):return '消息仍在采集队列中，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                snap=await loop.run_in_executor(self.pool,self._brief_snapshot_work,*identity,title)
                response=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                    prompt=make_brief_prompt(snap)),timeout=30)
                if self.capture_pending_by_scope.get(identity,0):return '采集状态已变化，请稍后重新生成。'
                draft=await loop.run_in_executor(self.pool,self._brief_save_work,*identity,snap,
                                                  getattr(response,'completion_text',None))
            except Exception:
                return '摘要未生成：事件须有1至20条完整原话，且来源、时间、更新状态及模型结果须可校验。原文未更改。'
            return self._render_brief_draft(draft)

    @staticmethod
    def _render_new_group(draft,page=1):
        sources=draft['sources'];pages=(len(sources)+4)//5
        if type(page) is not int or not 1<=page<=pages:return '页码超出范围，共 '+str(pages)+' 页。'
        chosen={item['seq']:(group['title'],item['quote']) for group in draft['result']['groups'] for item in group['items']}
        lines=['新事件分组草稿 '+draft['draft_id']+'，第 '+str(page)+'/'+str(pages)+' 页（引用匹配不证明分组正确）：']
        for source in sources[(page-1)*5:page*5]:
            seq=source['seq'];title,quote=chosen.get(seq,('未分组',''))
            lines.append(str(seq)+'（'+('已关联' if source['state']=='linked' else '待审')+'；候选：'+title+'）：'+source['content'])
            if quote:lines.append('模型引用：'+quote)
        if page<pages:lines.append('下一页：/dlm 新分组草稿 '+draft['draft_id']+'|'+str(page+1))
        if chosen:lines.append('全部确认：/dlm 确认新分组 '+draft['draft_id']+'；仅确认部分：/dlm 确认新分组 '+draft['draft_id']+'|序号,序号')
        lines.append('只建立选中组的新事件和关联，不自动判断更正关系；未分组原话保留待审。')
        return '\n'.join(lines)

    async def _generate_new_groups(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        raw=arg[len('模型新分组 '):].strip().replace('，',',');auto=raw=='待审' or raw.startswith('待审|')
        seqs=None;cursor=0
        if auto:
            if raw!='待审':
                value=raw[len('待审|'):]
                if not value.isascii() or not value.isdigit() or len(value)>19 or int(value)>9223372036854775807:
                    return '格式：/dlm 模型新分组 待审 或 待审|上页末尾序号。'
                cursor=int(value)
        else:
            parts=[x.strip() for x in raw.split(',')]
            if not 1<=len(parts)<=20 or any(not x.isascii() or not x.isdigit() or len(x)>19 or int(x)>9223372036854775807 for x in parts):
                return '格式：/dlm 模型新分组 待审 或 1,2,3（最多20条待审原话）。'
            seqs=[int(x) for x in parts]
            if len(set(seqs))!=len(seqs):return '记录序号不能重复。'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        if self.capture_pending_by_scope.get(identity,0):return '消息仍在采集队列中，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                sources=await loop.run_in_executor(self.pool,
                    self._next_new_group_sources_work if auto else self._new_group_sources_work,
                    *identity,cursor if auto else seqs)
                if not sources:return '该位置之后没有待审记录。'
                response=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                    prompt=make_new_group_prompt(sources)),timeout=30)
                if self.capture_pending_by_scope.get(identity,0):return '采集状态已变化，请稍后重新整理。'
                draft=await loop.run_in_executor(self.pool,self._new_group_save_work,*identity,sources,
                                                  getattr(response,'completion_text',None))
            except Exception:
                return '新分组未生成：来源或时间有变化、模型输出无效，或标题与已有事件/别名冲突；原文未更改。'
            output=self._render_new_group(draft)
            if auto:
                output+='\n继续向后整理：/dlm 模型新分组 待审|'+str(sources[-1]['seq'])+'；本批未确认记录仍留在待审区。'
            return output

    @staticmethod
    def _render_group_draft(draft):
        result=draft['result'];manifest=draft['manifest'];sources={x['seq']:x for x in draft['sources']}
        rule=draft.get('origin')=='rule'
        lines=[('规则' if rule else '模型')+'分组草稿 '+draft['draft_id']+'（未确认，引用匹配不证明关联正确）：']
        for group in result['groups']:
            lines.append('可能事件：'+group['title'])
            for item in group['items']:
                source=sources[item['seq']]
                lines.append(str(item['seq'])+'（'+('已关联' if source['state']=='linked' else '待审')+'）：'+source['content']+'\n'+('原文摘录：' if rule else '模型引用：')+item['quote'])
            lines.append('也可单组关联：/dlm 批量关联 '+','.join(str(x['seq']) for x in group['items'])+'|'+group['title'])
        if not result['groups']:lines.append('没有建议；不代表记录之间无关。')
        chosen={x['seq'] for g in result['groups'] for x in g['items']}
        remaining=[str(x['seq']) for x in manifest['sources'] if x['seq'] not in chosen]
        if remaining:
            lines.append('未分组原话：')
            for x in manifest['sources']:
                if x['seq'] not in chosen:lines.append(str(x['seq'])+'：'+sources[x['seq']]['content'])
        if result['groups']:
            lines.append('全部确认：/dlm 确认分组 '+draft['draft_id']+'；也可仅选记录：/dlm 确认分组 '+draft['draft_id']+'|序号,序号')
        lines.append('不会自动记录修正关系；本次确认统一提交。')
        return '\n'.join(lines)

    async def _generate_model_groups(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:
            return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        raw,sep,names=arg[len('模型分组 '):].partition('|')
        parts=[x.strip() for x in raw.replace('，',',').split(',')]
        titles=[x.strip() for x in names.replace('，',',').split(',')]
        if (not sep or not 1<=len(parts)<=5 or not 1<=len(titles)<=4 or
            any(not x.isascii() or not x.isdigit() or len(x)>19 for x in parts) or
            any(not 1<=len(x)<=80 or '|' in x for x in titles)):
            return '格式：/dlm 模型分组 1,2|事件一,事件二（最多5条原话、4个已有事件）。'
        seqs=[int(x) for x in parts]
        if len(set(seqs))!=len(seqs) or len(set(titles))!=len(titles):return '记录序号和事件标题不能重复。'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                snapshot=await loop.run_in_executor(self.pool,self._group_snapshot_work,*identity,seqs,titles)
                response=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                    prompt=make_group_prompt(snapshot)),timeout=30)
                result=await loop.run_in_executor(self.pool,self._group_check_work,*identity,snapshot,
                                                   getattr(response,'completion_text',None))
            except Exception:
                return '模型分组未完成：来源、时间、模型输出或历史可能不可用或已变化；原文仍保留。'
            return self._render_group_draft(result)

    @staticmethod
    def _render_contrast(draft):
        data=draft['result'];names={'correction':'可能更正','update':'可能补充或更新',
             'unrelated':'可能无关','uncertain':'不确定'}
        state='未审核' if draft['status']=='UNREVIEWED' else '已过期，请重新对照'
        rule=draft.get('origin')=='rule'
        return ('对照草稿 '+draft['draft_id']+'（'+state+'）：'+names[data['relation']]+'；事件：'+draft['event_title']+'\n'
                +('规则说明：' if rule else '模型说明：')+data['explanation']+'\n旧记录 '+data['old_id']+'：'+data['old_quote']
                +'\n待审记录 '+str(draft['pending_seq'])+'：'+data['new_quote']
                +'\n'+('仅为规则线索' if rule else '仅为模型猜测')+'，原文匹配不代表关系正确；不会自动关联或更正。')

    async def _generate_contrast(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        number,sep,title=arg[len('模型对照 '):].partition('|')
        if not sep or not number.strip().isdigit() or not 1<=len(title.strip())<=80:
            return '格式：/dlm 模型对照 待审序号|已有事件标题'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                snap=await loop.run_in_executor(self.pool,self._contrast_snapshot_work,*identity,int(number),title.strip())
                resp=await asyncio.wait_for(self.context.llm_generate(chat_provider_id=self.draft_provider,
                    prompt=make_contrast_prompt(snap)),timeout=30)
                draft=await loop.run_in_executor(self.pool,self._contrast_save_work,*identity,snap,
                                                  getattr(resp,'completion_text',None))
            except Exception:
                return '对照未完成：来源不可用、时间不满足、模型失败、输出无效或来源已变化；原文仍保留。'
            return self._render_contrast(draft)

    @staticmethod
    def _render_model_draft(draft):
        d=draft['result'];status='未审核' if draft['status']=='UNREVIEWED' else '已过期，请重新生成'
        lines=['模型草稿 '+draft['draft_id']+'（'+status+'；语义尚未核实）：',
               '候选标题：'+d['title'],'候选摘要：'+d['summary']]
        for x in d['evidence']:lines.append('引用记录 '+str(x['seq'])+'：'+x['quote'])
        lines.append('原文匹配不代表摘要真实或完整；核对后使用 /dlm 关联 序号|标题。')
        return '\n'.join(lines)

    async def _generate_model_draft(self,identity,arg):
        if not self.model_drafts_enabled or not self.draft_provider:
            return '模型整理未开启或未指定整理模型。'
        if ':friendmessage:' not in identity[1].casefold():return '不支持该会话类型。'
        raw=arg[len('模型整理 '):].strip().replace('，',',')
        parts=raw.split(',')
        if not 1<=len(parts)<=5 or any(not x.strip().isdigit() for x in parts):
            return '格式：/dlm 模型整理 1,2（最多5条待审记录）。'
        seqs=[int(x.strip()) for x in parts]
        if len(set(seqs))!=len(seqs):return '记录序号不能重复。'
        if self.model_draft_lock.locked():return '已有整理任务，请稍后再试。'
        async with self.model_draft_lock:
            loop=asyncio.get_running_loop()
            try:
                sources=await loop.run_in_executor(self.pool,self._model_sources_work,*identity,seqs)
                response=await asyncio.wait_for(self.context.llm_generate(
                    chat_provider_id=self.draft_provider,prompt=make_prompt(sources)),timeout=30)
                candidate=await loop.run_in_executor(self.pool,self._model_save_work,*identity,sources,
                    getattr(response,'completion_text',None))
            except Exception:
                return '整理未完成：模型不可用、结果格式无效或来源发生变化，原文仍保留。'
            return self._render_model_draft(candidate)

    def _work(self, sender, origin, arg):
        self._ensure_store()
        scope=self.store.scope(sender,origin)
        if arg=='记忆状态':
            state=self.store.memory_inventory(scope)
            health=self.store.auto_job_health(scope)
            failed=('；整理失败任务 '+str(health['dead'])+' 条（已停止重试，原话仍保留）' if health['dead'] else '')
            return ('本会话独立记忆：待审 '+str(state['pending'])+' 条；已关联采集记录 '+str(state['linked_captures'])+
                    ' 条；已审核用户原话 '+str(state['reviewed_sources'])+' 条；事件 '+str(state['events'])+
                    ' 个；历史导入记录 '+str(state['imported'])+' 条；已保存摘要版本 '+str(state['brief_versions'])+
                    ' 个'+failed+'。摘要数量含可能失效的旧版本；统计不代表聊天采集完整或事实已经核验。'
                    '\n上下文开关和预算可用 /dlm 上下文状态 查看。')
        if arg=='上下文状态':
            gap=bool(self.store.capture_gap_count(scope)) or (sender,origin) in self.capture_gaps
            if self.auto_gap_recovery_enabled:
                gap=bool(self.store.capture_gap_boundary(scope)) or (sender,origin) in self.capture_gaps
                gap_text='；已检测缺口，后续仅尝试提供缺口后的原话线索' if gap else ''
            else:gap_text='；本会话有未核对采集缺口，记忆注入已暂停' if gap else ''
            model_state={'idle':'未加载','loading':'后台加载中（语义暂降级为字面）',
                         'ready':'就绪','failed':'加载失败（仅字面可用）'}.get(
                             self.store.embedder_state,'未加载')
            return ('聊天上下文：'+('开启' if self.context_enabled else '关闭')+'；字符预算：'+str(self.context_budget_chars)+
                    ('（配置无效，使用默认值 450）' if not self.context_budget_valid else '')+
                    '；双事件：'+('开启' if self.multi_event_context_enabled else '关闭')+
                    '；语义原话试验：'+('开启' if self.semantic_source_context_enabled else '关闭')+
                    '；本地模型：'+model_state+
                    '；审核摘要：'+('开启' if self.reviewed_brief_context_enabled else '关闭')+
                    '；概览摘要优先：'+('开启' if self.brief_overview_priority_enabled else '关闭')+
                    gap_text+
                    '。预算不包含固定提示，字符数不是 Token。')
        if arg.startswith('上下文预览 '):
            if self.auto_gap_recovery_enabled and self.store.capture_gap_boundary(scope):
                return '存在采集缺口；当前预览不支持缺口后的自动线索，实际聊天按缺口边界保护。'
            if self.store.capture_gap_count(scope) or (sender,origin) in self.capture_gaps:
                return '本会话有未核对采集缺口，记忆注入及预览已暂停；请先查看 /dlm 采集状态。'
            raw=arg[len('上下文预览 '):].strip();question=raw;budget=self.context_budget_chars
            if '|' in raw:
                question,_,value=raw.rpartition('|');question=question.strip();value=value.strip()
                if not value.isascii() or not value.isdigit() or len(value)>4 or not 150<=int(value)<=2000:
                    return '格式：/dlm 上下文预览 问题 或 问题|字符预算（150至2000）。'
                budget=int(value)
            if not 1<=len(question)<=400:return '格式：/dlm 上下文预览 需要模拟的问题。'
            cutoff=datetime.now(timezone.utc);prior=self.store.brief_overview_priority_enabled
            try:
                self.store.brief_overview_priority_enabled=False
                baseline=self.store.context_packet(scope,question,budget=budget,as_of=cutoff)
                self.store.brief_overview_priority_enabled=True
                compared=self.store.context_packet(scope,question,budget=budget,as_of=cutoff)
            finally:self.store.brief_overview_priority_enabled=prior
            active=compared if prior else baseline
            status='开启' if self.context_enabled else '关闭'
            brief='开启' if self.reviewed_brief_context_enabled else '关闭'
            return ('独立问题预览（预算 '+str(budget)+' 字符；实际聊天上下文'+status+'；摘要上下文'+brief+
                    '；不含消息ID、多轮焦点及并发状态）：\n'
                    +'原话优先：'+str(len(baseline))+' 字符；概览摘要优先：'+str(len(compared))+' 字符；'
                    +'差值：'+str(len(baseline)-len(compared))+' 字符。不是 Token 或费用。\n'
                    +'原话优先结果：'+(baseline or '无可用内容')+'\n'
                    +'概览摘要优先结果：'+(compared or '无可用内容')+'\n'
                    +'当前配置采用：'+('概览摘要优先' if prior else '原话优先')+'（'+str(len(active))+' 字符）；预览不会请求模型。')
        if arg.startswith('导入来源 '):
            number=arg[len('导入来源 '):].strip()
            if not number.isascii() or not number.isdigit() or len(number)>19:return '格式：/dlm 导入来源 待审序号。'
            item=self.store.import_provenance(scope,int(number))
            if not item:return '该序号没有历史导入记录。'
            return ('历史导入来源：'+item['source_label']+'；文件原时间：'+item['original_timestamp']+
                    '；保存时间（UTC）：'+item['recorded_at']+'；导入时间：'+item['imported_at']+
                    '；状态：'+item['state']+'。文件信息未经独立核验。')
        if arg=='待审' or arg.startswith('待审 '):
            cursor=arg[2:].strip()
            if cursor and not cursor.isdigit():return '格式：/dlm 待审 或 /dlm 待审 上页末尾序号'
            rows=self.store.pending(scope,after=int(cursor) if cursor else 0)
            return ('待审记录：\n'+'\n'.join(f"{x['seq']} [{x['recorded_at']}]"+("（历史导入，时间来自文件）" if x['imported'] else "")+" "+x['content'] for x in rows)
                    +'\n下一页：/dlm 待审 '+str(rows[-1]['seq']) if rows else '没有更多待审记录。')
        if arg=='更新核对' or arg.startswith('更新核对 '):
            cursor=arg[len('更新核对'):].strip()
            if cursor and (not cursor.isascii() or not cursor.isdigit() or len(cursor)>19):
                return '格式：/dlm 更新核对 或 /dlm 更新核对 上页末尾序号。'
            try:batch=self.store.rule_update_drafts(scope,int(cursor) if cursor else 0)
            except (ValueError,OverflowError):return '更新核对未完成，来源或游标可能无效。'
            if not batch['scanned']:return '没有更多待审记录。'
            lines=[self._render_contrast(draft) for draft in batch['drafts']]
            if not lines:lines=['本页没有可生成对照的唯一规则线索，不代表没有更新。']
            else:
                lines.append('核对每组新旧原话后，可用 /dlm 确认对照 草稿ID|修正；同一基线的多个草稿可用 /dlm 批量确认对照 ID,ID|修正。也可选替代或相关。')
            lines.append('下一页：/dlm 更新核对 '+str(batch['next_cursor'])+'；未确认原话继续待审。')
            return '\n\n'.join(lines)
        if arg=='更新线索' or arg.startswith('更新线索 '):
            cursor=arg[len('更新线索'):].strip()
            if cursor and not cursor.isdigit():return '格式：/dlm 更新线索 或 /dlm 更新线索 上页末尾序号'
            rows=self.store.update_clues(scope,after=int(cursor) if cursor else 0)
            if not rows:return '没有更多待审记录。'
            lines=['可能的更新线索（字面规则，未确认；每页扫描10条待审）：']
            for row in rows:
                if not row['matches']:continue
                lines.append('待审 '+str(row['seq'])+'：'+row['content'])
                for match in row['matches']:
                    lines.append('可能相关事件：'+match['title']+'（'+match['method']+'）；旧原话 ['+match['old_id']+' | '+match['old_time']+'] '+match['old_content'])
            if len(lines)==1:lines.append('本页没有匹配；不代表没有更新。')
            lines.append('核对后可用 /dlm 关联更新 序号|事件标题|旧原话ID|修正；也可选择替代或相关。下一页：/dlm 更新线索 '+str(rows[-1]['seq']))
            return '\n'.join(lines)
        if arg=='规则分组草稿' or arg.startswith('规则分组草稿 '):
            cursor=arg[len('规则分组草稿'):].strip()
            if cursor and (not cursor.isascii() or not cursor.isdigit() or len(cursor)>19):
                return '格式：/dlm 规则分组草稿 或 /dlm 规则分组草稿 上页末尾序号。'
            try:batch=self.store.rule_group_draft(scope,int(cursor) if cursor else 0)
            except (ValueError,OverflowError):return '草稿未生成：来源或事件时间、状态可能已变化，请重新查看。'
            if not batch['scanned']:return '没有更多采集记录。'
            suffix='\n继续扫描：/dlm 规则分组草稿 '+str(batch['next_cursor'])+'；未确认的记录仍待审。'
            return (self._render_group_draft(batch['draft']) if batch['draft'] else '本页没有可生成草稿的规则分组。')+suffix
        if arg=='分组建议' or arg.startswith('分组建议 '):
            cursor=arg[len('分组建议'):].strip()
            if cursor and (not cursor.isascii() or not cursor.isdigit() or len(cursor)>19):
                return '格式：/dlm 分组建议 或 /dlm 分组建议 上页末尾序号。'
            try:result=self.store.group_pending_suggestions(scope,int(cursor) if cursor else 0)
            except ValueError:return '游标无效。'
            if not result['scanned']:return '没有更多采集记录。'
            lines=['可能的待审分组（规则线索，未确认；每页扫描最多10条采集记录）：']
            for group in result['groups']:
                lines.append('可能事件：'+group['title'])
                for item in group['items']:
                    lines.append(str(item['seq'])+' ['+item['recorded_at']+'] '+item['content']+'（'+item['method']+'）')
                lines.append('核对后可用：/dlm 批量关联 '+','.join(str(x['seq']) for x in group['items'])+'|'+group['title'])
            if not result['groups']:lines.append('本页没有分组；不代表没有相关记录。')
            lines.append('相邻采集不等于真实聊天相邻，可能误判；不会自动关联。下一页：/dlm 分组建议 '+str(result['next_cursor']))
            return '\n'.join(lines)
        if arg=='整理' or arg.startswith('整理 '):
            cursor=arg[2:].strip()
            if cursor and not cursor.isdigit():return '格式：/dlm 整理 或 /dlm 整理 上页末尾序号'
            rows=self.store.organize_batch(scope,after=int(cursor) if cursor else 0)
            if not rows:return '没有待审记录。'
            lines=['待审核建议（最多10条，字面规则）：']
            for row in rows:
                if row['status']=='ERROR':
                    lines.append(str(row['seq'])+'：生成失败，原文仍待审');continue
                found=[x['title']+'（已有事件）' for x in row['candidates']]
                if row['new_event_candidate']:
                    found.append(row['new_event_candidate']['title']+'（新标题候选）')
                lines.append(str(row['seq'])+'：'+('、'.join(found) if found else '没有明确候选'))
            lines.append('可用 /dlm 规则分组草稿 批量核对已有事件候选；下一页：/dlm 整理 '+str(rows[-1]['seq']))
            return '\n'.join(lines)
        if arg.startswith('多轮建议 '):
            raw=arg[len('多轮建议 '):].strip().replace('，',',')
            parts=[x.strip() for x in raw.split(',')]
            if not 2<=len(parts)<=4 or any(not x.isdigit() for x in parts):
                return '格式：/dlm 多轮建议 1,2（按顺序选择2至4条待审记录）。'
            result=self.store.multi_suggest(scope,[int(x) for x in parts],semantic=bool(self.model_dir))
            choices=result['candidates']
            response=('未确认的历史事件候选：'+'；'.join(x['title']+'（'+x['method']+'）' for x in choices)
                      if choices else '未找到历史事件候选。')
            response+=' 请核对每条原文后分别使用 /dlm 关联 序号|标题；不会自动关联。'
            if result['semantic_error']:response+=' 本次语义模型不可用，仅显示字面匹配。'
            return response
        if arg.startswith('偏好线索 '):
            title=arg[len('偏好线索 '):].strip()
            try:result=self.store.preference_clues(scope,title)
            except ValueError:return '事件不存在或原话校验失败。'
            if result['excluded']:return '该事件已排除偏好规则加权；原话仍保留。'
            lines=['自述偏好字面线索（未确认，只显示不同 UTC 日期的不同原话，最多10条）：']
            lines.extend('['+x['id']+' | '+x['recorded_at']+'] '+x['content'] for x in result['evidence'])
            if not result['evidence']:lines.append('没有匹配线索，或因否定线索停止加权。')
            lines.append('字面规则可能误判或遗漏；使用 /dlm 偏好排除 '+title+' 停用此事件的规则加权。')
            return '\n'.join(lines)
        if arg.startswith('偏好排除 ') or arg.startswith('偏好恢复 '):
            excluded=arg.startswith('偏好排除 ');title=arg[len('偏好排除 '):].strip()
            try:self.store.exclude_preference_rank(scope,title,excluded)
            except ValueError:return '事件不存在或来源校验失败。'
            return ('已排除此事件的偏好规则加权。' if excluded else '已恢复此事件的偏好规则检查。')+' 原话和其他排序设置保留。'
        if arg.startswith('线索 '):
            number=arg[len('线索 '):].strip()
            if not number.isdigit():return '格式：/dlm 线索 待审记录序号'
            try:rows=self.store.explain_weak_candidates(scope,int(number))
            except ValueError:return '待审原文不可用或校验失败。'
            if not rows:return '没有找到规则线索；不代表没有相关记忆。'
            lines=['未确认的词组联想及来源（不是事实判断）：']
            for row in rows:
                labels=[]
                if row.get('priority_boost',0)>0:labels.append('人工标记重要')
                if row.get('recency_boost',0)>0:labels.append('近期记录加权')
                if row.get('preference_boost',0)>0:labels.append('自述偏好字面线索（未确认）')
                lines.append('候选事件：'+row['title']+('（'+'、'.join(labels)+'）' if labels else ''))
                for clue in row['clues']:
                    lines.append('重合片段：'+clue['text']+'；来源：'+(clue['source_id'] or '事件标题'))
            return '\n'.join(lines)
        if arg.startswith('弱联想 '):
            number=arg[len('弱联想 '):].strip()
            if not number.isdigit():return '格式：/dlm 弱联想 待审记录序号'
            result=self.store.propose(scope,int(number),weak=True)
            if not result['candidates']:return '未找到规则候选；不代表没有相关记忆。'
            return ('未确认的事件候选：'+'；'.join(x['title']+'（'+x['method']+'）' for x in result['candidates'])
                    +'。请核对原文后再决定是否关联。')
        if arg.startswith('建议 '):
            number=arg[3:].strip()
            if not number.isdigit():return '格式：/dlm 建议 待审记录序号'
            result=self.store.propose(scope,int(number),semantic=bool(self.model_dir))
            choices=result['candidates']
            if choices:
                detail='；'.join(x['title']+'（'+x['method']+'）' for x in choices)
                response='未确认的事件候选：'+detail+'。请查看原文后使用 /dlm 关联 序号|标题。'
            else:response='未找到事件候选；仍可手动指定新事件。'
            if result['new_event_candidate']:
                x=result['new_event_candidate']
                response+=' 新事件标题候选：'+x['title']+'（'+x['method']+'，待确认）。'
            if result['semantic_error']:response+=' 本次语义模型不可用，仅显示本地规则候选。'
            return response
        if arg.startswith('新分组草稿 '):
            fields=arg[len('新分组草稿 '):].strip().split('|')
            if len(fields)>2 or (len(fields)==2 and (not fields[1].isascii() or not fields[1].isdigit() or len(fields[1])>4)):
                return '格式：/dlm 新分组草稿 草稿ID|页码。'
            try:return self._render_new_group(self.store.read_new_group_draft(scope,fields[0].strip()),
                                               int(fields[1]) if len(fields)==2 else 1)
            except Exception:return '新分组草稿不存在，或来源、标题、事件历史已变化。'
        if arg.startswith('确认新分组 '):
            did,sep,raw=arg[len('确认新分组 '):].partition('|');seqs=None
            if sep:
                parts=[x.strip() for x in raw.replace('，',',').split(',')]
                if not 1<=len(parts)<=20 or any(not x.isascii() or not x.isdigit() or len(x)>19 or int(x)>9223372036854775807 for x in parts):
                    return '格式：/dlm 确认新分组 草稿ID 或 草稿ID|序号,序号。'
                seqs=[int(x) for x in parts]
            try:result=self.store.confirm_new_group_draft(scope,did.strip(),seqs)
            except Exception:return '确认未开始：选择无效，来源、事件标题或历史有变化，或原话无法完整保留。'
            done=','.join(str(x) for x in result['completed']) or '无'
            if result['failed'] is not None:
                return '确认失败，本次写入已回滚；出错序号：'+str(result['failed'])+'。此前已保存的记录保留，核对后可重试。'
            return '已关联序号：'+done+'；新事件和原话关联由操作者确认，不等于事实已核验。'
        if arg.startswith('分组草稿 '):
            try:return self._render_group_draft(self.store.read_group_draft(scope,arg[len('分组草稿 '):].strip()))
            except Exception:return '草稿不存在或来源、事件已发生变化，无法继续确认。'
        if arg.startswith('确认分组 '):
            did,sep,raw=arg[len('确认分组 '):].partition('|')
            seqs=None
            if sep:
                parts=[x.strip() for x in raw.replace('，',',').split(',')]
                if not 1<=len(parts)<=10 or any(not x.isascii() or not x.isdigit() or len(x)>19 for x in parts):
                    return '格式：/dlm 确认分组 草稿ID 或 草稿ID|序号,序号。'
                seqs=[int(x) for x in parts]
            try:result=self.store.confirm_group_draft(scope,did.strip(),seqs)
            except Exception:return '确认未开始：草稿、所选记录、来源或事件历史不可用或已变化，请重新核对。'
            done=','.join(str(x) for x in result['completed']) or '无'
            if result['failed'] is not None:
                return '确认失败，本次写入已回滚；出错序号：'+str(result['failed'])+'。此前已保存的记录保留，核对后可重试。'
            return '已关联序号：'+done+'；原话保留，分组关系由操作者确认，不等于事实已核验。'
        if arg.startswith('待确认关系 '):
            raw=arg[len('待确认关系 '):];title,sep,page=raw.partition('|')
            if not title.strip() or (sep and (not page.isascii() or not page.isdigit() or len(page)>5)):
                return '格式：/dlm 待确认关系 事件标题 或 事件标题|页码。'
            try:result=self.store.linked_update_clues(scope,title.strip(),int(page) if sep else 1)
            except ValueError:return '事件不存在、来源异常或记录过多。'
            if not result['items']:return '没有更多规则线索；不代表没有其他更新。'
            lines=['可能需要核对关系的已关联原话（规则线索，可能误判）：']
            for x in result['items']:
                lines.append('新 ['+x['new_id']+'] '+x['content']+'\n较早 ['+x['old_id']+'] '+x['old_content'])
            lines.append('较早原话仅按时间选取，未确认相关；核对后可用 /dlm 关系 修正|新ID|旧ID，或 /dlm 排除更新提示 事件标题|新ID。')
            if result['has_more']:lines.append('下一页：/dlm 待确认关系 '+title.strip()+'|'+str((int(page) if sep else 1)+1))
            return '\n'.join(lines)
        if arg.startswith('排除更新提示 '):
            title,sep,mid=arg[len('排除更新提示 '):].partition('|')
            if not sep or not title.strip() or not mid.strip():return '格式：/dlm 排除更新提示 事件标题|原话ID。'
            try:self.store.dismiss_linked_update(scope,title.strip(),mid.strip())
            except ValueError:return '线索不存在、来源异常或已处理。'
            return '已排除此条更新提示，原话和已有关系均保留。'
        if arg.startswith('关联更新 '):
            fields=[x.strip() for x in arg[len('关联更新 '):].split('|')]
            names={'修正':'corrects','替代':'supersedes','相关':'related'}
            if (len(fields)!=4 or not fields[0].isascii() or not fields[0].isdigit() or
                len(fields[0])>19 or not fields[1] or not fields[2] or fields[3] not in names):
                return '格式：/dlm 关联更新 序号|事件标题|旧原话ID|修正；也可选替代或相关。'
            try:self.store.link_capture_relation(scope,int(fields[0]),fields[1],fields[2],names[fields[3]])
            except Exception:
                return '关联更新未完成：本次写入已回滚；请核对来源、时间和已有关系。'
            return '已按你的选择关联新原文并记录关系，旧原文保留；关系未经过客观事实核验。'
        if arg.startswith('批量关联 '):
            raw,sep,title=arg[len('批量关联 '):].partition('|')
            parts=raw.replace('，',',').split(',')
            if (not sep or not title.strip() or not 1<=len(parts)<=10 or
                any(not x.strip().isascii() or not x.strip().isdigit() or len(x.strip())>19 for x in parts)):
                return '格式：/dlm 批量关联 1,2,3|事件标题（最多10条，序号不重复）。'
            try:result=self.store.link_capture_batch(scope,[int(x.strip()) for x in parts],title.strip())
            except ValueError:
                return '批量关联未开始：序号无效、原文不可用或无法完整保留，或已有其他关联；请逐条核对。'
            done=','.join(str(x) for x in result['completed']) or '无'
            if result['failed'] is not None:
                return ('批量关联失败，本次写入已回滚；出错序号：'+str(result['failed'])+
                        '。此前已保存的记录保留，核对后可重试。')
            return '已关联序号：'+done+'。原文逐条保留；关联不等于事实核验或自动确认修正关系。'
        if arg.startswith('关联 '):
            rest=arg[3:]; number,sep,title=rest.partition('|')
            if not sep or not number.strip().isdigit() or not title.strip():
                return '格式：/dlm 关联 记录序号|事件标题'
            self.store.link_capture(scope,int(number.strip()),title.strip())
            return '已将原文关联事件；输入者审核的是关联关系，不能据此确认客观事实。'
        if arg=='采集状态':
            gaps=self.store.capture_gap_count(scope)
            active=(sender,origin) in self.capture_gaps or bool(gaps)
            if self.auto_gap_recovery_enabled:
                return ('采集开关：'+('开启' if self.capture_enabled else '关闭')+'；本次运行检测到的采集失败：'+str(self.capture_skipped)+
                    ('；缺口标记正在保存，暂不注入记忆' if (sender,origin) in self.capture_gaps else
                     '；历史存在缺口，仅尝试使用缺口后的原话' if self.store.capture_gap_boundary(scope) else '；未发现缺口')+
                    '。缺失原话无法自动恢复，未发现缺口不代表采集完整。')
            return ('采集开关：'+('开启' if self.capture_enabled else '关闭')+'；缓冲等待数：'+str(self.capture_queue.qsize())+
                    '；本次运行缓冲满或写入失败数：'+str(self.capture_skipped)+
                    '；本会话未核对缺口：'+str(gaps)+('（记忆注入已暂停）' if active else '（未发现缺口）')+
                    '。只覆盖已检测的缓冲满和写入异常，不代表采集完整。')
        if arg.startswith('记录 '):
            rest=arg[3:]; title,sep,content=rest.partition('|')
            if not sep: return '格式：/dlm 记录 事件标题|记录内容'
            title,content=title.strip(),content.strip()
            self.store.save(scope,title,content)
            return '已保存原文并关联到指定事件；这只是你的记录，不代表事实已被独立验证。'
        if arg.startswith('检索 '):
            try: names=self.store.search(scope,arg[3:].strip())
            except ValueError as exc:
                if str(exc)=='no_model_path':return '未设置本地模型路径；记录仍可保存和查看。'
                raise
            return ('联想候选：'+'、'.join(names)+'。可用 /dlm 查看 事件标题 阅读原文。'
                    if names else '没有找到达到阈值的候选。')
        if arg=='摘要缓存状态':
            return '分段缓存'+('已开启' if self.segment_cache_enabled else '已关闭')+'；当前作用域保存 '+str(self.store.segment_cache_count(scope))+' 条（含可能已过期的条目）。'
        if arg=='清理摘要缓存':
            return '已清理当前作用域 '+str(self.store.clear_segment_cache(scope))+' 条分段缓存；原话和已保存草稿保留。'
        if arg.startswith('摘要草稿 '):
            fields=arg[len('摘要草稿 '):].strip().split('|')
            if len(fields)>2 or (len(fields)==2 and (not fields[1].isascii() or not fields[1].isdigit() or len(fields[1])>4)):
                return '格式：/dlm 摘要草稿 草稿ID|页码。'
            try:return self._render_brief_draft(self.store.read_model_brief_draft(scope,fields[0].strip()),
                                                int(fields[1]) if len(fields)==2 else 1)
            except ValueError:return '摘要草稿不存在。'
        if arg.startswith('确认摘要 '):
            try:brief_id=self.store.confirm_model_brief_draft(scope,arg[len('确认摘要 '):].strip())
            except ValueError:return '确认失败：草稿不存在、来源或更新状态已变化，请重新生成。'
            return '已确认并保存摘要 '+str(brief_id)+'；这是人工审核的模型派生内容，并非事实核验，原话仍保留。'
        if arg.startswith('人工摘要保存 '):
            fields=arg[len('人工摘要保存 '):].split('|',3)
            if len(fields)!=4 or any(not x.strip() for x in fields):
                return '格式：/dlm 人工摘要保存 事件标题|原话ID|原文摘录|人工摘要。'
            try:brief_id=self.store.save_source_brief(scope,*(x.strip() for x in fields))
            except ValueError:return '保存失败：事件、来源或摘录不匹配，或内容超出长度。'
            return '已保存人工摘要 '+str(brief_id)+'；仅为派生说明，摘录匹配不等于摘要已核实，原话仍保留。'
        if arg.startswith('人工摘要查看 '):
            try:item=self.store.read_source_brief(scope,arg[len('人工摘要查看 '):].strip())
            except ValueError:return '事件不存在或来源校验失败。'
            if item is None:return '该事件没有人工摘要。'
            origin={'human_written':'人工编写','model_reviewed':'模型生成、人工确认',
                    'legacy_unknown':'旧摘要，生成方式未记录'}.get(item['origin'],'生成方式未知')
            return ('摘要（'+origin+'；'+('来源未变化' if item['current'] else '已过期，不参与上下文')+'）：'+item['brief']+
                    '\n原话ID：'+item['source_id']+'；摘录：'+item['excerpt']+'\n摘要可能遗漏或误解，不是客观事实核验。')
        if arg.startswith('摘录查 '):
            draft=self.store.read_extract_draft(scope,arg[4:].strip())
            return self._render_draft(draft)
        if arg.startswith('模型草稿 '):
            return self._render_model_draft(self.store.read_model_draft(scope,arg[5:].strip()))
        if arg.startswith('对照草稿 '):
            return self._render_contrast(self.store.read_contrast_draft(scope,arg[5:].strip()))
        if arg.startswith('批量确认对照 '):
            raw,sep,kind=arg[len('批量确认对照 '):].partition('|')
            names={'修正':'corrects','替代':'supersedes','相关':'related'}
            ids=[x.strip() for x in raw.replace('，',',').split(',')]
            if not sep or kind.strip() not in names:
                return '格式：/dlm 批量确认对照 ID,ID|修正；也可选择替代或相关。'
            try:result=self.store.confirm_contrast_batch(scope,ids,names[kind.strip()])
            except Exception:return '批量确认未开始：请核对草稿、来源、事件历史和共同基线。'
            done=','.join(result['completed']) or '无'
            if result['failed']:
                return '批量确认失败，已回滚本次操作；失败草稿：'+result['failed']+'。已有历史不变，请核对后重试。'
            return '已确认：'+done+'；每条仅与该草稿展示的旧原话建立所选关系，不自动关联新记录之间的关系；未验证客观事实。'
        if arg.startswith('确认对照 '):
            fields=arg[len('确认对照 '):].split('|')
            names={'修正':'corrects','替代':'supersedes','相关':'related'}
            if len(fields)!=2 or fields[1].strip() not in names:
                return '格式：/dlm 确认对照 草稿ID|修正；也可选择替代或相关。'
            try:
                self.store.confirm_contrast(scope,fields[0].strip(),names[fields[1].strip()])
            except Exception:
                return '确认未完成，本次写入已回滚；请核对来源、事件历史和已有关系。'
            return '已按你选择的关系关联原文；旧记录保留。该关系由操作者确认，不代表客观事实已核验。'
        if arg.startswith('摘录 '):
            draft=self.store.create_extract_draft(scope,arg[3:].strip())
            return self._render_draft(draft)
        if arg.startswith('优先级查看 '):
            try:level=self.store.event_priority(scope,arg[len('优先级查看 '):].strip())
            except ValueError:return '事件不存在、名称不唯一或来源校验失败。'
            return '事件优先级：'+('重要' if level=='high' else '普通')+'；优先级不是事实可信度。'
        if arg.startswith('优先级 '):
            title,sep,value=arg[len('优先级 '):].partition('|');levels={'重要':'high','普通':'normal'}
            if not sep or not title.strip() or value.strip() not in levels:return '格式：/dlm 优先级 事件标题|重要；恢复使用普通。'
            try:self.store.event_priority(scope,title.strip(),levels[value.strip()])
            except ValueError:return '事件不存在、名称不唯一或来源校验失败。'
            return '已设置事件优先级；只有开启优先级排序时才影响弱联想候选顺序，不改变原话或事实状态。'
        if arg.startswith('别名查看 '):
            title=arg[len('别名查看 '):].strip()
            try:aliases=self.store.list_aliases(scope,title)
            except ValueError:return '事件不存在、名称不唯一或来源校验失败。'
            return '事件别名：'+('、'.join(aliases) if aliases else '暂无')
        if arg.startswith('别名 ') or arg.startswith('别名删除 '):
            remove=arg.startswith('别名删除 ')
            value=arg[len('别名删除 '):] if remove else arg[len('别名 '):]
            title,sep,alias=value.partition('|')
            if not sep:return '格式：/dlm 别名 事件标题|别名；删除使用 /dlm 别名删除 事件标题|别名。'
            try:changed=self.store.event_alias(scope,title.strip(),alias.strip(),remove=remove)
            except ValueError:return '事件或别名不可用；请检查来源、长度和是否与其他事件名称冲突。'
            if remove:return '已删除别名。' if changed else '该别名不存在。'
            return '已添加事件别名。' if changed else '该别名已存在。'
        if arg.startswith('日期历史 '):
            try:rows=self.store.date_history(scope,arg[len('日期历史 '):].strip())
            except ValueError:return '找不到已关联原文或原文校验失败。'
            return ('日期标注历史（最近10条）：\n'+'\n'.join((x['occurred_date'] or '未知')+' | 标注于 '+x['annotated_at']+(' | 原文已变化' if not x['source_matches'] else '') for x in rows)
                    if rows else '该原文没有手动日期标注。')
        if arg.startswith('日期 '):
            mid,sep,value=arg[len('日期 '):].partition('|')
            if not sep:return '格式：/dlm 日期 原文ID|YYYY-MM-DD；日期不确定可填未知。'
            try:changed=self.store.annotate_date(scope,mid.strip(),value.strip())
            except ValueError:return '日期无效，或找不到已关联原文、原文校验失败。请使用 YYYY-MM-DD 或未知。'
            return '已保存发生日期标注；原文与记录时间保留。' if changed else '该日期标注已存在。'
        if arg=='事件' or arg.startswith('事件 '):
            value=arg[len('事件'):].strip()
            if value and (not value.isdigit() or not 1<=int(value)<=10000):return '格式：/dlm 事件 [页码]'
            number=int(value) if value else 1
            catalog=self.store.event_catalog(scope,number)
            if not catalog['events']:return '没有更多已关联事件。'
            lines=['事件目录（第'+str(number)+'页）：']
            for event in catalog['events']:
                lines.append(event['title']+'：'+str(event['records'])+'条；最近记录 '+event['last_recorded_at']+('；来源校验异常' if event['stale'] else ''))
            if catalog['has_more']:lines.append('下一页：/dlm 事件 '+str(number+1))
            lines.append('查看详情：/dlm 查看 事件标题')
            return '\n'.join(lines)
        if arg=='时间线' or arg.startswith('时间线 '):
            parts=arg[len('时间线'):].strip().split()
            if len(parts)>2 or (parts and parts[0] not in ('记录','发生')):
                return '格式：/dlm 时间线 记录 [游标]；或 /dlm 时间线 发生 [游标]。'
            mode=parts[0] if parts else '记录';cursor=parts[1] if len(parts)==2 else ''
            try:page=self.store.timeline(scope,mode,cursor)
            except ValueError:return '时间线游标无效，请从第一页重新查看。'
            if not page['records']:return '没有更多已关联记录。发生时间未知的记录不在发生时间线中。'
            names={'corrects':'修正','supersedes':'替代','related':'相关','retracts':'撤回'}
            lines=['已关联原话时间线（按'+('记录时间' if mode=='记录' else '发生日期标注')+'倒序；只代表用户曾说过）：']
            for row in page['records']:
                stamp=row['recorded_at'] if mode=='记录' else row['occurred_at']
                lines.append('['+row['id']+' | '+stamp+' | '+row['title']+'] '+(row['content'] if row['integrity'] else '原文校验失败'))
                for edge in row['relations']:
                    lines.append('关系：'+edge['newer']+' '+names.get(edge['kind'],'未知')+' '+edge['older'])
            if page['next_cursor']:lines.append('下一页：/dlm 时间线 '+mode+' '+page['next_cursor'])
            return '\n'.join(lines)
        if arg.startswith('查看 '):
            title,sep,page_text=arg[3:].strip().rpartition('|')
            if not sep:title=page_text;page=1
            elif not page_text.isdigit() or int(page_text)<1:return '格式：/dlm 查看 事件标题|页码'
            else:page=int(page_text)
            rows=self.store.show(scope,title.strip())
            if not rows:return '未找到对应的已关联记录。'
            end=len(rows)-(page-1)*5
            if end<=0:return '没有更多记录。'
            selected=rows[max(0,end-5):end]
            lines=['记录原文（从新到旧分页，第'+str(page)+'页）：']
            relations={}
            for row in selected:
                lines.append('['+row['id']+' | '+row['recorded_at']+'] '+('（历史导入，时间来自文件）' if row['imported'] else '')+row['content'])
                for edge in row['relations']:
                    relations[(edge['newer'],edge['older'],edge['kind'])]=edge
            names={'corrects':'修正','supersedes':'替代','related':'相关'}
            for newer,older,kind in relations:
                lines.append('关系：'+newer+' '+names.get(kind,kind)+' '+older)
            if end>5:lines.append('下一页：/dlm 查看 '+title.strip()+'|'+str(page+1))
            return '\n'.join(lines)
        if arg.startswith('关系 '):
            fields=arg[3:].split('|')
            names={'修正':'corrects','替代':'supersedes','相关':'related'}
            if len(fields)!=3 or fields[0].strip() not in names:
                return '格式：/dlm 关系 修正|较新记录ID|较旧记录ID；也支持替代、相关。'
            made=self.store.relate_sources(scope,fields[1].strip(),fields[2].strip(),names[fields[0].strip()])
            return '已记录两条原文的关系，旧记录仍保留。' if made else '该关系已存在。'
        if arg.startswith('备份校验 '):
            try:report=self.store.verify_stored_backup(arg[len('备份校验 '):].strip())
            except Exception:return '备份校验未完成：文件不存在、名称无效或无法读取。'
            if not report['ok']:return '备份校验失败：'+','.join(report['errors'])+'；未修改该文件。'
            warnings='；缺少可选表，可能是旧版备份' if report['warnings'] else ''
            return ('备份校验通过：原话及关联来源哈希、外键和 SQLite 完整性已检查；待审/采集记录 '
                    +str(report['counts']['captured'])+' 条、写入记录 '+str(report['counts']['ingested'])+' 条'+warnings+'。')
        if arg=='备份': return '独立记忆库备份完成并已校验：'+self.store.backup()
        if arg in ('帮助','help','?'):
            return ('数字生命记忆库 · 常用命令\n'
                    '状态：/dlm 上下文状态、/dlm 记忆状态、/dlm 采集状态\n'
                    '线索：/dlm 待审、/dlm 更新线索、/dlm 事件、/dlm 时间线 记录\n'
                    '查看：/dlm 查看 标题、/dlm 摘要缓存状态\n'
                    '记录：/dlm 记录 标题|内容、/dlm 关联 序号|标题、/dlm 关系 修正|较新ID|较旧ID\n'
                    '维护：/dlm 备份、/dlm 备份校验 <备份文件名>\n'
                    '整理：/dlm 整理、/dlm 检索 线索\n'
                    '说明：所有线索均为“未确认”，不会自动确认事实。')
        return '未知命令或参数不全。发送 /dlm 帮助 查看常用命令。'

    @staticmethod
    def _render_draft(draft):
        label={'CURRENT':'来源未变化','STALE':'来源已变化，摘录已过期','PENDING_UPDATE':'存在待审更新'}[draft['status']]
        result=['原文摘录（不是自动摘要） '+draft['draft_id']+'：'+label]
        if draft['partial']:result.append('仅包含最近5条，不代表完整历史。')
        for row in draft['records']:
            result.append('['+row['id']+' | '+row['recorded_at']+'] '+row['content'])
        return '\n'.join(result)
