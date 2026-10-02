from telethon.extensions import html as telegram_html
from telethon.tl.types import MessageEntityBlockquote, MessageEntityCode, MessageEntityTextUrl

from model.audit_messages import bounded_html, fold_audit_body, folded_summary_details, routine_copy, short_html_text, text_units


def test_short_notice_stays_short():
    body = '<code>user</code> 闯塔完成，修为 +300'
    assert fold_audit_body(body) == body


def test_long_details_use_real_collapsed_entity_and_preserve_links():
    body = '运行结果\n' + '详细过程 ' * 70 + '\n<a href="https://t.me/example/123">原消息</a>'
    text, entities = telegram_html.parse(fold_audit_body(body))
    quote = next(e for e in entities if isinstance(e, MessageEntityBlockquote))
    assert quote.collapsed is True
    assert quote.offset == text_units('运行结果\n')
    assert any(isinstance(e, MessageEntityTextUrl) and e.url == 'https://t.me/example/123' for e in entities)
    assert '原消息' in text


def test_failure_and_manual_action_never_hidden():
    body = '批次完成\n' + '过程记录 ' * 60 + '\nWA：验证失败\n下一步：请手动重新登录'
    result = fold_audit_body(body, critical=True)
    visible = result.split('<blockquote expandable>')[0]
    assert 'WA：验证失败' in visible
    assert '请手动重新登录' in visible


def test_pending_decision_stays_outside_quote():
    body = '运行结果\n' + '过程记录 ' * 60 + '\n待处理：委托目标变化\n人工抉择：保留还是取消'
    visible = fold_audit_body(body, critical=True).split('<blockquote expandable>')[0]
    assert '待处理：委托目标变化' in visible
    assert '人工抉择：保留还是取消' in visible


def test_bounded_html_preserves_unicode_and_valid_entities():
    body = '<b>🧪🧪🧪</b><a href="https://t.me/example/123">结果结果结果</a>'
    text, entities = telegram_html.parse(bounded_html(body, 10))
    assert text_units(text) <= 10
    assert '🧪🧪🧪' in text
    assert text.endswith('…')
    assert all(e.offset + e.length <= text_units(text) for e in entities)


def test_long_single_line_is_folded_except_safety_warning():
    body = '批次结果：' + '身份完成；' * 100
    result = fold_audit_body(body)
    assert '<blockquote expandable>' in result
    assert text_units(telegram_html.parse(result.split('<blockquote')[0])[0]) <= 180
    warning = '安全锁拦截：' + '仍在等待回包，' * 50
    assert fold_audit_body(warning, critical=True) == warning


def test_summary_bound_counts_unicode_not_html_bytes():
    text, entities = telegram_html.parse(folded_summary_details(['🧪<&>' * 500] * 20))
    assert text_units(text) <= 3200
    assert len(entities) == 2
    assert any(isinstance(e, MessageEntityBlockquote) and e.collapsed for e in entities)
    assert any(isinstance(e, MessageEntityCode) and e.length == text_units(text) for e in entities)


def test_summary_strips_markup_but_protects_names_from_auto_mentions():
    line = short_html_text('<code>@actor</code> 获得 &lt;碎片&gt;\n已完成', 140)
    assert line == '@actor 获得 <碎片> 已完成'
    text, entities = telegram_html.parse(folded_summary_details([line]))
    assert text == line
    code = next(e for e in entities if isinstance(e, MessageEntityCode))
    assert code.offset == 0 and code.length == text_units(text)


def test_existing_quote_never_nested():
    body = '<blockquote expandable>' + '明细' * 160 + '</blockquote>'
    assert fold_audit_body(body) == body


def test_copy_only_rewrites_known_success_templates():
    assert routine_copy('🧘 洞府闭关 start 完成：已同步｜阶段 running') == '🧘 闭关已开始'
    assert routine_copy('🧘 洞府闭关 settle 完成：已同步｜阶段 post_summary_wait') == '🧘 闭关结算已同步'
    unknown = '洞府闭关 start 未确认：timeout｜30 分钟后保守复查'
    assert routine_copy(unknown) == unknown


def test_routine_copy_keeps_cooldown_and_resource_values():
    voyage = ('👶 洞府天机阁元婴出窍：你心念一动，丹田中的元婴化作一道流光飞出，消失在天际。\n'
              '它将在外云游 **8** 小时，为你寻觅天地奇珍。下一次发言时若已归来，将自动结算收获。')
    assert routine_copy(voyage) == '👶 元婴已出窍｜预计 8 小时后归来'
    harvest = ('🌏 洞府小世界已收割香火：你大手一挥，将凡间供奉的 **5962** 点香火尽数收入紫府。\n'
               '当前香火库存: 166675')
    assert routine_copy(harvest) == '🌏 收割香火 +5962｜库存 166675'
    unknown = harvest + '\n但结算失败，需人工核实'
    assert routine_copy(unknown) == unknown
