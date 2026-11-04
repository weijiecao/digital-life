"""M2 评估集语料（自造、分层、版本化）。

口径说明（必须与 EVAL_SET.md 一致）：
- 这不是真实聊天记录，而是**按真实对话形态自造的样本**；路线图 W5–W7 允许"脱敏或自造"。
- 目的：给后续所有改动一把可复跑的尺子，并覆盖三类场景（无内容追问 / 有内容改写 / 负例）。
- 计数：20 明确事件 + 20 改期改口 + 15 取消否定 + 15 相似事件 + 15 跨日补充 + 15 指代追问
  + 20 负例 = 120 条。

每条 TOPIC：
  name       事件的可命名说法（用于"明确事件"查询）
  base       最初的原话
  change     改期/改口的原话
  cancel     取消/否定的原话
  distractor 相似但不同的事件原话（用于"相似事件"与干扰）
  q_named    明确提到事件的问法
  q_content  只提内容、不提事件名的问法
"""

BASE_DAY = '2025-01-01'
LATER_DAY = '2025-01-03'
QUERY_DAY = '2025-01-05'

TOPICS = [
    dict(name='书店', base='原计划周六去书店找编程资料', change='改到周日去书店',
         cancel='书店这周不去了', distractor='周六去爬山看日出',
         q_named='书店的事安排得怎么样？', q_content='之前找编程资料那件事怎么安排的？'),
    dict(name='搬家', base='下个月十五号搬到杭州西湖区', change='搬家改到滨江区',
         cancel='搬家先不搬了', distractor='下个月去成都出差一周',
         q_named='搬家的事定了吗？', q_content='杭州那边搬家的时间定了吗？'),
    dict(name='体检', base='约了周三上午做入职体检', change='体检改到周五上午',
         cancel='入职体检不做了', distractor='周三下午部门开会',
         q_named='体检约在什么时候？', q_content='入职体检最后是什么时候？'),
    dict(name='练车', base='科目二练车定在周六早上', change='练车挪到周一早上',
         cancel='这周不练车了', distractor='周六陪朋友去看车',
         q_named='练车是什么时候？', q_content='科目二练车改到哪天？'),
    dict(name='疫苗', base='周末带猫去打疫苗', change='疫苗改成下周三',
         cancel='疫苗先不打了', distractor='周末给狗洗澡',
         q_named='猫的疫苗打了吗？', q_content='带猫打疫苗安排在哪天？'),
    dict(name='青岛', base='国庆去青岛看海', change='改去威海看海',
         cancel='国庆不出门了', distractor='国庆回老家看外婆',
         q_named='青岛的行程怎么样？', q_content='国庆去海边这事定的哪里？'),
    dict(name='装修', base='客厅地面换木地板', change='客厅改铺瓷砖',
         cancel='装修先暂停', distractor='卧室刷白墙',
         q_named='装修进展如何？', q_content='客厅地面最后选了什么材料？'),
    dict(name='婚礼', base='十月二号参加表哥婚礼', change='婚礼推迟到十月底',
         cancel='婚礼不去了', distractor='十月二号同学聚会',
         q_named='婚礼是什么时候？', q_content='表哥婚礼还去吗？'),
    dict(name='面试', base='周四下午面试后端岗位', change='面试改到下周一下午',
         cancel='面试取消了', distractor='周四要交项目报告',
         q_named='面试安排在哪天？', q_content='后端岗位的面试改到什么时候？'),
    dict(name='健身', base='办了年卡每周三练腿', change='改到每周五练腿',
         cancel='不练了想退卡', distractor='每周三打羽毛球',
         q_named='健身卡怎么安排的？', q_content='每周练腿改到星期几了？'),
    dict(name='修车', base='车放4S店换刹车片', change='取车推迟到周六',
         cancel='先不修了', distractor='周六去给车做保养',
         q_named='修车的情况怎么样？', q_content='刹车片什么时候换好？'),
    dict(name='房租', base='月底交下季度房租六千', change='房租降到五千五',
         cancel='还没凑齐房租', distractor='月底交物业费两百',
         q_named='房租的事怎么说？', q_content='下季度房租最后定多少？'),
    dict(name='复查', base='爷爷下周二复查心脏', change='复查改到周四上午',
         cancel='复查暂时不去了', distractor='下周二陪奶奶去买菜',
         q_named='爷爷复查是什么时候？', q_content='爷爷心脏复查改到哪天了？'),
    dict(name='雅思', base='报名了十二月的雅思考试', change='雅思改到一月',
         cancel='雅思退考了', distractor='十二月考驾照理论',
         q_named='雅思报名怎么样？', q_content='雅思考试时间改到几月？'),
    dict(name='聚会', base='周五晚上同学聚会', change='聚会改到周六晚上',
         cancel='聚会取消了', distractor='周五晚上要加班',
         q_named='聚会定在什么时候？', q_content='同学聚会还办吗？'),
    dict(name='快递', base='快递寄到公司前台', change='快递改寄到家里',
         cancel='快递已经退了', distractor='快递到了邻居家',
         q_named='快递寄到哪里？', q_content='那个快递最后寄到哪了？'),
    dict(name='电影', base='周六下午看科幻电影', change='电影改到周日上午',
         cancel='电影不看了', distractor='周六下午去看展览',
         q_named='电影票买好了吗？', q_content='周六下午那场电影改成什么时候了？'),
    dict(name='报销', base='周一提交差旅报销一千二', change='报销改到周二交',
         cancel='报销先不交了', distractor='周一提离职申请',
         q_named='报销交了吗？', q_content='差旅报销改到哪天交？'),
    dict(name='生日', base='三月五号给妈妈过生日', change='生日提前到三号',
         cancel='生日不办了', distractor='三月五号出差',
         q_named='妈妈生日怎么安排？', q_content='给妈妈过生日定在哪天？'),
    dict(name='房贷', base='二十号前还房贷七千', change='还款日改到二十五号',
         cancel='这个月还不上了', distractor='二十号交物业费',
         q_named='房贷还了吗？', q_content='这个月房贷什么时候还？'),
]

NEGATIVES = [
    ('上回去火星租飞船的事情怎么样了？', '火星租飞船'),
    ('飞船码头的停车规则是什么？', '飞船码头停车'),
    ('今天上海天气怎么样？', '上海天气'),
    ('明天股票会涨还是跌？', '股票涨跌'),
    ('帮我推荐一部最近上映的纪录片', '纪录片推荐'),
    ('圆周率小数点后第十位是什么？', '圆周率'),
    ('怎么把冰箱里的剩饭做成炒饭？', '剩饭炒饭'),
    ('下周世界杯决赛几点开始？', '世界杯决赛'),
    ('可以教我一下傅里叶变换吗？', '傅里叶变换'),
    ('高铁票退票手续费怎么算？', '高铁退票手续费'),
    ('之前去看过的那部话剧叫什么名字？', '话剧名字'),
    ('我上次说的那个外星人朋友在哪？', '外星人朋友'),
    ('冰箱坏了应该找谁修？', '冰箱维修'),
    ('我家猫会开冰箱吗？', '猫开冰箱'),
    ('怎么申请护照？', '护照申请'),
    ('楼下咖啡店的拿铁多少钱？', '咖啡店拿铁'),
    ('这个月电费怎么这么高？', '电费偏高'),
    ('有没有好看的科幻小说推荐？', '科幻小说推荐'),
    ('明天会下雨吗？', '明天下雨'),
    ('怎么才能早起不困？', '早起不困'),
]

NEGATIVE_MEMORY = '周末在家整理了书架'


def _at(day, hour=10, minute=0):
    return '%sT%02d:%02d:00+00:00' % (day, hour, minute)


def build_cases():
    """返回 120 条用例；每条都自带作用域内的历史与预期。"""
    cases = []
    for index, topic in enumerate(TOPICS, 1):
        tid = '%02d' % index
        cases.append({
            'id': 'named-%s' % tid, 'category': 'named_event', 'kind': 'positive',
            'messages': [(topic['base'], _at(BASE_DAY))],
            'query': topic['q_named'], 'expect': [topic['base']],
            'forbid': [topic['distractor']], 'as_of': _at(QUERY_DAY),
        })
    for index, topic in enumerate(TOPICS, 1):
        tid = '%02d' % index
        cases.append({
            'id': 'change-%s' % tid, 'category': 'reschedule', 'kind': 'correction',
            'messages': [(topic['base'], _at(BASE_DAY, 10, 0)),
                         (topic['change'], _at(BASE_DAY, 10, 2))],
            'query': topic['q_content'], 'expect': [topic['base'], topic['change']],
            'forbid': [topic['distractor']], 'as_of': _at(QUERY_DAY),
            'require_unconfirmed': True,
        })
    for index, topic in enumerate(TOPICS[:15], 1):
        tid = '%02d' % index
        cases.append({
            'id': 'cancel-%s' % tid, 'category': 'cancel', 'kind': 'correction',
            'messages': [(topic['base'], _at(BASE_DAY, 10, 0)),
                         (topic['cancel'], _at(BASE_DAY, 10, 2))],
            'query': topic['q_content'], 'expect': [topic['base'], topic['cancel']],
            'forbid': [topic['distractor']], 'as_of': _at(QUERY_DAY),
            'require_unconfirmed': True,
        })
    for index, topic in enumerate(TOPICS[:15], 1):
        tid = '%02d' % index
        cases.append({
            'id': 'similar-%s' % tid, 'category': 'similar_event', 'kind': 'positive',
            'messages': [(topic['base'], _at(BASE_DAY, 10, 0)),
                         (topic['distractor'], _at(BASE_DAY, 10, 5))],
            'query': topic['q_named'], 'expect': [topic['base']],
            'forbid': [topic['distractor']], 'as_of': _at(QUERY_DAY),
        })
    for index, topic in enumerate(TOPICS[:15], 1):
        tid = '%02d' % index
        cases.append({
            'id': 'crossday-%s' % tid, 'category': 'cross_day', 'kind': 'positive',
            'messages': [(topic['base'], _at(BASE_DAY, 10, 0)),
                         (topic['distractor'], _at(LATER_DAY, 15, 0))],
            'query': topic['q_content'], 'expect': [topic['base']],
            'forbid': [topic['distractor']], 'as_of': _at(QUERY_DAY),
        })
    for index, topic in enumerate(TOPICS[:15], 1):
        tid = '%02d' % index
        cases.append({
            'id': 'followup-%s' % tid, 'category': 'anaphora', 'kind': 'positive',
            'messages': [(topic['base'], _at(BASE_DAY))],
            'query': topic['q_content'], 'followup': ['后来呢？'],
            'expect': [topic['base']], 'forbid': [],
            'as_of': _at(QUERY_DAY),
        })
    for index, (query, label) in enumerate(NEGATIVES, 1):
        cases.append({
            'id': 'negative-%02d' % index, 'category': 'negative', 'kind': 'negative',
            'messages': [(NEGATIVE_MEMORY, _at(BASE_DAY)),
                         ('下周准备去图书馆还书', _at(LATER_DAY))],
            'query': query, 'expect': [], 'forbid': [],
            'as_of': _at(QUERY_DAY), 'label': label,
        })
    return cases
