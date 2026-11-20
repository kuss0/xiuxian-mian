"""Real Chromium + pinned assistant + native public readers; all HTTP is mocked.

The native reader bodies and status shapes come from the 2026-11-20 read-only
audit. This exercises browser integration, not live dungeon completion.
"""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from tools import qingxi_quest_browser as worker


def test_real_chromium_reads_all_nine_native_reward_resources_without_gameplay():
    fixture = json.loads((Path(__file__).parent / 'fixtures/qingxi-native-reward-readers-20261120.json').read_text())
    bootstrap = """(() => {
      const state={preview:false,token:'fixture-token',initData:'fixture-init',sectionLoaded:{inventory:true}};
      const worldQuests={refresh(){}};
      const worldStoryContentAvailable=()=>true;
      async function postJson(url,body,options={}) {
        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(body),signal:options.signal});
        return await response.json();
      }
    """ + '\n'.join(fixture['functions']) + """
      Object.assign(window,{worldTrialRequest,worldMoonRequest,worldBloodRequest,worldSiegeRequest,worldEscortRequest});
      window.__fc={stats:()=>({active:false,overworld:false})};
    })();"""

    async def exercise():
        requests, unexpected, errors = [], [], []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking'])
            try:
                context = await browser.new_context()
                async def route(handler):
                    request = handler.request
                    path = urlsplit(request.url).path
                    if request.resource_type == 'document':
                        await handler.fulfill(content_type='text/html',
                            headers={'Content-Security-Policy': "default-src 'self'; script-src 'self'"},
                            body='<title>Native reward read integration</title>')
                        return
                    if not path.startswith('/api/miniapp/xianxia-dwelling/'):
                        unexpected.append(path)
                        await handler.abort()
                        return
                    resource = path.rsplit('/', 1)[-1]
                    body = request.post_data_json
                    requests.append((resource, body.get('action')))
                    if resource == 'start':
                        await handler.fulfill(json={'ok': True, 'account': {'playerId': 42}})
                        return
                    assert body.get('action') == 'status', 'Inspection must not start gameplay'
                    status = next(row for row in fixture['reward_statuses']
                                  if row['resource'] == resource and row['arena'] == body.get('arena'))
                    mapping = {row[0]: (row[1], row[2]) for row in (
                        ('moon-palace', 'moonPalace', 'moon'), ('blood-land', 'bloodLand', 'blood'),
                        ('south-gate', 'southGate', 'siege'), ('refugee-escort', 'refugeeEscort', 'escort'),
                        ('mulan-shrine', 'mulanShrine', 'shrine'), ('baiqiao-puppet', 'baiqiaoPuppet', 'puppet'),
                        ('jianzhong-trial', 'jianzhongTrial', 'jianzhong'), ('trial', 'trial', 'status'))}
                    envelope, key = mapping[resource]
                    await handler.fulfill(json={'ok': True, envelope: {key: status['status'], 'grants': []}})

                await context.route('**/*', route)
                await context.add_init_script(script=bootstrap + '\n' + worker.browser_script(42, reward_batch=True, read_only=True))
                page = await context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto('https://asc.aiopenai.app/fixture')
                await page.evaluate("fetch('/api/miniapp/xianxia-dwelling/start',{method:'POST',body:'{}'})")
                result = await page.evaluate('window.__qingxiRewardBatch.inspect()')
                assert result['verified'] is True and result['player'] == 42
                assert len(result['daily']) == 7 and all(row['hasKey'] is False for row in result['daily'])
                assert result['trials']['longwind']['remaining'] == 3
                assert result['trials']['bamboo']['remaining'] == 2
                assert len({resource for resource, _ in requests}) == 9  # auth + seven dungeons + shared trial route
                assert all(action == 'status' for resource, action in requests if resource != 'start')
                assert not unexpected and not errors
                assert await page.evaluate('window.__qx.pending.size') == 0
                assert await page.evaluate("localStorage.getItem('qingxi-local-pending:42')") in (None, '[]')
            finally:
                await browser.close()

    asyncio.run(exercise())
