"""Render the real activity control code and verify read-only action wiring."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


def test_reward_progress_renders_thirteen_slots_and_scoped_review_button():
    script = Path(__file__).parents[1] / 'model/web/static/js/dungeon_ui.js'
    states = ['locked'] * 7 + ['used'] + ['pending'] * 5
    progress = {'day': '2026-11-20', 'phase': 'partial', 'claimed': 0, 'trial_used': 1,
                'slots': [{'id': str(i), 'title': f'槽位{i + 1}', 'state': status} for i, status in enumerate(states)]}
    config = {'qingxi_rewards': {'enabled': False, 'participant_identity_ids': [42]},
              'qingxi_reward_progress': {'42': progress}}

    async def exercise():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                                       args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                page = await browser.new_page(viewport={'width': 390, 'height': 844})
                await page.set_content('<main id="root"></main>')
                await page.evaluate("""() => {
                  window.escapeHtml=value=>String(value??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
                  window.calls=[];window.postJson=async(url,payload)=>{calls.push({url,payload});return {message:'fixture'}};
                  window.updateFlash=()=>{};window.refreshState=async()=>{};
                }""")
                await page.add_script_tag(path=str(script))
                await page.evaluate("""config => {document.querySelector('#root').innerHTML=renderQingxiRewardConfig(
                    config,[{identity_id:42,display_name:'<fixture>'}],()=>'<p>选择参与账号</p>');}""", config)
                assert await page.locator('li').count() == 13
                assert not await page.locator('[name=qingxi_rewards_enabled]').is_checked()
                assert await page.locator('summary').inner_text() == '<fixture>｜2026-11-20｜每日已领 0/7｜试炼次数已用 1/6｜partial'
                await page.locator('summary').click()
                await page.get_by_role('button', name='只读核对').click()
                assert await page.evaluate('window.calls') == [{
                    'url': '/api/qingxi-quests', 'payload': {'action': 'rewards_refresh', 'send_as_id': '42'}}]
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            finally:
                await browser.close()
    asyncio.run(exercise())
